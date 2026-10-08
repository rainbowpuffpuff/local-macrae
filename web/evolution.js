// The Evolution panel (CONTRACT.md, v2 addendum): per task, run over run, how long it took, what it cost and whether
// it passed, plus the lessons the agent learned from its traces, each linked to the run (and event) it came from.
// Pure helpers and HTML strings, no DOM (web/tests/evolution.test.js).
//   GET /api/evolution → {"by_task": {task_id: [{"run_id", "ok", "reward", "wall_s", "total_usd", "lessons_used"}]},
//                         "lessons": [{task_id, lesson, evidence: ["run_id/step/seq"], kind: "do|avoid|setting|tool", created}]}
// `lessons` may also come keyed by task, or inside by_task entries as {runs, lessons}.
import { esc, truncate, plural, formatDuration, relativeTime } from "./core.js";
import { formatUsd } from "./live.js";

const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));

// "20261008-181714-small-calc-ab12/calc[0]/14" → {runId, step, seq}; objects pass through.
export function parseEvidence(ev) {
  if (isObj(ev)) {
    const runId = String(ev.run_id || ev.runId || ev.run || "");
    return runId ? { runId, step: String(ev.step || ""), seq: num(ev.seq) } : null;
  }
  const parts = String(ev || "").trim().split("/").filter((p) => p !== "");
  if (!parts.length || !/^[\w.:-]{1,200}$/.test(parts[0])) return null;
  const runId = parts[0];
  let seq = null;
  if (parts.length > 1 && /^\d+$/.test(parts[parts.length - 1])) seq = Number(parts.pop());
  return { runId, step: parts.slice(1).join("/"), seq };
}

// Run ids start with their time (agent_runner: 20261008-181714-…, the mock: 20261008181714-…).
export function runIdTime(runId) {
  const m = String(runId || "").match(/^(\d{4})(\d{2})(\d{2})-?(\d{2})(\d{2})(\d{2})/);
  if (!m) return null;
  const t = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000;
  return Number.isFinite(t) ? t : null;
}

function normRun(r) {
  if (!isObj(r) || !r.run_id) return null;
  const reward = num(r.reward);
  const ok = r.ok === undefined || r.ok === null ? (reward !== null ? reward >= 1 : null) : !!r.ok;
  return {
    run_id: String(r.run_id), ok, reward, wall_s: num(r.wall_s), total_usd: num(r.total_usd),
    lessons_used: num(r.lessons_used), started: num(r.started) ?? runIdTime(r.run_id),
  };
}

function normLesson(l, taskId = "") {
  if (typeof l === "string") return { task_id: taskId, lesson: l, kind: "do", evidence: [], created: null, tool: "" };
  if (!isObj(l) || !(l.lesson || l.text)) return null;
  const evidence = (Array.isArray(l.evidence) ? l.evidence : l.evidence ? [l.evidence] : []).map(parseEvidence).filter(Boolean);
  const kind = String(l.kind || "do").toLowerCase();
  return {
    task_id: String(l.task_id || taskId || ""), lesson: String(l.lesson || l.text), kind: ["do", "avoid", "setting", "tool"].includes(kind) ? kind : "do",
    evidence, created: num(l.created), tool: String(l.tool || l.name || ""),
  };
}

// → [{taskId, title, icon, runs: [...oldest first], lessons: [...newest first]}], tasks in the page's task order.
export function normalizeEvolution(data, tasks = []) {
  const groups = new Map();
  const group = (id) => {
    if (!groups.has(id)) groups.set(id, { taskId: id, runs: [], lessons: [] });
    return groups.get(id);
  };
  const byTask = isObj(data && data.by_task) ? data.by_task : {};
  for (const [id, v] of Object.entries(byTask)) {
    const runs = Array.isArray(v) ? v : isObj(v) && Array.isArray(v.runs) ? v.runs : [];
    group(id).runs.push(...runs.map(normRun).filter(Boolean));
    if (isObj(v) && Array.isArray(v.lessons)) group(id).lessons.push(...v.lessons.map((l) => normLesson(l, id)).filter(Boolean));
  }
  const lessons = data && data.lessons;
  if (Array.isArray(lessons)) for (const l of lessons.map((x) => normLesson(x)).filter(Boolean)) group(l.task_id || "other").lessons.push(l);
  else if (isObj(lessons)) for (const [id, list] of Object.entries(lessons)) if (Array.isArray(list)) group(id).lessons.push(...list.map((l) => normLesson(l, id)).filter(Boolean));

  const order = (tasks || []).map((t) => t && t.id);
  return [...groups.values()]
    .map((g) => {
      const task = (tasks || []).find((t) => t && t.id === g.taskId);
      const runs = g.runs.slice().sort((a, b) => (a.started ?? 0) - (b.started ?? 0) || a.run_id.localeCompare(b.run_id));
      const lessons = g.lessons.slice().sort((a, b) => (b.created ?? 0) - (a.created ?? 0));
      return { ...g, runs, lessons, title: (task && task.title) || g.taskId.replace(/[-_]+/g, " "), icon: task && task.icon };
    })
    .sort((a, b) => {
      const ia = order.indexOf(a.taskId);
      const ib = order.indexOf(b.taskId);
      return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib) || a.taskId.localeCompare(b.taskId);
    });
}

// First vs latest successful run: the headline "it got better" numbers.
export function improvement(runs, key) {
  const vals = (runs || []).filter((r) => r.ok !== false && r[key] !== null && r[key] !== undefined).map((r) => r[key]);
  if (vals.length < 2) return null;
  const first = vals[0];
  const last = vals[vals.length - 1];
  return { first, last, pct: first ? ((last - first) / first) * 100 : 0 };
}

export function successRate(runs, last = 5) {
  const recent = (runs || []).filter((r) => r.ok !== null).slice(-last);
  return { ok: recent.filter((r) => r.ok).length, n: recent.length };
}

// ---------- sparkline ----------
// One series, no axes: earlier runs in the muted ink, the latest in the accent; failed runs are hollow rings in the
// status red. Each point has a large invisible hit target with a <title> as its tooltip.
export function sparklineSVG(runs, key, { w = 200, h = 36, fmt = String, label = key } = {}) {
  const pts = (runs || []).map((r, i) => ({ r, i, v: r[key] })).filter((p) => p.v !== null && p.v !== undefined);
  if (!pts.length) return `<svg class="spark empty" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"></svg>`;
  const pad = 6;
  const vs = pts.map((p) => p.v);
  const lo = Math.min(...vs, 0);
  const hi = Math.max(...vs);
  const n = Math.max(1, (runs || []).length - 1);
  const x = (i) => pad + ((w - 2 * pad) * i) / n;
  const y = (v) => (hi === lo ? h / 2 : h - pad - ((h - 2 * pad) * (v - lo)) / (hi - lo));
  const line = pts.map((p) => `${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const dots = pts.map((p) => {
    const cls = p.r.ok === false ? "fail" : p === last ? "last" : "";
    const tip = `Run ${p.i + 1} (${p.r.run_id}): ${fmt(p.v)}${p.r.ok === false ? " · failed" : p.r.ok ? " · passed" : ""}`;
    return `<a href="/?run=${encodeURIComponent(p.r.run_id)}" data-run="${esc(p.r.run_id)}" class="sp-pt ${cls}"><title>${esc(tip)}</title><circle class="hit" cx="${x(p.i).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="9"/><circle class="pt" cx="${x(p.i).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="${p === last ? 4 : 3}"/></a>`;
  }).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(`${label}, run over run: ${pts.map((p) => fmt(p.v)).join(", ")}`)}"><polyline points="${line}"/>${dots}</svg>`;
}

// Pass/fail per run: a check or a cross in a coloured disc (shape and colour, never colour alone).
export function successDotsHTML(runs) {
  return `<span class="okdots">${(runs || []).map((r, i) => {
    const st = r.ok === null ? "unknown" : r.ok ? "ok" : "fail";
    const sym = st === "ok" ? "✓" : st === "fail" ? "✗" : "·";
    return `<a class="okdot ${st}" href="/?run=${encodeURIComponent(r.run_id)}" data-run="${esc(r.run_id)}" title="${esc(`Run ${i + 1}: ${st === "ok" ? "passed" : st === "fail" ? "failed" : "unknown"}${r.reward !== null ? ` · reward ${r.reward}` : ""}`)}">${sym}</a>`;
  }).join("")}</span>`;
}

const KIND = { do: { icon: "✅", label: "Do" }, avoid: { icon: "⛔", label: "Avoid" }, setting: { icon: "⚙️", label: "Setting" }, tool: { icon: "🧰", label: "Tool" } };

export function evidenceHref(ev) {
  const q = new URLSearchParams({ run: ev.runId });
  if (ev.seq !== null && ev.seq !== undefined) q.set("seq", String(ev.seq));
  return `/?${q.toString()}`;
}

function evidenceLabel(ev, runs) {
  const i = (runs || []).findIndex((r) => r.run_id === ev.runId);
  const which = i >= 0 ? `run ${i + 1}` : `run ${truncate(ev.runId, 22)}`;
  return [which, ev.step, ev.seq !== null ? `#${ev.seq}` : ""].filter(Boolean).join(" · ");
}

export function lessonHTML(l, runs = []) {
  const k = KIND[l.kind] || KIND.do;
  return `<li class="lesson k-${esc(l.kind)}"><span class="lk" title="${esc(k.label)}"><span aria-hidden="true">${k.icon}</span><span class="sr-only">${esc(k.label)}: </span></span>
    <div class="lb"><p>${esc(truncate(l.lesson, 360))}</p>
      <div class="lmeta">${l.tool ? `<span class="ltool">${esc(l.tool)}</span>` : ""}${l.evidence.map((ev) => `<a class="ev-link" href="${esc(evidenceHref(ev))}" data-run="${esc(ev.runId)}"${ev.seq !== null ? ` data-seq="${esc(ev.seq)}"` : ""}>${esc(evidenceLabel(ev, runs))} →</a>`).join("")}${l.created ? `<span class="lwhen">${esc(relativeTime(l.created))}</span>` : ""}</div></div></li>`;
}

function deltaHTML(imp, fmt) {
  if (!imp) return "";
  const better = imp.pct < 0;
  const flat = Math.abs(imp.pct) < 1;
  return `<span class="delta ${flat ? "" : better ? "good" : "bad"}" title="${esc(`first passing run ${fmt(imp.first)} → latest ${fmt(imp.last)}`)}">${flat ? "±0 %" : `${better ? "▼" : "▲"} ${Math.abs(Math.round(imp.pct))} %`}</span>`;
}

// One task: three small multiples (time, cost, pass/fail), the lessons, and a table of the runs.
export function evolutionTaskHTML(g, { icon = "" } = {}) {
  const runs = g.runs;
  const time = improvement(runs, "wall_s");
  const cost = improvement(runs, "total_usd");
  const sr = successRate(runs);
  const last = runs[runs.length - 1];
  const fmtT = (s) => formatDuration(s);
  const row = (label, spark, value, delta) => `<div class="evo-row"><span class="evo-label">${label}</span>${spark}<span class="evo-val">${value}${delta}</span></div>`;
  return `<article class="evo-task" data-task="${esc(g.taskId)}">
    <header class="evo-head">${icon ? `<span class="task-icon">${icon}</span>` : ""}<div class="task-text"><b>${esc(g.title)}</b><span>${esc([plural(runs.length, "run"), plural(g.lessons.length, "lesson")].join(" · "))}${last && last.lessons_used ? ` · last run used ${esc(plural(last.lessons_used, "lesson"))}` : ""}</span></div></header>
    ${runs.length ? `<div class="evo-rows">
      ${row("Time", sparklineSVG(runs, "wall_s", { fmt: fmtT, label: "Time" }), esc(last && last.wall_s !== null ? fmtT(last.wall_s) : "—"), deltaHTML(time, fmtT))}
      ${row("Cost", sparklineSVG(runs, "total_usd", { fmt: formatUsd, label: "Cost" }), esc(last && last.total_usd !== null ? formatUsd(last.total_usd) : "—"), deltaHTML(cost, formatUsd))}
      ${row("Passed", successDotsHTML(runs), `${sr.ok}/${sr.n}`, `<span class="delta">last ${sr.n}</span>`)}
    </div>` : `<p class="muted">No finished runs yet.</p>`}
    ${g.lessons.length ? `<h4>What it learned</h4><ul class="lessons">${g.lessons.slice(0, 8).map((l) => lessonHTML(l, runs)).join("")}</ul>` : `<p class="muted evo-none">No lessons yet: they are written after each run from its trace.</p>`}
    ${runs.length ? `<details class="evo-table"><summary>All ${esc(plural(runs.length, "run"))}</summary><table><thead><tr><th>#</th><th>Run</th><th>Result</th><th>Time</th><th>Cost</th><th>Lessons</th></tr></thead><tbody>
      ${runs.map((r, i) => `<tr><td>${i + 1}</td><td><a href="/?run=${encodeURIComponent(r.run_id)}" data-run="${esc(r.run_id)}">${esc(r.started ? relativeTime(r.started) : truncate(r.run_id, 18))}</a></td><td>${r.ok === null ? "—" : r.ok ? "✓ passed" : "✗ failed"}</td><td>${esc(r.wall_s !== null ? fmtT(r.wall_s) : "—")}</td><td>${esc(r.total_usd !== null ? formatUsd(r.total_usd) : "—")}</td><td>${r.lessons_used ?? "—"}</td></tr>`).join("")}
    </tbody></table></details>` : ""}
  </article>`;
}
