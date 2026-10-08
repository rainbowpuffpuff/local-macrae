// node --test web/tests: the Evolution panel (web/evolution.js).
import test from "node:test";
import assert from "node:assert/strict";
import * as evo from "../evolution.js";

const tasks = [{ id: "small-calc", title: "Ion–water", icon: "atom" }, { id: "bff-charges", title: "BFF", icon: "molecule" }];
const data = {
  by_task: {
    "bff-charges": [
      { run_id: "20261005-100000-bff-charges-c", ok: true, reward: 1, wall_s: 600, total_usd: 1.0, lessons_used: 2 },
      { run_id: "20261003-100000-bff-charges-a", ok: false, reward: 0, wall_s: 700, total_usd: 0.9, lessons_used: 0 },
      { run_id: "20261004-100000-bff-charges-b", ok: true, reward: 1, wall_s: 900, total_usd: 1.4, lessons_used: 1 },
    ],
    "small-calc": [{ run_id: "20261001-100000-small-calc-x", reward: 1, wall_s: 300, total_usd: 0.3 }],
  },
  lessons: [
    { task_id: "bff-charges", kind: "setting", lesson: "Use the A10G for MD.", evidence: ["20261003-100000-bff-charges-a/fit[0]/22"], created: 10 },
    { task_id: "bff-charges", kind: "weird", lesson: "Newer <lesson>", evidence: "20261004-100000-bff-charges-b/check", created: 20 },
    { task_id: "small-calc", kind: "tool", lesson: "Reuse run_calc.py", evidence: [{ run_id: "20261001-100000-small-calc-x", step: "calc[0]", seq: 9 }], tool: "run_calc.py" },
    { nope: true },
  ],
};

test("parseEvidence: run/step/seq strings and objects", () => {
  assert.deepEqual(evo.parseEvidence("r1/fit[0]/22"), { runId: "r1", step: "fit[0]", seq: 22 });
  assert.deepEqual(evo.parseEvidence("r1/check"), { runId: "r1", step: "check", seq: null });
  assert.deepEqual(evo.parseEvidence("r1"), { runId: "r1", step: "", seq: null });
  assert.deepEqual(evo.parseEvidence({ run_id: "r1", step: "s", seq: "4" }), { runId: "r1", step: "s", seq: 4 });
  assert.equal(evo.parseEvidence("<x>/a/1"), null);
  assert.equal(evo.parseEvidence(""), null);
});

test("runIdTime reads agent_runner and mock run ids", () => {
  assert.equal(evo.runIdTime("20261008-181714-small-calc-ab12"), Date.UTC(2026, 9, 8, 18, 17, 14) / 1000);
  assert.equal(evo.runIdTime("20261008181714-methods-card-1"), Date.UTC(2026, 9, 8, 18, 17, 14) / 1000);
  assert.equal(evo.runIdTime("latest"), null);
});

test("normalizeEvolution: tasks in page order, runs oldest first, lessons newest first, ok from reward", () => {
  const g = evo.normalizeEvolution(data, tasks);
  assert.deepEqual(g.map((x) => x.taskId), ["small-calc", "bff-charges"]);
  const bff = g[1];
  assert.equal(bff.title, "BFF");
  assert.deepEqual(bff.runs.map((r) => r.run_id.slice(-1)), ["a", "b", "c"]);
  assert.deepEqual(bff.lessons.map((l) => l.kind), ["do", "setting"], "unknown kinds read as 'do'");
  assert.deepEqual(bff.lessons[1].evidence, [{ runId: "20261003-100000-bff-charges-a", step: "fit[0]", seq: 22 }]);
  assert.equal(g[0].runs[0].ok, true, "ok from reward ≥ 1");
  assert.equal(g[0].lessons[0].tool, "run_calc.py");
  // lessons keyed by task, and by_task entries as {runs, lessons}
  const keyed = evo.normalizeEvolution({ by_task: { t: { runs: [{ run_id: "r", ok: true }], lessons: ["plain text"] } }, lessons: { u: [{ lesson: "x" }] } });
  assert.deepEqual(keyed.map((x) => [x.taskId, x.runs.length, x.lessons.length]), [["t", 1, 1], ["u", 0, 1]]);
  assert.deepEqual(evo.normalizeEvolution(null), []);
  assert.deepEqual(evo.normalizeEvolution({}), []);
});

test("improvement and success rate count passing runs from first to latest", () => {
  const runs = evo.normalizeEvolution(data, tasks)[1].runs;
  const t = evo.improvement(runs, "wall_s");
  assert.deepEqual([t.first, t.last, Math.round(t.pct)], [900, 600, -33]);
  assert.equal(evo.improvement(runs.slice(0, 2), "wall_s"), null, "one passing run is no trend");
  assert.deepEqual(evo.successRate(runs), { ok: 2, n: 3 });
});

test("sparkline: a point per run, latest in the accent, failed hollow, a link and tooltip per point", () => {
  const runs = evo.normalizeEvolution(data, tasks)[1].runs;
  const svg = evo.sparklineSVG(runs, "total_usd", { fmt: (v) => `$${v}`, label: "Cost" });
  assert.match(svg, /^<svg class="spark" viewBox="0 0 200 36"/);
  assert.match(svg, /aria-label="Cost, run over run: \$0\.9, \$1\.4, \$1"/);
  assert.equal((svg.match(/class="sp-pt/g) || []).length, 3);
  assert.match(svg, /class="sp-pt fail"[^>]*><title>Run 1 \(20261003-100000-bff-charges-a\): \$0\.9 · failed<\/title>/);
  assert.match(svg, /class="sp-pt last"/);
  assert.match(svg, /<polyline points="[\d.]+,[\d.]+ [\d.]+,[\d.]+ [\d.]+,[\d.]+"\/>/);
  assert.match(evo.sparklineSVG([], "x"), /class="spark empty"/);
  // all equal values: a flat line in the middle, no NaN
  assert.doesNotMatch(evo.sparklineSVG([{ run_id: "a", wall_s: 5 }, { run_id: "b", wall_s: 5 }], "wall_s"), /NaN/);
});

test("a task's card: three rows, deltas, lessons with evidence links to the event, a run table", () => {
  const g = evo.normalizeEvolution(data, tasks)[1];
  const html = evo.evolutionTaskHTML(g, { icon: "<svg/>" });
  assert.match(html, /<b>BFF<\/b><span>3 runs · 2 lessons · last run used 2 lessons<\/span>/);
  assert.match(html, /class="delta good"[^>]*>▼ 33 %/);
  assert.match(html, /class="okdot fail"[^>]*>✗<\/a><a class="okdot ok"/);
  assert.match(html, /2\/3/);
  assert.match(html, /<a class="ev-link" href="\/\?run=20261003-100000-bff-charges-a&amp;seq=22" data-run="20261003-100000-bff-charges-a" data-seq="22">run 1 · fit\[0\] · #22 →<\/a>/);
  assert.match(html, /Newer &lt;lesson&gt;/);
  assert.match(html, /All 3 runs/);
  assert.match(evo.evolutionTaskHTML({ taskId: "x", title: "X", runs: [], lessons: [] }), /No finished runs yet[\s\S]*No lessons yet/);
  assert.equal(evo.evidenceHref({ runId: "a b", seq: null }), "/?run=a+b");
});
