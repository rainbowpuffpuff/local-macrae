// The page: a chat with Jarvis (typed or by voice) in the middle, and a panel with the tasks, the current run's
// live trace, and recent runs on the right (a bottom sheet on a phone).
// Talks only to its own origin: /api/* (proxied by the Worker to the backend, CONTRACT.md) and /voice/signed-url.
import {
  esc, eventMeta, mergeEvents, runStats, plural, collectCitations, normalizeCitations, normalizeRunId, numberCitations,
  passagesAnswer, citationTarget, truncate, formatDuration, relativeTime, elapsed, statusState, isFinal, taskInputs,
  parseRoute, routeHref, parseToolResult, toolLabel, pushTranscript, healthLabel, runHeadline, taskIdFromRunId,
} from "./core.js";
import { taskIcon, citeHTML, messageHTML, eventHTML, stepHTML } from "./render.js";
import { planFromEvents, planHeadline, planCardHTML, runCosts, liveCosts, monotonic, costMeterHTML, byStepHTML, formatUsd, currentPhase } from "./live.js";
import { normalizeEvolution, evolutionTaskHTML } from "./evolution.js";
import { Voice, preloadSdk } from "./voice.js";
import { setTraceLinks } from "./traces.js";
import { createCostUI } from "./cost_ui.js";

const $ = (sel, el = document) => el.querySelector(sel);
const enc = encodeURIComponent;

const HEALTH_EVERY = 20_000;
const RUNS_EVERY = 10_000;
const EVENTS_EVERY = 1_500;
const RUN_INFO_EVERY = 3_000; // the cost meter and phase times come from the run, so read it often while it runs
const EVOLUTION_EVERY = 30_000;
const FOLLOW_PAUSE = 10_000; // ms after the reader scrolls the panel before the trace follows new rows again
const HOLD_TOP = 12_000; // ms after a run opens before the trace starts following its newest row
const WATCH_EVERY = 5_000;
const FRESH_RUN_GRACE = 30; // s: a run we just started may not be on disk yet; a 404 then means "starting"
const PHONE = matchMedia("(max-width: 860px)");
const TASKS_CACHE = "macrae-tasks"; // the last task list, shown greyed out when the backend is down

const S = {
  health: undefined, // undefined until /api/health first answers; null when it failed
  online: null, // null = not known yet
  reachable: false, // some /api call succeeded since the last failure
  tasks: null,
  runs: [],
  route: parseRoute(location.search),
  run: null,
  thread: [],
  pendingCites: null, // sources the agent showed before saying anything
  runChips: new Map(), // run id → {title, cls, label}: the run links in the thread
  hints: new Map(), // run id → {title, task_id, at}: what we knew when it started
  announced: new Set(),
  knownRuns: new Set(),
  sessionStart: 0,
  lastTyped: null,
  tab: "runs", // the panel's tab: "runs" (tasks, current run, recent runs) or "evolution"
  evo: { raw: null, groups: null, error: "", loading: false, at: 0, lessons: -1 },
  lessonWatch: new Map(), // run id → {title, until}: runs from this page whose distilled lessons Jarvis should mention
};
let runGen = 0;
let msgSeq = 0;
const newId = (p) => `${p}:${Date.now().toString(36)}:${++msgSeq}`;

// Every renderer runs on its own: one that throws can't stop the others (or the polling loop) from running.
function safe(fn, ...args) {
  try {
    return fn(...args);
  } catch (err) {
    console.error(err);
  }
}

// ---------- api ----------
class ApiError extends Error {
  constructor(message, status, offline) {
    super(message);
    this.status = status;
    this.offline = offline;
  }
}

async function api(path, { method = "GET", body, timeout = 15_000 } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? { accept: "application/json" } : { accept: "application/json", "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
      cache: "no-store",
    });
  } catch {
    throw new ApiError("agent offline", 0, true);
  } finally {
    clearTimeout(timer);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = (data && (data.error || (typeof data.detail === "string" ? data.detail : ""))) || `HTTP ${res.status}`;
    const offline = !data || !!data.offline || [502, 503, 504].includes(res.status);
    if (offline) reachable(false);
    else reachable(true); // the backend answered, even if it said no
    throw new ApiError(msg, res.status, offline);
  }
  // A static server without the Worker answers /api with HTML: that is "no backend" too.
  if (data === null || typeof data !== "object") {
    reachable(false);
    throw new ApiError("agent offline", res.status, true);
  }
  reachable(true);
  return data;
}

// Any answer from the backend proves it's up, so the pill and the offline banner don't wait for /api/health.
function reachable(ok) {
  if (S.reachable === ok) return;
  S.reachable = ok;
  if (ok && S.online !== true) setOnline(true);
  else if (!ok && S.online !== false) queueMicrotask(() => refreshHealth());
  renderHealth();
}

// What things cost: the header total, answer and step chips, task estimates, the breakdown popover (cost_ui.js).
const costUI = createCostUI({ api });

// ---------- small ui helpers ----------
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3600);
}

// ---------- health ----------
let healthInFlight = null;
function refreshHealth({ announce = false } = {}) {
  if (!healthInFlight) {
    healthInFlight = (async () => {
      let h = null;
      try {
        h = await api("/api/health", { timeout: 10_000 });
      } catch {}
      S.health = h;
      setOnline(!!(h && h.ok) || (S.reachable && h !== null));
    })().finally(() => (healthInFlight = null));
  }
  return healthInFlight.then(() => {
    if (announce) toast(S.online ? "Jarvis is online." : "Still offline.");
  });
}

function setOnline(on) {
  const was = S.online;
  S.online = on;
  if (!on) S.reachable = false;
  safe(renderHealth);
  $("#offline").hidden = on;
  if (on && was !== true) {
    loadTasks();
    loadRuns();
  }
  if (!on && was !== false) safe(renderTasks);
}

function renderHealth() {
  const h = S.health;
  const { cls, text } = healthLabel(S.online === false ? h || null : h, { reachable: S.reachable && S.online !== false });
  const pill = $("#health");
  pill.className = `pill ${cls}`;
  $("#health-text").textContent = PHONE.matches ? text.split(" · ")[0] : text;
  pill.title = h && h.ok
    ? `Backend online${h.chunks !== undefined ? ` · ${h.chunks} indexed passages` : ""}${h.modal ? " · Modal configured" : " · Modal not configured"}. Click to check again.`
    : S.online === false ? "The backend is not answering. Click to check again." : "Backend status. Click to check again.";
}

// ---------- tasks ----------
async function loadTasks() {
  try {
    const data = await api("/api/tasks");
    S.tasks = Array.isArray(data.tasks) ? data.tasks : [];
    costUI.refreshEstimates();
    try {
      localStorage.setItem(TASKS_CACHE, JSON.stringify(S.tasks));
    } catch {}
  } catch (err) {
    if (!err.offline) toast(`Couldn't load tasks: ${err.message}`);
  }
  safe(renderTasks);
  safe(renderPeek);
  if (S.run) safe(renderRunHeader); // task titles help name the run
  if (S.evo.raw) {
    // evolution data that came first: now with the tasks' titles, icons and order
    S.evo.groups = normalizeEvolution(S.evo.raw, S.tasks || []);
    if (S.tab === "evolution") safe(renderEvolution);
  }
}

function renderTasks() {
  const box = $("#tasks");
  const off = S.online === false;
  $("#tasks-count").hidden = !(S.tasks && S.tasks.length);
  $("#tasks-count").textContent = String((S.tasks || []).length);
  box.classList.toggle("off", off);
  if (!S.tasks || !S.tasks.length) {
    if (S.online === null) return; // still loading: keep the skeletons
    box.innerHTML = S.online
      ? `<div class="empty-card"><b>No tasks yet</b><p>The backend is up but lists no tasks. They come from <code>tasks/tasks.json</code>.</p></div>`
      : `<div class="empty-card"><b>Tasks are paused</b><p>Jarvis's backend isn't answering. Tasks come back when it does.</p><button class="btn tiny" type="button" data-retry>Check again</button></div>`;
    return;
  }
  box.innerHTML = S.tasks
    .map((t) => {
      const inputs = (t.inputs || []).filter((i) => i && i.name);
      return `<form class="task" data-task="${esc(t.id)}" title="${esc(t.prompt || "")}">
        <div class="task-head"><span class="task-icon">${taskIcon(t.icon)}</span>
          <div class="task-text"><b>${esc(t.title || t.id)}</b>${t.subtitle ? `<span>${esc(t.subtitle)}</span>` : ""}</div>
          <button class="btn primary sm" type="submit" ${off ? "disabled" : ""}>Run</button></div>
        ${t.prompt ? `<p class="task-prompt">${esc(truncate(t.prompt, 200))}</p>` : ""}
        ${inputs.length ? `<div class="task-inputs">${inputs.map((i) => `<label class="field"><span>${esc(i.label || i.name)}</span>${inputControl(i, off)}</label>`).join("")}</div>` : ""}
      </form>`;
    })
    .join("");
}

// tasks.json inputs may list allowed values (`options`): a select, so nothing invalid can be sent.
function inputControl(i, off) {
  const def = String(i.default ?? "");
  const dis = off ? " disabled" : "";
  if (Array.isArray(i.options) && i.options.length) {
    return `<select name="${esc(i.name)}"${dis}>${i.options
      .map((o) => `<option value="${esc(o)}"${String(o) === def ? " selected" : ""}>${esc(o)}</option>`)
      .join("")}</select>`;
  }
  return `<input name="${esc(i.name)}" value="${esc(def)}" spellcheck="false" autocomplete="off"${dis}>`;
}

async function startTask(taskId, form) {
  const task = (S.tasks || []).find((t) => t.id === taskId);
  if (!task) return;
  if (S.online === false) return toast("Jarvis is offline, so tasks can't start right now.");
  const btn = form.querySelector("button[type=submit]");
  const values = Object.fromEntries(new FormData(form).entries());
  btn.disabled = true;
  btn.textContent = "Starting…";
  try {
    const inputs = taskInputs(task, values);
    const data = await api(`/api/tasks/${enc(taskId)}/start`, { method: "POST", body: Object.keys(inputs).length ? { inputs } : {}, timeout: 30_000 });
    const runId = normalizeRunId(data.run_id);
    if (!runId) throw new Error("the backend did not return a run id");
    runStarted(runId, { title: task.title || task.id, task_id: task.id, inputs, by: "you" });
    voice.context(`The user clicked the task "${task.title || task.id}" on the page. It started as run_id ${runId}. Use run_status with this run_id when they ask how it is going.`);
  } catch (err) {
    toast(err.offline ? "Jarvis is offline, so the task can't start." : `Couldn't start the task: ${err.message}`);
  } finally {
    btn.disabled = S.online === false;
    btn.textContent = "Run";
  }
}

// A run began (from a task card, or the agent started one): a Jarvis message in the thread links to it,
// and the panel shows its trace.
function runStarted(runId, { title = "", task_id = "", inputs = {}, by = "you" } = {}) {
  if (!title) {
    const task = (S.tasks || []).find((t) => t.id === (task_id || taskIdFromRunId(runId, S.tasks)));
    if (task) [title, task_id] = [task.title || task.id, task.id];
  }
  if (!S.hints.has(runId)) S.hints.set(runId, { title, task_id, at: Date.now() / 1000 });
  costUI.trackRun(runId, { title });
  S.knownRuns.add(runId);
  if (!S.thread.some((m) => m.runId === runId)) {
    const what = title ? `**${title}**` : "the task";
    const args = Object.entries(inputs || {}).filter(([, v]) => v !== "" && v !== undefined).map(([k, v]) => `${k} \`${v}\``).join(", ");
    S.runChips.set(runId, { title: title || "Run", cls: "running", label: "Starting" });
    const expect = costUI.startNote(task_id || taskIdFromRunId(runId, S.tasks)); // "The last 4 runs took about 6 min and cost $0.45–0.62."
    addMsg({
      role: "jarvis",
      runId,
      text: `${by === "agent" ? "I started" : "Starting"} ${what}${args ? ` with ${args}` : ""} on Modal. Every paper it reads and every calculation it runs shows up in the trace.${expect ? ` ${expect}` : ""}`,
    });
  }
  navigate({ view: "run", runId });
  if (PHONE.matches) openSheet(true);
}

// ---------- recent runs ----------
async function loadRuns() {
  try {
    const data = await api("/api/runs");
    S.runs = Array.isArray(data.runs) ? data.runs : [];
    for (const r of S.runs) if (!S.sessionStart) S.knownRuns.add(r.run_id);
  } catch {
    return;
  }
  safe(renderRuns);
  safe(syncRunChips);
}

// Run links in the thread (some restored from earlier in the session) follow the runs' real status.
function syncRunChips() {
  let changed = false;
  for (const r of S.runs) {
    const old = S.runChips.get(r.run_id);
    if (!old || (S.run && S.run.id === r.run_id)) continue;
    const st = statusState(r.status);
    if (old.label !== st.label) {
      S.runChips.set(r.run_id, { ...old, title: r.title || old.title, cls: st.cls, label: st.label });
      changed = true;
    }
  }
  if (changed) renderThread({ keepScroll: true });
}

function renderRuns() {
  const list = S.runs.slice(0, 8);
  const current = S.route.view === "run" ? S.route.runId : "";
  $("#runs").innerHTML = list.length
    ? list
        .map((r) => {
          const st = statusState(r.status);
          const dur = elapsed(r);
          const cost = r.costs && Number.isFinite(Number(r.costs.total_usd)) && isFinal(r.status) ? formatUsd(r.costs.total_usd) : "";
          return `<a class="run-link${r.run_id === current ? " active" : ""}" href="${esc(routeHref({ view: "run", runId: r.run_id }))}" data-run="${esc(r.run_id)}">
            <span class="dot ${st.cls}" title="${esc(st.label)}"></span>
            <span class="name">${esc(r.title || r.task_id || r.run_id)}</span>
            <span class="when">${esc([relativeTime(r.started), dur !== null && isFinal(r.status) ? formatDuration(dur) : st.label, cost].filter(Boolean).join(" · "))}</span></a>`;
        })
        .join("")
    : `<p class="muted">${S.online === false ? "Runs show up here when Jarvis is back online." : "No runs yet. Pick a task above."}</p>`;
}

// ---------- routing ----------
// "/" is the chat; "/?run=<id>" is the chat with that run open in the panel. Reloading either works.
function navigate(route, { replace = false } = {}) {
  const href = routeHref(route);
  if (href !== location.pathname + location.search) history[replace ? "replaceState" : "pushState"](null, "", href);
  applyRoute(route);
}

function applyRoute(route = parseRoute(location.search)) {
  S.route = route;
  if (route.view === "run") {
    const known = S.runs.find((r) => r.run_id === route.runId);
    if (known && !S.hints.has(route.runId)) S.hints.set(route.runId, { title: known.title, task_id: known.task_id, at: Number(known.started) || 0 });
    if (!S.run || S.run.id !== route.runId) openRun(route.runId, { seq: route.seq });
    else if (route.seq !== undefined) {
      S.run.seqTarget = route.seq;
      safe(highlightSeq);
    }
    setTab("runs");
    $("#sec-run").hidden = false;
    // The run is what matters now: fold the task list away (one click brings it back) and show the trace.
    $("#sec-tasks").open = false;
    if (!PHONE.matches) $("#app").classList.remove("panel-closed");
    $("#panel-body").scrollTop = 0;
  } else {
    closeRun();
    $("#sec-run").hidden = true;
    $("#sec-tasks").open = true;
    document.title = "Macrae · ask Jarvis about the Jungwirth group's papers";
    if (route.view === "evolution") {
      setTab("evolution");
      if (!PHONE.matches) $("#app").classList.remove("panel-closed");
    }
  }
  safe(renderRuns);
  safe(renderPeek);
  syncPanelButton();
}

document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const home = e.target.closest("[data-nav=home]");
  const run = e.target.closest("a[data-run]");
  if (home) {
    e.preventDefault();
    navigate({ view: "home" });
  } else if (run) {
    e.preventDefault();
    const seq = run.dataset.seq;
    navigate(seq !== undefined && /^\d+$/.test(seq) ? { view: "run", runId: run.dataset.run, seq: Number(seq) } : { view: "run", runId: run.dataset.run });
    if (PHONE.matches) openSheet(true);
  }
});
window.addEventListener("popstate", () => applyRoute());

// ---------- panel / bottom sheet ----------
function openSheet(open) {
  const app = $("#app");
  app.classList.toggle("sheet-open", open);
  $("#scrim").hidden = !open || !PHONE.matches;
  $("#panel-head").setAttribute("aria-expanded", String(open));
}
$("#panel-head").addEventListener("click", () => {
  if (PHONE.matches) openSheet(!$("#app").classList.contains("sheet-open"));
});
$("#scrim").addEventListener("click", () => openSheet(false));
$("#panel-btn").addEventListener("click", () => {
  $("#app").classList.toggle("panel-closed");
  syncPanelButton();
});
// The sheet's header is a button only on a phone; on a wide screen it's just the panel's title.
const syncSheetHead = () => ($("#panel-head").tabIndex = PHONE.matches ? 0 : -1);
syncSheetHead();
PHONE.addEventListener?.("change", () => {
  openSheet(false);
  syncSheetHead();
  safe(renderHealth);
});
function syncPanelButton() {
  const open = !$("#app").classList.contains("panel-closed");
  $("#panel-btn").setAttribute("aria-expanded", String(open));
  $("#panel-badge").hidden = open || !(S.run && !S.run.done);
  $("#tab-runs").classList.toggle("busy", S.tab === "evolution" && !!(S.run && !S.run.done));
}

// The sheet's collapsed bar says what's going on: the current run, or how many tasks there are.
function renderPeek() {
  const peek = $("#panel-peek");
  const r = S.run;
  if (r) {
    const h = headline();
    const cost = r.shown ? r.shown.total : r.info && r.info.costs ? Number(r.info.costs.total_usd) : null;
    peek.innerHTML = `<span class="dot ${esc(h.state.cls)}"></span><span class="t">${esc(truncate(h.title, 40))}</span>${Number.isFinite(cost) && cost !== null ? `<span class="c">${esc(formatUsd(cost))}</span>` : ""}`;
  } else if (S.tasks && S.tasks.length) peek.textContent = plural(S.tasks.length, "task");
  else peek.textContent = "";
}

// ---------- panel tabs ----------
function setTab(tab) {
  const evo = tab === "evolution";
  S.tab = evo ? "evolution" : "runs";
  $("#tab-runs").setAttribute("aria-selected", String(!evo));
  $("#tab-evo").setAttribute("aria-selected", String(evo));
  $("#pane-runs").hidden = evo;
  $("#pane-evo").hidden = !evo;
  if (evo) {
    $("#evo-badge").hidden = true;
    if (!S.evo.groups || Date.now() - S.evo.at > 5_000) loadEvolution();
    else safe(renderEvolution);
  }
  // On the runs tab a dot says something is running elsewhere; on the evolution tab it says a lesson came in.
  $("#tab-runs").classList.toggle("busy", evo && !!(S.run && !S.run.done));
}
document.querySelector(".tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tab]");
  if (!b) return;
  setTab(b.dataset.tab);
  // /?view=evolution is a link to this tab; leaving it for the runs tab goes back to the chat's own URL.
  if (S.route.view === "evolution" && b.dataset.tab === "runs") navigate({ view: "home" }, { replace: true });
  else if (b.dataset.tab === "evolution" && S.route.view === "home") navigate({ view: "evolution" }, { replace: true });
});

// ---------- evolution ----------
let evoInFlight = null;
function loadEvolution() {
  if (evoInFlight) return evoInFlight;
  S.evo.loading = true;
  if (S.tab === "evolution") safe(renderEvolution);
  evoInFlight = (async () => {
    try {
      const data = await api("/api/evolution");
      S.evo.raw = data;
      S.evo.groups = normalizeEvolution(data, S.tasks || []);
      S.evo.error = "";
      const n = S.evo.groups.reduce((k, g) => k + g.lessons.length, 0);
      if (S.evo.lessons >= 0 && n > S.evo.lessons && S.tab !== "evolution") $("#evo-badge").hidden = false;
      S.evo.lessons = n;
      safe(announceLessons);
    } catch (err) {
      S.evo.error = err.offline ? "offline" : err.status === 404 ? "missing" : err.message;
    } finally {
      S.evo.loading = false;
      S.evo.at = Date.now();
      evoInFlight = null;
    }
    if (S.tab === "evolution") safe(renderEvolution);
  })();
  return evoInFlight;
}

function renderEvolution() {
  const box = $("#evo");
  const g = S.evo.groups;
  if (!g) {
    if (S.evo.loading) return; // keep the skeleton
    box.innerHTML = `<div class="empty-card">${
      S.evo.error === "offline" ? "<b>Jarvis is offline</b><p>The run history comes back with the backend.</p>"
      : S.evo.error === "missing" ? "<b>No evolution data on this backend</b><p><code>GET /api/evolution</code> isn't there yet. It lists each task's runs and the lessons distilled from their traces.</p>"
      : `<b>Couldn't load the run history</b><p>${esc(S.evo.error || "No answer.")}</p>`
    }<button class="btn tiny" type="button" id="evo-retry">Try again</button></div>`;
    return;
  }
  if (!g.length) {
    box.innerHTML = `<div class="empty-card"><b>No finished runs yet</b><p>Run a task: when it ends, Jarvis distills lessons from its trace, and the next run of that task starts with them.</p></div>`;
    return;
  }
  // tasks.json may have loaded after the evolution data: use its titles and icons now.
  box.innerHTML = g
    .map((x) => {
      const task = (S.tasks || []).find((t) => t.id === x.taskId);
      return evolutionTaskHTML(task ? { ...x, title: task.title || x.title } : x, { icon: taskIcon(task ? task.icon : x.icon) });
    })
    .join("");
}
$("#evo-refresh").addEventListener("click", () => loadEvolution());
$("#evo").addEventListener("click", (e) => {
  if (e.target.closest("#evo-retry")) loadEvolution();
});
setInterval(() => {
  if (!document.hidden && S.online && S.tab === "evolution") loadEvolution();
}, EVOLUTION_EVERY);

// ---------- current run ----------
function openRun(id, { seq } = {}) {
  closeRun();
  const gen = ++runGen;
  S.run = {
    id, gen, info: null, events: [], lastSeq: null, done: false, failures: 0, stats: null, told: false, rendered: 0, timers: [], settled: false, infoBusy: false,
    plan: null, planKey: "", planTold: false, snap: null, prevSnap: null, shown: null, seqTarget: seq ?? null, costsKey: "",
    scrolledAt: Date.now() + HOLD_TOP - FOLLOW_PAUSE, // the plan card and the meter stay in view for the first seconds
  };
  $("#run-id").textContent = id;
  safe(setTraceLinks, $("#run-links"), id);
  $("#run-plan").hidden = true;
  $("#run-plan").innerHTML = "";
  $("#run-costs").hidden = true;
  $("#cost-main").innerHTML = "";
  $("#cost-steps-body").innerHTML = "";
  $("#run-strip").hidden = true;
  S.meterVisible = undefined;
  $("#run-timer").textContent = "";
  $("#run-steps").innerHTML = "";
  $("#timeline").innerHTML = "";
  $("#run-end").hidden = true;
  $("#run-notice").hidden = true;
  $("#run-sources").hidden = true;
  $("#run-working").hidden = false;
  $(".thinking", $("#run-working")).textContent = "Waiting for the first trace event…";
  safe(renderRunHeader);
  safe(renderStats);
  // Both requests go out at once; neither waits for the other, and neither waits for /api/health.
  pollEvents(gen);
  pollRunInfo(gen);
  S.run.clock = setInterval(() => safe(renderTimer), 1000);
}

function closeRun() {
  if (!S.run) return;
  $("#run-strip").hidden = true;
  clearInterval(S.run.clock);
  for (const t of S.run.timers) clearTimeout(t);
  S.run = null;
  runGen++;
  syncPanelButton();
}

const current = (gen) => S.run && S.run.gen === gen;

function later(gen, fn, ms) {
  if (!current(gen)) return;
  S.run.timers.push(setTimeout(() => current(gen) && fn(gen), ms));
}

function freshRun(r) {
  const hint = S.hints.get(r.id);
  return hint && Date.now() / 1000 - hint.at < FRESH_RUN_GRACE;
}

async function pollEvents(gen) {
  const r = S.run;
  if (!current(gen)) return;
  try {
    const q = r.lastSeq === null ? "" : `?after=${enc(r.lastSeq)}`;
    const data = await api(`/api/runs/${enc(r.id)}/events${q}`);
    if (!current(gen)) return;
    r.failures = 0;
    r.settled = true;
    const before = r.lastSeq;
    const merged = mergeEvents(r.events, data.events);
    r.events = merged.events;
    r.lastSeq = merged.lastSeq;
    r.done = !!data.done;
    showNotice(null);
    if (merged.added) {
      safe(renderEvents, before);
      safe(renderPlan);
      safe(renderCosts);
      // after everything above the timeline has its height, or the row would be pushed out of view again
      requestAnimationFrame(() => current(gen) && safe(highlightSeq));
    }
    safe(renderRunHeader);
    // Events but no run info yet (it was slow or failed): ask again now instead of waiting for the next tick.
    if (!r.info && r.events.length) pollRunInfo(gen, { once: true });
  } catch (err) {
    if (!current(gen)) return;
    r.settled = true;
    if (err.status === 404 && !freshRun(r)) {
      r.done = true;
      showNotice(`There is no run with the id ${r.id}. It may have been cleaned up.`, true);
      $("#run-title").textContent = "Run not found";
      $("#run-state").className = "state failed";
      $("#run-state").textContent = "Not found";
      $("#run-working").hidden = true;
      return;
    }
    if (err.status !== 404) r.failures++;
    if (r.failures >= 2) showNotice(err.offline ? "Jarvis is offline. The run may still be going; this keeps trying." : `Couldn't read the trace: ${err.message}. Retrying…`);
    safe(renderRunHeader);
  }
  if (r.done) return finishRun(gen);
  later(gen, pollEvents, Math.min(10_000, EVENTS_EVERY * 2 ** Math.min(r.failures, 3)));
}

async function pollRunInfo(gen, { once = false } = {}) {
  const r = S.run;
  if (!current(gen)) return;
  if (r.infoBusy) return;
  r.infoBusy = true;
  try {
    const info = await api(`/api/runs/${enc(r.id)}`);
    if (!current(gen)) return;
    setRunInfo(r, info);
    safe(renderRunHeader);
  } catch {
  } finally {
    r.infoBusy = false;
  }
  if (!once && !r.done) later(gen, pollRunInfo, RUN_INFO_EVERY);
}

// Every run poll is a snapshot for the cost meter, which keeps counting between snapshots (live.js liveCosts).
function setRunInfo(r, info) {
  r.info = info;
  costUI.noteRun(r.id, info);
  const costs = runCosts(info, r.events);
  if (costs && costs.source === "run") {
    r.prevSnap = r.snap;
    r.snap = { at: Date.now() / 1000, costs, lastSeq: r.lastSeq };
  }
  safe(renderPlan);
  safe(renderCosts);
}

async function finishRun(gen) {
  const r = S.run;
  // The trace can finish a moment before state.json says so: read the run once more.
  for (let i = 0; i < 3; i++) {
    try {
      setRunInfo(r, await api(`/api/runs/${enc(r.id)}`));
    } catch {}
    if (!current(gen)) return;
    if (!r.info || isFinal(r.info.status)) break;
    await new Promise((ok) => setTimeout(ok, 1500));
  }
  if (!current(gen)) return;
  safe(renderRunHeader);
  $("#run-working").hidden = true;
  const h = headline();
  const st = h.state;
  const s = runStats(r.events);
  const took = elapsed(r.info && r.info.started ? r.info : { started: r.events[0]?.t, finished: r.events[r.events.length - 1]?.t });
  const lastError = [...r.events].reverse().find((e) => e.type === "error");
  const costs = runCosts(r.info, r.events);
  r.shown = null;
  safe(renderCosts);
  const spent = costs ? costText(costs) : "";
  const end = $("#run-end");
  end.className = `run-end ${st.cls}`;
  end.innerHTML =
    st.cls === "ok"
      ? `<span>✅</span><span><b>Finished${took !== null ? ` in ${esc(formatDuration(took))}` : ""}${costs ? ` for ${esc(formatUsd(costs.total))}` : ""}.</b> ${esc([plural(s.papers, "paper"), plural(s.calc, "calculation"), plural(s.write, "file")].join(" · "))}</span>`
      : `<span>⚠️</span><span><b>${esc(st.label)}.</b> ${esc(lastError ? truncate(lastError.title, 160) : "The run did not finish.")}</span>`;
  end.hidden = false;
  if (!r.told) {
    r.told = true;
    voice.context(`Run ${r.id} (${h.title}) finished with status ${h.status}. ${plural(s.papers, "paper")} read, ${plural(s.calc, "calculation")}.${spent ? ` It cost ${spent}.` : ""}`);
    announceFinish(r, h, s, took, lastError, spent);
  }
  if (S.thread.some((m) => m.runId === r.id) && costs) {
    const chip = S.runChips.get(r.id);
    if (chip && chip.cost !== formatUsd(costs.total)) {
      S.runChips.set(r.id, { ...chip, cost: formatUsd(costs.total) });
      renderThread({ keepScroll: true });
    }
  }
  if (S.online) loadRuns();
  // The backend distills lessons from the trace once the run ends (one Claude call): look for them a little later,
  // and if this page started the run, Jarvis says what it learned.
  if (S.thread.some((m) => m.runId === r.id) && !S.announced.has(`lessons:${r.id}`)) S.lessonWatch.set(r.id, { title: h.title, until: Date.now() + 120_000 });
  for (const ms of [4_000, 15_000, 40_000, 90_000]) {
    setTimeout(() => S.online && (S.evo.groups || S.tab === "evolution" || S.lessonWatch.size) && loadEvolution(), ms);
  }
}

// Lessons whose evidence points at a run this page started: one Jarvis message per run, with a link to Evolution.
function announceLessons() {
  if (!S.lessonWatch.size || !S.evo.groups) return;
  const all = S.evo.groups.flatMap((g) => g.lessons);
  for (const [runId, w] of [...S.lessonWatch]) {
    const mine = all.filter((l) => l.evidence.some((e) => e.runId === runId));
    if (!mine.length) {
      if (Date.now() > w.until) S.lessonWatch.delete(runId);
      continue;
    }
    S.lessonWatch.delete(runId);
    S.announced.add(`lessons:${runId}`);
    const first = mine[0];
    addMsg({
      role: "jarvis", evoLink: true,
      text: `From that run I learned: **${truncate(first.lesson.trim().replace(/[.\s]+$/, ""), 240)}**${mine.length > 1 ? ` (and ${plural(mine.length - 1, "more lesson")})` : ""}. The next **${w.title}** run starts with ${mine.length > 1 ? "these" : "it"}.`,
    });
    voice.context(`After run ${runId} you distilled ${plural(mine.length, "lesson")} from its trace. The first: ${first.lesson}`);
  }
}

// "$0.63 ($0.61 LLM + $0.02 compute)"
function costText(c) {
  return c.compute === null ? `${formatUsd(c.total)} in tokens` : `${formatUsd(c.total)} (${formatUsd(c.llm)} LLM + ${formatUsd(c.compute)} compute)`;
}

// A run started from this page gets a closing Jarvis message, with the papers it cited as sources.
function announceFinish(r, h, s, took, lastError, spent = "") {
  if (S.announced.has(r.id) || !S.thread.some((m) => m.runId === r.id)) return;
  S.announced.add(r.id);
  const cites = numberCitations(collectCitations(r.events));
  const refs = cites.map((c) => c.key).join("");
  const text = h.state.cls === "ok"
    ? `**${h.title}** finished${took !== null ? ` in ${formatDuration(took)}` : ""}${spent ? ` and cost ${spent}` : ""}. It did ${plural(s.read, "read")}, ${plural(s.search, "search", "searches")} and ${plural(s.calc, "calculation")}, and wrote ${plural(s.write, "file")}.${cites.length ? ` It cited ${plural(cites.length, "paper")} ${refs}.` : ""}`
    : `**${h.title}** ${h.state.label.toLowerCase()}${lastError ? `: ${truncate(lastError.title, 160)}` : "."}`;
  addMsg({ role: "jarvis", text, citations: cites, bad: h.state.cls === "failed" });
}

function headline() {
  const r = S.run;
  return runHeadline({ runId: r.id, info: r.info, events: r.events, tasks: S.tasks, hint: S.hints.get(r.id), done: r.done, settled: r.settled });
}

function renderRunHeader() {
  const r = S.run;
  if (!r) return;
  const h = headline();
  $("#run-title").textContent = h.title;
  if (S.route.view === "run") document.title = `${h.title} · Macrae`;
  $("#run-state").className = `state ${h.state.cls}`;
  $("#run-state").textContent = h.state.label;
  $("#run-steps").innerHTML = ((r.info && r.info.steps) || []).map((s) => stepHTML(s, costUI.stepNote(r.id, s, r.info.costs))).join("");
  const old = S.runChips.get(r.id);
  const chip = { ...old, title: h.title, cls: h.state.cls, label: h.state.label, plan: r.plan ? planHeadline(r.plan) : (old && old.plan) || "" };
  if (S.thread.some((m) => m.runId === r.id) && (!old || old.label !== chip.label || old.title !== chip.title || old.plan !== chip.plan)) {
    S.runChips.set(r.id, chip);
    renderThread({ keepScroll: true });
  }
  renderTimer();
  renderPeek();
  syncPanelButton();
}

function renderTimer() {
  const r = S.run;
  if (!r) return;
  const base = r.info && r.info.started ? r.info : r.events[0] ? { started: r.events[0].t, finished: r.done ? r.events[r.events.length - 1].t : null } : null;
  const d = elapsed(base);
  $("#run-timer").textContent = d === null ? "" : formatDuration(d);
  safe(renderCosts);
  safe(renderWorking);
}

// The line under the trace while the run goes: what happened last, and how long ago (it keeps ticking through a long
// MD run, so a quiet stretch never looks like a frozen page).
function renderWorking() {
  const r = S.run;
  if (!r || r.done) return;
  const last = r.events[r.events.length - 1];
  const el = $(".thinking", $("#run-working"));
  if (!last) {
    el.textContent = "Waiting for the first trace event…";
    return;
  }
  const ago = r.lastAt ? Math.max(0, (Date.now() - r.lastAt) / 1000) : 0;
  el.textContent = `${last.type === "result" ? "Wrapping up" : "Working"} · last: ${truncate(last.title || last.type, 64)} · ${ago < 2 ? "just now" : `${formatDuration(ago)} ago`}`;
}

// The planner's decision: the first card of the run.
function renderPlan() {
  const r = S.run;
  if (!r) return;
  const plan = planFromEvents(r.events, r.info);
  const box = $("#run-plan");
  if (!plan) {
    box.hidden = true;
    return;
  }
  r.plan = plan;
  const key = JSON.stringify(plan);
  if (key !== r.planKey) {
    r.planKey = key;
    box.innerHTML = planCardHTML(plan);
    box.hidden = false;
  }
  if (!r.planTold && !r.done) {
    r.planTold = true;
    voice.context(`The planner decided for run ${r.id}: ${planHeadline(plan)}.${plan.why ? ` Why: ${truncate(plan.why, 300)}` : ""}`);
  }
}

// The cost meter: exact when the run is over, counting up between polls while it goes.
function renderCosts() {
  const r = S.run;
  if (!r) return;
  const running = !r.done && !(r.info && isFinal(r.info.status));
  let c = running
    ? liveCosts({ snap: r.snap, prev: r.prevSnap, events: r.events, now: Date.now() / 1000, running: true })
    : runCosts(r.info, r.events);
  if (running) c = monotonic(r.shown, c);
  r.shown = running ? c : null;
  const box = $("#run-costs");
  if (!c) {
    box.hidden = true;
    $("#run-strip").hidden = true;
    return;
  }
  box.hidden = false;
  $("#cost-main").innerHTML = costMeterHTML(c, { budget: r.plan && r.plan.budget, running });
  const steps = $("#cost-steps");
  steps.hidden = !c.byStep.length;
  const key = JSON.stringify(c.byStep);
  if (key !== r.costsKey) {
    r.costsKey = key;
    $("#cost-steps-body").innerHTML = byStepHTML(c);
  }
  // The strip that sticks to the top of the panel while the trace scrolls under it.
  const phase = running ? currentPhase(c.phases) : "";
  $("#run-strip").innerHTML = `<span class="dot ${running ? "running" : headline().state.cls}"></span><b>${esc(formatUsd(c.total))}</b><span>${esc(formatUsd(c.llm))} LLM</span>${c.compute !== null ? `<span>${esc(formatUsd(c.compute))} compute</span>` : ""}${phase ? `<span class="strip-phase">${esc(phase)}</span>` : ""}<span class="mono">${esc($("#run-timer").textContent)}</span>`;
  syncStrip();
  if (PHONE.matches) safe(renderPeek);
}

// The strip shows only once the meter has scrolled out of the panel.
let stripObserver = null;
function syncStrip() {
  const strip = $("#run-strip");
  if (!("IntersectionObserver" in window)) return void (strip.hidden = true);
  if (!stripObserver) {
    stripObserver = new IntersectionObserver((entries) => {
      for (const en of entries) S.meterVisible = en.isIntersecting;
      $("#run-strip").hidden = !S.run || $("#run-costs").hidden || S.meterVisible !== false;
    }, { root: $("#panel-body"), threshold: 0 });
    stripObserver.observe($("#cost-main"));
  }
  strip.hidden = !S.run || $("#run-costs").hidden || S.meterVisible !== false;
}

// A lesson's evidence link points at one event (/?run=<id>&seq=<n>): scroll to it and flash it once it's drawn.
function highlightSeq() {
  const r = S.run;
  if (!r || r.seqTarget === null || r.seqTarget === undefined) return;
  const row = document.querySelector(`#timeline li[data-seq="${CSS.escape(String(r.seqTarget))}"]`);
  if (!row) return; // not polled yet: the next renderEvents tries again
  r.seqTarget = null;
  r.scrolledAt = Date.now(); // and don't follow new rows away from it
  row.classList.add("flash");
  // Scroll the panel only (scrollIntoView would also move the page under a phone's sheet).
  const body = $("#panel-body");
  const top = row.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
  body.scrollTo({ top: Math.max(0, top - body.clientHeight / 2 + row.offsetHeight / 2), behavior: "smooth" });
  setTimeout(() => row.classList.remove("flash"), 3000);
}

function t0() {
  const r = S.run;
  return Number(r.info?.started) || Number(r.events[0]?.t) || 0;
}

// The timeline first: it's what the counters describe, so it must never be the part that's missing.
function renderEvents(prevLastSeq) {
  const r = S.run;
  const tl = $("#timeline");
  const fresh = prevLastSeq === null ? r.events : r.events.filter((e) => Number(e.seq) > prevLastSeq);
  // Out-of-order arrivals (rare) are easiest to handle with a full redraw.
  if (fresh.length !== r.events.length - r.rendered || tl.children.length !== r.rendered) {
    tl.innerHTML = r.events.map((e) => eventHTML(e, t0())).join("");
  } else {
    tl.insertAdjacentHTML("beforeend", fresh.map((e) => eventHTML(e, t0())).join(""));
  }
  r.rendered = r.events.length;
  for (const d of tl.querySelectorAll(".ev-detail:not([data-checked])")) {
    d.dataset.checked = "1";
    if (d.scrollHeight > d.clientHeight + 4) d.insertAdjacentHTML("afterend", '<button type="button" class="ev-more">Show more</button>');
  }
  r.lastAt = Date.now();
  safe(renderStats);
  safe(renderSources);
  safe(renderTimer);
  // Live, the trace follows its newest row (the meter's strip stays stuck on top), unless the reader scrolled the
  // panel in the last few seconds. The first batch leaves the plan card and the meter in view.
  const body = $("#panel-body");
  const nearBottom = body.scrollTop > 0 && body.scrollHeight - body.scrollTop - body.clientHeight < 160;
  const idle = Date.now() - r.scrolledAt > FOLLOW_PAUSE;
  if (prevLastSeq !== null && S.tab === "runs" && (nearBottom || (idle && !r.done)) && r.seqTarget === null) {
    const row = tl.lastElementChild;
    if (row && row.getBoundingClientRect().bottom > body.getBoundingClientRect().bottom) body.scrollTop += row.getBoundingClientRect().bottom - body.getBoundingClientRect().bottom + 56;
  }
}
for (const ev of ["wheel", "touchmove", "keydown"]) $("#panel-body").addEventListener(ev, () => S.run && (S.run.scrolledAt = Date.now()), { passive: true });

function renderStats() {
  const s = S.run ? runStats(S.run.events) : runStats([]);
  const prev = S.run?.stats;
  const items = [
    ["read", "📄", "read", "papers read", s.read],
    ["search", "🔎", "searches", "searches", s.search],
    ["calc", "🧮", "calcs", "calculations", s.calc],
    ["write", "✍️", "files", "files written", s.write],
  ];
  $("#run-stats").innerHTML = items
    .map(([k, ic, label, long, n]) => `<div class="stat ${prev && prev[k] !== n ? "bump" : ""}" title="${n} ${esc(long)}"><b>${n}<i aria-hidden="true">${ic}</i></b><span>${label}</span></div>`)
    .join("");
  if (S.run) S.run.stats = s;
}

function renderSources() {
  const list = collectCitations(S.run.events);
  $("#run-sources").hidden = !list.length;
  $("#run-sources-count").textContent = String(list.length);
  $("#run-sources-list").innerHTML = list.map(citeHTML).join("");
}

function showNotice(text, bad = false) {
  const n = $("#run-notice");
  n.hidden = !text;
  n.className = `notice${bad ? " bad" : ""}`;
  if (text) n.textContent = text;
}

$("#timeline").addEventListener("click", (e) => {
  const more = e.target.closest(".ev-more");
  if (!more) return;
  const d = more.previousElementSibling;
  const open = d.classList.toggle("open");
  more.textContent = open ? "Show less" : "Show more";
});

// ---------- the thread ----------
// A message with an id replaces the earlier one with that id (a pending search, a streamed voice turn).
function addMsg(m) {
  const msg = { ...m, id: m.id || newId(m.role) };
  const i = S.thread.findIndex((x) => x.id === msg.id);
  if (i >= 0) {
    S.thread = S.thread.slice();
    S.thread[i] = { ...S.thread[i], ...msg };
  } else S.thread = pushTranscript(S.thread, msg);
  renderThread({ follow: true });
  return msg.id;
}

// The conversation survives a reload (and opening /?run= links) for the rest of the browser session.
const THREAD_KEY = "macrae-thread";
function saveThread() {
  try {
    const keep = S.thread.filter((m) => !m.pending).slice(-80);
    sessionStorage.setItem(THREAD_KEY, JSON.stringify({ thread: keep, runChips: [...S.runChips], announced: [...S.announced] }));
  } catch {}
}
function restoreThread() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(THREAD_KEY) || "null");
    if (!saved || !Array.isArray(saved.thread)) return;
    S.thread = saved.thread.filter((m) => m && typeof m === "object" && m.role && !m.pending);
    S.runChips = new Map(Array.isArray(saved.runChips) ? saved.runChips : []);
    S.announced = new Set(Array.isArray(saved.announced) ? saved.announced : []);
    msgSeq = S.thread.length;
    renderThread({ follow: true });
  } catch {}
}

function renderThread({ follow = false, keepScroll = false } = {}) {
  saveThread();
  const sc = $("#scroll");
  const nearBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 140;
  const top = sc.scrollTop;
  $("#app").classList.toggle("chatting", S.thread.length > 0);
  $("#thread").innerHTML = S.thread.map((m) => messageHTML(m, { runs: S.runChips, costHTML: costUI.answerChip })).join("");
  if (keepScroll && !nearBottom) sc.scrollTop = top;
  else if (follow || nearBottom) sc.scrollTop = sc.scrollHeight;
}

function updateMsg(id, fn) {
  const i = S.thread.findIndex((m) => m.id === id);
  if (i < 0) return;
  S.thread = S.thread.slice();
  S.thread[i] = { ...S.thread[i], ...fn(S.thread[i]) };
  renderThread({ keepScroll: true });
}

// Sources the agent showed go on its answer to the user's latest turn, or wait for that answer.
function attachCitations(list) {
  const cites = numberCitations(list);
  if (!cites.length) return;
  const i = citationTarget(S.thread);
  if (i < 0) {
    S.pendingCites = cites;
    return;
  }
  const m = S.thread[i];
  updateMsg(m.id, () => ({ citations: numberCitations([...(m.citations || []), ...cites]) }));
}

// Source chips and [n] markers open the matching card under their own message.
$("#thread").addEventListener("click", (e) => {
  const msgEl = e.target.closest(".msg");
  if (!msgEl) return;
  const chip = e.target.closest(".src-chip");
  const all = e.target.closest(".src-all");
  const ref = e.target.closest(".ref");
  if (!chip && !all && !ref) return;
  let m = S.thread.find((x) => x.id === msgEl.dataset.id);
  if (!m) return;
  if (all) {
    const open = all.dataset.all === "open";
    return updateMsg(m.id, () => ({ open: open ? m.citations.map((c, i) => c.key || `[${i + 1}]`) : [] }));
  }
  const key = chip ? chip.dataset.key : ref.dataset.ref;
  // A spoken [n] before its sources arrived: use the latest message that has source n.
  if (!(m.citations || []).some((c) => c.key === key)) {
    m = [...S.thread].reverse().find((x) => (x.citations || []).some((c) => c.key === key));
    if (!m) return toast("Jarvis hasn't shown that source yet.");
  }
  const open = new Set(m.open || []);
  const opening = ref ? true : !open.has(key);
  if (opening) open.add(key);
  else open.delete(key);
  updateMsg(m.id, () => ({ open: [...open] }));
  if (opening) {
    const card = document.querySelector(`.msg[data-id="${CSS.escape(m.id)}"] .cite[data-key="${CSS.escape(key)}"]`);
    if (card) {
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
      card.classList.add("flash");
      setTimeout(() => card.classList.remove("flash"), 1500);
    }
  }
});

// ---------- voice ----------
const voice = new Voice({
  onStatus: renderVoiceState,
  onMode: (mode) => ($("#composer").dataset.mode = mode),
  onLevel: (l) => $("#mic-level").style.setProperty("--level", l.toFixed(3)),
  onMessage: ({ role, text, id }) => {
    const lt = S.lastTyped;
    if (role === "user" && lt && lt.text === text.trim() && Date.now() - lt.at < 15_000) return;
    const msg = { role: role === "user" ? "user" : "jarvis", text, via: "voice" };
    if (id !== undefined) msg.id = `${msg.role}:voice:${id}`;
    if (msg.role === "jarvis" && S.pendingCites && citationTarget(S.thread) < 0) {
      msg.citations = S.pendingCites;
      S.pendingCites = null;
    }
    if (msg.role === "user") S.pendingCites = null;
    addMsg(msg);
  },
  onTool: onAgentTool,
  onError: (msg) => {
    const now = Date.now();
    if (onErrorShown.msg === msg && now - onErrorShown.at < 5000) return;
    Object.assign(onErrorShown, { msg, at: now });
    console.warn("voice:", msg);
  },
  clientTools: {
    show_citations: (params) => {
      const list = normalizeCitations(params);
      if (!list.length) return "There were no citations in the request, nothing shown.";
      attachCitations(list);
      return `Showing ${plural(list.length, "source")} under your answer on the page.`;
    },
    open_run: (params) => {
      const id = normalizeRunId(params);
      if (!id) return "No valid run_id was given, so nothing was opened.";
      runStarted(id, { by: "agent" });
      return `Opened run ${id} on the page. The user can see its live trace.`;
    },
  },
});
const onErrorShown = { msg: "", at: 0 };

function renderVoiceState(state, text) {
  const live = state === "live";
  $("#composer").dataset.voice = state;
  document.body.classList.toggle("talking", live);
  const mic = $("#mic");
  mic.setAttribute("aria-pressed", String(live));
  mic.setAttribute("aria-label", live ? "End the voice conversation" : state === "connecting" ? "Connecting, click to cancel" : "Talk to Jarvis");
  mic.title = live ? "End the conversation" : "Talk to Jarvis (voice)";
  $("#mic-text").textContent = { connecting: "Connecting…", live: "End", error: "Talk" }[state] || "Talk";
  $("#mute").hidden = !live;
  if (!live) {
    $("#mute").setAttribute("aria-pressed", "false");
    $("#composer").dataset.mode = "";
  }
  const status = $("#voice-status");
  status.classList.toggle("bad", state === "error");
  status.textContent = state === "idle" ? "" : text || "";
  if (state === "error" && text) addMsg({ role: "note", bad: true, text });
  $("#input").placeholder = live ? "Type to Jarvis" : "Ask Jarvis about the papers";
  $("#hint").textContent = live
    ? "You're in a voice conversation. Typed messages go to Jarvis too."
    : "Typed questions search the papers. Press the microphone to talk to Jarvis.";
  costUI.voice(live);
  if (live && !S.sessionStart) startWatch();
  if (!live && state !== "connecting") stopWatch();
}

$("#mic").addEventListener("click", () => {
  if (voice.state === "live" || voice.state === "connecting") voice.stop();
  else voice.start();
});
$("#mic").addEventListener("pointerenter", preloadSdk, { once: true });
$("#mic").addEventListener("focus", preloadSdk, { once: true });
$("#mute").addEventListener("click", () => {
  const muted = voice.toggleMute();
  $("#mute").setAttribute("aria-pressed", String(muted));
  $("#mute").setAttribute("aria-label", muted ? "Unmute microphone" : "Mute microphone");
});

// While a conversation is on, a run the agent starts (start_task) opens by itself, even if it forgets open_run.
function startWatch() {
  S.sessionStart = Date.now() / 1000;
  clearInterval(S.watch);
  S.watch = setInterval(watchRuns, WATCH_EVERY);
}
function stopWatch() {
  clearInterval(S.watch);
  S.sessionStart = 0;
}
async function watchRuns() {
  if (!S.sessionStart || S.online === false) return;
  let runs;
  try {
    runs = (await api("/api/runs")).runs || [];
  } catch {
    return;
  }
  S.runs = runs;
  safe(renderRuns);
  safe(syncRunChips);
  const fresh = runs.find((r) => !S.knownRuns.has(r.run_id) && Number(r.started) >= S.sessionStart - 10);
  for (const r of runs) S.knownRuns.add(r.run_id);
  if (fresh && !(S.route.view === "run" && S.route.runId === fresh.run_id)) {
    runStarted(fresh.run_id, { title: fresh.title, task_id: fresh.task_id, by: "agent" });
  }
}

const pendingTools = new Map();
function onAgentTool({ name, phase, isError, result }) {
  if (phase === "request") {
    const id = newId("tool");
    pendingTools.set(name, id);
    addMsg({ role: "tool", id, text: `${toolLabel(name)}…`, pending: true });
    return;
  }
  const id = pendingTools.get(name);
  pendingTools.delete(name);
  const entry = { role: "tool", text: isError ? `${toolLabel(name)} failed` : toolLabel(name), err: isError, pending: false };
  addMsg(id ? { ...entry, id } : entry);
  const data = parseToolResult(result);
  if (!data || isError) return;
  if (name === "start_task") {
    const runId = normalizeRunId(data.run_id);
    if (runId && !(S.route.view === "run" && S.route.runId === runId)) runStarted(runId, { by: "agent" });
  } else if (name === "search_papers" && Array.isArray(data.citations) && data.citations.length) {
    attachCitations(normalizeCitations(data.citations));
  }
}

// ---------- typed questions ----------
async function ask(text) {
  const q = text.trim();
  if (!q) return;
  if (voice.live) {
    S.lastTyped = { text: q, at: Date.now() };
    S.pendingCites = null;
    addMsg({ role: "user", text: q });
    voice.sendText(q);
    return;
  }
  addMsg({ role: "user", text: q });
  if (S.online === false) {
    addMsg({ role: "jarvis", bad: true, text: "I can't reach the papers right now: my backend is offline. Try again in a minute; this page keeps checking and the banner goes away when I'm back." });
    return;
  }
  const id = addMsg({ role: "jarvis", pending: true, text: "Searching the papers…" });
  const asked = performance.now();
  try {
    const data = await api("/api/search", { method: "POST", body: { query: q, k: 6 }, timeout: 30_000 });
    const { text: answer, citations } = passagesAnswer(Array.isArray(data.passages) ? data.passages : []);
    const cost = costUI.noteAnswer(id, data.cost, (performance.now() - asked) / 1000);
    addMsg({ id, role: "jarvis", pending: false, text: answer, citations, cost });
  } catch (err) {
    addMsg({
      id, role: "jarvis", pending: false, bad: true,
      text: err.offline ? "I can't reach the papers right now: my backend is offline. Try again in a minute." : `The search failed: ${err.message}`,
    });
  }
}

const input = $("#input");
function fitInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
  $("#send").disabled = !input.value.trim();
}
input.addEventListener("input", fitInput);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value;
  input.value = "";
  fitInput();
  ask(text);
});
$("#suggest").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ask]");
  if (b) ask(b.dataset.ask);
});

// ---------- panel events ----------
$("#tasks").addEventListener("submit", (e) => {
  const form = e.target.closest("form.task");
  if (!form) return;
  e.preventDefault();
  startTask(form.dataset.task, form);
});
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-retry]")) refreshHealth({ announce: true });
  // "Run over run" links in the thread open the Evolution tab without closing the current run
  if (e.target.closest("[data-open-tab=evolution]")) {
    e.preventDefault();
    setTab("evolution");
    $("#panel-body").scrollTop = 0;
    if (PHONE.matches) openSheet(true);
    else {
      $("#app").classList.remove("panel-closed");
      syncPanelButton();
    }
  }
});
$("#health").addEventListener("click", () => refreshHealth({ announce: true }));

// ---------- theme ----------
$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try {
    localStorage.setItem("macrae-theme", root.dataset.theme);
  } catch {}
});

// ---------- start ----------
try {
  const cached = JSON.parse(localStorage.getItem(TASKS_CACHE) || "null");
  if (Array.isArray(cached) && cached.length) S.tasks = cached;
} catch {}
safe(renderTasks);
// Health first, and nothing below can stop it: a throw while opening the run used to leave the pill on "Checking…".
window.addEventListener("unhandledrejection", (e) => console.error("unhandled", e.reason));
refreshHealth();
safe(costUI.start);
safe(restoreThread);
safe(applyRoute, S.route);
setInterval(() => {
  if (!document.hidden) refreshHealth();
}, HEALTH_EVERY);
setInterval(() => {
  if (!document.hidden && S.online && !S.sessionStart) loadRuns();
}, RUNS_EVERY);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshHealth();
});
