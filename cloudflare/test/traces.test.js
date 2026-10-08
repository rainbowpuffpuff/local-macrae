// The run's downloadable proof through the Worker: GET /api/runs/{id}/trace.zip and /report.html (server/traces.py).
import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import worker from "../worker.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

const ENV = { BACKEND_URL: "https://backend.example.com", MACRAE_TOOL_SECRET: "s3cret" };
const call = (path, init = {}) => worker.fetch(new Request(`https://macrae.example.workers.dev${path}`, init), ENV);

function stubFetch(reply) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), headers: new Headers(init.headers), method: init.method });
    return reply();
  };
  return calls;
}

test("trace.zip: bytes, type and file name pass through, with the secret added upstream", async () => {
  const zip = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 255, 1, 2, 3, 0x0a, 0x0d]);
  const calls = stubFetch(() => new Response(zip, { headers: {
    "content-type": "application/zip", "content-length": String(zip.length),
    "content-disposition": 'attachment; filename="macrae-trace-r1.zip"', "x-internal": "no",
  } }));
  const res = await call("/api/runs/r1/trace.zip", { headers: { "X-Macrae-Secret": "forged" } });
  assert.equal(res.status, 200);
  assert.equal(calls[0].url, "https://backend.example.com/api/runs/r1/trace.zip");
  assert.equal(calls[0].headers.get("x-macrae-secret"), "s3cret");
  assert.deepEqual(new Uint8Array(await res.arrayBuffer()), zip);
  assert.equal(res.headers.get("content-type"), "application/zip");
  assert.equal(res.headers.get("content-disposition"), 'attachment; filename="macrae-trace-r1.zip"');
  assert.equal(res.headers.get("cache-control"), "no-store");
  assert.equal(res.headers.get("x-internal"), null);
});

test("report.html: served inline under the page's CSP (the report needs no scripts)", async () => {
  stubFetch(() => new Response("<!doctype html><title>r</title>", { headers: {
    "content-type": "text/html; charset=utf-8", "content-disposition": 'inline; filename="macrae-report-r1.html"',
  } }));
  const res = await call("/api/runs/r1/report.html?download=0");
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "<!doctype html><title>r</title>");
  assert.equal(res.headers.get("content-type"), "text/html; charset=utf-8");
  assert.equal(res.headers.get("content-disposition"), 'inline; filename="macrae-report-r1.html"');
  assert.match(res.headers.get("content-security-policy"), /script-src 'self'/);
  assert.equal(res.headers.get("x-content-type-options"), "nosniff");
});

test("a missing run stays a 404 with the backend's JSON", async () => {
  stubFetch(() => Response.json({ detail: "no run 'x'" }, { status: 404 }));
  const res = await call("/api/runs/x/trace.zip");
  assert.equal(res.status, 404);
  assert.deepEqual(await res.json(), { detail: "no run 'x'" });
  assert.equal(res.headers.get("content-disposition"), null);
});
