// node --test web/tests: capabilities, their ledger, the fixed authority and dusk → dawn (web/frankenstein.js).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as fr from "../frankenstein.js";

// The live site's GET /api/capabilities before the final deploy (one gap → build → test → rejected story).
const LIVE = JSON.parse(readFileSync(new URL("../../proof/live-before-final-deploy/capabilities.json", import.meta.url), "utf8"));
const NOW = 1791491778.72 + 600;

test("normalizeCapabilities: the live payload, oldest event first, counts and the policy's own rules", () => {
  const c = fr.normalizeCapabilities(LIVE);
  assert.deepEqual(c.ledger.map((e) => e.event), ["gap", "gap", "create", "test", "rejected"]);
  assert.equal(c.caps.length, 0);
  assert.equal(c.counts.rejected, 1);
  assert.equal(c.counts.install, 0);
  assert.ok(c.rules.length >= 5 && c.rules.every((r) => typeof r === "string"));
  assert.equal(c.policySource, "server/policy.py");
  assert.equal(c.forges[0].status, "failed");
  const test_ = c.ledger.find((e) => e.event === "test");
  assert.equal(test_.passed, false);
  assert.equal(test_.nTests, 3);
});

test("normalizeCapabilities: junk and missing fields don't throw", () => {
  const c = fr.normalizeCapabilities({ ledger: [{ event: "nope" }, null, "x", { event: "use", name: "t" }], capabilities: [{}, { name: "tool-a" }], authority: null });
  assert.deepEqual(c.ledger.map((e) => e.event), ["use"]);
  assert.deepEqual(c.caps.map((x) => x.name), ["tool-a"]);
  assert.deepEqual(c.rules, []);
  assert.equal(c.counts.use, 1);
  assert.deepEqual(fr.normalizeCapabilities(null).ledger, []);
});

test("eventDetail says why, what and how the tests went", () => {
  const c = fr.normalizeCapabilities(LIVE);
  const by = (k) => c.ledger.find((e) => e.event === k);
  assert.match(fr.eventDetail(by("gap")), /installing packages/);
  assert.match(fr.eventDetail(by("create")), /pinned numpy/);
  assert.match(fr.eventDetail(by("test")), /^failed in a fresh sandbox · 2 of 3 tests passed · setup 1 min 53 s$/);
  assert.match(fr.eventDetail(by("rejected")), /did not pass in a fresh sandbox \(reward 0\.0\); failed in the fresh sandbox: static/);
});

test("capabilitiesHTML: counts, empty install list, the ledger with run links, the authority card", () => {
  const html = fr.capabilitiesHTML(fr.normalizeCapabilities(LIVE), { now: NOW });
  assert.match(html, /rejected <b>1<\/b>/);
  assert.match(html, /Nothing installed yet/);
  assert.match(html, /Missing capability<\/b> <span class="cap-name">small-calc-env/);
  assert.match(html, /Rejected<\/b>/);
  assert.match(html, /data-run="20261008-202740-forge-2742"/);
  assert.match(html, /href="\/\?run=20261008-192909-small-calc-3935"/);
  assert.match(html, /Authority: fixed/);
  assert.match(html, /Capabilities run only inside the Harbor sandbox/);
  assert.match(html, /<code>server\/policy\.py<\/code>/);
});

test("capabilitiesHTML: installed capabilities, a forge in progress, and everything escaped", () => {
  const html = fr.capabilitiesHTML(fr.normalizeCapabilities({
    capabilities: [{ name: "calc-image", kind: "image", version: "2", purpose: "Pinned <PySCF>", uses: 3, last_used: NOW - 120,
      sha256: "a83ba9b3cd6c6168", tests: { n: 3, passed: true }, created_by: { run_id: "r-forge", step: "create" } }],
    forges: [{ name: "x<y", run_id: "r-new", status: "running" }],
    ledger: [{ t: NOW - 60, event: "install", name: "calc-image", run_id: "r-forge", step: "install", version: "2", sha256: "a83ba9b3cd" }],
    authority: { rules: ["No <script>."] },
  }), { now: NOW });
  assert.match(html, /calc-image<\/span><span class="cap-kind">environment<\/span><span class="cap-ver">v2/);
  assert.match(html, /used 3 times, last 2 min ago/);
  assert.match(html, /3 tests passed/);
  assert.match(html, /sha256 a83ba9b3/);
  assert.match(html, /Building <span class="cap-name">x&lt;y<\/span> now/);
  assert.match(html, /Pinned &lt;PySCF&gt;/);
  assert.match(html, /No &lt;script&gt;\./);
  assert.doesNotMatch(html, /<script>|<PySCF>/);
});

const side = (status, tasks, totals) => ({ status, tasks, totals });
const row = (label, run_id, o) => ({ label, task_id: "small-calc", run_id, ok: true, errors: 0, capabilities_used: [], ...o });

test("dawnReportHTML: not ready says so plainly and shows where each side is", () => {
  let html = fr.dawnReportHTML({ dusk: null, dawn: null, ready: false });
  assert.match(html, /Dusk \(nothing learned\): not run/);
  assert.match(html, /Dawn \(everything learned\): not run/);
  assert.match(html, /No comparison yet/);
  html = fr.dawnReportHTML({ dusk: side("running", [], { n: 2, done: 1 }), dawn: null, ready: false });
  assert.match(html, /running, 1 of 2 runs finished/);
  assert.doesNotMatch(html, /<table/);
  assert.match(fr.dawnReportHTML(null), /No comparison yet/);
});

test("dawnReportHTML: ready shows totals, per-task dusk vs dawn with run links, and what was installed between", () => {
  const dusk = side("done", [row("small-calc Na+", "r-dusk-na", { wall_s: 354.8, setup_s: 41, llm_usd: 0.27, compute_usd: 0.006, errors: 2 })],
    { n: 1, done: 1, ok: 1, wall_s: 354.8, setup_s: 41, llm_usd: 0.27, compute_usd: 0.006, errors: 2 });
  const dawn = side("done", [row("small-calc Na+", "r-dawn-na", { wall_s: 200, setup_s: 3, llm_usd: 0.2, compute_usd: 0.004, errors: 0, capabilities_used: ["calc-image"] })],
    { n: 1, done: 1, ok: 1, wall_s: 200, setup_s: 3, llm_usd: 0.2, compute_usd: 0.004, errors: 0 });
  const html = fr.dawnReportHTML({
    dusk, dawn, ready: true,
    delta: { wall_s: { dusk: 354.8, dawn: 200, pct: -43.6, better: true }, errors: { dusk: 2, dawn: 0, pct: -100, better: true },
      llm_usd: { dusk: 0.27, dawn: 0.2, pct: -25.9, better: true }, by_task: { "small-calc Na+": { wall_s: { pct: -43.6, better: true }, setup_s: { pct: 12, better: false } } } },
    capabilities_installed_between: [{ name: "calc-image", kind: "image", purpose: "pinned env", why: "41 s installing", gap_run: "r-dusk-na", forge_run: "r-forge" }],
  });
  assert.match(html, /done, 1 run/);
  assert.match(html, /<th scope="row">Wall time<\/th><td>5 min 55 s<\/td><td>3 min 20 s<\/td><td><span class="delta good">better -44 %/);
  assert.match(html, /<th scope="row">Passed<\/th><td>1\/1<\/td><td>1\/1<\/td>/);
  assert.match(html, /data-run="r-dusk-na"/);
  assert.match(html, /data-run="r-dawn-na"/);
  assert.match(html, /delta bad">worse \+12 %/);
  assert.match(html, /Used at dawn: <span class="cap-name">calc-image/);
  assert.match(html, /Installed between dusk and dawn/);
  assert.match(html, /Why: 41 s installing/);
  assert.match(html, /data-run="r-forge"/);
});

test("dawnReportHTML: ready with nothing installed says the difference is the lessons alone", () => {
  const s = side("done", [row("a", "r1")], { n: 1, done: 1, ok: 1 });
  assert.match(fr.dawnReportHTML({ dusk: s, dawn: s, ready: true, delta: {}, capabilities_installed_between: [] }), /lessons alone/);
});
