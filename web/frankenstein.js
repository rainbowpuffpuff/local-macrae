// The Frankenstein half of the Evolution tab (CONTRACT.md v3): the capabilities the agent built for itself, the
// ledger of how each came to be (gap → create → test → install → use, or rejected), the authority it can't change,
// and the dusk → dawn benchmark. Pure helpers and HTML strings, no DOM (web/tests/frankenstein.test.js).
//   GET /api/capabilities → {"capabilities": [installed, newest first], "ledger": [{t, event, name, run_id, step,
//       kind, why, reason, reasons, passed, n_tests, n_passed, sha256, purpose, seconds, setup_s}],
//       "authority": {"rules": [...], ...server/policy.py}, "forges": [{name, run_id, status, kind, gap_run, t}],
//       "counts": {gap, create, test, install, use, rejected}}
//   GET /api/dawn-report → {"dusk": summary|null, "dawn": summary|null, "delta", "capabilities_installed_between",
//       "runs": [{label, task_id, dusk, dawn}], "ready"}   (evolve/benchmark.py report())
import { esc, truncate, plural, formatDuration, relativeTime } from "./core.js";
import { formatUsd } from "./live.js";

const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const str = (v) => (v === null || v === undefined ? "" : String(v));

export const EVENTS = ["gap", "create", "test", "install", "use", "rejected"];
export const EVENT_META = {
  gap: { icon: "🕳️", label: "Missing capability" },
  create: { icon: "🛠️", label: "Built" },
  test: { icon: "🧪", label: "Tested" },
  install: { icon: "📦", label: "Installed" },
  use: { icon: "🔁", label: "Used again" },
  rejected: { icon: "⛔", label: "Rejected" },
};
const KIND_LABEL = { image: "environment", tool: "tool", writing: "writing" };

const runLink = (runId, text) => (runId ? `<a class="ev-link" href="/?run=${encodeURIComponent(runId)}" data-run="${esc(runId)}">${esc(text)} →</a>` : "");
const shortSha = (s) => (s ? str(s).slice(0, 8) : "");

// ---------- capabilities ----------
function normEvent(e) {
  if (!isObj(e) || !EVENTS.includes(str(e.event))) return null;
  return {
    t: num(e.t), event: str(e.event), name: str(e.name), runId: str(e.run_id), step: str(e.step), kind: str(e.kind),
    why: str(e.why), purpose: str(e.purpose), reasons: Array.isArray(e.reasons) ? e.reasons.map(str).filter(Boolean) : e.reason ? [str(e.reason)] : [],
    passed: e.passed === undefined || e.passed === null ? null : !!e.passed, nTests: num(e.n_tests), nPassed: num(e.n_passed),
    seconds: num(e.seconds), setupS: num(e.setup_s), sha: str(e.sha256), version: str(e.version),
  };
}

function normCap(c) {
  if (!isObj(c) || !c.name) return null;
  const tests = isObj(c.tests) ? c.tests : {};
  const by = isObj(c.created_by) ? c.created_by : {};
  return {
    name: str(c.name), kind: str(c.kind || "tool"), version: str(c.version), purpose: str(c.purpose), usage: str(c.usage),
    uses: num(c.uses) ?? 0, lastUsed: num(c.last_used), installed: num(c.installed), sha: str(c.sha256),
    tests: { n: num(tests.n), passed: tests.passed === undefined || tests.passed === null ? null : !!tests.passed, runId: str(tests.run_id) },
    createdBy: str(by.run_id),
  };
}

// → {caps, ledger (oldest first), forges, counts, rules}
export function normalizeCapabilities(data) {
  const d = isObj(data) ? data : {};
  const ledger = (Array.isArray(d.ledger) ? d.ledger : []).map(normEvent).filter(Boolean)
    .sort((a, b) => (a.t ?? 0) - (b.t ?? 0));
  const counts = {};
  for (const k of EVENTS) counts[k] = num(isObj(d.counts) ? d.counts[k] : null) ?? ledger.filter((e) => e.event === k).length;
  const auth = isObj(d.authority) ? d.authority : {};
  return {
    caps: (Array.isArray(d.capabilities) ? d.capabilities : []).map(normCap).filter(Boolean),
    ledger,
    forges: (Array.isArray(d.forges) ? d.forges : []).filter((f) => isObj(f) && f.name)
      .map((f) => ({ name: str(f.name), runId: str(f.run_id), status: str(f.status || "unknown"), kind: str(f.kind), gapRun: str(f.gap_run), t: num(f.t) })),
    counts,
    rules: (Array.isArray(auth.rules) ? auth.rules : []).map(str).filter(Boolean),
    policySha: str(auth.policy_sha256),
    policySource: str(auth.source),
  };
}

// One line of detail per event: why it was needed, what was built, how the tests went, why it was refused.
export function eventDetail(e) {
  switch (e.event) {
    case "gap": return e.why;
    case "create": return e.purpose || (e.kind ? `a new ${KIND_LABEL[e.kind] || e.kind}` : "");
    case "test": {
      const tally = e.nTests !== null ? `${e.nPassed ?? 0} of ${plural(e.nTests, "test")} passed` : "";
      const verdict = e.passed === null ? "" : e.passed ? "passed in a fresh sandbox" : "failed in a fresh sandbox";
      return [verdict, tally, e.setupS !== null ? `setup ${formatDuration(e.setupS)}` : ""].filter(Boolean).join(" · ");
    }
    case "install": return [e.version ? `version ${e.version}` : "", e.sha ? `sha256 ${shortSha(e.sha)}` : ""].filter(Boolean).join(" · ");
    case "use": return e.step ? `in step ${e.step}` : "";
    case "rejected": return e.reasons.join("; ");
    default: return "";
  }
}

export function ledgerEventHTML(e, now = Date.now() / 1000) {
  const m = EVENT_META[e.event];
  const detail = eventDetail(e);
  return `<li class="cap-ev ce-${esc(e.event)}${e.event === "test" && e.passed === false ? " bad" : ""}">
    <span class="ce-icon" aria-hidden="true">${m.icon}</span>
    <div class="ce-body"><div class="ce-head"><b>${esc(m.label)}</b> <span class="cap-name">${esc(e.name)}</span>${e.t ? `<span class="ce-when">${esc(relativeTime(e.t, now))}</span>` : ""}</div>
      ${detail ? `<p>${esc(truncate(detail, 300))}</p>` : ""}
      ${e.runId ? `<div class="lmeta">${runLink(e.runId, e.step ? `${truncate(e.runId, 26)} · ${e.step}` : truncate(e.runId, 26))}</div>` : ""}</div></li>`;
}

export function capabilityHTML(c, now = Date.now() / 1000) {
  const tests = c.tests.n !== null ? `${plural(c.tests.n, "test")}${c.tests.passed === true ? " passed" : c.tests.passed === false ? " failed" : ""}` : "";
  return `<li class="cap">
    <div class="cap-top"><span class="cap-name">${esc(c.name)}</span><span class="cap-kind">${esc(KIND_LABEL[c.kind] || c.kind)}</span>${c.version ? `<span class="cap-ver">v${esc(c.version)}</span>` : ""}</div>
    ${c.purpose ? `<p>${esc(truncate(c.purpose, 240))}</p>` : ""}
    <div class="lmeta"><span>${esc(c.uses ? `used ${plural(c.uses, "time")}` : "not used yet")}${c.lastUsed ? `, last ${esc(relativeTime(c.lastUsed, now))}` : ""}</span>${tests ? `<span>${esc(tests)}</span>` : ""}${c.sha ? `<span class="mono">sha256 ${esc(shortSha(c.sha))}</span>` : ""}${runLink(c.createdBy, "built in")}</div></li>`;
}

export function authorityHTML(cap) {
  const rules = cap.rules.length ? cap.rules : ["The backend sent no policy."];
  return `<div class="authority"><div class="auth-head"><span aria-hidden="true">🔒</span><b>Authority: fixed</b></div>
    <p class="auth-sub">Capabilities evolve; what they may do does not. These rules are constants in the backend's code${cap.policySource ? ` (<code>${esc(cap.policySource)}</code>)` : ""}, and no capability can change them.</p>
    <ul>${rules.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    ${cap.policySha ? `<p class="auth-sha mono">policy sha256 ${esc(shortSha(cap.policySha))}</p>` : ""}</div>`;
}

export function capabilitiesHTML(cap, { now = Date.now() / 1000, maxEvents = 30 } = {}) {
  const counts = EVENTS.map((k) => `<span class="cc ce-${k}" title="${esc(EVENT_META[k].label)}"><span aria-hidden="true">${EVENT_META[k].icon}</span> ${esc(k)} <b>${cap.counts[k] || 0}</b></span>`).join("");
  const building = cap.forges.filter((f) => f.status === "running" || f.status === "starting");
  const shown = cap.ledger.slice(-maxEvents);
  return `<div class="cap-counts">${counts}</div>
    ${building.length ? `<div class="notice">${building.map((f) => `Building <span class="cap-name">${esc(f.name)}</span> now ${runLink(f.runId, "watch")}`).join("<br>")}</div>` : ""}
    <h4>Installed</h4>
    ${cap.caps.length ? `<ul class="caps">${cap.caps.map((c) => capabilityHTML(c, now)).join("")}</ul>`
      : `<p class="muted evo-none">Nothing installed yet. A capability is installed only after its tests pass in a fresh sandbox and the policy check accepts it.</p>`}
    <h4>How they came to be</h4>
    ${shown.length ? `<ol class="cap-ledger">${shown.map((e) => ledgerEventHTML(e, now)).join("")}</ol>${cap.ledger.length > shown.length ? `<p class="muted evo-none">${esc(plural(cap.ledger.length - shown.length, "older event"))} not shown.</p>` : ""}`
      : `<p class="muted evo-none">No capability events yet: a gap is recorded when a run's trace shows something missing.</p>`}
    ${authorityHTML(cap)}`;
}

// ---------- dusk → dawn ----------
const METRICS = [
  { key: "ok", label: "Passed", fmt: (v, r) => (r && r.n !== undefined ? `${v ?? 0}/${r.n}` : v === true ? "✓" : v === false ? "✗" : "—") },
  { key: "wall_s", label: "Wall time", fmt: (v) => (v === null ? "—" : formatDuration(v)) },
  { key: "setup_s", label: "Setup", fmt: (v) => (v === null ? "—" : formatDuration(v)) },
  { key: "llm_usd", label: "LLM", fmt: (v) => (v === null ? "—" : formatUsd(v)) },
  { key: "compute_usd", label: "Compute", fmt: (v) => (v === null ? "—" : formatUsd(v)) },
  { key: "errors", label: "Errors", fmt: (v) => (v === null ? "—" : String(v)) },
];

function deltaMark(d) {
  if (!isObj(d) || d.better === null || d.better === undefined) return "";
  const pct = num(d.pct);
  return `<span class="delta ${d.better ? "good" : "bad"}">${d.better ? "better" : "worse"}${pct !== null ? ` ${pct > 0 ? "+" : ""}${Math.round(pct)} %` : ""}</span>`;
}

function sideState(s) {
  if (!isObj(s)) return "not run";
  const t = isObj(s.totals) ? s.totals : {};
  if (s.status === "done") return `done, ${plural(num(t.n) ?? 0, "run")}`;
  if (s.status === "failed") return "failed to start";
  return `running, ${num(t.done) ?? 0} of ${plural(num(t.n) ?? 0, "run")} finished`;
}

export function dawnReportHTML(rep) {
  const r = isObj(rep) ? rep : {};
  const ds = isObj(r.dusk) ? r.dusk : null;
  const dw = isObj(r.dawn) ? r.dawn : null;
  const status = `<div class="dd-status"><span class="dd-side dusk">🌆 Dusk (nothing learned): ${esc(sideState(ds))}</span><span class="dd-side dawn">🌅 Dawn (everything learned): ${esc(sideState(dw))}</span></div>`;
  if (!r.ready) {
    return `${status}<p class="muted evo-none">No comparison yet: the benchmark needs a finished dusk run and a finished dawn run of the same tasks. Until both exist there are no dusk → dawn numbers to show.</p>`;
  }
  const td = isObj(ds.totals) ? ds.totals : {};
  const tw = isObj(dw.totals) ? dw.totals : {};
  const delta = isObj(r.delta) ? r.delta : {};
  const totalRow = (m) => {
    const a = m.key === "ok" ? m.fmt(num(td.ok), { n: num(td.n) }) : m.fmt(num(td[m.key]));
    const b = m.key === "ok" ? m.fmt(num(tw.ok), { n: num(tw.n) }) : m.fmt(num(tw[m.key]));
    return `<tr><th scope="row">${esc(m.label)}</th><td>${esc(a)}</td><td>${esc(b)}</td><td>${deltaMark(delta[m.key])}</td></tr>`;
  };
  const dRows = new Map((Array.isArray(ds.tasks) ? ds.tasks : []).map((x) => [x.label, x]));
  const byTask = isObj(delta.by_task) ? delta.by_task : {};
  const tasks = (Array.isArray(dw.tasks) ? dw.tasks : []).map((b) => {
    const a = dRows.get(b.label) || {};
    const dt = isObj(byTask[b.label]) ? byTask[b.label] : {};
    const cell = (m, row) => (m.key === "ok" ? m.fmt(row.ok === undefined ? null : !!row.ok) : m.fmt(num(row[m.key])));
    return `<div class="dd-task"><div class="dd-task-head"><b>${esc(b.label || b.task_id || "")}</b><span class="lmeta">${runLink(a.run_id, "dusk run")}${runLink(b.run_id, "dawn run")}</span></div>
      <div class="dd-scroll"><table class="dd-table"><thead><tr><th></th><th>Dusk</th><th>Dawn</th><th></th></tr></thead><tbody>
      ${METRICS.map((m) => `<tr><th scope="row">${esc(m.label)}</th><td>${esc(cell(m, a))}</td><td>${esc(cell(m, b))}</td><td>${deltaMark(dt[m.key])}</td></tr>`).join("")}
      </tbody></table></div>
      ${(b.capabilities_used || []).length ? `<p class="dd-used">Used at dawn: ${b.capabilities_used.map((n) => `<span class="cap-name">${esc(n)}</span>`).join(" ")}</p>` : ""}</div>`;
  }).join("");
  const between = Array.isArray(r.capabilities_installed_between) ? r.capabilities_installed_between.filter(isObj) : [];
  return `${status}
    <div class="dd-scroll"><table class="dd-table dd-totals"><thead><tr><th>All tasks</th><th>Dusk</th><th>Dawn</th><th></th></tr></thead><tbody>${METRICS.map(totalRow).join("")}</tbody></table></div>
    ${tasks}
    <h4>Installed between dusk and dawn</h4>
    ${between.length ? `<ul class="caps">${between.map((c) => `<li class="cap"><div class="cap-top"><span class="cap-name">${esc(c.name)}</span>${c.kind ? `<span class="cap-kind">${esc(KIND_LABEL[c.kind] || c.kind)}</span>` : ""}</div>
      ${c.purpose ? `<p>${esc(truncate(str(c.purpose), 240))}</p>` : ""}${c.why ? `<p class="muted">Why: ${esc(truncate(str(c.why), 240))}</p>` : ""}
      <div class="lmeta">${runLink(str(c.gap_run), "gap seen in")}${runLink(str(c.forge_run), "built in")}</div></li>`).join("")}</ul>`
      : `<p class="muted evo-none">No capability was installed between the two benchmarks: any difference comes from the lessons alone.</p>`}`;
}
