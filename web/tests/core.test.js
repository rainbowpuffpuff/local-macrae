// node --test web/tests   (Node ≥ 18, no dependencies)
import test from "node:test";
import assert from "node:assert/strict";
import * as core from "../core.js";

const cite = (over = {}) => ({
  key: "[1]", title: "Ions at the air/water interface", authors: "P. Jungwirth; D. J. Tobias", year: 2006,
  journal: "Chem. Rev.", doi: "10.1021/cr0403741", page: 3, url: "https://doi.org/10.1021/cr0403741", quote: "…", ...over,
});

test("esc escapes html", () => {
  assert.equal(core.esc(`<a href="x">'&'</a>`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
  assert.equal(core.esc(null), "");
});

test("eventMeta maps every contract type to its icon and falls back to status", () => {
  const want = { read: "📄", search: "🔎", calc: "🧮", write: "✍️", result: "✅", error: "⚠️" };
  for (const [t, icon] of Object.entries(want)) assert.equal(core.eventMeta(t).icon, icon);
  for (const t of ["status", "think", "cite"]) assert.ok(core.eventMeta(t).icon);
  assert.equal(core.eventMeta("bogus"), core.EVENT_TYPES.status);
});

test("mergeEvents dedupes by seq, sorts, and reports what is new", () => {
  const a = [{ seq: 1, title: "a" }, { seq: 2, title: "b" }];
  const { events, lastSeq, added } = core.mergeEvents(a, [{ seq: 2, title: "b2" }, { seq: 4 }, { seq: 3 }, { title: "no seq" }, null]);
  assert.deepEqual(events.map((e) => e.seq), [1, 2, 3, 4]);
  assert.equal(events[1].title, "b2");
  assert.equal(lastSeq, 4);
  assert.equal(added, 2);
  assert.deepEqual(core.mergeEvents([], []), { events: [], lastSeq: null, added: 0 });
  assert.equal(core.mergeEvents(undefined, [{ seq: 0 }]).lastSeq, 0);
});

test("runStats counts work and distinct papers", () => {
  const ev = [
    { type: "read", citation: cite() },
    { type: "read", citation: cite({ page: 7 }) },
    { type: "search", citation: cite({ doi: "10.1/x", page: 1 }) },
    { type: "calc" }, { type: "calc" }, { type: "write" }, { type: "think" }, { type: "error" },
  ];
  assert.deepEqual(core.runStats(ev), { read: 2, search: 1, calc: 2, write: 1, error: 1, papers: 2 });
});

test("citationUrl prefers a safe url, else builds a doi link, never javascript:", () => {
  assert.equal(core.citationUrl(cite()), "https://doi.org/10.1021/cr0403741");
  assert.equal(core.citationUrl(cite({ url: "", doi: "doi:10.1021/acs.jctc.5c02051" })), "https://doi.org/10.1021/acs.jctc.5c02051");
  assert.equal(core.citationUrl(cite({ url: "javascript:alert(1)", doi: "" })), "");
  assert.equal(core.citationUrl(cite({ url: "javascript:alert(1)", doi: "10.1021/a<b" })), "https://doi.org/10.1021/a%3Cb");
  assert.equal(core.citationUrl(cite({ url: "", doi: "not a doi" })), "");
  assert.equal(core.citationUrl(null), "");
});

test("dedupeCitations keeps one card per paper and page", () => {
  const list = [cite(), cite({ key: "[2]" }), cite({ page: 4 }), null, "x", {}];
  assert.equal(core.dedupeCitations(list).length, 2);
  assert.equal(core.collectCitations([{ citation: cite() }, { citation: cite({ page: 9 }) }, { citation: null }, {}]).length, 1);
  assert.equal(core.collectCitations([{ citation: cite() }, { citation: cite({ doi: "10.1/b", url: "" }) }]).length, 2);
});

test("shortAuthors and citationMeta", () => {
  assert.equal(core.shortAuthors("V. Košťál; P. Jungwirth; H. Martinez-Seara"), "Košťál et al.");
  assert.equal(core.shortAuthors("P. Jungwirth; D. J. Tobias"), "Jungwirth & Tobias");
  assert.equal(core.shortAuthors("Jungwirth, Pavel"), "Jungwirth");
  assert.equal(core.shortAuthors(""), "");
  assert.equal(core.citationMeta(cite()), "Jungwirth & Tobias · Chem. Rev. · 2006 · p. 3");
  assert.equal(core.citationMeta({ year: 1970 }), "");
});

test("normalizeCitations accepts the shapes an LLM might send", () => {
  assert.equal(core.normalizeCitations({ citations: [cite()] }).length, 1);
  assert.equal(core.normalizeCitations({ citations: JSON.stringify([cite(), cite({ doi: "10.1/b" })]) }).length, 2);
  assert.equal(core.normalizeCitations(JSON.stringify({ citations: [cite()] })).length, 1);
  assert.equal(core.normalizeCitations([cite()]).length, 1);
  assert.equal(core.normalizeCitations(cite()).length, 1);
  const fromPassage = core.normalizeCitations({ passages: [{ id: "p", text: "passage text", citation: cite({ quote: "" }) }] });
  assert.equal(fromPassage[0].quote, "passage text");
  assert.deepEqual(core.normalizeCitations("not json"), []);
  assert.deepEqual(core.normalizeCitations({ other: 1 }), []);
  assert.deepEqual(core.normalizeCitations(null), []);
});

test("normalizeRunId", () => {
  assert.equal(core.normalizeRunId({ run_id: "20261008-abc" }), "20261008-abc");
  assert.equal(core.normalizeRunId({ runId: "x1" }), "x1");
  assert.equal(core.normalizeRunId('{"run_id":"r-2"}'), "r-2");
  assert.equal(core.normalizeRunId(" r-3 "), "r-3");
  assert.equal(core.normalizeRunId("../../etc/passwd"), "");
  assert.equal(core.normalizeRunId("<script>"), "");
  assert.equal(core.normalizeRunId({}), "");
  assert.equal(core.normalizeRunId(null), "");
});

test("richText escapes, formats, and links citation markers", () => {
  const html = core.richText("Ions **adsorb** at the surface [1][2].\n\n- one\n- two <b>", new Set(["[1]"]));
  assert.match(html, /<strong>adsorb<\/strong>/);
  assert.match(html, /data-ref="\[1\]"/);
  assert.doesNotMatch(html, /data-ref="\[2\]"/);
  assert.match(html, /\[2\]/);
  assert.match(html, /<ul><li>one<\/li><li>two &lt;b&gt;<\/li><\/ul>/);
  assert.doesNotMatch(core.richText("<img src=x onerror=alert(1)>"), /<img/);
  assert.match(core.richText("see https://doi.org/10.1/x."), /href="https:\/\/doi.org\/10.1\/x"/);
  assert.match(core.richText("any [3]"), /data-ref="\[3\]"/);
});

test("truncate", () => {
  assert.equal(core.truncate("abcdef", 4), "abc…");
  assert.equal(core.truncate("abc", 4), "abc");
});

test("time formatting", () => {
  assert.equal(core.formatDuration(4.4), "4 s");
  assert.equal(core.formatDuration(125), "2 min 05 s");
  assert.equal(core.formatDuration(3720), "1 h 02 min");
  assert.equal(core.formatDuration(-3), "0 s");
  assert.equal(core.formatOffset(105.25, 100), "+5.3 s");
  assert.equal(core.formatOffset(130, 100), "+30 s");
  assert.equal(core.formatOffset(100 + 125, 100), "+2:05");
  assert.equal(core.formatOffset(undefined, 100), "");
  const now = 1_760_000_000;
  assert.equal(core.relativeTime(now - 10, now), "just now");
  assert.equal(core.relativeTime(now - 300, now), "5 min ago");
  assert.equal(core.relativeTime(now - 7200, now), "2 h ago");
  assert.equal(core.relativeTime(now - 100000, now), "yesterday");
  assert.equal(core.relativeTime(now - 864000, now), "10 days ago");
  assert.equal(core.relativeTime(0, now), "");
  assert.equal(core.elapsed({ started: now - 50, finished: null }, now), 50);
  assert.equal(core.elapsed({ started: now - 50, finished: now - 20 }, now), 30);
  assert.equal(core.elapsed({ started: null }, now), null);
});

test("statusState and isFinal", () => {
  assert.deepEqual(core.statusState("ok"), { cls: "ok", label: "Done" });
  assert.equal(core.statusState("failed").cls, "failed");
  assert.equal(core.statusState("cancelled").cls, "cancelled");
  assert.equal(core.statusState("running").label, "Running");
  assert.equal(core.statusState("").label, "Starting");
  assert.equal(core.statusState("weird").label, "Weird");
  assert.ok(core.isFinal("ok") && core.isFinal("failed") && core.isFinal("cancelled"));
  assert.ok(!core.isFinal("running") && !core.isFinal(undefined));
});

test("taskInputs falls back to defaults", () => {
  const task = { inputs: [{ name: "doi", default: "10.1/x" }, { name: "n", default: 3 }, { name: "" }] };
  assert.deepEqual(core.taskInputs(task, { doi: "  " }), { doi: "10.1/x", n: 3 });
  assert.deepEqual(core.taskInputs(task, { doi: " 10.2/y ", n: "5" }), { doi: "10.2/y", n: "5" });
  assert.deepEqual(core.taskInputs({}, {}), {});
});

test("routes", () => {
  assert.deepEqual(core.parseRoute("?run=abc-1"), { view: "run", runId: "abc-1" });
  assert.deepEqual(core.parseRoute("?run=<x>"), { view: "home" });
  assert.deepEqual(core.parseRoute(""), { view: "home" });
  assert.equal(core.routeHref({ view: "run", runId: "a b" }), "/?run=a%20b");
  assert.equal(core.routeHref({ view: "home" }), "/");
});

test("parseToolResult and toolLabel", () => {
  assert.deepEqual(core.parseToolResult('{"run_id":"r1"}'), { run_id: "r1" });
  assert.equal(core.parseToolResult("nope"), null);
  assert.equal(core.parseToolResult('"str"'), null);
  assert.deepEqual(core.parseToolResult({ a: 1 }), { a: 1 });
  assert.match(core.toolLabel("search_papers"), /Searched/);
  assert.match(core.toolLabel("some_tool"), /some tool/);
});

test("pushTranscript merges same-id agent parts and stays bounded", () => {
  let t = [];
  t = core.pushTranscript(t, { role: "agent", id: 1, text: "Hel" });
  t = core.pushTranscript(t, { role: "agent", id: 1, text: "Hello" });
  t = core.pushTranscript(t, { role: "user", text: "hi" });
  assert.equal(t.length, 2);
  assert.equal(t[0].text, "Hello");
  for (let i = 0; i < 10; i++) t = core.pushTranscript(t, { role: "user", text: String(i) }, 5);
  assert.equal(t.length, 5);
  assert.equal(t[4].text, "9");
});

test("healthLabel", () => {
  assert.deepEqual(core.healthLabel(null), { cls: "err", text: "Agent offline" });
  assert.deepEqual(core.healthLabel({ ok: true, papers: 1, chunks: 3, modal: true }), { cls: "live", text: "Online · 1 paper · Modal ready" });
  assert.equal(core.healthLabel({ ok: true, papers: 0, modal: false }).text, "Online · 0 papers");
});

// ---------- run-methods-card.png: header stuck on "Loading the run…" / "Starting", pill stuck on "Checking…" ----------
test("healthLabel: still checking, offline, and online from any other API answer", () => {
  assert.deepEqual(core.healthLabel(undefined), { cls: "", text: "Checking…" });
  assert.deepEqual(core.healthLabel(undefined, { reachable: true }), { cls: "live", text: "Online" });
  assert.deepEqual(core.healthLabel(null, { reachable: false }), { cls: "err", text: "Agent offline" });
  // full health wins over the bare "Online"
  assert.equal(core.healthLabel({ ok: true, papers: 2, modal: true }, { reachable: true }).text, "Online · 2 papers · Modal ready");
});

test("taskIdFromRunId finds the task in mock and agent_runner run ids", () => {
  const tasks = [{ id: "methods-card" }, { id: "card" }, { id: "small-calc" }];
  assert.equal(core.taskIdFromRunId("20261008181714-methods-card-1", tasks), "methods-card");
  assert.equal(core.taskIdFromRunId("20261008-181714-small-calc-ab12", tasks), "small-calc");
  assert.equal(core.taskIdFromRunId("20261008-181714-other-ab12", tasks), "");
  assert.equal(core.taskIdFromRunId("x", null), "");
});

test("eventsOutcome: an error that no result followed is a failure", () => {
  assert.equal(core.eventsOutcome([{ type: "read" }, { type: "result" }]), "ok");
  assert.equal(core.eventsOutcome([{ type: "error" }, { type: "result" }]), "ok"); // retried, then succeeded
  assert.equal(core.eventsOutcome([{ type: "result" }, { type: "error" }]), "failed");
  assert.equal(core.eventsOutcome([]), "ok");
});

test("runHeadline: events alone are enough for a title and a status (no 'Loading…' / 'Starting')", () => {
  const tasks = [{ id: "methods-card", title: "Methods card for a paper" }];
  const events = [{ seq: 0, type: "status" }, { seq: 1, type: "search" }, { seq: 2, type: "read" }];
  // the screenshot's state: events arrived, GET /api/runs/{id} hasn't
  const h = core.runHeadline({ runId: "20261008181714-methods-card-1", events, tasks });
  assert.equal(h.title, "Methods card for a paper");
  assert.equal(h.status, "running");
  assert.equal(h.state.label, "Running");
  // nothing at all yet
  assert.equal(core.runHeadline({ runId: "r1" }).title, "Opening the run…");
  assert.equal(core.runHeadline({ runId: "r1" }).state.label, "Starting");
  // a first answer came back empty: never stay on the loading text
  assert.equal(core.runHeadline({ runId: "r1", settled: true }).title, "Run r1");
  // what we knew when we started it
  assert.equal(core.runHeadline({ runId: "r1", hint: { title: "From the card" } }).title, "From the card");
  // run info wins once it arrives
  const info = { title: "Real title", status: "ok", task_id: "methods-card" };
  assert.deepEqual([core.runHeadline({ runId: "r1", info, events }).title, core.runHeadline({ runId: "r1", info, events }).state.label], ["Real title", "Done"]);
  // the trace says done but state.json still says running: the trace decides
  assert.equal(core.runHeadline({ runId: "r1", info: { title: "T", status: "running" }, events: [...events, { seq: 3, type: "result" }], done: true }).state.label, "Done");
  assert.equal(core.runHeadline({ runId: "r1", events: [{ seq: 0, type: "error" }], done: true }).state.label, "Failed");
});

// ---------- chat ----------
test("numberCitations keeps clean keys and renumbers messy ones", () => {
  const a = cite({ key: "[1]" });
  const b = cite({ key: "[2]", doi: "10.1/b", title: "B" });
  assert.deepEqual(core.numberCitations([a, b]).map((c) => c.key), ["[1]", "[2]"]);
  assert.deepEqual(core.numberCitations([{ ...a, key: "" }, { ...b, key: "[1]" }]).map((c) => c.key), ["[1]", "[2]"]);
  assert.deepEqual(core.numberCitations([a, a]).length, 1);
});

test("passagesAnswer turns search passages into a cited answer", () => {
  const p = (doi, page, text) => ({ text, citation: cite({ doi, page, key: "[9]", title: doi }) });
  const { text, citations } = core.passagesAnswer([p("10.1/a", 2, "Alpha."), p("10.1/a", 2, "Alpha again."), p("10.1/b", 5, "Beta.")]);
  assert.match(text, /^From the group's papers, the 3 closest passages:/);
  assert.match(text, /- Alpha\. \[1\]\n- Alpha again\. \[1\]\n- Beta\. \[2\]$/);
  assert.deepEqual(citations.map((c) => [c.key, c.doi]), [["[1]", "10.1/a"], ["[2]", "10.1/b"]]);
  assert.match(core.passagesAnswer([]).text, /couldn't find anything/);
  assert.equal(core.passagesAnswer(Array.from({ length: 9 }, (_, i) => p(`10.1/${i}`, 1, "x"))).citations.length, 4);
});

test("citationTarget: the agent's answer to the latest user turn, else wait", () => {
  assert.equal(core.citationTarget([]), -1);
  assert.equal(core.citationTarget([{ role: "jarvis" }, { role: "user" }]), -1);
  assert.equal(core.citationTarget([{ role: "user" }, { role: "tool" }, { role: "jarvis" }, { role: "tool" }]), 2);
  assert.equal(core.citationTarget([{ role: "user" }, { role: "jarvis", runId: "r" }]), -1);
});

test("citationShort", () => {
  assert.equal(core.citationShort(cite()), "Jungwirth & Tobias 2006");
  assert.equal(core.citationShort({ title: "A rather long title that goes on and on", year: 0 }), "A rather long title that go…");
});
