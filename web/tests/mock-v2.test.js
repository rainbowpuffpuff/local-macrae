// node --test web/tests: the mock backend (cloudflare/dev/mock-backend.mjs) speaks the v2 addendum the page relies on,
// so demos and screenshots without Modal show the same things a real run does. A fake clock drives it.
import test from "node:test";
import assert from "node:assert/strict";
import { createMock } from "../../cloudflare/dev/mock-backend.mjs";
import { planFromEvents, planHeadline, runCosts } from "../live.js";
import { normalizeEvolution } from "../evolution.js";

function harness(opts = {}) {
  let now = 1_800_000_000;
  const mock = createMock({ now: () => now, speed: 1, ...opts });
  const get = async (path) => {
    let status;
    let body;
    await mock.handle({ url: path, method: "GET", headers: { "x-macrae-secret": "dev-secret" } }, { writeHead: (s) => (status = s), end: (s) => (body = JSON.parse(s)) });
    return { status, body };
  };
  return { mock, get, tick: (s) => (now += s) };
}

test("the plan is the first event, with its decision and its token cost", async () => {
  const h = harness();
  const id = h.mock.start("bff-charges", { fragment: "acetate", samples: "32" });
  h.tick(1.5);
  const { body } = await h.get(`/api/runs/${id}/events`);
  assert.equal(body.events[0].type, "plan");
  assert.equal(body.events[0].seq, 0);
  assert.ok(body.events[0].cost.usd > 0);
  const plan = planFromEvents(body.events);
  assert.equal(planHeadline(plan), "Decided: GPU A10G, 7 stages, budget $2.00");
  assert.ok(plan.lessonsUsed > 0, "seeded lessons are applied");
});

test("events stream while the agent step runs, and costs and phases grow", async () => {
  const h = harness();
  const id = h.mock.start("bff-charges", {});
  h.tick(25);
  const mid = (await h.get(`/api/runs/${id}`)).body;
  const evs = (await h.get(`/api/runs/${id}/events`)).body;
  assert.equal(mid.status, "running");
  assert.equal(evs.done, false);
  assert.ok(evs.events.some((e) => e.step === "fit[0]" && e.type === "calc" && e.elapsed_s > 0), "tool calls before the step ends");
  assert.equal(mid.steps.find((s) => s.key === "fit[0]").status, "running");
  h.tick(30);
  const later = (await h.get(`/api/runs/${id}`)).body;
  for (const k of ["llm_usd", "compute_usd", "total_usd", "wall_s"]) assert.ok(later.costs[k] > mid.costs[k], k);
  assert.ok(later.costs.phases.work > mid.costs.phases.work);
  assert.equal(later.costs.by_step["fit[0]"].hardware, "gpu-a10g");
  const c = runCosts(later, []);
  assert.ok(Math.abs(c.total - (c.llm + c.compute)) < 1e-4);
  h.tick(60);
  const done = (await h.get(`/api/runs/${id}`)).body;
  assert.equal(done.status, "ok");
  assert.ok(Math.abs(Object.values(done.costs.phases).reduce((a, b) => a + b, 0) - done.costs.wall_s) < 0.5, "phases add up to the wall time");
});

test("a failing planner says so in the plan event", async () => {
  const h = harness({ planner: "fail", history: false });
  const id = h.mock.start("small-calc", { ion: "K+" });
  h.tick(1);
  const plan = planFromEvents((await h.get(`/api/runs/${id}/events`)).body.events);
  assert.equal(plan.fallback, true);
});

test("/api/evolution: per-task metrics, lessons whose evidence opens a real event, and a new row after a run", async () => {
  const h = harness();
  const before = (await h.get("/api/evolution")).body;
  const groups = normalizeEvolution(before, h.mock.tasks);
  assert.deepEqual(groups.map((g) => g.taskId), ["methods-card", "small-calc", "bff-charges"]);
  for (const g of groups) {
    assert.ok(g.runs.length >= 4 && g.lessons.length >= 3, g.taskId);
    for (const l of g.lessons) {
      for (const ev of l.evidence) {
        const res = await h.get(`/api/runs/${ev.runId}/events`);
        assert.equal(res.status, 200, ev.runId);
        assert.ok(res.body.events.some((e) => e.seq === ev.seq && e.step === ev.step), `${ev.runId}/${ev.step}/${ev.seq}`);
      }
    }
  }
  const bff = groups.find((g) => g.taskId === "bff-charges");
  assert.ok(bff.runs[0].wall_s > bff.runs[bff.runs.length - 1].wall_s, "later runs are faster");
  const id = h.mock.start("small-calc", {});
  h.tick(45);
  const after = normalizeEvolution((await h.get("/api/evolution")).body, h.mock.tasks).find((g) => g.taskId === "small-calc");
  const row = after.runs.find((r) => r.run_id === id);
  assert.ok(row && row.ok && row.total_usd > 0 && row.lessons_used >= 1);
  assert.ok(after.lessons.some((l) => l.evidence.some((e) => e.runId === id)), "a lesson distilled from the new run");
  // the history doesn't crowd the recent runs out: the new run is first
  assert.equal((await h.get("/api/runs")).body.runs[0].run_id, id);
});

test("the v1 shapes stay: every event has the contract's keys; seq has no gaps", async () => {
  const h = harness({ history: false });
  const id = h.mock.start("methods-card", {});
  h.tick(40);
  const { body } = await h.get(`/api/runs/${id}/events`);
  assert.equal(body.done, true);
  body.events.forEach((e, i) => {
    assert.equal(e.seq, i);
    for (const k of ["seq", "t", "step", "type", "title", "detail", "citation"]) assert.ok(k in e, k);
  });
  assert.equal((await h.get("/api/evolution")).status, 200);
});

test("v3: capabilities and the dawn report are the live site's by default, an illustrative finished one on request", async () => {
  const { normalizeCapabilities, capabilitiesHTML, dawnReportHTML } = await import("../frankenstein.js");
  const h = harness();
  const caps = (await h.get("/api/capabilities")).body;
  assert.ok(caps.ledger.length >= 1 && caps.authority.rules.length >= 1);
  assert.match(capabilitiesHTML(normalizeCapabilities(caps)), /Authority: fixed/);
  const live = (await h.get("/api/dawn-report")).body;
  assert.equal(live.ready, false);
  assert.match(dawnReportHTML(live), /No comparison yet/);
  const ready = (await harness({ dawn: "ready" }).get("/api/dawn-report")).body;
  assert.equal(ready.ready, true);
  assert.equal(ready.dawn.tasks.length, 2);
  assert.match(dawnReportHTML(ready), /Installed between dusk and dawn/);
});

test("v3: a finished small-calc run has the demo manuscript and its figure; a running one has none", async () => {
  const h = harness();
  const id = h.mock.start("small-calc", { ion: "Na+" });
  h.tick(2);
  assert.equal((await h.get(`/api/runs/${id}/manuscript`)).body.content, null);
  h.tick(10_000);
  const done = (await h.get(`/api/runs/${id}/manuscript`)).body;
  assert.equal(done.path, "results/manuscript.md");
  assert.match(done.content, /^# How strongly does Na⁺ bind/);
});
