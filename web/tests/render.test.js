// node --test web/tests: the HTML the page builds for messages, sources and trace rows.
import test from "node:test";
import assert from "node:assert/strict";
import * as r from "../render.js";

const cite = (over = {}) => ({
  key: "[1]", title: "Ions at the air/water interface", authors: "P. Jungwirth; D. J. Tobias", year: 2006,
  journal: "Chem. Rev.", doi: "10.1021/cr0403741", page: 3, quote: "Soft anions…", ...over,
});

test("a trace event renders as a visible row with its icon, title, step and citation", () => {
  const html = r.eventHTML({ seq: 4, t: 12, step: "card[0]", type: "read", title: "Read paper.json", detail: "6 passages", citation: cite() }, 10);
  assert.match(html, /^<li class="ev ev-read" data-seq="4">/);
  assert.match(html, /📄/);
  assert.match(html, /Read paper\.json/);
  assert.match(html, /\+2\.0 s/);
  assert.match(html, /card\[0\]/);
  assert.match(html, /href="https:\/\/doi\.org\/10\.1021\/cr0403741"/);
  // nothing in the markup hides it (the old blank timeline was rows stuck at opacity 0)
  assert.doesNotMatch(html, /\shidden[\s>=]|opacity|display:\s*none/);
});

test("unknown event types draw as status; text is escaped", () => {
  const html = r.eventHTML({ seq: 1, type: "weird", title: "<img src=x onerror=alert(1)>" }, 0);
  assert.match(html, /class="ev ev-status"/);
  assert.doesNotMatch(html, /<img/);
});

test("every contract event type has its icon", () => {
  const icons = { read: "📄", search: "🔎", calc: "🧮", write: "✍️", result: "✅", error: "⚠️" };
  for (const [type, icon] of Object.entries(icons)) assert.match(r.eventHTML({ seq: 1, type, title: "x" }, 0), new RegExp(icon), type);
});

test("a Jarvis answer: [n] buttons, numbered source chips, and an open card with title/authors/journal/year/page/DOI", () => {
  const m = { id: "j1", role: "jarvis", text: "Soft anions go up [1], hard ones stay down [2].", citations: [cite(), cite({ key: "[2]", doi: "10.1/b", title: "B", authors: "A. Person" })], open: ["[1]"] };
  const html = r.messageHTML(m);
  assert.match(html, /class="msg jarvis" data-id="j1"/);
  assert.match(html, /<button type="button" class="ref" data-ref="\[1\]"/);
  assert.match(html, /<button type="button" class="ref" data-ref="\[2\]"/);
  assert.match(html, /class="src-chip on" data-key="\[1\]" aria-expanded="true"/);
  assert.match(html, /class="src-chip" data-key="\[2\]" aria-expanded="false"/);
  assert.match(html, /Jungwirth &amp; Tobias 2006/);
  const card = html.slice(html.indexOf('<div class="cite"'));
  for (const s of ["Ions at the air/water interface", "P. Jungwirth; D. J. Tobias", "Chem. Rev. · 2006 · p. 3", "doi:10.1021/cr0403741", 'href="https://doi.org/10.1021/cr0403741"'])
    assert.ok(card.includes(s), s);
  assert.equal((html.match(/class="cite"/g) || []).length, 1, "only the open source shows its card");
});

test("[n] for a source the message doesn't have stays text; before any sources arrive every [n] is a button", () => {
  assert.doesNotMatch(r.messageHTML({ role: "jarvis", text: "See [3].", citations: [cite()] }), /data-ref="\[3\]"/);
  assert.match(r.messageHTML({ role: "jarvis", text: "See [3].", via: "voice" }), /data-ref="\[3\]"/);
});

test("user, pending, run and offline messages", () => {
  assert.match(r.messageHTML({ role: "user", text: "<b>hi</b>", via: "voice" }), /&lt;b&gt;hi&lt;\/b&gt;.*spoken/s);
  assert.match(r.messageHTML({ role: "jarvis", pending: true, text: "Searching the papers…" }), /class="typing".*Searching the papers…/s);
  const run = r.messageHTML({ role: "jarvis", text: "Starting **X**", runId: "r 1" }, { runs: new Map([["r 1", { title: "X", cls: "ok", label: "Done" }]]) });
  assert.match(run, /<a class="run-chip" href="\/\?run=r%201" data-run="r 1">/);
  assert.match(run, /<span class="state ok">Done<\/span>/);
  assert.match(r.messageHTML({ role: "jarvis", bad: true, text: "offline" }), /class="msg jarvis bad"/);
});

test("steps show kind, status, reward and retries", () => {
  assert.match(r.stepHTML({ key: "card[0]", kind: "agent", status: "ok", reward: 1, attempt: 2 }), /card\[0\].*agent · done · reward 1 · attempt 2/s);
});
