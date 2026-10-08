// node --test web/tests: the run view's "Download trace" / "Open report" links (web/traces.js).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { traceLinks, setTraceLinks } from "../traces.js";

const read = (f) => fs.readFileSync(new URL(`../${f}`, import.meta.url), "utf8");

test("links point at the backend's trace routes, run id encoded", () => {
  assert.deepEqual(traceLinks("20261008-201851-small-calc-71d4"), {
    zip: "/api/runs/20261008-201851-small-calc-71d4/trace.zip",
    zipName: "macrae-trace-20261008-201851-small-calc-71d4.zip",
    report: "/api/runs/20261008-201851-small-calc-71d4/report.html",
  });
  const odd = traceLinks("a/b c?");
  assert.equal(odd.zip, "/api/runs/a%2Fb%20c%3F/trace.zip");
  assert.equal(odd.zipName, "macrae-trace-a-b-c-.zip");
  assert.equal(traceLinks(""), null);
  assert.equal(traceLinks(null), null);
});

// The two anchors as the page has them, with just enough DOM for setTraceLinks.
function fakeBox() {
  const el = () => ({ href: "#", attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } });
  const zip = el(), report = el();
  return { hidden: true, zip, report, querySelector: (s) => (s.includes("zip") ? zip : report) };
}

test("setTraceLinks fills the buttons, and hides them without a run", () => {
  const box = fakeBox();
  setTraceLinks(box, "r1");
  assert.equal(box.hidden, false);
  assert.equal(box.zip.href, "/api/runs/r1/trace.zip");
  assert.equal(box.zip.attrs.download, "macrae-trace-r1.zip");
  assert.equal(box.report.href, "/api/runs/r1/report.html");
  setTraceLinks(box, "");
  assert.equal(box.hidden, true);
});

test("the run view has both buttons and app.js points them at the open run", () => {
  const html = read("index.html");
  const box = html.match(/<div class="run-links" id="run-links" hidden>([\s\S]*?)<\/div>/);
  assert.ok(box, "#run-links in the run card");
  assert.match(box[1], /data-trace="zip"[^>]*>Download trace<\/a>/);
  assert.match(box[1], /data-trace="report"[^>]*target="_blank"[^>]*rel="noopener"[^>]*>Open report<\/a>/);
  const app = read("app.js");
  assert.match(app, /import \{ setTraceLinks \} from "\.\/traces\.js";/);
  const open = app.slice(app.indexOf("function openRun("), app.indexOf("function closeRun("));
  assert.match(open, /safe\(setTraceLinks, \$\("#run-links"\), id\)/);
});
