// Costs on the page: the session total in the header, a $ chip under every typed answer, cost and time on every
// step, an estimate on every task card (and in the "Starting…" message), and one "what this cost" popover for all
// of them. app.js calls the few hooks below; everything else (the ledger, the popover, the task-card estimates,
// the cost on recent runs) lives here. The HTML comes from costs.js.
//   GET /api/costs/prices     the price table and its sources (once)
//   GET /api/costs/estimates  per task, from its past runs, plus what each finished run cost (on start, after runs)
//   GET /api/runs/{id}        runs of this session that aren't open in the panel, while they are still going
import { esc } from "./core.js";
import {
  answerCost, answerCostText, costChipHTML, stepCost, stepCostText, estimateText, estimateSentence, ledgerPut, sessionTotals,
  sessionPillText, answerBreakdownHTML, runBreakdownHTML, stepBreakdownHTML, estimateBreakdownHTML,
  sessionBreakdownHTML, formatCost,
} from "./costs.js";

const LEDGER_KEY = "macrae-costs";
const RUNS_EVERY = 15_000; // session runs not open in the panel
const TICK = 1_000; // the pill while voice is live or a run is going
const FINAL = new Set(["ok", "failed", "cancelled"]);

export function createCostUI({ api }) {
  const $ = (sel, el = document) => el.querySelector(sel);
  const st = {
    ledger: [],
    prices: null,
    pricesAt: 0,
    estimates: {},
    runCosts: {}, // run id → {total_usd, …} for finished runs (from /api/costs/estimates)
    infos: new Map(), // run id → latest GET /api/runs/{id}
    titles: new Map(), // task id → title (for the estimate popover)
    open: null, // the ref the popover shows
    anchor: null,
  };

  // ---------- the ledger (survives a reload, like the thread) ----------
  function load() {
    try {
      const v = JSON.parse(sessionStorage.getItem(LEDGER_KEY) || "[]");
      if (Array.isArray(v)) st.ledger = v.filter((e) => e && e.kind && e.id);
      // a voice session can't survive a reload
      st.ledger = st.ledger.map((e) => (e.kind === "voice" && e.live ? { ...e, live: false, seconds: Number(e.seconds) || 0 } : e));
    } catch {}
  }
  function save() {
    try {
      sessionStorage.setItem(LEDGER_KEY, JSON.stringify(st.ledger));
    } catch {}
  }
  function put(entry) {
    st.ledger = ledgerPut(st.ledger, { at: Date.now() / 1000, ...entry });
    save();
    renderPill();
  }
  const voicePerMin = () => (st.prices && st.prices.voice ? st.prices.voice.usd_per_min : null);
  const totals = () => sessionTotals(st.ledger, { voiceUsdPerMin: voicePerMin() });

  // ---------- data ----------
  async function loadPrices() {
    if (st.prices || Date.now() - st.pricesAt < 30_000) return st.prices;
    st.pricesAt = Date.now();
    try {
      st.prices = await api("/api/costs/prices");
      renderPill();
    } catch {}
    return st.prices;
  }

  let estBusy = false;
  async function refreshEstimates() {
    if (estBusy) return;
    estBusy = true;
    try {
      const data = await api("/api/costs/estimates");
      st.estimates = data && typeof data.estimates === "object" && data.estimates ? data.estimates : {};
      st.runCosts = data && typeof data.runs === "object" && data.runs ? data.runs : {};
      decorateTasks();
      decorateRuns();
    } catch {
      // an older backend has no estimates: the cards stay as they are
    } finally {
      estBusy = false;
    }
  }

  // ---------- hooks for app.js ----------
  // A typed answer arrived: its `cost` from the backend and how long the page waited. → the cost to keep on the message.
  function noteAnswer(msgId, rawCost, clientSeconds) {
    const c = answerCost(rawCost, clientSeconds);
    if (!c) return null;
    put({ kind: "answer", id: msgId, llm: c.llm, compute: c.compute, seconds: c.seconds, cost: c, final: true });
    return c;
  }

  // A run this page started (or the voice agent did): it counts toward the session.
  function trackRun(runId, { title = "" } = {}) {
    if (!runId) return;
    const old = st.ledger.find((e) => e.kind === "run" && e.id === runId);
    put({ kind: "run", id: runId, title: title || (old && old.title) || "", final: old ? !!old.final : false });
    const info = st.infos.get(runId);
    if (info) noteRun(runId, info);
  }

  // Any GET /api/runs/{id} the page made: refresh the run's share of the session, and an open popover.
  function noteRun(runId, info) {
    if (!runId || !info) return;
    st.infos.set(runId, info);
    const btn = $("#cost-why");
    if (btn && $("#run-id") && $("#run-id").textContent === runId) btn.dataset.cost = `run:${runId}`;
    const e = st.ledger.find((x) => x.kind === "run" && x.id === runId);
    const c = info.costs;
    if (e && c && typeof c === "object") {
      put({ kind: "run", id: runId, title: info.title || e.title, llm: Number(c.llm_usd) || 0, compute: Number(c.compute_usd) || 0,
        seconds: Number(c.wall_s) || 0, final: FINAL.has(info.status) });
    } else if (e && FINAL.has(info.status) && !e.final) put({ kind: "run", id: runId, final: true });
    if (FINAL.has(info.status) && !st.runCosts[runId]) setTimeout(refreshEstimates, 1500);
    if (st.open && (st.open === `run:${runId}` || st.open.startsWith(`step:${runId}|`))) renderPopover();
  }

  // Voice minutes count too (ElevenLabs bills per minute).
  let voiceId = null;
  function voice(live) {
    const now = Date.now() / 1000;
    if (live && !voiceId) {
      voiceId = `voice:${Date.now().toString(36)}`;
      put({ kind: "voice", id: voiceId, live: true, since: now, seconds: 0 });
      loadPrices();
    } else if (!live && voiceId) {
      const e = st.ledger.find((x) => x.id === voiceId);
      put({ kind: "voice", id: voiceId, live: false, seconds: (e ? Number(e.seconds) || 0 : 0) + Math.max(0, now - Number(e && e.since ? e.since : now)) });
      voiceId = null;
    }
  }

  const estimateFor = (taskId) => st.estimates[taskId] || null;
  // "Starting X on Modal…" gets one more sentence when past runs say what to expect.
  const startNote = (taskId) => estimateSentence(estimateFor(taskId));

  // The chip under an answer (render.js calls it through messageHTML's `costHTML` option).
  const answerChip = (m) => (m && m.cost ? costChipHTML(`answer:${m.id}`, answerCostText(m.cost), "What this answer cost") : "");

  // A step chip's small print: "$0.12 · 4 min 05 s"
  function stepNote(runId, step, costs) {
    const text = stepCostText(step, costs);
    return text ? { ref: `step:${runId}|${step.key}`, text } : null;
  }

  // ---------- header pill ----------
  function renderPill() {
    const pill = $("#session-cost");
    if (!pill) return;
    const t = totals();
    const text = sessionPillText(t);
    const b = $("b", pill);
    if (b && b.textContent !== text) b.textContent = text;
    pill.classList.toggle("live", t.running > 0 || !!voiceId);
    pill.title = `This session: ${text}${t.answers || t.runs ? ` (${[t.answers ? `${t.answers} answers` : "", t.runs ? `${t.runs} runs` : ""].filter(Boolean).join(", ")})` : ""}. Click for the breakdown.`;
    if (st.open === "session") renderPopover();
  }

  // ---------- task cards and recent runs ----------
  function decorateTasks() {
    for (const form of document.querySelectorAll("#tasks form.task[data-task]")) {
      const id = form.dataset.task;
      const t = form.querySelector(".task-text b");
      if (t) st.titles.set(id, t.textContent);
      const text = estimateText(st.estimates[id]);
      let el = form.querySelector(".task-est");
      if (!text) {
        if (el) el.remove();
        continue;
      }
      if (!el) {
        el = document.createElement("button");
        el.type = "button";
        el.className = "task-est";
        el.setAttribute("aria-haspopup", "dialog");
        el.title = "What it will likely cost, from past runs";
        const head = form.querySelector(".task-head");
        (head || form).insertAdjacentElement("afterend", el);
      }
      el.dataset.cost = `estimate:${id}`;
      if (el.textContent !== text) el.textContent = text;
    }
  }

  function decorateRuns() {
    for (const a of document.querySelectorAll("#runs .run-link[data-run]")) {
      const c = st.runCosts[a.dataset.run];
      const when = a.querySelector(".when");
      if (!c || !when || a.querySelector(".run-cost") || /\$/.test(when.textContent)) continue;
      when.insertAdjacentHTML("beforeend", `<span class="run-cost"> · ${esc(formatCost(c.total_usd))}</span>`);
    }
  }

  // ---------- the popover ----------
  async function contentFor(ref) {
    const i = ref.indexOf(":");
    const kind = i < 0 ? ref : ref.slice(0, i);
    const rest = i < 0 ? "" : ref.slice(i + 1);
    const prices = st.prices;
    if (kind === "session") return sessionBreakdownHTML(st.ledger, { prices });
    if (kind === "answer") {
      const e = st.ledger.find((x) => x.kind === "answer" && x.id === rest);
      return answerBreakdownHTML(e ? e.cost : null, prices);
    }
    if (kind === "estimate") return estimateBreakdownHTML(st.estimates[rest], { title: st.titles.get(rest) || rest, prices });
    if (kind === "run" || kind === "step") {
      const [runId, key] = kind === "step" ? [rest.slice(0, rest.indexOf("|")), rest.slice(rest.indexOf("|") + 1)] : [rest, ""];
      let info = st.infos.get(runId);
      if (!info) {
        try {
          info = await api(`/api/runs/${encodeURIComponent(runId)}`);
          st.infos.set(runId, info);
        } catch {}
      }
      const running = !(info && FINAL.has(info.status));
      if (kind === "step") return stepBreakdownHTML(key, stepCost(key, info && info.costs), { prices });
      return runBreakdownHTML(info && info.costs, { title: (info && info.title) || "", running, prices });
    }
    return "";
  }

  let renderGen = 0;
  async function renderPopover() {
    const pop = $("#cost-pop");
    if (!pop || !st.open) return;
    const gen = ++renderGen;
    if (!st.prices) await loadPrices();
    const html = await contentFor(st.open);
    if (gen !== renderGen || !st.open) return;
    pop.innerHTML = html;
    place();
  }

  function place() {
    const pop = $("#cost-pop");
    const a = st.anchor;
    if (!pop || pop.hidden) return;
    if (matchMedia("(max-width: 860px)").matches || !a || !a.isConnected) {
      pop.classList.add("sheet");
      pop.style.left = pop.style.top = "";
      return;
    }
    pop.classList.remove("sheet");
    const r = a.getBoundingClientRect();
    const w = pop.offsetWidth;
    const h = pop.offsetHeight;
    const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
    const below = r.bottom + 8;
    const top = below + h > innerHeight - 8 && r.top - h - 8 > 8 ? r.top - h - 8 : Math.min(below, Math.max(8, innerHeight - h - 8));
    pop.style.left = `${Math.round(left)}px`;
    pop.style.top = `${Math.round(top)}px`;
  }

  function openPopover(ref, anchor) {
    const pop = $("#cost-pop");
    if (!pop) return;
    if (st.open === ref && !pop.hidden) return closePopover();
    st.open = ref;
    st.anchor = anchor;
    for (const el of document.querySelectorAll("[data-cost][aria-expanded=true]")) el.setAttribute("aria-expanded", "false");
    anchor.setAttribute("aria-expanded", "true");
    pop.hidden = false;
    pop.innerHTML = '<p class="cp-loading">Adding it up…</p>';
    place();
    renderPopover().then(() => pop.querySelector(".cp-close")?.focus({ preventScroll: true }));
  }

  function closePopover({ refocus = false } = {}) {
    const pop = $("#cost-pop");
    if (!pop || pop.hidden) return;
    pop.hidden = true;
    st.open = null;
    const a = st.anchor;
    st.anchor = null;
    if (a) {
      a.setAttribute("aria-expanded", "false");
      if (refocus && a.isConnected) a.focus({ preventScroll: true });
    }
  }

  // ---------- wiring ----------
  function start() {
    load();
    const pill = $("#session-cost");
    if (pill) pill.hidden = false;
    renderPill();
    document.addEventListener("click", (e) => {
      const pop = $("#cost-pop");
      if (e.target.closest("[data-cost-close]")) return closePopover({ refocus: true });
      const trigger = e.target.closest("[data-cost]");
      if (trigger && !(pop && pop.contains(trigger))) {
        e.preventDefault();
        return openPopover(trigger.dataset.cost, trigger);
      }
      if (pop && !pop.hidden) {
        if (!pop.contains(e.target)) closePopover();
        else if (e.target.closest("a[href]")) setTimeout(() => closePopover(), 0); // a run link: let app.js open it first
      }
    });
    // Keyboard: Enter/Space on a step chip (a span with role=button) opens it too.
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && st.open) return closePopover({ refocus: true });
      const t = e.target.closest && e.target.closest("[data-cost][role=button]");
      if (t && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        openPopover(t.dataset.cost, t);
      }
    });
    addEventListener("resize", place);
    document.addEventListener("scroll", () => st.open && place(), true);
    // app.js re-renders the task cards and the run list; put the estimates and costs back each time.
    const watch = (sel, fn) => {
      const el = $(sel);
      if (el && "MutationObserver" in window) new MutationObserver(() => fn()).observe(el, { childList: true });
    };
    watch("#tasks", decorateTasks);
    watch("#runs", decorateRuns);
    setInterval(() => {
      const t = totals();
      if (voiceId || t.running) renderPill();
    }, TICK);
    setInterval(pollSessionRuns, RUNS_EVERY);
    setTimeout(() => {
      loadPrices();
      refreshEstimates();
    }, 400);
  }

  // Session runs that aren't open in the panel still add to the total while they go.
  async function pollSessionRuns() {
    if (document.hidden) return;
    const open = $("#run-id") ? $("#run-id").textContent : "";
    for (const e of st.ledger.filter((x) => x.kind === "run" && !x.final && x.id !== open).slice(-4)) {
      try {
        noteRun(e.id, await api(`/api/runs/${encodeURIComponent(e.id)}`));
      } catch (err) {
        if (err && err.status === 404 && Date.now() / 1000 - Number(e.at || 0) > 120) put({ kind: "run", id: e.id, final: true });
      }
    }
  }

  return { start, noteAnswer, trackRun, noteRun, voice, estimateFor, startNote, answerChip, stepNote, refreshEstimates, decorateTasks };
}
