// The container's R2 endpoint (worker.js dataHandler) on Node, backed by a folder instead of the macrae-data bucket.
// Lets deploy/r2sync.py run against the real handler without Cloudflare:
//   node cloudflare/dev/data-server.mjs [--port 8789] [--dir /tmp/macrae-r2]     → prints {"port": …, "dir": …}
//   MACRAE_SYNC_URL=http://127.0.0.1:8789 python deploy/start.py                  (the container's sync, locally)
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { dataHandler } from "../worker.js";

// The subset of the R2 bucket API dataHandler uses: list (with customMetadata), get, put. Objects are files under
// `dir`; the mtime metadata is kept as the file's mtime.
export function folderBucket(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const file = (key) => path.join(dir, ...key.split("/"));
  const object = (key, st) => ({ key, size: st.size, customMetadata: st.mtimeMs > 1000 ? { mtime: String(st.mtimeMs / 1000) } : {} });
  const walk = (d, out = []) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p, out);
      else out.push(path.relative(dir, p).split(path.sep).join("/"));
    }
    return out;
  };
  return {
    async list({ prefix = "", cursor, limit = 1000 } = {}) {
      const keys = walk(dir).filter((k) => k.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0;
      const page = keys.slice(start, start + limit);
      const truncated = start + limit < keys.length;
      return { objects: page.map((k) => object(k, fs.statSync(file(k)))), truncated, cursor: truncated ? String(start + limit) : undefined };
    },
    async get(key) {
      const f = file(key);
      if (!fs.existsSync(f)) return null;
      const st = fs.statSync(f);
      return { ...object(key, st), body: fs.readFileSync(f) };
    },
    async put(key, body, opts = {}) {
      const f = file(key);
      fs.mkdirSync(path.dirname(f), { recursive: true });
      const buf = typeof body === "string" ? Buffer.from(body) : Buffer.from(await new Response(body).arrayBuffer());
      fs.writeFileSync(f, buf);
      const m = Number(opts.customMetadata && opts.customMetadata.mtime);
      if (m > 0) fs.utimesSync(f, m, m);
      return object(key, fs.statSync(f));
    },
  };
}

export function serveData({ port = 8789, host = "127.0.0.1", dir } = {}) {
  const env = { DATA: folderBucket(dir) };
  const server = http.createServer(async (req, res) => {
    try {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const headers = new Headers();
      for (const [k, v] of Object.entries(req.headers)) if (v !== undefined) headers.set(k, Array.isArray(v) ? v.join(", ") : v);
      const hasBody = !["GET", "HEAD"].includes(req.method);
      const out = await dataHandler(new Request(`http://r2.macrae${req.url}`, {
        method: req.method, headers, body: hasBody ? Buffer.concat(chunks) : undefined, duplex: "half",
      }), env);
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
  const arg = (name, dflt) => {
    const i = process.argv.indexOf(name);
    return i > 0 ? process.argv[i + 1] : dflt;
  };
  const dir = arg("--dir", fs.mkdtempSync(path.join(os.tmpdir(), "macrae-r2-")));
  const { port } = await serveData({ port: Number(arg("--port", process.env.PORT || 8789)), dir });
  console.log(JSON.stringify({ port, dir }));
}
