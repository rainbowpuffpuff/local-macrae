// node --test web/tests: static checks on index.html, style.css and app.js that guard the run-view bugs.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const read = (f) => fs.readFileSync(new URL(`../${f}`, import.meta.url), "utf8");
const html = read("index.html");
const css = read("style.css");
const app = read("app.js");

test("every element app.js looks up by id exists in index.html", () => {
  // A missing id throws while the page starts, and everything after it (the health check) never runs.
  const ids = new Set([...app.matchAll(/\$\("#([\w-]+)"/g)].map((m) => m[1]));
  assert.ok(ids.size > 20);
  for (const id of ids) assert.match(html, new RegExp(`id="${id}"`), `#${id}`);
});

test("no entrance animation starts content at opacity 0", () => {
  // The blank timeline in run-methods-card.png: rows faded in from opacity 0, and a frame captured before the
  // animation ran (headless capture, background tab) showed none of them.
  for (const m of css.matchAll(/@keyframes\s+([\w-]+)\s*\{([^{}]*\{[^{}]*\}[^{}]*)*\}/g)) {
    assert.doesNotMatch(m[0], /(from|0%)\s*\{[^}]*opacity:\s*0[;\s}]/, `@keyframes ${m[1]}`);
  }
  const ev = css.match(/\.ev\s*\{[^}]*\}/);
  assert.ok(ev, ".ev rule");
  assert.doesNotMatch(ev[0], /animation|opacity/);
});

test("the page starts its health check before it opens a run", () => {
  const start = app.slice(app.indexOf("// ---------- start ----------"));
  assert.ok(start.indexOf("refreshHealth();") >= 0);
  assert.ok(start.indexOf("refreshHealth();") < start.indexOf("applyRoute"), "refreshHealth before applyRoute");
  assert.match(start, /safe\(applyRoute/);
});

test("API calls are exactly the contract's", () => {
  const calls = new Set([...app.matchAll(/\bapi\(\s*[`"]([^`"]+)[`"]/g)].map((m) => m[1].replace(/\$\{[^}]+\}/g, "{}")));
  assert.ok(calls.size >= 6, [...calls].join(" "));
  // v1 routes, GET /api/evolution (v2), POST /api/chat (typed chat with Jarvis), and the v3 reads behind the
  // Evolution tab: GET /api/capabilities and GET /api/dawn-report (the page never starts a benchmark or a forge),
  // and GET /api/runs/{id}/manuscript for a finished run's manuscript
  const allowed = ["/api/health", "/api/tasks", "/api/tasks/{}/start", "/api/runs", "/api/runs/{}", "/api/runs/{}/events{}", "/api/search", "/api/evolution", "/api/chat", "/api/capabilities", "/api/dawn-report", "/api/runs/{}/manuscript"];
  for (const c of calls) assert.ok(allowed.includes(c), `unexpected API call ${c}`);
  assert.doesNotMatch(app, /\/api\/tools\//, "the browser never calls the agent's tools");
});

test("the logo is inline SVG with the gold centre, the favicon is the mark, and review/ stays off the site", () => {
  assert.match(html, /<svg class="mark mark-sm"[^>]*>.*class="core"/s);
  assert.match(html, /<svg class="mark mark-lg"/);
  assert.match(html, /class="wordmark">macrae</);
  assert.match(html, /<link rel="icon" href="data:image\/svg\+xml,[^"]*ffb847/);
  assert.match(read(".assetsignore"), /^review\/?$/m);
});
