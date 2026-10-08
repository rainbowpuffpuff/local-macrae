// Runs worker.js on Node, with ../web as the ASSETS binding, for local development without wrangler.
// (`npx wrangler dev` does the same on the real runtime but needs Node 22+.)
//   node cloudflare/dev/serve.mjs                 → http://127.0.0.1:8787
// Reads cloudflare/.dev.vars (KEY=VALUE lines) and then the environment for BACKEND_URL, MACRAE_TOOL_SECRET,
// ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID. Pair it with dev/mock-backend.mjs when there is no backend.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import worker from "../worker.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "../../web");
const VARS = ["BACKEND_URL", "MACRAE_TOOL_SECRET", "ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID"];
const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
  ".jpg": "image/jpeg", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".webmanifest": "application/manifest+json",
};

export function readDevVars(file = path.resolve(HERE, "../.dev.vars")) {
  const out = {};
  try {
    for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
      if (m) out[m[1]] = m[2].replace(/^(['"])(.*)\1$/, "$2");
    }
  } catch {}
  return out;
}

// Mirrors wrangler's asset rules: .assetsignore entries are not served, "/" is index.html, unknown
// paths get index.html (not_found_handling = "single-page-application").
function ignoredByAssets(rel) {
  let rules = [];
  try {
    rules = fs.readFileSync(path.join(WEB, ".assetsignore"), "utf8").split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith("#"));
  } catch {}
  const parts = rel.split("/");
  return rules.some((r) => {
    if (r.startsWith("*.")) return rel.endsWith(r.slice(1));
    return parts.includes(r.replace(/\/$/, ""));
  });
}

export const assets = {
  async fetch(req) {
    const url = new URL(req.url);
    let rel = decodeURIComponent(url.pathname).replace(/^\/+/, "");
    if (!rel || rel.endsWith("/")) rel += "index.html";
    let file = path.resolve(WEB, rel);
    const inside = file.startsWith(WEB + path.sep);
    if (!inside || ignoredByAssets(rel) || !fs.existsSync(file) || !fs.statSync(file).isFile()) file = path.join(WEB, "index.html");
    const ext = path.extname(file);
    return new Response(req.method === "HEAD" ? null : fs.readFileSync(file), {
      headers: { "content-type": TYPES[ext] || "application/octet-stream", "cache-control": "no-cache" },
    });
  },
};

export function serve({ port = 8787, host = "127.0.0.1", env = {} } = {}) {
  const fullEnv = { ...env, ASSETS: assets };
  const server = http.createServer(async (req, res) => {
    try {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const body = chunks.length ? Buffer.concat(chunks) : undefined;
      const headers = new Headers();
      for (const [k, v] of Object.entries(req.headers)) if (v !== undefined) headers.set(k, Array.isArray(v) ? v.join(", ") : v);
      headers.set("cf-connecting-ip", req.socket.remoteAddress || "127.0.0.1");
      const request = new Request(`http://${req.headers.host || `${host}:${port}`}${req.url}`, {
        method: req.method, headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
      });
      const out = await worker.fetch(request, fullEnv);
      const h = {};
      out.headers.forEach((v, k) => (h[k] = v));
      res.writeHead(out.status, h);
      if (out.body) for await (const chunk of out.body) res.write(chunk);
      res.end();
    } catch (err) {
      res.writeHead(500, { "content-type": "text/plain" });
      res.end(String(err && err.stack ? err.stack : err));
    }
  });
  return new Promise((ok) => server.listen(port, host, () => ok({ server, port: server.address().port })));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const file = readDevVars();
  const env = {};
  for (const k of VARS) env[k] = process.env[k] ?? file[k] ?? "";
  const { port } = await serve({ port: Number(process.env.PORT || 8787), env });
  console.log(`macrae on http://127.0.0.1:${port}  (backend: ${env.BACKEND_URL || "not set → offline"}; voice: ${env.ELEVENLABS_API_KEY && env.ELEVENLABS_AGENT_ID ? "configured" : "not configured"})`);
}
