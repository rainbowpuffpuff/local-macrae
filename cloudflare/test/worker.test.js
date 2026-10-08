// node --test cloudflare/test   (Node ≥ 18: global fetch/Request/Response, no dependencies)
import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import worker, { backendBase, sameSecret } from "../worker.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

const ENV = {
  BACKEND_URL: "https://backend.example.com/",
  MACRAE_TOOL_SECRET: "s3cret",
  ELEVENLABS_API_KEY: "xi-key",
  ELEVENLABS_AGENT_ID: "agent_123",
  ASSETS: { fetch: async (req) => new Response(`asset ${new URL(req.url).pathname}`, { headers: { "content-type": "text/html" } }) },
};

// Records what the worker sends upstream and answers with `reply`.
function stubFetch(reply = () => Response.json({ ok: true })) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const call = { url: String(url), init, headers: new Headers(init.headers) };
    if (init.body) call.body = new TextDecoder().decode(init.body);
    calls.push(call);
    return reply(call);
  };
  return calls;
}

const call = (path, init = {}, env = ENV) => worker.fetch(new Request(`https://macrae.example.workers.dev${path}`, init), env);

test("assets get the page with security headers", async () => {
  const res = await call("/");
  assert.equal(await res.text(), "asset /");
  const csp = res.headers.get("content-security-policy");
  assert.match(csp, /default-src 'self'/);
  assert.match(csp, /script-src 'self' https:\/\/cdn\.jsdelivr\.net/);
  assert.match(csp, /wss:\/\/\*\.elevenlabs\.io/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.equal(res.headers.get("x-content-type-options"), "nosniff");
  assert.equal(res.headers.get("referrer-policy"), "no-referrer");
  assert.match(res.headers.get("permissions-policy"), /microphone=\(self\)/);
});

test("/api is proxied with the secret, path and query kept, client secret ignored", async () => {
  const calls = stubFetch(() => Response.json({ events: [], done: false }));
  const res = await call("/api/runs/r1/events?after=12", { headers: { "X-Macrae-Secret": "forged", cookie: "a=b", "cf-connecting-ip": "1.2.3.4" } });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { events: [], done: false });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://backend.example.com/api/runs/r1/events?after=12");
  assert.equal(calls[0].headers.get("x-macrae-secret"), "s3cret");
  assert.equal(calls[0].headers.get("cookie"), null);
  assert.equal(calls[0].headers.get("x-forwarded-for"), "1.2.3.4");
  assert.equal(res.headers.get("cache-control"), "no-store");
  assert.ok(res.headers.get("content-security-policy"));
});

test("POST bodies pass through", async () => {
  const calls = stubFetch(() => Response.json({ run_id: "r9" }));
  const res = await call("/api/tasks/methods-card/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ inputs: { doi: "10.1/x" } }) });
  assert.deepEqual(await res.json(), { run_id: "r9" });
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].headers.get("content-type"), "application/json");
  assert.deepEqual(JSON.parse(calls[0].body), { inputs: { doi: "10.1/x" } });
});

test("upstream status codes pass through", async () => {
  stubFetch(() => Response.json({ detail: "no such run" }, { status: 404 }));
  const res = await call("/api/runs/nope");
  assert.equal(res.status, 404);
  assert.deepEqual(await res.json(), { detail: "no such run" });
});

test("backend down → 502 agent offline", async () => {
  stubFetch(() => { throw new TypeError("fetch failed"); });
  const res = await call("/api/health");
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.equal(body.offline, true);
  assert.equal(body.error, "agent offline");
});

test("backend too slow → 504 agent offline", async () => {
  stubFetch(() => { throw new DOMException("timed out", "TimeoutError"); });
  const res = await call("/api/health");
  assert.equal(res.status, 504);
  assert.equal((await res.json()).offline, true);
});

test("no BACKEND_URL or secret → 503 agent offline, no upstream call", async () => {
  const calls = stubFetch();
  for (const env of [{ ...ENV, BACKEND_URL: "" }, { ...ENV, MACRAE_TOOL_SECRET: "" }, { ...ENV, BACKEND_URL: "ftp://x" }]) {
    const res = await call("/api/tasks", {}, env);
    assert.equal(res.status, 503);
    assert.equal((await res.json()).offline, true);
  }
  assert.equal(calls.length, 0);
});

test("only GET, HEAD and POST reach the backend", async () => {
  const calls = stubFetch();
  for (const method of ["PUT", "DELETE", "PATCH"]) {
    const res = await call("/api/tasks", { method });
    assert.equal(res.status, 405);
  }
  assert.equal(calls.length, 0);
});

test("big bodies are refused", async () => {
  const calls = stubFetch();
  const res = await call("/api/search", { method: "POST", body: "x".repeat(70 * 1024) });
  assert.equal(res.status, 413);
  assert.equal(calls.length, 0);
});

test("/api/tools needs the real secret (the browser never has it)", async () => {
  const calls = stubFetch(() => Response.json({ answer_context: "", citations: [] }));
  let res = await call("/api/tools/search_papers", { method: "POST", body: "{}" });
  assert.equal(res.status, 403);
  res = await call("/api/tools/search_papers", { method: "POST", body: "{}", headers: { "X-Macrae-Secret": "wrong" } });
  assert.equal(res.status, 403);
  assert.equal(calls.length, 0);
  res = await call("/api/tools/search_papers", { method: "POST", body: '{"query":"ions"}', headers: { "X-Macrae-Secret": "s3cret" } });
  assert.equal(res.status, 200);
  assert.equal(calls[0].headers.get("x-macrae-secret"), "s3cret");
});

test("rate limit binding stops task starts", async () => {
  stubFetch(() => Response.json({ run_id: "r1" }));
  const env = { ...ENV, START_LIMIT: { limit: async ({ key }) => ({ success: !key.endsWith("9.9.9.9") }) } };
  let res = await call("/api/tasks/x/start", { method: "POST", body: "{}", headers: { "cf-connecting-ip": "9.9.9.9" } }, env);
  assert.equal(res.status, 429);
  res = await call("/api/tasks/x/start", { method: "POST", body: "{}", headers: { "cf-connecting-ip": "1.1.1.1" } }, env);
  assert.equal(res.status, 200);
  res = await call("/api/tasks", { headers: { "cf-connecting-ip": "9.9.9.9" } }, env);
  assert.equal(res.status, 200);
});

test("signed url comes from ElevenLabs with the api key", async () => {
  const calls = stubFetch(() => Response.json({ signed_url: "wss://api.elevenlabs.io/v1/convai/conversation?agent_id=agent_123&conversation_signature=abc" }));
  const res = await call("/voice/signed-url", { headers: { "sec-fetch-site": "same-origin" } });
  assert.equal(res.status, 200);
  assert.match((await res.json()).signed_url, /^wss:\/\/api\.elevenlabs\.io\//);
  assert.equal(calls[0].url, "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=agent_123");
  assert.equal(calls[0].headers.get("xi-api-key"), "xi-key");
  assert.equal(res.headers.get("cache-control"), "no-store");
});

test("signed url: not configured, cross-site, wrong method, ElevenLabs errors", async () => {
  const calls = stubFetch(() => Response.json({ detail: { status: "invalid_api_key", message: "Invalid API key" } }, { status: 401 }));
  let res = await call("/voice/signed-url", {}, { ...ENV, ELEVENLABS_API_KEY: "" });
  assert.equal(res.status, 503);
  assert.match((await res.json()).error, /not configured/);
  res = await call("/voice/signed-url", { headers: { "sec-fetch-site": "cross-site" } });
  assert.equal(res.status, 403);
  res = await call("/voice/signed-url", { method: "POST" });
  assert.equal(res.status, 405);
  assert.equal(calls.length, 0);
  res = await call("/voice/signed-url");
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.match(body.error, /401/);
  assert.equal(body.detail, "Invalid API key");
  assert.doesNotMatch(JSON.stringify(body), /xi-key/);
});

test("signed url: ElevenLabs unreachable", async () => {
  stubFetch(() => { throw new TypeError("fetch failed"); });
  const res = await call("/voice/signed-url");
  assert.equal(res.status, 502);
});

test("a crash inside the worker still answers with JSON and headers", async () => {
  const env = { ...ENV, ASSETS: { fetch: async () => { throw new Error("boom"); } } };
  const res = await call("/style.css", {}, env);
  assert.equal(res.status, 500);
  assert.ok(res.headers.get("content-security-policy"));
});

test("backendBase", () => {
  assert.equal(backendBase("https://a.example.com/"), "https://a.example.com");
  assert.equal(backendBase(" http://1.2.3.4:8080 "), "http://1.2.3.4:8080");
  assert.equal(backendBase("https://a.example.com/prefix/"), "https://a.example.com/prefix");
  assert.equal(backendBase("https://a.example.com/?x=1"), "https://a.example.com");
  assert.equal(backendBase("javascript:alert(1)"), "");
  assert.equal(backendBase("not a url"), "");
  assert.equal(backendBase(undefined), "");
});

test("sameSecret", () => {
  assert.ok(sameSecret("abc", "abc"));
  assert.ok(!sameSecret("abd", "abc"));
  assert.ok(!sameSecret("ab", "abc"));
  assert.ok(!sameSecret("abcd", "abc"));
  assert.ok(!sameSecret("", "abc"));
  assert.ok(!sameSecret(null, "abc"));
  assert.ok(!sameSecret("", ""));
});
