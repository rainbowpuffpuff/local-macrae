// The run's economics and the planner's decision (CONTRACT.md, v2 addendum): pure helpers and HTML strings, no DOM,
// so `node --test` can check them (web/tests/live.test.js).
//   - the plan: the first `plan` event(s) of a run, `{"plan": [stage…], "hardware", "params", "budget_usd", "why"}`
//   - costs: `GET /api/runs/{id}` → `costs` {llm_usd, compute_usd, total_usd, tokens, by_step, wall_s, phases},
//     or, while that isn't there, the sum of the events' own `cost` {usd, tokens}
//   - between two polls the meter keeps moving: compute cost and the current phase grow at the rate seen so far,
//     and LLM cost adds the `cost` of every event that arrived since the last run poll.
import { esc, truncate, formatDuration, plural } from "./core.js";

// ---------- formatting ----------
export function formatUsd(x) {
  const n = Number(x);
  if (x === null || x === undefined || x === "" || !Number.isFinite(n)) return "—";
  if (n === 0) return "$0.00";
  const a = Math.abs(n);
  const s = a < 0.01 ? a.toFixed(4) : a < 1 ? a.toFixed(3) : a < 1000 ? a.toFixed(2) : Math.round(a).toLocaleString("en-US");
  return `${n < 0 ? "−" : ""}$${s}`;
}

export function formatTokens(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return "0";
  if (v < 1000) return String(Math.round(v));
  if (v < 1e6) return `${(v / 1000).toFixed(v < 10_000 ? 1 : 0).replace(/\.0$/, "")}k`;
  return `${(v / 1e6).toFixed(v < 1e7 ? 1 : 0).replace(/\.0$/, "")}M`;
}

// Short wall times for the phase bar: "0.4 s", "12 s", "4 min 05 s".
export function formatSeconds(s) {
  const v = Number(s);
  if (!Number.isFinite(v) || v <= 0) return "0 s";
  return v < 10 ? `${v.toFixed(1).replace(/\.0$/, "")} s` : formatDuration(v);
}

// "gpu-a10g" → "GPU A10G", "cpu-8" → "8 CPU", "gpu-a100-80gb" → "GPU A100-80GB", {gpu: "L4"} → "GPU L4".
export function hardwareLabel(hw) {
  if (!hw) return "";
  if (typeof hw === "object") {
    if (hw.label) return String(hw.label);
    if (hw.gpu) return `GPU ${String(hw.gpu).toUpperCase()}`;
    if (hw.cpu) return `${hw.cpu} CPU`;
    return hardwareLabel(hw.name || hw.type || "");
  }
  const s = String(hw).trim();
  let m;
  if ((m = s.match(/^cpu[-_ ]?(\d+(?:\.\d+)?)$/i))) return `${m[1]} CPU`;
  if (/^cpu$/i.test(s)) return "CPU";
  if ((m = s.match(/^gpu[-_ ]?(.+)$/i))) return `GPU ${m[1].toUpperCase()}`;
  return s;
}

// ---------- the plan ----------
const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);

function tryJSON(s) {
  if (typeof s !== "string" || !/^\s*\{/.test(s)) return null;
  try {
    const v = JSON.parse(s);
    return isObj(v) ? v : null;
  } catch {
    return null;
  }
}

const looksLikePlan = (v) => isObj(v) && ("plan" in v || "stages" in v || "hardware" in v || "budget_usd" in v);

// The structured decision carried by a plan event, wherever the backend put it.
function planData(e) {
  for (const v of [e.plan, e.data && e.data.plan, e.data, tryJSON(e.detail)]) {
    if (looksLikePlan(v)) return v;
  }
  return null;
}

const isPlanEvent = (e) => e && (e.type === "plan" || ((e.step === "plan" || e.step === "planner") && e.type !== "result"));

function stageTitle(s) {
  if (typeof s === "string") return { title: s, note: "" };
  if (!isObj(s)) return { title: String(s ?? ""), note: "" };
  const title = s.title || s.name || s.stage || s.id || s.step || "";
  const bits = [];
  if (s.hardware) bits.push(hardwareLabel(s.hardware));
  if (s.minutes) bits.push(`~${s.minutes} min`);
  if (s.what && s.what !== title) bits.push(s.what);
  return { title: String(title), note: bits.join(" · ") };
}

// → null, or {title, why, hardware, hw, stages: [{title, note}], params: [[k, v]], budget, fallback, model, lessonsUsed,
//   lessons, cost, seq, t}. The last structured plan event wins; failing that, the last plan event as text.
export function planFromEvents(events, info = null) {
  const evs = (events || []).filter(isPlanEvent);
  let e = null;
  let data = null;
  for (let i = evs.length - 1; i >= 0 && !data; i--) {
    const d = planData(evs[i]);
    if (d) [e, data] = [evs[i], d];
  }
  if (!data && looksLikePlan(info && info.plan)) data = info.plan;
  if (!e) e = evs[evs.length - 1] || null;
  if (!e && !data) return null;
  const d = data || {};
  const stagesRaw = Array.isArray(d.plan) ? d.plan : Array.isArray(d.stages) ? d.stages : [];
  const params = isObj(d.params) ? Object.entries(d.params).filter(([, v]) => v !== null && v !== undefined && v !== "") : [];
  const text = `${(e && e.title) || ""} ${(e && e.detail) || ""} ${d.why || ""}`;
  const fallback = !!(d.fallback || d.error || /^(default|defaults|fallback|task)$/i.test(String(d.source || ""))) ||
    (!data && /\b(default|fall ?back|unavailable|failed)\b/i.test(text));
  const budget = Number(d.budget_usd ?? d.budget);
  const plan = {
    title: (e && e.title) || "",
    why: String(d.why || (e && e.detail && !tryJSON(e.detail) ? e.detail : "") || ""),
    hardware: d.hardware || "",
    hw: hardwareLabel(d.hardware),
    stages: stagesRaw.map(stageTitle).filter((s) => s.title),
    params: params.map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)]),
    budget: Number.isFinite(budget) && budget > 0 ? budget : null,
    fallback,
    model: String(d.planner_model || d.model_used || (e && e.model) || ""),
    lessonsUsed: Number.isFinite(Number(d.lessons_used)) ? Number(d.lessons_used) : Array.isArray(d.lessons) ? d.lessons.length : null,
    lessons: Array.isArray(d.lessons) ? d.lessons.map(String) : [],
    cost: e && isObj(e.cost) ? e.cost : null,
    seq: e ? e.seq : null,
    t: e ? e.t : null,
  };
  return plan;
}

// "Decided: GPU A10G, 3 stages, budget $2.00": the line the panel, the run chip and Jarvis use.
export function planHeadline(plan) {
  if (!plan) return "";
  if (plan.fallback) return plan.hw ? `Planner unavailable: task defaults (${plan.hw})` : "Planner unavailable: using the task's defaults";
  const bits = [];
  if (plan.hw) bits.push(plan.hw);
  if (plan.stages.length) bits.push(plural(plan.stages.length, "stage"));
  if (plan.budget !== null) bits.push(`budget ${formatUsd(plan.budget)}`);
  if (!bits.length) return truncate(plan.title || "Planned the run", 90);
  return `Decided: ${bits.join(", ")}`;
}

// ---------- costs ----------
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

export function normalizeTokens(t) {
  if (!isObj(t)) return { in: 0, out: 0, cache: 0, total: 0 };
  const inp = num(t.in ?? t.input ?? t.input_tokens ?? t.prompt);
  const out = num(t.out ?? t.output ?? t.output_tokens ?? t.completion);
  const cache = isObj(t.cache) ? num(t.cache.read) + num(t.cache.write) :
    num(t.cache ?? 0) + num(t.cache_read ?? t.cache_read_input_tokens ?? 0) + num(t.cache_write ?? t.cache_creation_input_tokens ?? 0);
  return { in: inp, out, cache, total: inp + out + cache };
}

export const PHASES = ["plan", "setup", "work", "check"];

function phaseList(phases) {
  if (!isObj(phases)) return [];
  const keys = [...PHASES.filter((k) => k in phases), ...Object.keys(phases).filter((k) => !PHASES.includes(k))];
  return keys.map((key) => {
    const v = phases[key];
    if (isObj(v) && v.seconds === undefined && v.start !== undefined) {
      // {start, end} in Unix seconds; an open phase runs until now
      const end = v.end === null || v.end === undefined ? Date.now() / 1000 : num(v.end);
      return { key, seconds: Math.max(0, end - num(v.start)) };
    }
    return { key, seconds: num(isObj(v) ? v.seconds ?? v.s ?? v.wall_s : v) };
  });
}

// Costs from the events alone (each event's `cost` is its own share): LLM only; compute is unknown without the run.
export function costsFromEvents(events) {
  let usd = 0;
  let any = false;
  const tok = { in: 0, out: 0, cache: 0, total: 0 };
  for (const e of events || []) {
    if (!e || !isObj(e.cost)) continue;
    any = true;
    usd += num(e.cost.usd);
    const t = normalizeTokens(e.cost.tokens);
    for (const k of Object.keys(tok)) tok[k] += t[k];
  }
  return any ? { llm: usd, tokens: tok } : null;
}

// → null, or {llm, compute, total, tokens, byStep: [{step, llm, compute, seconds, model, hardware}], wall, phases:
//   [{key, seconds}], source: "run" | "events"}
export function runCosts(info, events) {
  const c = info && isObj(info.costs) ? info.costs : null;
  if (c) {
    const llm = num(c.llm_usd);
    const compute = num(c.compute_usd);
    const byStep = isObj(c.by_step)
      ? Object.entries(c.by_step).map(([step, s]) => ({
        step, llm: num(s && s.llm_usd), compute: num(s && s.compute_usd), seconds: num(s && s.seconds),
        model: String((s && s.model) || ""), hardware: (s && s.hardware) || "",
      }))
      : [];
    return {
      llm, compute, total: c.total_usd !== undefined && c.total_usd !== null ? num(c.total_usd) : llm + compute,
      tokens: normalizeTokens(c.tokens), byStep, wall: c.wall_s === undefined || c.wall_s === null ? null : num(c.wall_s),
      phases: phaseList(c.phases), source: "run",
    };
  }
  const ev = costsFromEvents(events);
  if (!ev) return null;
  return { llm: ev.llm, compute: null, total: ev.llm, tokens: ev.tokens, byStep: [], wall: null, phases: [], source: "events" };
}

// The phase the run is in: the last one with time on it (they run in order).
export function currentPhase(phases) {
  let cur = "";
  for (const p of phases || []) if (p.seconds > 0) cur = p.key;
  return cur;
}

// What the meter shows at `now` (seconds): the last run poll (`snap` = {at, costs, lastSeq}) plus what happened since.
// prev: the poll before it, for the compute rate. Never extrapolates more than `horizon` seconds past a poll, so a
// stalled backend doesn't make up money.
export function liveCosts({ snap, prev = null, events = [], now, running = true, horizon = 10 } = {}) {
  if (!snap || !snap.costs) {
    const ev = runCosts(null, events);
    return ev ? { ...ev, live: false } : null;
  }
  const base = snap.costs;
  const out = { ...base, tokens: { ...base.tokens }, phases: base.phases.map((p) => ({ ...p })), live: false };
  if (!running) return out;
  const dt = Math.max(0, Math.min(horizon, num(now) - num(snap.at)));
  // LLM: events that came in after the poll, with their own cost.
  if (snap.lastSeq !== null && snap.lastSeq !== undefined) {
    const later = (events || []).filter((e) => Number(e.seq) > Number(snap.lastSeq));
    const extra = costsFromEvents(later);
    if (extra) {
      out.llm += extra.llm;
      for (const k of Object.keys(out.tokens)) out.tokens[k] += extra.tokens[k] || 0;
    }
  }
  // Compute: the rate between the last two polls.
  if (base.compute !== null && prev && prev.costs && prev.costs.compute !== null && snap.at > prev.at) {
    const rate = (base.compute - prev.costs.compute) / (snap.at - prev.at);
    if (rate > 0 && rate < 1) out.compute = base.compute + rate * dt;
  }
  out.total = out.llm + (out.compute || 0);
  if (out.wall !== null) out.wall += dt;
  const cur = currentPhase(out.phases);
  if (cur) out.phases.find((p) => p.key === cur).seconds += dt;
  out.live = true;
  return out;
}

// A number that only goes up while a run is live (two sources can disagree by a cent for a second).
export function monotonic(prev, next) {
  if (!prev || !next) return next;
  const keep = (a, b) => (a === null || a === undefined ? b : b === null || b === undefined ? a : Math.max(a, b));
  return { ...next, llm: keep(prev.llm, next.llm), compute: keep(prev.compute, next.compute), total: keep(prev.total, next.total) };
}

export function budgetState(total, budget) {
  if (!(Number(budget) > 0) || !Number.isFinite(Number(total))) return null;
  const pct = (Number(total) / Number(budget)) * 100;
  return { pct, cls: pct > 100 ? "over" : pct >= 80 ? "warn" : "ok" };
}

// ---------- html ----------
const PHASE_LABELS = { plan: "Plan", setup: "Setup", work: "Work", check: "Check" };

// The first card of the run: what the planner decided, and why.
export function planCardHTML(plan) {
  if (!plan) return "";
  const head = planHeadline(plan);
  const cost = plan.cost && Number(plan.cost.usd) > 0 ? formatUsd(plan.cost.usd) : "";
  const foot = [
    plan.fallback ? "" : plan.model ? `planned by ${plan.model}` : "",
    cost ? `planning ${cost}` : "",
    plan.lessonsUsed ? `${plural(plan.lessonsUsed, "lesson")} from earlier runs applied` : "",
  ].filter(Boolean);
  return `<div class="plan${plan.fallback ? " fallback" : ""}" data-seq="${esc(plan.seq ?? "")}">
    <div class="plan-head"><span class="plan-icon" aria-hidden="true">🧭</span><b>${esc(head)}</b></div>
    ${plan.why ? `<p class="plan-why">${plan.fallback ? "" : "<span>because </span>"}${esc(truncate(plan.why, 420))}</p>` : ""}
    ${plan.stages.length ? `<ol class="plan-stages${plan.stages.length > 4 ? " two" : ""}">${plan.stages.map((s) => `<li><span>${esc(s.title)}</span>${s.note ? `<em>${esc(s.note)}</em>` : ""}</li>`).join("")}</ol>` : ""}
    ${plan.params.length ? `<div class="plan-params">${plan.params.slice(0, 10).map(([k, v]) => `<span class="kv"><i>${esc(k)}</i>${esc(truncate(v, 40))}</span>`).join("")}</div>` : ""}
    ${foot.length ? `<div class="plan-foot">${esc(foot.join(" · "))}</div>` : ""}
  </div>`;
}

// The running meter: total, LLM and compute, tokens, the budget, and the time per phase.
export function costMeterHTML(costs, { budget = null, running = false } = {}) {
  if (!costs) return "";
  const b = budgetState(costs.total, budget);
  const tok = costs.tokens || { in: 0, out: 0, cache: 0, total: 0 };
  const parts = [
    ["LLM", formatUsd(costs.llm)],
    ["compute", costs.compute === null ? "—" : formatUsd(costs.compute)],
  ];
  return `<div class="meter${running ? " live" : ""}">
    <div class="meter-top">
      <div class="meter-total"><span class="meter-label">${running ? "Cost so far" : "Cost"}</span><b>${esc(formatUsd(costs.total))}</b></div>
      <div class="meter-parts">${parts.map(([k, v]) => `<span><i>${esc(k)}</i>${esc(v)}</span>`).join("")}</div>
    </div>
    <div class="meter-tokens" title="${esc(`${tok.in} input · ${tok.out} output · ${tok.cache} cache tokens`)}">${esc(formatTokens(tok.total))} tokens · ${esc(formatTokens(tok.in))} in · ${esc(formatTokens(tok.out))} out · ${esc(formatTokens(tok.cache))} cache</div>
    ${b ? `<div class="budget ${b.cls}" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(Math.min(b.pct, 100))}" aria-label="Share of the budget used">
      <span class="budget-track"><span class="budget-fill" style="width:${Math.min(100, b.pct).toFixed(1)}%"></span></span>
      <span class="budget-text">${b.cls === "over" ? "⚠️ " : ""}${Math.round(b.pct)} % of the ${esc(formatUsd(budget))} budget</span></div>` : ""}
    ${phaseBarHTML(costs.phases, { running, wall: costs.wall })}
  </div>`;
}

// Time per phase as one bar, in order; the phase in progress is marked. Labels are direct (no legend needed).
export function phaseBarHTML(phases, { running = false, wall = null } = {}) {
  const list = (phases || []).filter((p) => PHASES.includes(p.key) || p.seconds > 0);
  if (!list.length) return "";
  const total = list.reduce((s, p) => s + p.seconds, 0);
  const cur = running ? currentPhase(list) : "";
  const segs = list
    .filter((p) => p.seconds > 0)
    .map((p) => `<span class="ph ph-${esc(p.key)}${p.key === cur ? " now" : ""}" style="flex-grow:${Math.max(p.seconds / (total || 1), 0.02).toFixed(4)}" title="${esc(`${PHASE_LABELS[p.key] || p.key}: ${formatSeconds(p.seconds)}`)}"></span>`)
    .join("");
  return `<div class="phases">
    <div class="phase-head"><span>Time</span><b>${esc(formatSeconds(wall !== null && wall !== undefined ? Math.max(wall, total) : total))}</b></div>
    <div class="phase-bar" aria-hidden="true">${segs || '<span class="ph ph-empty"></span>'}</div>
    <div class="phase-legend">${list.map((p) => `<span class="pl${p.key === cur ? " now" : ""}${p.seconds > 0 ? "" : " zero"}"><i class="sw ph-${esc(p.key)}"></i>${esc(PHASE_LABELS[p.key] || p.key)} <b>${esc(formatSeconds(p.seconds))}</b></span>`).join("")}</div>
  </div>`;
}

// One row per step: model or hardware, seconds, LLM and compute cost.
export function byStepHTML(costs) {
  const rows = (costs && costs.byStep) || [];
  if (!rows.length) return "";
  return `<table class="by-step"><thead><tr><th>Step</th><th>Ran on</th><th>Time</th><th>LLM</th><th>Compute</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td class="mono">${esc(r.step)}</td><td>${esc([hardwareLabel(r.hardware), r.model].filter(Boolean).join(" · ") || "backend")}</td><td>${esc(formatSeconds(r.seconds))}</td><td>${esc(formatUsd(r.llm))}</td><td>${esc(r.compute ? formatUsd(r.compute) : "—")}</td></tr>`).join("")}
  </tbody></table>`;
}

// A trace row's small print: how long the call took and what it cost. If `elapsed_s` is just the event's offset from
// the run's start (t0), the row's own "+m:ss" already says that, so it isn't repeated as a duration.
export function eventCostText(e, t0 = null) {
  const bits = [];
  const el = e ? Number(e.elapsed_s) : 0;
  const offset = t0 !== null && Number.isFinite(Number(t0)) && Number.isFinite(Number(e && e.t)) ? Number(e.t) - Number(t0) : null;
  if (el > 0 && !(offset !== null && el > 5 && Math.abs(el - offset) < 1.5)) bits.push(formatSeconds(el));
  if (e && isObj(e.cost) && Number(e.cost.usd) > 0) {
    const t = normalizeTokens(e.cost.tokens);
    bits.push(t.total ? `${formatUsd(e.cost.usd)} · ${formatTokens(t.total)} tok` : formatUsd(e.cost.usd));
  }
  return bits.join(" · ");
}
