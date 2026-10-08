// node --test web/tests: web/costs.js (what an answer, a step, a run, a task and the session cost) and the hooks
// that put it on the page.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  formatCost, formatRange, roughDuration, answerCost, answerCostText, costChipHTML, stepCost, stepCostText,
  estimateText, estimateSentence, ledgerPut, sessionTotals, sessionPillText, modelPriceText, answerBreakdownHTML,
  runBreakdownHTML, stepBreakdownHTML, estimateBreakdownHTML, sessionBreakdownHTML,
} from "../costs.js";
import { messageHTML, stepHTML } from "../render.js";

const read = (f) => fs.readFileSync(new URL(`../${f}`, import.meta.url), "utf8");

const PRICES = {
  as_of: "2026-10-08",
  sources: {
    anthropic: { label: "Claude API list prices", url: "https://platform.claude.com/docs/en/about-claude/pricing" },
    modal: { label: "Modal sandbox and GPU prices", url: "https://modal.com/pricing" },
    evil: { label: "x", url: "javascript:alert(1)" },
  },
  llm: { models: [{ id: "claude-opus-5-5", input: 4, output: 20, cache_read: 0.2 }, { id: "claude-sonnet-5-5", input: 2, output: 10, cache_read: 0.1 }] },
  backend: { usd_per_hour: 0.074, instance: { name: "standard-1" } },
  voice: { usd_per_min: 0.08 },
};

const RUN_COSTS = {
  llm_usd: 0.41, compute_usd: 0.06, total_usd: 0.47, tokens: { in: 12000, out: 3400, cache_read: 90000, cache_write: 8000 },
  wall_s: 322, hardware: "gpu-a10g", hardware_label: "GPU A10G", usd_per_hour: 1.64, estimated: false,
  phases: { plan: 4, setup: 40, work: 260, check: 18 },
  by_step: {
    plan: { llm_usd: 0.004, compute_usd: 0, seconds: 4, model: "claude-sonnet-5-5", hardware: "" },
    prepare: { llm_usd: 0, compute_usd: 0, seconds: 1.2, model: "", hardware: "" },
    "card[0]": { llm_usd: 0.2, compute_usd: 0.03, seconds: 120, model: "claude-opus-5-5", hardware: "gpu-a10g", tokens: { in: 5000, out: 1000, cache: 40000 } },
    "card[1]": { llm_usd: 0.206, compute_usd: 0.03, seconds: 150, model: "claude-opus-5-5", hardware: "gpu-a10g", tokens: { in: 7000, out: 2400, cache: 58000 } },
  },
};

test("money and time read well at every size", () => {
  assert.equal(formatCost(0.00002), "<$0.0001");
  assert.equal(formatCost(0), "$0.00");
  assert.equal(formatCost(0.0123), "$0.012");
  assert.equal(formatCost(null), "—");
  assert.equal(formatRange(0.3, 0.55), "$0.300–0.550");
  assert.equal(formatRange(0.5, 0.5), "~$0.500");
  assert.equal(roughDuration(42), "42 s");
  assert.equal(roughDuration(390), "7 min");
  assert.equal(roughDuration(4900), "1 h 22 min");
});

test("a typed answer: the backend's cost plus the time the page waited", () => {
  const c = answerCost({ llm_usd: 0, compute_usd: 0.000016, total_usd: 0.000016, seconds: 0.8, model: "" }, 1.1);
  assert.equal(c.seconds, 1.1);
  assert.equal(c.serverSeconds, 0.8);
  assert.equal(answerCostText(c), "<$0.0001 · 1.1 s");
  assert.equal(answerCost(undefined, 1), null, "an older backend sends no cost");
  const llm = answerCost({ llm_usd: 0.012, compute_usd: 0.0001, seconds: 3.2, model: "claude-sonnet-5-5", tokens: { in: 3000, out: 400 } });
  assert.equal(llm.total, 0.0121);
  assert.match(answerBreakdownHTML(llm, PRICES), /claude-sonnet-5-5/);
  assert.match(answerBreakdownHTML(llm, PRICES), /Sonnet 5\.5: \$2\.00 in · \$10\.00 out · \$0\.100 cache read/);
  const html = answerBreakdownHTML(c, PRICES);
  assert.match(html, /no model call/);
  assert.match(html, /standard-1/);
  assert.match(html, /List prices as of 2026-10-08/);
  assert.match(html, /href="https:\/\/modal.com\/pricing"/);
  assert.doesNotMatch(html, /javascript:/, "only https source links");
});

test("the answer chip shows under the message and opens its breakdown", () => {
  const chip = costChipHTML("answer:jarvis:1", "$0.0012 · 0.8 s");
  assert.match(chip, /data-cost="answer:jarvis:1"/);
  assert.match(chip, /aria-haspopup="dialog"/);
  const m = { id: "jarvis:1", role: "jarvis", text: "Ions pair [1].", cost: { total: 0.0012, seconds: 0.8 } };
  const html = messageHTML(m, { costHTML: (x) => costChipHTML(`answer:${x.id}`, answerCostText(x.cost)) });
  assert.match(html, /class="cost-chip" data-cost="answer:jarvis:1"/);
  assert.doesNotMatch(messageHTML(m), /cost-chip/, "no chip without the hook");
  assert.doesNotMatch(messageHTML({ ...m, cost: undefined }, { costHTML: () => "X" }), /X<\/div>/);
});

test("a step's cost and time, a fan-out parent sums its children", () => {
  const one = stepCost("card[0]", RUN_COSTS);
  assert.equal(one.total, 0.23);
  const parent = stepCost("card", RUN_COSTS);
  assert.ok(Math.abs(parent.total - 0.466) < 1e-9);
  assert.equal(parent.seconds, 150, "children run side by side: the longest");
  assert.equal(parent.tokens.in, 12000);
  assert.equal(stepCost("nope", RUN_COSTS), null);
  assert.equal(stepCostText({ key: "card[1]" }, RUN_COSTS), "$0.236 · 2 min 30 s");
  assert.equal(stepCostText({ key: "prepare" }, RUN_COSTS), "1.2 s");
  assert.equal(stepCostText({ key: "x" }, null), "");
  const chip = stepHTML({ key: "card[1]", kind: "agent", status: "ok" }, { ref: "step:r1|card[1]", text: "$0.236 · 2 min 30 s" });
  assert.match(chip, /data-cost="step:r1\|card\[1\]" role="button" tabindex="0"/);
  assert.match(chip, /<span class="step-cost">\$0\.236 · 2 min 30 s<\/span>/);
  assert.doesNotMatch(stepHTML({ key: "a", kind: "run", status: "ok" }, 3), /data-cost/, "Array.map's index is not a cost");
  const bd = stepBreakdownHTML("card", parent, { prices: PRICES });
  assert.match(bd, /GPU A10G/);
  assert.match(bd, /Opus 5\.5: \$4\.00 in/);
  assert.match(stepBreakdownHTML("prepare", stepCost("prepare", RUN_COSTS)), /ran on the backend/);
});

test("a run's breakdown: LLM by model, compute by hardware, time, the biggest steps", () => {
  const html = runBreakdownHTML(RUN_COSTS, { title: "Ion–water binding", prices: PRICES });
  assert.match(html, /What this run cost/);
  assert.match(html, /claude-sonnet-5-5, claude-opus-5-5/);
  assert.match(html, /4 min 30 s on GPU A10G × \$1\.64\/h/);
  assert.match(html, /\$0\.470/);
  assert.ok(html.indexOf("card[1]") < html.indexOf("card[0]"), "most expensive step first");
  assert.match(html, /GPU A10G: \$1\.64 per sandbox hour \(Modal sandbox CPU \+ memory \+ GPU\)/);
  assert.match(runBreakdownHTML(RUN_COSTS, { running: true }), /has cost so far/);
  assert.match(runBreakdownHTML(null, { running: true }), /not known yet/);
});

test("estimates before a task starts", () => {
  const est = { basis: "past_runs", n: 4, n_ok: 4, usd: 0.55, usd_low: 0.475, usd_high: 0.625, llm_usd: 0.5, compute_usd: 0.05,
    seconds: 390, seconds_low: 330, seconds_high: 450, success_rate: 0.8, hardware: "cpu-2", usd_per_hour: 0.38, runs: ["r-3", "r-2"] };
  assert.equal(estimateText(est), "≈ $0.550 · ~7 min · 4 past runs");
  assert.equal(estimateSentence(est), "The last 4 runs took about 7 min and cost $0.475–0.625.");
  assert.equal(estimateSentence({ ...est, n: 1, usd_low: 0.55, usd_high: 0.55 }), "The last run took about 7 min and cost about $0.550.");
  const none = { basis: "none", n: 0, usd: null, usd_per_hour: 0.38, hardware: "cpu-2" };
  assert.equal(estimateText(none), "no runs yet · $0.380/h of 2 CPU");
  assert.equal(estimateSentence(none), "");
  assert.equal(estimateText(null), "");
  const html = estimateBreakdownHTML(est, { title: "Small calc", prices: PRICES });
  assert.match(html, /usually \$0\.475–0\.625/);
  assert.match(html, /6 min to 8 min/);
  assert.match(html, /80 %/);
  assert.match(html, /<a href="\/\?run=r-3" data-run="r-3">/);
  assert.match(estimateBreakdownHTML(none), /none yet/);
});

test("the session: answers, runs (going and done) and voice minutes", () => {
  let l = [];
  l = ledgerPut(l, { kind: "answer", id: "a1", llm: 0, compute: 0.00002, seconds: 0.9 });
  l = ledgerPut(l, { kind: "run", id: "r1", title: "Methods card", llm: 0.3, compute: 0.02, seconds: 200, final: true });
  l = ledgerPut(l, { kind: "run", id: "r2", title: "Small calc", llm: 0.1, compute: 0.01, seconds: 60, final: false });
  l = ledgerPut(l, { kind: "run", id: "r2", llm: 0.2 }); // updated in place
  l = ledgerPut(l, { kind: "voice", id: "v1", seconds: 90, live: true, since: 1000 });
  assert.equal(l.length, 4);
  const t = sessionTotals(l, { voiceUsdPerMin: 0.08, now: 1030 });
  assert.equal(t.answers, 1);
  assert.equal(t.runs, 2);
  assert.equal(t.running, 1);
  assert.equal(t.voiceSeconds, 120);
  assert.ok(Math.abs(t.voice - 0.16) < 1e-9);
  assert.ok(Math.abs(t.total - (0.00002 + 0.3 + 0.02 + 0.2 + 0.01 + 0.16)) < 1e-9);
  assert.equal(sessionPillText(t), "$0.690");
  assert.equal(sessionPillText(sessionTotals([])), "$0.00");
  const html = sessionBreakdownHTML(l, { prices: PRICES, now: 1030 });
  assert.match(html, /1 typed answer/);
  assert.match(html, /2 runs, 1 still going/);
  assert.match(html, /2 min 00 s of conversation × \$0\.080\/min/);
  assert.match(html, /data-run="r2"/);
  assert.ok(html.indexOf('data-run="r2"') < html.indexOf('data-run="r1"'), "newest run first");
  assert.match(sessionBreakdownHTML([]), /none yet/);
});

test("everything from the network is escaped", () => {
  const evil = "<img src=x onerror=alert(1)>";
  const html = [
    runBreakdownHTML({ ...RUN_COSTS, hardware_label: evil, by_step: { [evil]: { llm_usd: 1, seconds: 1, model: evil } } }, { title: evil }),
    estimateBreakdownHTML({ basis: "past_runs", n: 1, usd: 1, hardware: evil, runs: [evil] }, { title: evil }),
    sessionBreakdownHTML([{ kind: "run", id: evil, title: evil, llm: 1 }]),
    answerBreakdownHTML({ llm: 1, compute: 0, total: 1, model: evil, tokens: {} }),
    costChipHTML(evil, evil),
  ].join("");
  assert.doesNotMatch(html, /<img/);
  assert.equal(modelPriceText(PRICES, "claude-opus-5-5"), "Opus 5.5: $4.00 in · $20.00 out · $0.200 cache read per M tokens");
});

test("the page wires the hooks: header pill, popover, stylesheet, meter link", () => {
  const html = read("index.html");
  const app = read("app.js");
  const ui = read("cost_ui.js");
  assert.match(html, /id="session-cost"[^>]*data-cost="session"/);
  assert.match(html, /id="cost-pop" role="dialog"/);
  assert.match(html, /id="cost-why"[^>]*data-cost="run:"/);
  assert.match(html, /<link rel="stylesheet" href="costs.css">/);
  for (const hook of ["costUI.noteAnswer(", "costUI.trackRun(", "costUI.noteRun(", "costUI.voice(", "costUI.startNote(", "costHTML: costUI.answerChip", "costUI.stepNote(", "safe(costUI.start)"]) {
    assert.ok(app.includes(hook), hook);
  }
  // the cost UI only calls these routes (server/cost_api.py, and the contract's run route)
  const calls = new Set([...ui.matchAll(/\bapi\(\s*[`"]([^`"]+)[`"]/g)].map((m) => m[1].replace(/\$\{[^}]+\}/g, "{}")));
  assert.deepEqual([...calls].sort(), ["/api/costs/estimates", "/api/costs/prices", "/api/runs/{}"]);
  for (const id of [...ui.matchAll(/\$\("#([\w-]+)"/g)].map((m) => m[1])) assert.match(html, new RegExp(`id="${id}"`), `#${id}`);
});

test("the mock backend answers the cost routes the page uses", async () => {
  const { createMock } = await import("../../cloudflare/dev/mock-backend.mjs");
  const mock = createMock({ now: () => 1_800_000_000, speed: 1 });
  const call = async (url, method = "GET", body = undefined) => {
    let status;
    let out;
    const req = { url, method, headers: { "x-macrae-secret": "dev-secret" }, on: (ev, fn) => (ev === "data" ? body && fn(JSON.stringify(body)) : fn()) };
    await mock.handle(req, { writeHead: (s) => (status = s), end: (s) => (out = JSON.parse(s)) });
    return { status, body: out };
  };
  const prices = (await call("/api/costs/prices")).body;
  assert.equal(prices.as_of, "2026-10-08");
  assert.match(prices.sources.anthropic.url, /^https:\/\/platform\.claude\.com\//);
  const est = (await call("/api/costs/estimates")).body;
  const e = est.estimates["small-calc"];
  assert.equal(e.basis, "past_runs");
  assert.ok(e.usd_low <= e.usd && e.usd <= e.usd_high);
  assert.match(estimateText(e), /^≈ \$/);
  assert.ok(Object.keys(est.runs).length >= 3);
  assert.deepEqual((await call("/api/tasks/small-calc/estimate")).body, e);
  const s = (await call("/api/search", "POST", { query: "ion pairing water", k: 3 })).body;
  assert.ok(s.passages.length > 0);
  assert.equal(s.cost.llm_usd, 0);
  assert.ok(answerCost(s.cost, 1).compute > 0);
});
