// The page's whole path on one machine: browser-style requests → worker.js (on Node) → mock backend.
import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { listen } from "../dev/mock-backend.mjs";
import { serve } from "../dev/serve.mjs";

let backend, site, base;
before(async () => {
  backend = await listen({ port: 0, secret: "e2e-secret", speed: 40 });
  site = await serve({ port: 0, env: { BACKEND_URL: `http://127.0.0.1:${backend.port}`, MACRAE_TOOL_SECRET: "e2e-secret" } });
  base = `http://127.0.0.1:${site.port}`;
});
after(() => {
  backend.server.close();
  site.server.close();
});

const get = (p, init) => fetch(base + p, init);
const post = (p, body, headers = {}) => fetch(base + p, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });

test("the page and its scripts are served with security headers", async () => {
  const res = await get("/");
  assert.equal(res.status, 200);
  const html = await res.text();
  assert.match(html, /<script type="module" src="app.js">/);
  assert.match(res.headers.get("content-security-policy"), /script-src 'self'/);
  for (const f of ["/app.js", "/core.js", "/voice.js", "/theme.js", "/style.css"]) {
    const r = await get(f);
    assert.equal(r.status, 200, f);
    assert.doesNotMatch(r.headers.get("content-type"), /html/, f);
  }
});

test("dev files are not served", async () => {
  for (const f of ["/package.json", "/tests/core.test.js", "/NOTES.md", "/smoke.md"]) {
    const html = await (await get(f)).text();
    assert.match(html, /<!DOCTYPE html>/, `${f} should fall back to the page`);
  }
});

test("a run start-to-finish through the worker, as the page polls it", async () => {
  const health = await (await get("/api/health")).json();
  assert.equal(health.ok, true);
  const { tasks } = await (await get("/api/tasks")).json();
  assert.ok(tasks.length >= 2);
  for (const t of tasks) for (const k of ["id", "title", "subtitle", "icon", "prompt", "flow", "inputs"]) assert.ok(k in t, `task.${k}`);

  const started = await post(`/api/tasks/${tasks[1].id}/start`, { inputs: { ion: "K+" } });
  assert.equal(started.status, 200);
  const { run_id } = await started.json();
  assert.ok(run_id);

  let after = null;
  const seen = [];
  let done = false;
  for (let i = 0; i < 60 && !done; i++) {
    const r = await (await get(`/api/runs/${run_id}/events${after === null ? "" : `?after=${after}`}`)).json();
    for (const e of r.events) {
      for (const k of ["seq", "t", "step", "type", "title", "detail", "citation"]) assert.ok(k in e, `event.${k}`);
      seen.push(e);
    }
    if (r.events.length) after = r.events[r.events.length - 1].seq;
    done = r.done;
    if (!done) await new Promise((ok) => setTimeout(ok, 100));
  }
  assert.ok(done, "run finished");
  assert.deepEqual(seen.map((e) => e.seq), [...seen.keys()], "each event exactly once, in order");
  const types = new Set(seen.map((e) => e.type));
  for (const t of ["read", "search", "calc", "write", "result"]) assert.ok(types.has(t), t);

  const run = await (await get(`/api/runs/${run_id}`)).json();
  assert.equal(run.status, "ok");
  assert.ok(run.steps.length);
  const { runs } = await (await get("/api/runs")).json();
  assert.equal(runs[0].run_id, run_id);
});

test("search returns passages with citations", async () => {
  const { passages } = await (await post("/api/search", { query: "water ions interface", k: 3 })).json();
  assert.ok(passages.length > 0 && passages.length <= 3);
  assert.ok(passages[0].citation.doi);
});

test("the voice agent's tools are closed to the browser but open with the secret", async () => {
  assert.equal((await post("/api/tools/run_status", { run_id: "x" })).status, 403);
  const res = await post("/api/tools/search_papers", { query: "calcium" }, { "X-Macrae-Secret": "e2e-secret" });
  assert.equal(res.status, 200);
  assert.ok("answer_context" in (await res.json()));
});

test("unknown runs are a clean 404", async () => {
  const res = await get("/api/runs/does-not-exist/events");
  assert.equal(res.status, 404);
});

test("voice without ElevenLabs config says so", async () => {
  const res = await get("/voice/signed-url");
  assert.equal(res.status, 503);
  assert.match((await res.json()).error, /not configured/);
});

test("backend gone → the page gets 'agent offline'", async () => {
  const dead = await serve({ port: 0, env: { BACKEND_URL: "http://127.0.0.1:9", MACRAE_TOOL_SECRET: "x" } });
  try {
    const res = await fetch(`http://127.0.0.1:${dead.port}/api/health`);
    assert.equal(res.status, 502);
    assert.equal((await res.json()).offline, true);
  } finally {
    dead.server.close();
  }
});
