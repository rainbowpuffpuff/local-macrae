// The backend container: env forwarding, /api routing to the Durable Object, admin routes, the R2 endpoint the
// container syncs through, and the MacraeBackend class (with a fake @cloudflare/containers).
import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { register } from "node:module";
import worker, { containerEnv, countRunning, dataHandler, validKey, DATA_HOST, BACKEND_INSTANCE } from "../worker.js";
import { folderBucket } from "../dev/data-server.mjs";

register("./fakes/loader.mjs", import.meta.url);
const { MacraeBackend, ContainerProxy } = await import("../index.js");

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

// A Durable Object namespace with one stub that records requests and answers with `reply`.
function fakeNamespace(reply = () => Response.json({ ok: true })) {
  const ns = { names: [], requests: [], restarts: 0 };
  const stub = {
    fetch: async (req) => {
      ns.requests.push({ url: req.url, method: req.method, headers: req.headers, body: req.body ? await req.text() : "" });
      return reply(req);
    },
    restart: async () => (ns.restarts++, { ok: true, was_running: true }),
    getState: async () => ({ status: "healthy", lastChange: 1 }),
  };
  ns.idFromName = (name) => (ns.names.push(name), `id:${name}`);
  ns.get = (id) => (assert.equal(id, `id:${BACKEND_INSTANCE}`), stub);
  return ns;
}

const SECRET = "s3cret";
const base = (extra = {}) => ({ MACRAE_TOOL_SECRET: SECRET, ASSETS: { fetch: async () => new Response("page") }, ...extra });
const call = (p, init = {}, env) => worker.fetch(new Request(`https://macrae.example.workers.dev${p}`, init), env);

test("containerEnv forwards the backend's secrets and every AGENT_RUNNER_TOKEN_*, nothing else", () => {
  const env = {
    MACRAE_TOOL_SECRET: "t", MODAL_TOKEN_ID: "ak", MODAL_TOKEN_SECRET: "as", ANTHROPIC_API_KEY: "sk",
    AGENT_RUNNER_TOKEN_MAIN: "a", AGENT_RUNNER_TOKEN_CAROL_2: "b", AGENT_RUNNER_TOKEN_: "no name", AGENT_RUNNER_TOKEN_bad: "lower",
    AGENT_RUNNER_TOKEN_EMPTY: "  ", MODAL_PROFILE: "p", ELEVENLABS_API_KEY: "xi", BACKEND_URL: "http://x",
    DATA: {}, BACKEND: {}, ASSETS: {},
  };
  assert.deepEqual(containerEnv(env), {
    MACRAE_SYNC_URL: `http://${DATA_HOST}`, MACRAE_TOOL_SECRET: "t", MODAL_TOKEN_ID: "ak", MODAL_TOKEN_SECRET: "as",
    ANTHROPIC_API_KEY: "sk", AGENT_RUNNER_TOKEN_MAIN: "a", AGENT_RUNNER_TOKEN_CAROL_2: "b", MODAL_PROFILE: "p",
  });
  assert.deepEqual(containerEnv({}), { MACRAE_SYNC_URL: "http://r2.macrae" });
});

test("/api goes to the container when BACKEND_URL is not set: path, query, secret, body", async () => {
  const ns = fakeNamespace(() => Response.json({ events: [], done: false }));
  const res = await call("/api/runs/r1/events?after=3", { headers: { "X-Macrae-Secret": "client-guess", cookie: "a=b" } }, base({ BACKEND: ns }));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { events: [], done: false });
  assert.deepEqual(ns.names, [BACKEND_INSTANCE]);
  const r = ns.requests[0];
  assert.equal(r.url, "http://backend/api/runs/r1/events?after=3");
  assert.equal(r.headers.get("x-macrae-secret"), SECRET);
  assert.equal(r.headers.get("cookie"), null);
  assert.match(res.headers.get("content-security-policy"), /default-src 'self'/);

  await call("/api/search", { method: "POST", body: '{"query":"ions"}', headers: { "content-type": "application/json" } }, base({ BACKEND: ns }));
  assert.equal(ns.requests[1].method, "POST");
  assert.equal(ns.requests[1].body, '{"query":"ions"}');
});

test("BACKEND_URL overrides the container (local dev)", async () => {
  const ns = fakeNamespace();
  const urls = [];
  globalThis.fetch = async (url) => (urls.push(String(url)), Response.json({ ok: true }));
  const res = await call("/api/health", {}, base({ BACKEND: ns, BACKEND_URL: "http://127.0.0.1:8080" }));
  assert.equal(res.status, 200);
  assert.deepEqual(urls, ["http://127.0.0.1:8080/api/health"]);
  assert.equal(ns.requests.length, 0);
  const bad = await call("/api/health", {}, base({ BACKEND: ns, BACKEND_URL: "ftp://x" }));
  assert.equal(bad.status, 503);
  assert.match((await bad.json()).detail, /not an http/);
});

test("container not ready (Container class plain-text errors) → 503 agent offline; backend JSON errors pass through", async () => {
  let reply = () => new Response("Failed to start container: no instance", { status: 500 });
  const ns = fakeNamespace(() => reply());
  let res = await call("/api/health", {}, base({ BACKEND: ns }));
  assert.equal(res.status, 503);
  let body = await res.json();
  assert.equal(body.offline, true);
  assert.match(body.detail, /Failed to start container/);

  reply = () => Response.json({ detail: "internal error: KeyError" }, { status: 500 });
  res = await call("/api/runs", {}, base({ BACKEND: ns }));
  assert.equal(res.status, 500);
  assert.equal((await res.json()).detail, "internal error: KeyError");

  reply = () => { throw new Error("Network connection lost."); };
  res = await call("/api/runs", {}, base({ BACKEND: ns }));
  assert.equal(res.status, 502);
  assert.equal((await res.json()).offline, true);
});

test("no backend at all → 503 agent offline", async () => {
  const res = await call("/api/health", {}, base());
  assert.equal(res.status, 503);
  assert.match((await res.json()).detail, /no backend/);
});

test("/admin needs the secret; restart and status go to the container", async () => {
  const ns = fakeNamespace();
  assert.equal((await call("/admin/restart", { method: "POST" }, base({ BACKEND: ns }))).status, 403);
  assert.equal((await call("/admin/restart", { method: "POST", headers: { "x-macrae-secret": "nope" } }, base({ BACKEND: ns }))).status, 403);
  const ok = await call("/admin/restart", { method: "POST", headers: { "x-macrae-secret": SECRET } }, base({ BACKEND: ns }));
  assert.equal(ok.status, 200);
  assert.equal(ns.restarts, 1);
  const st = await call("/admin/status", { headers: { "x-macrae-secret": SECRET } }, base({ BACKEND: ns }));
  assert.equal((await st.json()).status, "healthy");
  assert.equal((await call("/admin/nope", { headers: { "x-macrae-secret": SECRET } }, base({ BACKEND: ns }))).status, 404);
  assert.equal((await call("/admin/status", { headers: { "x-macrae-secret": SECRET } }, base())).status, 404);
});

test("countRunning", () => {
  assert.equal(countRunning({ runs: [{ status: "running" }, { status: "ok" }, { status: "running" }, null] }), 2);
  assert.equal(countRunning({}), 0);
  assert.equal(countRunning(null), 0);
});

// ---------- the R2 endpoint ----------
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "macrae-r2-test-"));
const r2 = (p, init = {}, env) => dataHandler(new Request(`http://${DATA_HOST}${p}`, init), env);

test("data endpoint: put, get (with mtime), list with prefix and pagination", async () => {
  const env = { DATA: folderBucket(tmp()) };
  const put = await r2("/state/runs/r%201/state.json", { method: "PUT", body: '{"status":"ok"}', headers: { "content-length": "15", "x-macrae-mtime": "1760000000.5" } }, env);
  assert.equal(put.status, 200);
  const got = await r2("/state/runs/r%201/state.json", {}, env);
  assert.equal(got.status, 200);
  assert.equal(await got.text(), '{"status":"ok"}');
  assert.equal(Number(got.headers.get("x-macrae-mtime")), 1760000000.5);

  const list = await (await r2("/?prefix=state/runs/", {}, env)).json();
  assert.deepEqual(list, { objects: [{ key: "state/runs/r 1/state.json", size: 15, mtime: 1760000000.5 }], cursor: null });
  assert.equal((await r2("/state/runs/missing", {}, env)).status, 404);

  for (let i = 0; i < 1005; i++) await env.DATA.put(`state/jobs/f${String(i).padStart(4, "0")}`, "x");
  const p1 = await (await r2("/?prefix=state/jobs/", {}, env)).json();
  assert.equal(p1.objects.length, 1000);
  const p2 = await (await r2(`/?prefix=state/jobs/&cursor=${p1.cursor}`, {}, env)).json();
  assert.equal(p2.objects.length, 5);
  assert.equal(p2.cursor, null);
});

test("data endpoint refuses bad keys, index writes, missing length, other methods", async () => {
  const env = { DATA: folderBucket(tmp()) };
  const put = (p, headers = { "content-length": "1" }) => r2(p, { method: "PUT", body: "x", headers }, env);
  assert.equal((await put("/index/manifest.json")).status, 400); // the index is uploaded by deploy.sh only
  assert.equal((await put("/state/../secrets")).status, 400);
  assert.equal((await put("/state/a%2F..%2F..%2Fb")).status, 400); // ".." only visible after decoding
  assert.equal((await put("/state/a%5Cb")).status, 400); // backslash
  assert.equal((await put("/other/x")).status, 400);
  assert.equal((await put("/state/x", {})).status, 411);
  assert.equal((await put("/state/x", { "content-length": String(200 * 1024 * 1024) })).status, 413);
  assert.equal((await r2("/?prefix=", {}, env)).status, 400);
  assert.equal((await r2("/state/x", { method: "DELETE" }, env)).status, 405);
  assert.equal((await r2("/state/x", {}, { })).status, 503);
  assert.ok(validKey("index/emb-1.npy") && !validKey("index/emb-1.npy", { write: true }) && validKey("state/a/b c", { write: true }));
});

// ---------- MacraeBackend ----------
function backend({ running = true, runs = [], fail = false } = {}) {
  const calls = [];
  const ctx = {
    container: { running },
    containerFetch: async (url, init) => {
      calls.push({ url, secret: new Headers(init && init.headers).get("x-macrae-secret") });
      if (fail) throw new Error("container hung");
      return Response.json({ runs });
    },
  };
  const env = { MACRAE_TOOL_SECRET: SECRET, MODAL_TOKEN_ID: "ak", AGENT_RUNNER_TOKEN_X: "t", ELEVENLABS_API_KEY: "xi" };
  return { be: new MacraeBackend(ctx, env), calls, ctx };
}

test("MacraeBackend: port 8080, 2 h sleep, env from Worker secrets, R2 host wired, ContainerProxy exported", () => {
  const { be } = backend();
  assert.equal(be.defaultPort, 8080);
  assert.equal(be.sleepAfter, "2h");
  assert.equal(be.enableInternet, true);
  assert.deepEqual(be.envVars, { MACRAE_SYNC_URL: "http://r2.macrae", MACRAE_TOOL_SECRET: SECRET, MODAL_TOKEN_ID: "ak", AGENT_RUNNER_TOKEN_X: "t" });
  assert.equal(typeof MacraeBackend.outboundByHost[DATA_HOST], "function");
  assert.equal(typeof ContainerProxy, "function");
});

test("MacraeBackend sleeps when idle, stays up while task runs are going", async () => {
  let b = backend({ runs: [{ status: "ok" }] });
  await b.be.onActivityExpired();
  assert.equal(b.be.stops, 1);
  assert.equal(b.calls[0].url, "http://backend/api/runs");
  assert.equal(b.calls[0].secret, SECRET);

  b = backend({ runs: [{ status: "running" }, { status: "failed" }] });
  await b.be.onActivityExpired();
  assert.equal(b.be.stops, 0, "a running flow keeps the container up");

  b = backend({ fail: true });
  await b.be.onActivityExpired();
  assert.equal(b.be.stops, 1, "an unresponsive backend is stopped");

  b = backend({ running: false });
  await b.be.onActivityExpired();
  assert.equal(b.be.stops, 0);
  assert.equal(b.calls.length, 0);
});

test("MacraeBackend.restart stops a running container, no-op otherwise", async () => {
  let b = backend();
  assert.deepEqual((await b.be.restart()).was_running, true);
  assert.equal(b.be.stops, 1);
  b = backend({ running: false });
  assert.equal((await b.be.restart()).was_running, false);
  assert.equal(b.be.stops, 0);
});

test("R2 handler registered on the class answers like dataHandler", async () => {
  const env = { DATA: folderBucket(tmp()) };
  await env.DATA.put("index/manifest.json", '{"papers":3}');
  const res = await MacraeBackend.outboundByHost[DATA_HOST](new Request(`http://${DATA_HOST}/index/manifest.json`), env, {});
  assert.equal(await res.text(), '{"papers":3}');
});

test("MacraeBackend.fetch starts a cold container with a longer budget, then proxies", async () => {
  const b = backend({ running: false });
  let res = await b.be.fetch(new Request("http://backend/api/health"));
  assert.equal(res.status, 200);
  assert.deepEqual(b.be.startArgs, { ports: 8080, cancellationOptions: { instanceGetTimeoutMS: 30_000, portReadyTimeoutMS: 60_000 } });
  assert.equal(b.calls[0].url, "http://backend/api/health");

  const warm = backend();
  warm.ctx.container.healthy = true;
  await warm.be.fetch(new Request("http://backend/api/tasks"));
  assert.equal(warm.be.startArgs, undefined, "a healthy container is not started again");

  const broken = backend({ running: false });
  broken.ctx.failStart = "no instance available";
  res = await broken.be.fetch(new Request("http://backend/api/health"));
  assert.equal(res.status, 503);
  assert.match(await res.text(), /Failed to start container: no instance available/);
  assert.equal(broken.calls.length, 0);
});
