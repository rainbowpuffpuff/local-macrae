// node --test web/tests: the manuscript view (web/manuscript.js) on a real agent-written manuscript
// (research/demo/small-calc-na/manuscript.md, the reference small-calc run).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderBlocks, manuscriptStats, artifactPath, artifactUrl, parseReferences } from "../manuscript.js";

const MS = readFileSync(new URL("../../research/demo/small-calc-na/manuscript.md", import.meta.url), "utf8");

test("stats: words, display equations, one figure, five references", () => {
  const st = manuscriptStats(MS);
  assert.ok(st.words > 500, String(st.words));
  assert.ok(st.equations >= 1);
  assert.equal(st.figures, 1);
  assert.equal(st.references, 5);
  assert.equal(parseReferences(MS).size, 5);
});

test("render: title, sections, the figure from the run's artifacts, [n] buttons, raw TeX without KaTeX", () => {
  const html = renderBlocks(MS, { runId: "r1", path: "results/manuscript.md" }).map((b) => b.html).join("\n");
  assert.match(html, /<h1 class="ms-title">How strongly does Na⁺ bind/);
  assert.match(html, /<h2>Methods<\/h2>/);
  assert.match(html, /<img src="\/api\/runs\/r1\/artifacts\/results\/fig1\.png"/);
  assert.match(html, /<b>Figure 1\.<\/b> Na⁺–water interaction energy/);  // the agent's own "Figure 1." isn't repeated
  assert.match(html, /class="ref" data-ref="\[1\]"/);
  assert.match(html, /class="math-src display"/);
  assert.doesNotMatch(html, /<script/);
});

test("figure paths stay inside the run's app folder", () => {
  assert.equal(artifactPath("results/manuscript.md", "fig1.png"), "results/fig1.png");
  assert.equal(artifactPath("results/manuscript.md", "../../etc/passwd.png"), null);
  assert.equal(artifactPath("results/manuscript.md", "https://evil.example/x.png"), null);
  assert.equal(artifactUrl("r 1", "results/fig 1.png"), "/api/runs/r%201/artifacts/results/fig%201.png");
});
