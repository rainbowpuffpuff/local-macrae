// node --test web/tests: the planner's card, the cost meter and its live extrapolation (web/live.js).
import test from "node:test";
import assert from "node:assert/strict";
import * as live from "../live.js";
import { eventHTML, runChipHTML } from "../render.js";
import { parseRoute, routeHref, eventMeta } from "../core.js";

const planEvent = (over = {}) => ({
  seq: 0, t: 100, step: "plan", type: "plan", title: "Decided: GPU A10G, 3 stages, budget $2.00", detail: "MD dominates.",
  plan: { plan: ["Read", "MD", "MCMC"], hardware: "gpu-a10g", params: { samples: 32, fragment: "acetate" }, budget_usd: 2, why: "MD dominates, a GPU is cheaper.", lessons_used: 3, planner_model: "claude-sonnet-5-5" },
  cost: { usd: 0.0186, tokens: { in: 3400, out: 420, cache: 0 } }, ...over,
});

const runInfo = (over = {}) => ({
  run_id: "r1", status: "running", started: 100,
  costs: {
    llm_usd: 0.25, compute_usd: 0.01, total_usd: 0.26, tokens: { in: 20000, out: 3000, cache: 150000 },
    by_step: { plan: { llm_usd: 0.02, compute_usd: 0, seconds: 1.4, model: "claude-sonnet-5-5" }, "fit[0]": { llm_usd: 0.23, compute_usd: 0.01, seconds: 40, model: "claude-opus-5-5", hardware: "gpu-a10g" } },
    wall_s: 48, phases: { plan: 2, setup: 6, work: 40, check: 0 },
  },
  ...over,
});

test("formatting: dollars, tokens, seconds, hardware", () => {
  assert.equal(live.formatUsd(0), "$0.00");
  assert.equal(live.formatUsd(0.00412), "$0.0041");
  assert.equal(live.formatUsd(0.2687), "$0.269");
  assert.equal(live.formatUsd(1.8), "$1.80");
  assert.equal(live.formatUsd(null), "—");
  assert.equal(live.formatUsd("x"), "—");
  assert.equal(live.formatTokens(950), "950");
  assert.equal(live.formatTokens(4120), "4.1k");
  assert.equal(live.formatTokens(194000), "194k");
  assert.equal(live.formatTokens(1_250_000), "1.3M");
  assert.equal(live.formatSeconds(0.4), "0.4 s");
  assert.equal(live.formatSeconds(125), "2 min 05 s");
  assert.equal(live.hardwareLabel("gpu-a10g"), "GPU A10G");
  assert.equal(live.hardwareLabel("cpu-8"), "8 CPU");
  assert.equal(live.hardwareLabel("gpu-a100-80gb"), "GPU A100-80GB");
  assert.equal(live.hardwareLabel({ gpu: "l4" }), "GPU L4");
  assert.equal(live.hardwareLabel(""), "");
});

test("the plan comes from the plan event's object, wherever the backend put it", () => {
  const p = live.planFromEvents([planEvent(), { seq: 1, type: "status", title: "x" }]);
  assert.equal(p.hw, "GPU A10G");
  assert.deepEqual(p.stages.map((s) => s.title), ["Read", "MD", "MCMC"]);
  assert.equal(p.budget, 2);
  assert.equal(p.lessonsUsed, 3);
  assert.equal(p.fallback, false);
  assert.deepEqual(p.params, [["samples", "32"], ["fragment", "acetate"]]);
  assert.equal(live.planHeadline(p), "Decided: GPU A10G, 3 stages, budget $2.00");
  // JSON in detail, `stages` with objects, `data`
  const fromDetail = live.planFromEvents([{ seq: 0, type: "plan", title: "Plan", detail: JSON.stringify({ stages: [{ name: "MD", hardware: "gpu-l4", minutes: 4 }], hardware: "gpu-l4", budget_usd: 1 }) }]);
  assert.deepEqual(fromDetail.stages, [{ title: "MD", note: "GPU L4 · ~4 min" }]);
  assert.equal(fromDetail.why, "");
  assert.equal(live.planFromEvents([{ seq: 0, type: "plan", title: "P", data: { hardware: "cpu-2", plan: ["a"] } }]).hw, "2 CPU");
  // in the run info only
  assert.equal(live.planFromEvents([], { plan: { hardware: "cpu-8", plan: ["a", "b"] } }).stages.length, 2);
  assert.equal(live.planFromEvents([{ seq: 0, type: "status", title: "x" }]), null);
});

test("a planner failure reads as 'task defaults', from the object or from the text alone", () => {
  const p = live.planFromEvents([planEvent({ plan: { source: "defaults", hardware: "cpu-2", plan: [], why: "The planner call failed." } })]);
  assert.equal(p.fallback, true);
  assert.equal(live.planHeadline(p), "Planner unavailable: task defaults (2 CPU)");
  const text = live.planFromEvents([{ seq: 0, step: "plan", type: "status", title: "Planner failed, using the task's defaults", detail: "529 overloaded" }]);
  assert.equal(text.fallback, true);
  assert.equal(text.why, "529 overloaded");
  assert.match(live.planCardHTML(text), /class="plan fallback"/);
});

test("the plan card: headline, reasons, stages, params, planner cost and lessons; escaped", () => {
  const html = live.planCardHTML(live.planFromEvents([planEvent({ plan: { ...planEvent().plan, why: "<b>x</b>" } })]));
  assert.match(html, /Decided: GPU A10G, 3 stages, budget \$2\.00/);
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.equal((html.match(/<li>/g) || []).length, 3);
  assert.match(html, /<i>samples<\/i>32/);
  assert.match(html, /planned by claude-sonnet-5-5 · planning \$0\.019 · 3 lessons from earlier runs applied/);
  assert.equal(live.planCardHTML(null), "");
});

test("run costs from GET /api/runs/{id}, or the events' own costs while that isn't there", () => {
  const c = live.runCosts(runInfo(), []);
  assert.equal(c.source, "run");
  assert.equal(c.total, 0.26);
  assert.deepEqual(c.tokens, { in: 20000, out: 3000, cache: 150000, total: 173000 });
  assert.deepEqual(c.phases.map((p) => p.key), ["plan", "setup", "work", "check"]);
  assert.equal(c.byStep[1].hardware, "gpu-a10g");
  assert.equal(live.runCosts({ costs: { llm_usd: 1, compute_usd: 0.5 } }).total, 1.5, "total from the parts");
  const ev = live.runCosts({ status: "running" }, [planEvent(), { seq: 1, cost: { usd: 0.01, tokens: { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 100 } } }]);
  assert.equal(ev.source, "events");
  assert.ok(Math.abs(ev.llm - 0.0286) < 1e-9);
  assert.equal(ev.compute, null);
  assert.equal(ev.tokens.total, 3400 + 420 + 115);
  assert.equal(live.runCosts(null, [{ seq: 1 }]), null);
  assert.deepEqual(live.normalizeTokens({ in: 1, out: 2, cache: { read: 3, write: 4 } }), { in: 1, out: 2, cache: 7, total: 10 });
  // phases as objects, and an extra phase
  assert.deepEqual(live.runCosts({ costs: { phases: { work: { seconds: 5 }, plan: 1, upload: 2 } } }).phases, [{ key: "plan", seconds: 1 }, { key: "work", seconds: 5 }, { key: "upload", seconds: 2 }]);
});

test("between polls the meter keeps counting: compute at the polled rate, LLM from new events, the current phase", () => {
  const prev = { at: 1000, costs: live.runCosts(runInfo({ costs: { ...runInfo().costs, compute_usd: 0.008 } })), lastSeq: 5 };
  const snap = { at: 1002, costs: live.runCosts(runInfo()), lastSeq: 7 };
  const events = [{ seq: 7, cost: { usd: 5 } }, { seq: 8, cost: { usd: 0.02, tokens: { in: 100, out: 10 } } }];
  const c = live.liveCosts({ snap, prev, events, now: 1004, running: true });
  assert.ok(Math.abs(c.compute - 0.012) < 1e-9, `compute ${c.compute}`); // 0.001/s for 2 s
  assert.ok(Math.abs(c.llm - 0.27) < 1e-9, "only events after the poll are added");
  assert.ok(Math.abs(c.total - (c.llm + c.compute)) < 1e-12);
  assert.equal(c.tokens.in, 20100);
  assert.equal(c.wall, 50);
  assert.equal(c.phases.find((p) => p.key === "work").seconds, 42);
  assert.equal(c.live, true);
  // a stalled backend: at most `horizon` seconds of extrapolation
  assert.equal(live.liveCosts({ snap, prev, events: [], now: 1302, running: true, horizon: 10 }).wall, 58);
  // finished: exactly the snapshot
  assert.equal(live.liveCosts({ snap, prev, events, now: 1004, running: false }).total, 0.26);
  // no snapshot yet: the events
  assert.equal(live.liveCosts({ snap: null, events: [planEvent()], now: 0 }).source, "events");
  assert.equal(live.currentPhase([{ key: "plan", seconds: 1 }, { key: "work", seconds: 0 }]), "plan");
});

test("the meter never runs backwards while live, and the budget bar has states", () => {
  const a = { llm: 0.3, compute: 0.02, total: 0.32 };
  assert.deepEqual(live.monotonic(a, { llm: 0.29, compute: 0.025, total: 0.315 }), { llm: 0.3, compute: 0.025, total: 0.32 });
  assert.equal(live.monotonic(null, a), a);
  assert.equal(live.budgetState(0.5, 2).cls, "ok");
  assert.equal(live.budgetState(1.7, 2).cls, "warn");
  assert.equal(live.budgetState(2.5, 2).cls, "over");
  assert.equal(live.budgetState(1, null), null);
});

test("the meter: total, parts, tokens, budget, phases with the current one marked; per-step table", () => {
  const c = live.runCosts(runInfo(), []);
  const html = live.costMeterHTML(c, { budget: 2, running: true });
  assert.match(html, /class="meter live"/);
  assert.match(html, /Cost so far<\/span><b>\$0\.260<\/b>/);
  assert.match(html, /<i>LLM<\/i>\$0\.250/);
  assert.match(html, /<i>compute<\/i>\$0\.010/);
  assert.match(html, /173k tokens · 20k in · 3k out · 150k cache/);
  assert.match(html, /13 % of the \$2\.00 budget/);
  assert.match(html, /class="ph ph-work now"/);
  assert.match(html, /class="pl zero"><i class="sw ph-check"><\/i>Check <b>0 s<\/b>/);
  assert.doesNotMatch(live.costMeterHTML(c, { running: false }), /now|budget/);
  const table = live.byStepHTML(c);
  assert.match(table, /<td class="mono">fit\[0\]<\/td><td>GPU A10G · claude-opus-5-5<\/td><td>40 s<\/td><td>\$0\.230<\/td><td>\$0\.010<\/td>/);
  assert.equal(live.costMeterHTML(null), "");
  // compute unknown (costs from events only)
  assert.match(live.costMeterHTML(live.runCosts(null, [planEvent()])), /<i>compute<\/i>—/);
});

test("trace rows show their duration and cost; plan rows are the headline only", () => {
  assert.equal(eventMeta("plan").icon, "🧭");
  const row = eventHTML({ seq: 3, t: 10, step: "fit[0]", type: "calc", title: "MD", elapsed_s: 38.4, cost: { usd: 0.019, tokens: { in: 900, out: 120, cache: 23500 } } }, 0);
  assert.match(row, /<span class="ev-step">fit\[0\] · 38 s · \$0\.019 · 25k tok<\/span>/);
  const plan = eventHTML(planEvent(), 100);
  assert.match(plan, /class="ev ev-plan"/);
  assert.doesNotMatch(plan, /MD dominates/);
  assert.equal(live.eventCostText({}), "");
  // elapsed_s that is only the offset from the run's start isn't shown as a duration
  assert.equal(live.eventCostText({ t: 160, elapsed_s: 60 }, 100), "");
  assert.equal(live.eventCostText({ t: 160, elapsed_s: 41 }, 100), "41 s");
  assert.equal(live.eventCostText({ t: 101, elapsed_s: 0.6 }, 100), "0.6 s");
  assert.deepEqual(live.runCosts({ costs: { phases: { plan: { start: 10, end: 12.5 } } } }).phases, [{ key: "plan", seconds: 2.5 }]);
});

test("the run chip in the thread shows the decision and the cost", () => {
  const html = runChipHTML("r1", { title: "BFF", cls: "ok", label: "Done", plan: "Decided: GPU A10G, 3 stages", cost: "$0.63" });
  assert.match(html, /<span class="rc-sub">Decided: GPU A10G, 3 stages · \$0\.63<\/span>/);
  assert.doesNotMatch(runChipHTML("r1", { title: "BFF", cls: "running", label: "Running" }), /rc-sub/);
});

test("a lessons message links to the Evolution tab", async () => {
  const { messageHTML } = await import("../render.js");
  const html = messageHTML({ id: "j9", role: "jarvis", text: "From that run I learned: **x**.", evoLink: true });
  assert.match(html, /<a class="run-chip evo-chip" href="\/\?view=evolution" data-open-tab="evolution">/);
  assert.doesNotMatch(messageHTML({ id: "j8", role: "jarvis", text: "hi" }), /evo-chip/);
});

test("routes: a run with an event to point at, and the evolution view", () => {
  assert.deepEqual(parseRoute("?run=r1&seq=14"), { view: "run", runId: "r1", seq: 14 });
  assert.deepEqual(parseRoute("?run=r1&seq=x"), { view: "run", runId: "r1" });
  assert.deepEqual(parseRoute("?view=evolution"), { view: "evolution" });
  assert.deepEqual(parseRoute("?view=other"), { view: "home" });
  assert.equal(routeHref({ view: "run", runId: "r 1", seq: 3 }), "/?run=r%201&seq=3");
  assert.equal(routeHref({ view: "run", runId: "r1", seq: "1;x" }), "/?run=r1");
  assert.equal(routeHref({ view: "evolution" }), "/?view=evolution");
});
