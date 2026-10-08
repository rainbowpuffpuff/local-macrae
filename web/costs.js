// What things cost, for people: a chat answer, a step, a run, an estimate before a task starts, the session so far,
// and the "what this cost" popover that breaks any of them down. Pure helpers and HTML strings, no DOM, so
// `node --test` can check them (web/tests/costs.test.js). The numbers come from the backend (server/costs.py,
// server/cost_api.py): an answer's `cost`, a run's `costs`, GET /api/costs/estimates and GET /api/costs/prices.
import { esc, formatDuration, plural, truncate } from "./core.js";
import { formatUsd, formatTokens, formatSeconds, hardwareLabel, normalizeTokens } from "./live.js";

const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const has = (v) => v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v));

// Small amounts stay readable: "<$0.0001" instead of "$0.0000".
export function formatCost(x) {
  if (!has(x)) return "—";
  const n = Number(x);
  if (n > 0 && n < 0.0001) return "<$0.0001";
  return formatUsd(n);
}

// "~$0.40", "$0.30–0.55"
export function formatRange(lo, hi) {
  if (!has(lo) || !has(hi)) return "";
  if (Math.abs(Number(hi) - Number(lo)) < 0.005 || formatUsd(lo) === formatUsd(hi)) return `~${formatUsd(lo)}`;
  return `${formatUsd(lo)}–${formatUsd(hi).replace("$", "")}`;
}

// Estimates are rough, so their times are too: "40 s", "6 min", "1 h 20 min".
export function roughDuration(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  if (s < 60) return `${Math.max(1, Math.round(s))} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return formatDuration(Math.round(s / 60) * 60);
}

// ---------- one chat answer ----------
// The backend's `cost` on an answer, plus the time the page waited for it → {llm, compute, total, seconds,
// serverSeconds, model, tokens}. null when the answer has no cost (an older backend).
export function answerCost(cost, clientSeconds = null) {
  if (!isObj(cost)) return null;
  const llm = num(cost.llm_usd);
  const compute = num(cost.compute_usd);
  const total = has(cost.total_usd) ? Number(cost.total_usd) : llm + compute;
  const server = has(cost.seconds) ? Number(cost.seconds) : null;
  const seconds = has(clientSeconds) ? Math.max(Number(clientSeconds), server || 0) : server;
  return { llm, compute, total, seconds, serverSeconds: server, model: String(cost.model || ""), tokens: normalizeTokens(cost.tokens) };
}

// "$0.0012 · 0.8 s" under an answer
export function answerCostText(c) {
  if (!c) return "";
  return [formatCost(c.total), has(c.seconds) ? formatSeconds(c.seconds) : ""].filter(Boolean).join(" · ");
}

export function costChipHTML(ref, text, label = "What this cost") {
  if (!text) return "";
  return `<button type="button" class="cost-chip" data-cost="${esc(ref)}" aria-haspopup="dialog" aria-expanded="false" title="${esc(label)}">${esc(text)}</button>`;
}

// ---------- steps ----------
// A step's share of the run's `costs.by_step`. A fan-out parent ("card") sums its children ("card[0]", "card[1]"…).
export function stepCost(key, costs) {
  const by = costs && isObj(costs.by_step) ? costs.by_step : null;
  if (!by || !key) return null;
  const rows = by[key] ? [by[key]] : Object.entries(by).filter(([k]) => k.startsWith(`${key}[`)).map(([, v]) => v);
  if (!rows.length) return null;
  const out = { llm: 0, compute: 0, seconds: 0, model: "", hardware: "", tokens: { in: 0, out: 0, cache: 0, total: 0 } };
  for (const s of rows) {
    out.llm += num(s.llm_usd);
    out.compute += num(s.compute_usd);
    out.seconds = Math.max(out.seconds, num(s.seconds)); // children run side by side
    out.model ||= String(s.model || "");
    out.hardware ||= String(s.hardware || "");
    const t = normalizeTokens(s.tokens);
    for (const k of Object.keys(out.tokens)) out.tokens[k] += t[k];
  }
  out.total = out.llm + out.compute;
  return out;
}

// "$0.12 · 4 min 05 s" on a step chip
export function stepCostText(step, costs) {
  const c = stepCost(step && step.key, costs);
  if (!c || (!c.total && !c.seconds)) return "";
  return [c.total ? formatCost(c.total) : "", c.seconds ? formatSeconds(c.seconds) : ""].filter(Boolean).join(" · ");
}

// ---------- estimates (GET /api/costs/estimates) ----------
// "≈ $0.55 · ~6 min · 4 past runs", or before any run: "$0.38/h on 2 CPU cores · no runs yet"
export function estimateText(est) {
  if (!isObj(est)) return "";
  if (est.basis !== "past_runs" || !has(est.usd)) {
    return has(est.usd_per_hour) ? `no runs yet · ${formatUsd(est.usd_per_hour)}/h of ${hardwareLabel(est.hardware) || "compute"}` : "";
  }
  const bits = [`≈ ${formatUsd(est.usd)}`];
  if (has(est.seconds) && Number(est.seconds) > 0) bits.push(`~${roughDuration(est.seconds)}`);
  bits.push(plural(num(est.n), "past run"));
  return bits.join(" · ");
}

// One sentence for the thread when a run starts: "Past runs took about 6 min and cost $0.45–0.62."
export function estimateSentence(est) {
  if (!isObj(est) || est.basis !== "past_runs" || !has(est.usd)) return "";
  const cost = formatRange(est.usd_low ?? est.usd, est.usd_high ?? est.usd) || formatUsd(est.usd);
  const time = has(est.seconds) && Number(est.seconds) > 0 ? ` took about ${roughDuration(est.seconds)} and` : "";
  return `${num(est.n) === 1 ? "The last run" : `The last ${num(est.n)} runs`}${time} cost ${cost.replace(/^~/, "about ")}.`;
}

// ---------- the session (this browser tab) ----------
// Ledger entries: {kind: "answer"|"run"|"voice", id, llm, compute, total, seconds, title?, model?, final?, at}.
export function ledgerPut(ledger, entry) {
  const list = Array.isArray(ledger) ? ledger.slice() : [];
  const i = list.findIndex((e) => e.kind === entry.kind && e.id === entry.id);
  if (i >= 0) list[i] = { ...list[i], ...entry };
  else list.push(entry);
  return list.slice(-300);
}

export function sessionTotals(ledger, { voiceUsdPerMin = null, now = Date.now() / 1000 } = {}) {
  const t = { llm: 0, compute: 0, voice: 0, total: 0, answers: 0, runs: 0, running: 0, voiceSeconds: 0, seconds: 0 };
  for (const e of ledger || []) {
    if (e.kind === "voice") {
      const s = num(e.seconds) + (e.live && has(e.since) ? Math.max(0, now - Number(e.since)) : 0);
      t.voiceSeconds += s;
      continue;
    }
    t.llm += num(e.llm);
    t.compute += num(e.compute);
    t.seconds += num(e.seconds);
    if (e.kind === "answer") t.answers++;
    if (e.kind === "run") {
      t.runs++;
      if (!e.final) t.running++;
    }
  }
  if (has(voiceUsdPerMin)) t.voice = (t.voiceSeconds / 60) * Number(voiceUsdPerMin);
  t.total = t.llm + t.compute + t.voice;
  return t;
}

// The header pill: "$0.42" (with a dot while a run is still adding to it)
export function sessionPillText(t) {
  return formatCost(t ? t.total : 0);
}

// ---------- the popover ----------
function row(label, value, note = "", cls = "") {
  return `<tr${cls ? ` class="${cls}"` : ""}><th scope="row">${esc(label)}</th><td>${esc(value)}</td><td class="cp-note">${esc(note)}</td></tr>`;
}

function modelPrice(prices, model) {
  const list = prices && prices.llm && Array.isArray(prices.llm.models) ? prices.llm.models : [];
  return list.find((m) => m.id === model) || null;
}

// "Opus 5.5: $4 in · $20 out · $0.20 cache read per M tokens"
export function modelPriceText(prices, model) {
  const p = modelPrice(prices, model);
  if (!p) return "";
  const name = model.replace(/^claude-/, "").replace(/-(\d+)-(\d+)$/, " $1.$2").replace(/-(\d+)$/, " $1");
  return `${name[0].toUpperCase()}${name.slice(1)}: ${formatUsd(p.input)} in · ${formatUsd(p.output)} out · ${formatUsd(p.cache_read)} cache read per M tokens`;
}

function tokensNote(t) {
  const n = normalizeTokens(t);
  return n.total ? `${formatTokens(n.in)} in · ${formatTokens(n.out)} out · ${formatTokens(n.cache)} cache` : "";
}

function sourcesHTML(prices) {
  if (!prices || !isObj(prices.sources)) return `<p class="cp-src">Prices: list prices in server/costs.py.</p>`;
  const links = Object.values(prices.sources)
    .filter((s) => s && /^https:\/\//.test(String(s.url || "")))
    .map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label || s.url)}</a>`);
  return `<p class="cp-src">List prices as of ${esc(prices.as_of || "?")}: ${links.join(" · ")}</p>`;
}

function shell(title, sub, rows, foot = "") {
  return `<div class="cp-head"><b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ""}<button type="button" class="cp-close" data-cost-close aria-label="Close">×</button></div>
    <table class="cp-table"><tbody>${rows.join("")}</tbody></table>${foot}`;
}

// An answer: what the LLM did (if anything) and what the backend's time cost.
export function answerBreakdownHTML(c, prices = null) {
  if (!c) return shell("What this answer cost", "", [row("Cost", "not reported by this backend")], sourcesHTML(prices));
  const backend = prices && prices.backend ? prices.backend : null;
  const rows = [
    c.llm > 0 || c.model
      ? row("LLM", formatCost(c.llm), [c.model, tokensNote(c.tokens)].filter(Boolean).join(" · "))
      : row("LLM", "$0", "no model call: typed questions search the paper index directly"),
    row("Compute", formatCost(c.compute), `backend ${has(c.serverSeconds) ? formatSeconds(c.serverSeconds) : ""}${backend ? ` × ${formatUsd(backend.usd_per_hour)}/h (${backend.instance && backend.instance.name ? backend.instance.name : "container"})` : ""}`),
    row("Time", has(c.seconds) ? formatSeconds(c.seconds) : "—", "from asking to the answer on screen"),
    row("Total", formatCost(c.total), "", "cp-total"),
  ];
  const mp = c.model ? modelPriceText(prices, c.model) : "";
  return shell("What this answer cost", "", rows, `${mp ? `<p class="cp-src">${esc(mp)}</p>` : ""}${sourcesHTML(prices)}`);
}

// A run (its `costs` object from GET /api/runs/{id}): LLM by model, compute by hardware, time, the biggest steps.
export function runBreakdownHTML(costs, { title = "", running = false, prices = null } = {}) {
  if (!isObj(costs)) return shell("What this run cost", title, [row("Cost", running ? "not known yet" : "not reported")], sourcesHTML(prices));
  const steps = isObj(costs.by_step) ? Object.entries(costs.by_step) : [];
  const models = [...new Set(steps.map(([, s]) => s && s.model).filter(Boolean))];
  const rate = has(costs.usd_per_hour) ? `${formatUsd(costs.usd_per_hour)}/h` : "";
  const sandboxSecs = steps.filter(([, s]) => s && s.hardware && s.hardware !== "local").reduce((a, [, s]) => a + num(s.seconds), 0);
  const rows = [
    row("LLM", formatCost(costs.llm_usd), [models.join(", "), tokensNote(costs.tokens)].filter(Boolean).join(" · ")),
    row("Compute", formatCost(costs.compute_usd), sandboxSecs ? `${formatSeconds(sandboxSecs)} on ${costs.hardware_label || hardwareLabel(costs.hardware)}${rate ? ` × ${rate}` : ""}` : "no Modal sandbox time"),
    row("Time", has(costs.wall_s) ? formatSeconds(costs.wall_s) : "—", running ? "so far" : "start to finish, planning included"),
    row(running ? "So far" : "Total", formatCost(costs.total_usd), costs.estimated ? "LLM partly estimated from tokens" : "", "cp-total"),
  ];
  const top = steps
    .map(([k, s]) => ({ k, usd: num(s && s.llm_usd) + num(s && s.compute_usd), secs: num(s && s.seconds) }))
    .filter((s) => s.usd > 0 || s.secs > 0)
    .sort((a, b) => b.usd - a.usd)
    .slice(0, 5);
  const stepsHTML = top.length
    ? `<p class="cp-sub">By step</p><table class="cp-table cp-steps"><tbody>${top.map((s) => row(s.k, formatCost(s.usd), formatSeconds(s.secs))).join("")}</tbody></table>`
    : "";
  const mp = models.map((m) => modelPriceText(prices, m)).filter(Boolean);
  return shell(running ? "What this run has cost so far" : "What this run cost", title, rows,
    `${stepsHTML}${mp.map((t) => `<p class="cp-src">${esc(t)}</p>`).join("")}${has(costs.usd_per_hour) ? `<p class="cp-src">${esc(costs.hardware_label || hardwareLabel(costs.hardware))}: ${esc(formatUsd(costs.usd_per_hour))} per sandbox hour (Modal sandbox CPU + memory${costs.hardware && String(costs.hardware).startsWith("gpu") ? " + GPU" : ""})</p>` : ""}${sourcesHTML(prices)}`);
}

// One step (from stepCost)
export function stepBreakdownHTML(key, c, { prices = null } = {}) {
  if (!c) return shell("What this step cost", key, [row("Cost", "not known yet")], sourcesHTML(prices));
  const rows = [
    row("LLM", formatCost(c.llm), [c.model, tokensNote(c.tokens)].filter(Boolean).join(" · ")),
    row("Compute", formatCost(c.compute), c.hardware && c.hardware !== "local" ? `${formatSeconds(c.seconds)} on ${hardwareLabel(c.hardware)}` : "ran on the backend: no sandbox time"),
    row("Time", formatSeconds(c.seconds)),
    row("Total", formatCost(c.total), "", "cp-total"),
  ];
  const mp = c.model ? modelPriceText(prices, c.model) : "";
  return shell("What this step cost", key, rows, `${mp ? `<p class="cp-src">${esc(mp)}</p>` : ""}${sourcesHTML(prices)}`);
}

// A task's estimate, before it starts
export function estimateBreakdownHTML(est, { title = "", prices = null } = {}) {
  if (!isObj(est)) return shell("What it will likely cost", title, [row("Estimate", "not available")], sourcesHTML(prices));
  if (est.basis !== "past_runs" || !has(est.usd)) {
    const rows = [row("Past runs", "none yet", "the first run sets the estimate")];
    if (has(est.usd_per_hour)) rows.push(row("Compute", `${formatUsd(est.usd_per_hour)}/h`, `${hardwareLabel(est.hardware)} sandbox, plus the agent's tokens`));
    if (has(est.budget_usd)) rows.push(row("Budget", formatUsd(est.budget_usd), "the task's default"));
    return shell("What it will likely cost", title, rows, sourcesHTML(prices));
  }
  const rows = [
    row("Cost", `≈ ${formatUsd(est.usd)}`, formatRange(est.usd_low, est.usd_high) ? `usually ${formatRange(est.usd_low, est.usd_high)}` : ""),
    row("LLM", `≈ ${formatUsd(est.llm_usd)}`, "median"),
    row("Compute", `≈ ${formatUsd(est.compute_usd)}`, est.hardware ? `mostly on ${hardwareLabel(est.hardware)}${has(est.usd_per_hour) ? ` (${formatUsd(est.usd_per_hour)}/h)` : ""}` : ""),
    row("Time", has(est.seconds) ? `~${roughDuration(est.seconds)}` : "—", has(est.seconds_low) && has(est.seconds_high) && est.seconds_high > est.seconds_low ? `${roughDuration(est.seconds_low)} to ${roughDuration(est.seconds_high)}` : ""),
  ];
  if (has(est.success_rate)) rows.push(row("Succeeded", `${Math.round(Number(est.success_rate) * 100)} %`, "of the recent runs"));
  const runs = Array.isArray(est.runs) ? est.runs.slice(0, 5) : [];
  const basis = `<p class="cp-src">From the last ${esc(plural(num(est.n), num(est.n_ok) ? "successful run" : "finished run"))}${runs.length ? `: ${runs.map((r) => `<a href="/?run=${encodeURIComponent(r)}" data-run="${esc(r)}">${esc(truncate(r, 32))}</a>`).join(", ")}` : ""}. The planner may pick other hardware, so the real cost can differ.</p>`;
  return shell("What it will likely cost", title, rows, `${basis}${sourcesHTML(prices)}`);
}

// The session: answers, runs and voice since this tab opened.
export function sessionBreakdownHTML(ledger, { prices = null, now = Date.now() / 1000 } = {}) {
  const vpm = prices && prices.voice ? prices.voice.usd_per_min : null;
  const t = sessionTotals(ledger, { voiceUsdPerMin: vpm, now });
  const answers = (ledger || []).filter((e) => e.kind === "answer");
  const runs = (ledger || []).filter((e) => e.kind === "run");
  const rows = [
    row("Answers", formatCost(answers.reduce((a, e) => a + num(e.llm) + num(e.compute), 0)), answers.length ? `${plural(answers.length, "typed answer")}, ${formatSeconds(answers.reduce((a, e) => a + num(e.seconds), 0))} in all` : "none yet"),
    row("Runs", formatCost(runs.reduce((a, e) => a + num(e.llm) + num(e.compute), 0)), runs.length ? `${plural(runs.length, "run")}${t.running ? `, ${t.running} still going` : ""}` : "none yet"),
  ];
  if (t.voiceSeconds > 0) rows.push(row("Voice", has(vpm) ? `≈ ${formatCost(t.voice)}` : "—", `${formatSeconds(t.voiceSeconds)} of conversation${has(vpm) ? ` × ${formatUsd(vpm)}/min` : ""}`));
  rows.push(row("LLM", formatCost(t.llm), "Claude tokens (runs, planner, answers)"));
  rows.push(row("Compute", formatCost(t.compute), "Modal sandboxes and the backend"));
  rows.push(row("Total", formatCost(t.total), t.running ? "still counting" : "", "cp-total"));
  const list = runs.slice(-6).reverse();
  const runsHTML = list.length
    ? `<p class="cp-sub">Runs this session</p><table class="cp-table cp-steps"><tbody>${list.map((e) => `<tr><th scope="row" class="cp-run"><a href="/?run=${encodeURIComponent(e.id)}" data-run="${esc(e.id)}">${esc(truncate(e.title || e.id, 34))}</a></th><td>${esc(formatCost(num(e.llm) + num(e.compute)))}</td><td class="cp-note">${esc([has(e.seconds) && e.seconds > 0 ? formatSeconds(e.seconds) : "", e.final ? "" : "running"].filter(Boolean).join(" · "))}</td></tr>`).join("")}</tbody></table>`
    : "";
  return shell("What this session cost", "since this tab opened", rows, `${runsHTML}${sourcesHTML(prices)}`);
}
