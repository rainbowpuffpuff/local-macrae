// macrae on Cloudflare Workers.
//
// Serves the page in ../web, proxies /api/* to the backend (adding the shared secret so the browser never sees
// it), and hands the page a short-lived ElevenLabs signed URL for the voice session.
//
// The backend is the container in index.js (Durable Object binding BACKEND, one instance named "macrae-backend").
// BACKEND_URL, when set, overrides it: /api/* then goes to that URL instead (local dev: `make serve` on :8080).
// This file has no Cloudflare-only imports, so the tests and dev/serve.mjs run it on plain Node.
//
// Vars/secrets: MACRAE_TOOL_SECRET, ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, optional BACKEND_URL; the container's
// own secrets (MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, AGENT_RUNNER_TOKEN_<NAME>, ANTHROPIC_API_KEY) see containerEnv.
// Bindings: BACKEND (container), DATA (R2 bucket macrae-data), optional START_LIMIT and VOICE_LIMIT (rate limits).

const ELEVENLABS_API = "https://api.elevenlabs.io";
const SDK_CDN = "https://cdn.jsdelivr.net";
const SECURITY = {
  "Content-Security-Policy":
    `default-src 'self'; script-src 'self' ${SDK_CDN} 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; ` +
    "img-src 'self' data:; font-src 'self' data:; media-src 'self' blob: data:; " +
    `connect-src 'self' ${SDK_CDN} https://*.elevenlabs.io wss://*.elevenlabs.io; ` +
    "worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Strict-Transport-Security": "max-age=31536000",
};
const METHODS = new Set(["GET", "HEAD", "POST"]);
const MAX_BODY = 64 * 1024;
const BACKEND_TIMEOUT_MS = 30_000;
// A cold container start (image pull, then uvicorn) can take longer than a warm backend answer.
const CONTAINER_TIMEOUT_MS = 90_000;
const VOICE_TIMEOUT_MS = 10_000;
const PASS_HEADERS = ["content-type", "accept"];
// /api/live/{run}/{step}: the claude wrapper in the Modal sandbox posts the agent's stream-json here while it runs.
// It authenticates with the run's own token (X-Macrae-Live), never the shared secret, which the Worker does not add.
const LIVE_PATH = /^\/api\/live\/[^/]+\/[^/]+$/;
const MAX_LIVE_BODY = 4 * 1024 * 1024;
const LIVE_HEADERS = ["x-macrae-live", "x-macrae-live-stream", "x-macrae-live-offset"];

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    let res;
    try {
      if (url.pathname.startsWith("/api/")) res = await proxy(req, env, url);
      else if (url.pathname === "/voice/signed-url") res = await signedUrl(req, env);
      else if (url.pathname.startsWith("/admin/")) res = await admin(req, env, url);
      else res = await env.ASSETS.fetch(req);
    } catch (err) {
      console.error("worker error", err && err.stack ? err.stack : err);
      res = json({ error: "something went wrong in the worker" }, 500);
    }
    return secure(res);
  },
};

export function secure(res) {
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
  return out;
}

export function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra },
  });
}

const offline = (detail) => json({ error: "agent offline", offline: true, detail }, 503);

// ---------- /api/* → backend ----------
export async function proxy(req, env, url) {
  if (!METHODS.has(req.method)) return json({ error: "method not allowed" }, 405, { allow: [...METHODS].join(", ") });
  if (url.pathname.startsWith("/api/live/")) return live(req, env, url);
  const target = backendTarget(env);
  if (target.error) return offline(target.error);
  if (!env.MACRAE_TOOL_SECRET) return offline("MACRAE_TOOL_SECRET is not set on the worker");

  // The ElevenLabs tools are for the voice agent. It may call them through this worker, but only with the secret.
  if (url.pathname.startsWith("/api/tools/") && !sameSecret(req.headers.get("x-macrae-secret"), env.MACRAE_TOOL_SECRET)) {
    return json({ error: "this endpoint is for the voice agent" }, 403);
  }

  if (req.method === "POST" && /^\/api\/tasks\/[^/]+\/start$/.test(url.pathname)) {
    const limited = await overLimit(env.START_LIMIT, req, "start");
    if (limited) return limited;
  }

  const headers = new Headers();
  for (const h of PASS_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  headers.set("X-Macrae-Secret", env.MACRAE_TOOL_SECRET);
  // Our public origin: the backend tells the Modal sandbox to post live agent output to <origin>/api/live/…
  headers.set("X-Macrae-Origin", url.origin);
  const ip = req.headers.get("cf-connecting-ip");
  if (ip) headers.set("X-Forwarded-For", ip);

  let body;
  if (req.method === "POST") {
    if (Number(req.headers.get("content-length") || 0) > MAX_BODY) return json({ error: "request too large" }, 413);
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BODY) return json({ error: "request too large" }, 413);
    if (!headers.has("content-type")) headers.set("content-type", "application/json");
  }

  let upstream;
  try {
    upstream = await target.fetch(url.pathname + url.search, { method: req.method, headers, body, redirect: "manual" });
  } catch (err) {
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    if (!timedOut) console.error("backend unreachable", target.kind, err && err.message ? err.message : err);
    return json({ error: "agent offline", offline: true, detail: timedOut ? "the backend did not answer in time" : "could not reach the backend" }, timedOut ? 504 : 502);
  }
  // The Container class answers in plain text when it can't start or reach the container (backend errors are JSON).
  if (target.kind === "container" && upstream.status >= 500 && !/json/i.test(upstream.headers.get("content-type") || "")) {
    const text = (await upstream.text().catch(() => "")).trim();
    console.error("backend container not ready", upstream.status, text.slice(0, 300));
    return offline(`the backend container is not ready (${text.slice(0, 160) || upstream.status}); it may be starting, try again in a minute`);
  }

  const out = new Headers();
  for (const h of ["content-type", "content-length", "etag", "last-modified"]) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  out.set("cache-control", "no-store");
  return new Response(req.method === "HEAD" ? null : upstream.body, { status: upstream.status, headers: out });
}

// ---------- /api/live/{run}/{step} → backend (agent output from the sandbox; the run token is the auth) ----------
export async function live(req, env, url) {
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405, { allow: "POST" });
  if (!LIVE_PATH.test(url.pathname)) return json({ error: "not found" }, 404);
  if (!req.headers.get("x-macrae-live")) return json({ error: "missing X-Macrae-Live token" }, 401);
  const target = backendTarget(env);
  if (target.error) return offline(target.error);
  if (Number(req.headers.get("content-length") || 0) > MAX_LIVE_BODY) return json({ error: "batch too large" }, 413);
  const body = await req.arrayBuffer();
  if (body.byteLength > MAX_LIVE_BODY) return json({ error: "batch too large" }, 413);
  const headers = new Headers({ "content-type": req.headers.get("content-type") || "application/json" });
  for (const h of LIVE_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  let upstream;
  try {
    upstream = await target.fetch(url.pathname, { method: "POST", headers, body, redirect: "manual" });
  } catch (err) {
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    return json({ error: "agent offline", offline: true }, timedOut ? 504 : 502);
  }
  // The wrapper only looks at the status (2xx: delivered; 401/404/410/413: stop; anything else: retry).
  const text = await upstream.text().catch(() => "");
  return new Response(text.slice(0, 2000), {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") || "application/json", "cache-control": "no-store" },
  });
}

// Where /api/* goes: BACKEND_URL if set (an override, e.g. a local backend), else the backend container.
// Returns { kind, fetch(pathAndQuery, init) } or { error }.
export function backendTarget(env) {
  if (env.BACKEND_URL && String(env.BACKEND_URL).trim()) {
    const base = backendBase(env.BACKEND_URL);
    if (!base) return { error: "BACKEND_URL on the worker is not an http(s) URL" };
    return { kind: "url", fetch: (path, init) => fetch(base + path, { ...init, signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS) }) };
  }
  if (env.BACKEND && typeof env.BACKEND.idFromName === "function") {
    const stub = backendStub(env);
    return { kind: "container", fetch: (path, init) => withTimeout(stub.fetch(new Request(`http://backend${path}`, init)), CONTAINER_TIMEOUT_MS) };
  }
  return { error: "no backend: the BACKEND container binding is missing and BACKEND_URL is not set on the worker" };
}

// The one backend container everybody shares (runs and their traces live in it).
export const BACKEND_INSTANCE = "macrae-backend";
export function backendStub(env) {
  return env.BACKEND.get(env.BACKEND.idFromName(BACKEND_INSTANCE));
}

function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new DOMException("the backend did not answer in time", "TimeoutError")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

// "https://x.example.com/" → "https://x.example.com", rejecting anything that is not http(s).
export function backendBase(raw) {
  if (!raw) return "";
  try {
    const u = new URL(String(raw).trim());
    if (u.protocol !== "https:" && u.protocol !== "http:") return "";
    return (u.origin + u.pathname).replace(/\/+$/, "");
  } catch {
    return "";
  }
}

export function sameSecret(given, want) {
  if (typeof given !== "string" || typeof want !== "string" || !want) return false;
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(want);
  let diff = a.length ^ b.length;
  for (let i = 0; i < b.length; i++) diff |= (a[i % (a.length || 1)] ?? 0) ^ b[i];
  return diff === 0;
}

async function overLimit(binding, req, scope) {
  if (!binding || typeof binding.limit !== "function") return null;
  const key = `${scope}:${req.headers.get("cf-connecting-ip") || "anon"}`;
  try {
    const { success } = await binding.limit({ key });
    return success ? null : json({ error: "too many requests, try again in a minute" }, 429, { "retry-after": "60" });
  } catch {
    return null;
  }
}

// ---------- /voice/signed-url ----------
export async function signedUrl(req, env) {
  if (req.method !== "GET") return json({ error: "method not allowed" }, 405, { allow: "GET" });
  // Only this page may start conversations on our ElevenLabs bill.
  const site = req.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return json({ error: "cross-site request refused" }, 403);
  if (!env.ELEVENLABS_API_KEY || !env.ELEVENLABS_AGENT_ID) {
    return json({ error: "voice is not configured", detail: "set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID on the worker" }, 503);
  }
  const limited = await overLimit(env.VOICE_LIMIT, req, "voice");
  if (limited) return limited;

  const target = `${ELEVENLABS_API}/v1/convai/conversation/get-signed-url?agent_id=${encodeURIComponent(env.ELEVENLABS_AGENT_ID)}`;
  let res;
  try {
    res = await fetch(target, { headers: { "xi-api-key": env.ELEVENLABS_API_KEY }, signal: AbortSignal.timeout(VOICE_TIMEOUT_MS) });
  } catch {
    return json({ error: "could not reach ElevenLabs" }, 502);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || typeof data.signed_url !== "string") {
    const detail = typeof data.detail === "string" ? data.detail : data.detail && data.detail.message;
    console.error("elevenlabs signed url failed", res.status, detail || "");
    return json({ error: `ElevenLabs refused the session (${res.status})`, detail: detail ? String(detail).slice(0, 200) : undefined }, 502);
  }
  return json({ signed_url: data.signed_url });
}

// ---------- /admin/* (deploy.sh: status and restart of the backend container) ----------
export async function admin(req, env, url) {
  if (!sameSecret(req.headers.get("x-macrae-secret"), env.MACRAE_TOOL_SECRET)) return json({ error: "forbidden" }, 403);
  if (!env.BACKEND || typeof env.BACKEND.idFromName !== "function") return json({ error: "no backend container on this worker" }, 404);
  const stub = backendStub(env);
  try {
    if (url.pathname === "/admin/status" && req.method === "GET") return json(await stub.getState());
    // Graceful: the container gets SIGTERM, lets running flows finish (up to ~14 min), saves its state to R2 and
    // exits. The next request starts a fresh one, with the Worker's current secrets.
    if (url.pathname === "/admin/restart" && req.method === "POST") return json(await stub.restart());
  } catch (err) {
    return json({ error: "the backend container is not available", detail: String(err && err.message ? err.message : err).slice(0, 300) }, 502);
  }
  return json({ error: "not found" }, 404);
}

// ---------- the container's environment ----------
// Worker secrets the backend needs, passed to the container as env vars when it starts. Every secret named
// AGENT_RUNNER_TOKEN_<NAME> is forwarded too (one per Claude login). A running container keeps the values it
// started with: after changing a secret, restart it (cloudflare/deploy.sh restart).
export const CONTAINER_SECRETS = ["MACRAE_TOOL_SECRET", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "ANTHROPIC_API_KEY"];
export const TOKEN_PREFIX = "AGENT_RUNNER_TOKEN_";
// Optional backend knobs, forwarded when set as a var or secret on the Worker.
export const CONTAINER_VARS = ["MODAL_PROFILE", "AGENT_RUNNER_MODAL_IMAGE", "MACRAE_MAX_ACTIVE_RUNS", "LOG_LEVEL",
  "MACRAE_SYNC_INTERVAL", "MACRAE_SYNC_MAX_MB", "MACRAE_DRAIN_SECONDS",
  // v2: live agent output, planner, costs, evolution (MACRAE_LIVE_URL is optional: the Worker sends its origin)
  "MACRAE_LIVE_URL", "MACRAE_PLANNER", "MACRAE_PLANNER_MODEL", "MACRAE_PLANNER_EFFORT", "MACRAE_COMPUTE_RATES",
  "MACRAE_EVOLVE"];
// The container reaches R2 through this made-up host: index.js maps it to dataHandler (an outbound handler that
// runs in the Worker, next to the DATA binding). Plain HTTP; the request never leaves Cloudflare.
export const DATA_HOST = "r2.macrae";

export function containerEnv(env) {
  const out = { MACRAE_SYNC_URL: `http://${DATA_HOST}` };
  for (const [k, v] of Object.entries(env || {})) {
    if (typeof v !== "string" || !v.trim()) continue;
    const token = k.startsWith(TOKEN_PREFIX) && /^[A-Z0-9_]+$/.test(k.slice(TOKEN_PREFIX.length)) && k.length > TOKEN_PREFIX.length;
    if (CONTAINER_SECRETS.includes(k) || CONTAINER_VARS.includes(k) || token) out[k] = v;
  }
  return out;
}

// Runs still going, from GET /api/runs. The container is kept up while this is > 0.
export function countRunning(data) {
  const runs = data && Array.isArray(data.runs) ? data.runs : [];
  return runs.filter((r) => r && r.status === "running").length;
}

// ---------- http://r2.macrae/* → R2 bucket macrae-data (binding DATA), for the container only ----------
//   GET  /?prefix=state/&cursor=…   → {"objects": [{"key", "size", "mtime"}], "cursor": str|null}
//   GET  /<key>                     → the object (header x-macrae-mtime), 404 if missing
//   PUT  /<key>                     → store it (content-length required; x-macrae-mtime kept as metadata)
// Keys live under index/ (the paper index, read-only here; deploy.sh uploads it) and state/ (runs, traces).
export const MAX_OBJECT = 100 * 1024 * 1024;
const BUFFER_MAX = 32 * 1024 * 1024;
const KEY_RE = /^(index|state)\/[^\0\\]{1,1000}$/;

export function validKey(key, { write = false } = {}) {
  if (typeof key !== "string" || !KEY_RE.test(key)) return false;
  if (write && !key.startsWith("state/")) return false;
  return key.split("/").every((p) => p !== "" && p !== "." && p !== "..");
}

export async function dataHandler(req, env) {
  const bucket = env.DATA;
  if (!bucket) return json({ error: "no R2 bucket bound as DATA" }, 503);
  const url = new URL(req.url);
  if (url.pathname === "/" || url.pathname === "") {
    if (req.method !== "GET") return json({ error: "method not allowed" }, 405);
    const prefix = url.searchParams.get("prefix") || "";
    if (!/^(index|state)\//.test(prefix)) return json({ error: "prefix must start with index/ or state/" }, 400);
    const page = await bucket.list({ prefix, cursor: url.searchParams.get("cursor") || undefined, limit: 1000, include: ["customMetadata"] });
    const objects = page.objects.map((o) => ({ key: o.key, size: o.size, mtime: mtimeOf(o) }));
    return json({ objects, cursor: page.truncated ? page.cursor : null });
  }
  let key;
  try {
    key = decodeURIComponent(url.pathname.slice(1));
  } catch {
    return json({ error: "bad key" }, 400);
  }
  if (req.method === "GET" || req.method === "HEAD") {
    if (!validKey(key)) return json({ error: "bad key" }, 400);
    const obj = await bucket.get(key);
    if (!obj) return json({ error: "not found" }, 404);
    const headers = { "content-type": "application/octet-stream", "content-length": String(obj.size) };
    const mtime = mtimeOf(obj);
    if (mtime !== null) headers["x-macrae-mtime"] = String(mtime);
    return new Response(req.method === "HEAD" ? null : obj.body, { headers });
  }
  if (req.method === "PUT") {
    if (!validKey(key, { write: true })) return json({ error: "bad key (writes go under state/)" }, 400);
    const length = Number(req.headers.get("content-length"));
    if (!Number.isFinite(length) || req.headers.get("content-length") === null) return json({ error: "content-length required" }, 411);
    if (length > MAX_OBJECT) return json({ error: "object too large" }, 413);
    const mtime = Number(req.headers.get("x-macrae-mtime"));
    const customMetadata = Number.isFinite(mtime) && mtime > 0 ? { mtime: String(mtime) } : undefined;
    // R2 needs a body of known length: buffer the usual small files, stream big ones through a FixedLengthStream.
    let body = "";
    if (length > 0 && length <= BUFFER_MAX) body = await req.arrayBuffer();
    else if (length > 0 && typeof FixedLengthStream === "function") {
      const { readable, writable } = new FixedLengthStream(length);
      req.body.pipeTo(writable).catch(() => {});
      body = readable;
    } else if (length > 0) body = req.body;
    await bucket.put(key, body, customMetadata ? { customMetadata } : undefined);
    return json({ key, size: length });
  }
  return json({ error: "method not allowed" }, 405);
}

function mtimeOf(obj) {
  const m = Number(obj && obj.customMetadata && obj.customMetadata.mtime);
  return Number.isFinite(m) && m > 0 ? m : null;
}
