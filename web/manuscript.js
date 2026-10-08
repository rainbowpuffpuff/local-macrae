// The Manuscript view (CONTRACT.md v3): the run's results/manuscript.md, rebuilt from the agent's Write/Edit calls
// and replayed like live typing (deletions and replacements too), rendered as Markdown with KaTeX math, the run's
// figures and [n] citations that open source cards. Pure (no DOM, no network): web/tests/manuscript.test.js.
//   GET /api/runs/{id}/manuscript → [{seq, t, op, path, old, new, content}] (or {"edits": [...]})
//   GET /api/runs/{id}/artifacts/<path> → the figures (images only)
import { esc, citationUrl, safeUrl, truncate } from "./core.js";

const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const str = (v) => (v === null || v === undefined ? null : String(v));

// Zero-width marks the typist puts into the text before rendering: where the caret is, and the span about to be
// deleted. Private-use code points, so they can't collide with what the agent wrote.
export const CARET = "";
export const SEL_ON = "";
export const SEL_OFF = "";
const MARKS = /[-]/g;
const clean = (s) => String(s).replace(MARKS, "");
const hasMarks = (s) => /[-]/.test(s);

// ---------- the edit stream ----------
const OPS = {
  write: "write", create: "write", overwrite: "write", put: "write",
  edit: "edit", replace: "edit", str_replace: "edit", multiedit: "edit", update: "edit",
  delete: "delete", remove: "delete",
  append: "append", insert: "append",
};

// → [{seq, t, op: write|edit|delete|append, path, old, new, content, replaceAll}], in seq order, one per seq.
// A MultiEdit ({edits: [{old_string, new_string}]}) becomes one edit per change.
export function normalizeEdits(data) {
  const list = Array.isArray(data) ? data : isObj(data) ? data.edits || data.manuscript || data.stream || data.events || [] : [];
  const out = [];
  (Array.isArray(list) ? list : []).forEach((e, i) => {
    if (!isObj(e)) return;
    const seq = num(e.seq) ?? i;
    const base = { seq, t: num(e.t), path: String(e.path || e.file || e.file_path || "") };
    if (Array.isArray(e.edits)) {
      e.edits.forEach((x, j) => isObj(x) && out.push({ ...base, seq: seq + j / 1000, op: "edit", old: str(x.old ?? x.old_string), new: str(x.new ?? x.new_string) ?? "", content: null, replaceAll: !!x.replace_all }));
      return;
    }
    const content = str(e.content ?? e.text);
    const old = str(e.old ?? e.old_string);
    const op = OPS[String(e.op || e.type || e.tool || "").toLowerCase()] || (old !== null ? "edit" : "write");
    out.push({ ...base, op, old, new: str(e.new ?? e.new_string), content, replaceAll: !!e.replace_all });
  });
  const bySeq = new Map(out.map((e) => [e.seq, e]));
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

// The manuscript among the files the stream touches: the one called manuscript.md, else the most edited one.
export function pickPath(edits) {
  const paths = [...new Set((edits || []).map((e) => e.path).filter(Boolean))];
  const ms = paths.find((p) => /(^|\/)manuscript\.md$/i.test(p));
  if (ms || paths.length < 2) return ms || paths[0] || "";
  const n = (p) => edits.filter((e) => e.path === p).length;
  return paths.sort((a, b) => n(b) - n(a))[0];
}

// One edit applied to the text. `ok: false` when an Edit's old text isn't there (the stream missed something):
// a full `content` then wins, otherwise the text stays.
export function applyEdit(text, e) {
  const t = String(text ?? "");
  switch (e.op) {
    case "write":
      return { text: e.content ?? e.new ?? "", ok: true };
    case "append":
      return { text: t + (e.content ?? e.new ?? ""), ok: true };
    case "delete":
      if (!e.old) return { text: "", ok: true };
      return t.includes(e.old) ? { text: t.replace(e.old, ""), ok: true } : { text: t, ok: false };
    default: {
      if (!e.old) return e.content !== null ? { text: e.content, ok: true } : { text: t, ok: false };
      const i = t.indexOf(e.old);
      if (i < 0) return e.content !== null ? { text: e.content, ok: true } : { text: t, ok: false };
      const nw = e.new ?? "";
      return { text: e.replaceAll ? t.split(e.old).join(nw) : t.slice(0, i) + nw + t.slice(i + e.old.length), ok: true };
    }
  }
}

// The text after each edit of the manuscript's own file.
export function buildStates(edits) {
  const path = pickPath(edits);
  const own = (edits || []).filter((e) => !path || !e.path || e.path === path);
  let text = "";
  return own.map((edit) => {
    const r = applyEdit(text, edit);
    text = r.text;
    return { edit, text, ok: r.ok };
  });
}

// ---------- diff ----------
// Words, whitespace runs and single punctuation marks: an edit animates as words being retyped, not whole lines.
export function tokenize(s) {
  return String(s).match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) || [];
}

const MAX_D = 800; // Myers' cost grows with the number of changes; past this a rewrite is one hunk anyway

// Myers' O(ND) diff on two token arrays → [[op, token]] with op "=", "-" or "+". null when too different.
function myers(a, b) {
  const n = a.length;
  const m = b.length;
  const max = Math.min(n + m, MAX_D);
  const off = max + 1;
  let v = new Int32Array(2 * max + 3);
  const trace = [];
  for (let d = 0; d <= max; d++) {
    trace.push(v.slice());
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }
      v[off + k] = x;
      if (x >= n && y >= m) {
        // walk back through the trace
        const ops = [];
        let cx = n;
        let cy = m;
        for (let dd = d; dd > 0; dd--) {
          const pv = trace[dd];
          const kk = cx - cy;
          const prevK = kk === -dd || (kk !== dd && pv[off + kk - 1] < pv[off + kk + 1]) ? kk + 1 : kk - 1;
          const px = pv[off + prevK];
          const py = px - prevK;
          while (cx > px && cy > py) ops.push(["=", a[--cx]]), cy--;
          if (cx === px) ops.push(["+", b[--cy]]);
          else ops.push(["-", a[--cx]]);
        }
        while (cx > 0 && cy > 0) ops.push(["=", a[--cx]]), cy--;
        return ops.reverse();
      }
    }
    v = v.slice();
  }
  return null;
}

// a → b as hunks {at, del, ins} in a's character offsets, left to right. Changes separated by a few unchanged
// characters merge into one hunk, so a reworded sentence is replaced as a phrase rather than word by word.
export function diffHunks(a, b, { merge = 12 } = {}) {
  a = String(a ?? "");
  b = String(b ?? "");
  if (a === b) return [];
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p++;
  // don't split a word: back up to its start
  while (p > 0 && /[\p{L}\p{N}_]/u.test(a[p - 1]) && (/[\p{L}\p{N}_]/u.test(a[p] || "") || /[\p{L}\p{N}_]/u.test(b[p] || ""))) p--;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
  while (s > 0 && /[\p{L}\p{N}_]/u.test(a[a.length - s]) && (/[\p{L}\p{N}_]/u.test(a[a.length - s - 1] || "") || /[\p{L}\p{N}_]/u.test(b[b.length - s - 1] || ""))) s--;
  const midA = a.slice(p, a.length - s);
  const midB = b.slice(p, b.length - s);
  const ops = midA && midB ? myers(tokenize(midA), tokenize(midB)) : null;
  if (!ops) return [{ at: p, del: midA, ins: midB }];
  const hunks = [];
  let pos = p;
  let cur = null;
  let gap = "";
  for (const [op, tok] of ops) {
    if (op === "=") {
      if (cur) gap += tok;
      pos += tok.length;
      continue;
    }
    if (cur && gap) {
      if (gap.length <= merge) {
        cur.del += gap;
        cur.ins += gap;
      } else {
        hunks.push(cur);
        cur = null;
      }
      gap = "";
    }
    if (!cur) cur = { at: pos, del: "", ins: "" };
    if (op === "-") {
      cur.del += tok;
      pos += tok.length;
    } else cur.ins += tok;
  }
  if (cur) hunks.push(cur);
  return hunks;
}

// ---------- what an edit did, in a few words ----------
const words = (s) => (String(s).match(/[\p{L}\p{N}]+/gu) || []).length;

export function headingAt(text, pos) {
  const before = String(text).slice(0, Math.max(0, pos));
  const all = [...before.matchAll(/^#{1,6}[ \t]+(.+)$/gm)];
  return all.length ? all[all.length - 1][1].replace(/[#*_`$]/g, "").trim() : "";
}

// {label, section, from, to}: "First draft", "Added a figure", "Replaced 3 words", "Deleted a sentence"…
export function describeEdit(before, after, hunks = diffHunks(before, after)) {
  const del = hunks.map((h) => h.del).join(" ");
  const ins = hunks.map((h) => h.ins).join(" ");
  let label;
  if (!String(before).trim()) label = "First draft";
  else if (!hunks.length) label = "No change";
  else if (/!\[[^\]]*\]\([^)]+\)/.test(ins) && !/!\[/.test(del)) label = "Added a figure";
  else if (/\$\$/.test(ins) && !/\$\$/.test(del)) label = "Added an equation";
  else if (!del.trim()) label = /^\s*#{1,6}\s/m.test(ins) ? "Added a section" : `Added ${plural(words(ins), "word")}`;
  else if (!ins.trim()) label = /[.!?]\s*$/.test(del.trim()) && words(del) > 5 ? "Deleted a sentence" : `Deleted ${plural(words(del), "word")}`;
  else label = words(del) <= 4 && words(ins) <= 8 ? "Replaced" : `Rewrote ${plural(words(del), "word")}`;
  const first = hunks[0];
  const small = hunks.length === 1 && first.del.trim() && first.ins.trim() && words(first.del) <= 6 && words(first.ins) <= 10;
  return {
    label,
    section: first ? headingAt(after, first.at + Math.max(1, first.ins.length) - 1) || headingAt(after, first.at) : "",
    from: small ? truncate(first.del.trim().replace(/\s+/g, " "), 60) : "",
    to: small ? truncate(first.ins.trim().replace(/\s+/g, " "), 60) : "",
  };
}

function plural(n, one) {
  return `${n} ${n === 1 ? one : `${one}s`}`;
}

// ---------- the typist ----------
// Replays the states one edit at a time: for each hunk the caret goes there, the old words are selected, removed,
// and the new ones typed. Driven by step(ms) from the page's animation frame; seek(i) jumps to a state.
export const SPEEDS = [1, 2, 4, 8];
const CPS = 55; // characters per second at 1×
const TYPE_CAP = 4.5; // s: a long insert (a first draft, a new section) types faster rather than for a minute
const DEL_CAP = 0.9; // s: the most a deletion takes
const HOLD = 520; // ms the doomed words stay selected before they go
const BETWEEN = 420; // ms between two edits
const HUNK_GAP = 160; // ms between two hunks of one edit

export class Typist {
  constructor() {
    this.states = []; // texts: states[i] is the manuscript after edit i
    this.at = -1; // the last state fully shown (-1: the empty page)
    this.text = "";
    this.ops = [];
    this.caret = null;
    this.sel = null;
    this.speed = 1;
    this.playing = true;
    this.current = null; // {index, label, section, from, to} of the edit being typed
  }

  // New states (live: the stream grew). The part already shown stays as it is.
  setStates(texts) {
    const old = this.states;
    this.states = texts.slice();
    if (this.at >= this.states.length || (this.at >= 0 && old[this.at] !== this.states[this.at])) this.seek(this.states.length - 1);
  }

  get done() {
    return this.at >= this.states.length - 1 && !this.ops.length;
  }

  get busy() {
    return this.ops.length > 0;
  }

  // Jump to state i (-1: empty) with nothing in flight.
  seek(i) {
    this.at = Math.max(-1, Math.min(i, this.states.length - 1));
    this.text = this.at >= 0 ? this.states[this.at] : "";
    this.ops = [];
    this.caret = null;
    this.sel = null;
    this.current = null;
  }

  restart() {
    this.seek(-1);
    this.playing = true;
  }

  finish() {
    this.seek(this.states.length - 1);
  }

  // Micro-steps for the edit at → at+1.
  begin() {
    const i = this.at + 1;
    const before = this.text;
    const after = this.states[i];
    const hunks = diffHunks(before, after);
    this.current = { index: i, ...describeEdit(before, after, hunks) };
    const ops = [{ kind: "pause", ms: BETWEEN }];
    let shift = 0;
    hunks.forEach((h, j) => {
      const at = h.at + shift;
      if (j) ops.push({ kind: "pause", ms: HUNK_GAP, at });
      if (h.del) {
        ops.push({ kind: "select", from: at, to: at + h.del.length, ms: HOLD });
        ops.push({ kind: "delete", at, n: h.del.length, rate: Math.max(CPS * 4, h.del.length / DEL_CAP), done: 0 });
      }
      if (h.ins) ops.push({ kind: "type", at, str: h.ins, rate: Math.max(CPS, h.ins.length / TYPE_CAP), done: 0 });
      shift += h.ins.length - h.del.length;
    });
    ops.push({ kind: "end", index: i });
    this.ops = ops;
  }

  // Advance by ms of wall time (scaled by the speed). `boost` speeds up a live backlog.
  step(ms, { boost = 1 } = {}) {
    if (!this.playing) return this.snapshot();
    let budget = ms * this.speed * Math.max(1, boost);
    let guard = 0;
    while (budget > 0 && guard++ < 10_000) {
      if (!this.ops.length) {
        if (this.at >= this.states.length - 1) break;
        this.begin();
      }
      const op = this.ops[0];
      if (op.kind === "pause") {
        this.sel = null;
        if (op.at !== undefined) this.caret = op.at;
        const use = Math.min(budget, op.ms);
        op.ms -= use;
        budget -= use;
        if (op.ms <= 0) this.ops.shift();
      } else if (op.kind === "select") {
        this.caret = op.to;
        this.sel = [op.from, op.to];
        const use = Math.min(budget, op.ms);
        op.ms -= use;
        budget -= use;
        if (op.ms <= 0) this.ops.shift();
      } else if (op.kind === "delete") {
        const want = Math.min(op.n, op.done + Math.max(1, Math.floor(((budget / 1000) * op.rate) + 1e-9)));
        const k = want - op.done;
        const spent = (k / op.rate) * 1000;
        const end = op.at + (op.n - op.done);
        this.text = this.text.slice(0, end - k) + this.text.slice(end);
        op.done = want;
        this.caret = op.at + (op.n - op.done);
        this.sel = op.done < op.n ? [op.at, this.caret] : null;
        budget -= spent;
        if (op.done >= op.n) this.ops.shift();
      } else if (op.kind === "type") {
        this.sel = null;
        const want = Math.min(op.str.length, op.done + Math.max(1, Math.floor(((budget / 1000) * op.rate) + 1e-9)));
        const piece = op.str.slice(op.done, want);
        const pos = op.at + op.done;
        this.text = this.text.slice(0, pos) + piece + this.text.slice(pos);
        budget -= (piece.length / op.rate) * 1000;
        op.done = want;
        this.caret = op.at + op.done;
        if (op.done >= op.str.length) this.ops.shift();
      } else {
        // end of an edit: settle on the exact state (whatever rounding did on the way)
        this.at = op.index;
        this.text = this.states[op.index];
        this.ops.shift();
        this.sel = null;
        if (this.at >= this.states.length - 1) {
          this.caret = null;
          this.current = null;
        }
      }
    }
    return this.snapshot();
  }

  snapshot() {
    return { text: this.text, caret: this.caret, sel: this.sel, at: this.at, current: this.current, done: this.done };
  }
}

// The text with the caret and selection marks in it, for renderBlocks.
export function marked(text, caret = null, sel = null) {
  const t = String(text ?? "");
  const ins = [];
  if (sel && sel[1] > sel[0]) ins.push([sel[0], SEL_ON], [sel[1], SEL_OFF]);
  if (caret !== null && caret !== undefined && caret >= 0) ins.push([Math.min(caret, t.length), CARET]);
  if (!ins.length) return t;
  // right to left so earlier offsets stay valid; at one offset: the caret after the selection's end
  ins.sort((a, b) => b[0] - a[0] || (a[1] === CARET ? -1 : 1));
  let out = t;
  for (const [at, mark] of ins) out = out.slice(0, at) + mark + out.slice(at);
  return out;
}

// ---------- references and figures ----------
const REF_LINE = /^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)?\[(\d{1,3})\]\s+(.+?)\s*$/;

// The manuscript's own reference list: "[n] Authors, Title, Journal Year. doi:…" → Map "[n]" → {key, text, doi, url, year}.
export function parseReferences(text) {
  const src = clean(text);
  const head = src.search(/^#{1,6}\s+(references|bibliography|literature cited|works cited)\b/im);
  const body = head >= 0 ? src.slice(head) : src;
  const refs = new Map();
  for (const line of body.split("\n")) {
    const m = line.match(REF_LINE);
    if (!m || (head < 0 && !/10\.\d{4,9}\/|\b(19|20)\d{2}\b/.test(m[2]))) continue;
    const key = `[${Number(m[1])}]`;
    if (refs.has(key)) continue;
    const doi = (m[2].match(/10\.\d{4,9}\/[^\s,;<>"]+/) || [""])[0].replace(/[.)\]]+$/, "");
    const year = Number((m[2].match(/\b(19|20)\d{2}\b/) || [""])[0]) || null;
    refs.set(key, { key, text: m[2].replace(/\s*\b(?:doi:\s*|https?:\/\/(?:dx\.)?doi\.org\/)10\.\d{4,9}\/\S+/i, "").replace(/[\s.,;]+$/, ""), doi, year, url: safeUrl((m[2].match(/https?:\/\/[^\s<>"]+/) || [""])[0]) });
  }
  return refs;
}

// Each [n] → a source card: the manuscript's reference, filled in from the run's citations with the same DOI (title,
// authors, journal). With no reference list, the run's citations by their own keys (or in order).
export function resolveSources(refs, runCitations = []) {
  const cites = (runCitations || []).filter(isObj);
  const byDoi = new Map(cites.filter((c) => c.doi).map((c) => [String(c.doi).toLowerCase(), c]));
  const out = new Map();
  if (refs && refs.size) {
    for (const [key, r] of refs) {
      const c = r.doi ? byDoi.get(r.doi.toLowerCase()) : null;
      out.set(key, c ? { ...c, key, quote: "" } : { key, title: r.text || r.doi || key, doi: r.doi, year: r.year, url: r.url || (r.doi ? "" : "") });
    }
    return out;
  }
  const keys = cites.map((c) => String(c.key || ""));
  const clean = keys.every((k) => /^\[\d{1,3}\]$/.test(k)) && new Set(keys).size === keys.length;
  cites.forEach((c, i) => out.set(clean ? c.key : `[${i + 1}]`, { ...c, key: clean ? c.key : `[${i + 1}]` }));
  return out;
}

const IMAGE = /\.(png|jpe?g|gif|svg|webp)$/i;

// A figure's src → its path in the run folder, or null (external URLs and anything that leaves the run aren't loaded).
// Relative to the manuscript's folder, except when it already starts with that folder ("results/fig1.png" from
// results/manuscript.md): agents write paths both ways.
export function artifactPath(mdPath, src) {
  let s = String(src ?? "").trim().replace(/^<|>$/g, "");
  if (!s || /^[a-z][\w+.-]*:/i.test(s) || s.startsWith("//") || s.includes("\\")) return null;
  s = s.split(/[?#]/)[0];
  const dir = String(mdPath || "").split("/").filter(Boolean).slice(0, -1);
  const parts = s.split("/").filter((p) => p && p !== ".");
  const rooted = s.startsWith("/") || (dir.length && parts[0] === dir[0]);
  const out = rooted ? [] : dir.slice();
  for (const p of parts) {
    if (p === "..") {
      if (!out.length) return null;
      out.pop();
    } else out.push(p);
  }
  return out.length && IMAGE.test(out[out.length - 1]) ? out.join("/") : null;
}

export function artifactUrl(runId, path, version = 0) {
  if (!runId || !path) return "";
  return `/api/runs/${encodeURIComponent(runId)}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}${version ? `?v=${version}` : ""}`;
}

// ---------- Markdown ----------
// The subset a manuscript needs: headings, paragraphs, lists, block quotes, tables, code, rules, figures, display and
// inline math ($…$, $$…$$, \(…\), \[…\]), **bold**, *italic*, `code`, ~~strike~~, links, doi: links and [n] citations.
// Everything is escaped; only http(s) links and the run's own artifacts become URLs.

const PH_OPEN = "";
const PH_CLOSE = "";
const KATEX_CACHE = new Map();

function tex(katex, src, display) {
  const key = `${display ? 1 : 0}${src}`;
  if (KATEX_CACHE.has(key)) return KATEX_CACHE.get(key);
  let html;
  try {
    html = katex.renderToString(src, { displayMode: display, throwOnError: true, strict: "ignore", trust: false, output: "htmlAndMathml" });
  } catch {
    html = null; // half-typed or invalid: shown as source
  }
  if (KATEX_CACHE.size > 600) KATEX_CACHE.delete(KATEX_CACHE.keys().next().value);
  KATEX_CACHE.set(key, html);
  return html;
}

// Raw TeX while it's being typed (or when KaTeX hasn't loaded or can't parse it).
function mathSource(raw, display) {
  return `<span class="math-src${display ? " display" : ""}">${marksHTML(esc(raw))}</span>`;
}

function mathHTML(raw, inner, display, ctx) {
  if (hasMarks(raw) || !ctx.katex) return mathSource(raw, display);
  const html = tex(ctx.katex, inner.trim(), display);
  return html === null ? mathSource(raw, display) : display ? `<span class="math display">${html}</span>` : `<span class="math">${html}</span>`;
}

function marksHTML(s) {
  return s.replace(//g, '<span class="caret" aria-hidden="true"></span>').replace(//g, '<del class="sel">').replace(//g, "</del>");
}

function refButtons(list) {
  const nums = [];
  for (const part of list.split(/\s*,\s*/)) {
    const r = clean(part).match(/^(\d{1,3})\s*[–-]\s*(\d{1,3})$/);
    if (r && Number(r[2]) >= Number(r[1]) && Number(r[2]) - Number(r[1]) < 30) for (let n = Number(r[1]); n <= Number(r[2]); n++) nums.push(n);
    else if (/^\d{1,3}$/.test(clean(part))) nums.push(Number(clean(part)));
  }
  const marks = (list.match(MARKS) || []).join("");
  return `<span class="cites">${nums.map((n) => `<button type="button" class="ref" data-ref="[${n}]" aria-label="Source ${n}">${n}</button>`).join("")}${marksHTML(marks)}</span>`;
}

export function inlineHTML(src, ctx = {}) {
  const held = [];
  const hold = (html) => `${PH_OPEN}${held.push(html) - 1}${PH_CLOSE}`;
  let s = String(src ?? "");
  s = s.replace(/\\\$/g, () => hold("$"));
  s = s.replace(/`([^`\n]+)`/g, (m, code) => hold(`<code>${marksHTML(esc(code))}</code>`));
  s = s.replace(/\$\$([^$]+?)\$\$/g, (m, inner) => hold(mathHTML(m, clean(inner), true, ctx)));
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (m, inner) => hold(mathHTML(m, clean(inner), true, ctx)));
  s = s.replace(/\\\(([\s\S]+?)\\\)/g, (m, inner) => hold(mathHTML(m, clean(inner), false, ctx)));
  // $…$: no space just inside the dollars, and no digit right after the closing one ("$5 and $6" is money)
  s = s.replace(/\$(?![\s$])((?:[^$\n\\]|\\.)+?)(?<![\s\\])\$(?!\d)/g, (m, inner) => hold(mathHTML(m, clean(inner), false, ctx)));
  s = esc(s);
  s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (m, alt, href) => {
    const path = artifactPath(ctx.path, clean(href).replace(/&amp;/g, "&"));
    return path && ctx.runId ? hold(`<img class="inline-fig" src="${esc(artifactUrl(ctx.runId, path, ctx.version))}" alt="${esc(clean(alt))}" loading="lazy">`) : m;
  });
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, (m, label, href) => {
    const u = safeUrl(clean(href).replace(/&amp;/g, "&"));
    return u ? hold(`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${label}</a>`) : m;
  });
  s = s.replace(/\[((?:[-]*\d{1,3}[-]*)(?:\s*[,–-]\s*[-]*\d{1,3}[-]*)*)\](?!\()/g, (m, list) => hold(refButtons(list)));
  s = s.replace(/\b(?:doi:\s?|https?:\/\/(?:dx\.)?doi\.org\/)(10\.\d{4,9}\/[^\s<]*[^\s<.,;:)\]])/gi, (m, doi) => {
    const u = citationUrl({ doi: clean(doi) });
    return u ? hold(`<a class="doi" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${m}</a>`) : m;
  });
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g, (m, pre, u) => {
    const ok = safeUrl(clean(u).replace(/&amp;/g, "&"));
    return ok ? `${pre}${hold(`<a href="${esc(ok)}" target="_blank" rel="noopener noreferrer">${u}</a>`)}` : m;
  });
  s = s
    .replace(/\*\*(?=\S)([\s\S]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(?=\S)([\s\S]+?)__(?![\p{L}\p{N}])/gu, "<strong>$1</strong>")
    .replace(/~~(?=\S)([\s\S]+?)~~/g, "<s>$1</s>")
    .replace(/(^|[^\p{L}\p{N}*\\])\*(?![\s*])([^*\n]+?)\*(?![\p{L}\p{N}*])/gu, "$1<em>$2</em>")
    .replace(/(^|[^\p{L}\p{N}_\\])_(?![\s_])([^_\n]+?)_(?![\p{L}\p{N}_])/gu, "$1<em>$2</em>");
  for (let k = 0; k < 3 && s.includes(PH_OPEN); k++) s = s.replace(/(\d+)/g, (m, i) => held[Number(i)]);
  return marksHTML(s);
}

// ---------- blocks ----------
const LIST = /^(\s*)([-*+]|\d{1,3}[.)])\s+/;
const FENCE = /^\s*(```|~~~)\s*([\w+-]*)/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const FIGURE = /^\s*!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"([^"]*)")?\s*\)\s*$/;

// Removes the first n visible characters (a "## " or "- " prefix) and keeps the caret/selection marks among them.
function dropPrefix(line, n) {
  let out = "";
  let i = 0;
  let seen = 0;
  for (; i < line.length && seen < n; i++) {
    if (/[-]/.test(line[i])) out += line[i];
    else seen++;
  }
  return out + line.slice(i);
}

const startsBlock = (c) => /^#{1,6}\s/.test(c) || FENCE.test(c) || /^\s*\$\$/.test(c) || /^\s*\\\[/.test(c) || /^\s*>/.test(c) || LIST.test(c) || /^\s*([-*_])(\s*\1){2,}\s*$/.test(c);

// Lines → blocks {type, lines (with marks), …}. Blank lines separate blocks; one holding the caret becomes an empty
// paragraph so the caret shows where the next paragraph starts.
export function parseBlocks(text) {
  const lines = String(text ?? "").split("\n");
  const blocks = [];
  let i = 0;
  let pendingMarks = "";
  const push = (b) => {
    if (pendingMarks) {
      b.lines[0] = pendingMarks + b.lines[0];
      pendingMarks = "";
    }
    blocks.push(b);
  };
  while (i < lines.length) {
    const raw = lines[i];
    const c = clean(raw);
    if (!c.trim()) {
      if (raw.includes(CARET)) push({ type: "p", lines: [raw] });
      else pendingMarks += (raw.match(MARKS) || []).join("");
      i++;
      continue;
    }
    let m;
    if ((m = c.match(FENCE))) {
      const fence = m[1];
      const body = [];
      i++;
      while (i < lines.length && !clean(lines[i]).trim().startsWith(fence)) body.push(lines[i++]);
      const closed = i < lines.length;
      if (closed) i++;
      push({ type: "code", lang: m[2] || "", lines: [raw, ...body], body, closed });
      continue;
    }
    const tc = c.trim();
    if (tc.startsWith("$$") || tc.startsWith("\\[")) {
      const close = tc.startsWith("$$") ? "$$" : "\\]";
      const body = [raw];
      let closed = tc.length > 2 && tc.slice(2).includes(close);
      i++;
      while (!closed && i < lines.length) {
        body.push(lines[i]);
        if (clean(lines[i]).includes(close)) closed = true;
        i++;
      }
      push({ type: "math", lines: body, closed, close });
      continue;
    }
    if ((m = c.match(/^(#{1,6})\s+/))) {
      push({ type: "h", level: m[1].length, lines: [dropPrefix(raw, m[0].length)] });
      i++;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(c)) {
      push({ type: "hr", lines: [raw] });
      i++;
      continue;
    }
    if (c.includes("|") && i + 1 < lines.length && TABLE_SEP.test(clean(lines[i + 1]))) {
      const rows = [raw];
      const sep = lines[i + 1];
      i += 2;
      while (i < lines.length && clean(lines[i]).includes("|") && clean(lines[i]).trim()) rows.push(lines[i++]);
      push({ type: "table", lines: rows, sep });
      continue;
    }
    if (/^\s*>/.test(c)) {
      const body = [];
      while (i < lines.length && /^\s*>/.test(clean(lines[i]))) {
        const l = lines[i++];
        body.push(dropPrefix(l, clean(l).match(/^\s*>\s?/)[0].length));
      }
      push({ type: "quote", lines: body });
      continue;
    }
    if (LIST.test(c)) {
      const items = [];
      while (i < lines.length) {
        const l = lines[i];
        const lc = clean(l);
        if (!lc.trim()) break;
        const lm = lc.match(LIST);
        if (lm) items.push({ indent: lm[1].replace(/\t/g, "  ").length, ordered: /\d/.test(lm[2]), start: parseInt(lm[2], 10) || 1, text: dropPrefix(l, lm[0].length) });
        else if (/^\s{2,}/.test(lc) && items.length) items[items.length - 1].text += `\n${l.trim()}`;
        else break;
        i++;
      }
      push({ type: "list", items, lines: items.map((x) => x.text) });
      continue;
    }
    const body = [raw];
    i++;
    while (i < lines.length && clean(lines[i]).trim() && !startsBlock(clean(lines[i]))) body.push(lines[i++]);
    push({ type: "p", lines: body });
  }
  if (pendingMarks && blocks.length) blocks[blocks.length - 1].lines.push(pendingMarks);
  return blocks;
}

function listHTML(items, ctx) {
  // nesting by indentation: an item indented more than the one before starts a sublist inside it
  let html = "";
  const stack = [];
  for (const it of items) {
    while (stack.length && it.indent < stack[stack.length - 1].indent) html += `</li></${stack.pop().tag}>`;
    const top = stack[stack.length - 1];
    if (!top || it.indent > top.indent) {
      const tag = it.ordered ? "ol" : "ul";
      html += `<${tag}${it.ordered && it.start !== 1 ? ` start="${it.start}"` : ""}>`;
      stack.push({ indent: it.indent, tag });
    } else html += "</li>";
    html += `<li>${inlineHTML(it.text.replace(/\n/g, " "), ctx)}`;
  }
  while (stack.length) html += `</li></${stack.pop().tag}>`;
  return html;
}

function cells(line) {
  let l = line.trim();
  if (clean(l).startsWith("|")) l = l.replace(/^([-]*)\|/, "$1");
  if (clean(l).endsWith("|")) l = l.replace(/\|([-]*)$/, "$1");
  return l.split(/(?<!\\)\|/).map((x) => x.trim());
}

function tableHTML(b, ctx) {
  const align = cells(clean(b.sep)).map((s) => (/^:-+:$/.test(s) ? "center" : /-:$/.test(s) ? "right" : ""));
  const [head, ...rows] = b.lines;
  const td = (tag, x, j) => `<${tag}${align[j] ? ` style="text-align:${align[j]}"` : ""}>${inlineHTML(x, ctx)}</${tag}>`;
  return `<div class="ms-table"><table><thead><tr>${cells(head).map((x, j) => td("th", x, j)).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${cells(r).map((x, j) => td("td", x, j)).join("")}</tr>`)
    .join("")}</tbody></table></div>`;
}

// One block → {key, html, cls}. ctx: {katex, runId, path, version, figure (counter)}.
function blockHTML(b, ctx) {
  const joined = b.lines.join("\n");
  switch (b.type) {
    case "h": {
      const lvl = Math.min(6, b.level);
      return `<h${lvl}${lvl === 1 ? ' class="ms-title"' : ""}>${inlineHTML(joined, ctx)}</h${lvl}>`;
    }
    case "hr":
      return `<hr>${marksHTML(joined.replace(/[^-]/g, ""))}`;
    case "code":
      return `<pre class="ms-code"><code>${marksHTML(esc(b.body.join("\n")))}${marksHTML((b.lines[0].match(MARKS) || []).join(""))}</code></pre>`;
    case "math": {
      const raw = joined;
      if (!b.closed) return `<div class="ms-math">${mathSource(raw, true)}</div>`;
      const inner = clean(raw).trim().replace(/^\$\$|^\\\[/, "").replace(/\$\$$|\\\]$/, "");
      return `<div class="ms-math">${mathHTML(raw, inner, true, ctx)}</div>`;
    }
    case "table":
      return tableHTML(b, ctx);
    case "quote":
      return `<blockquote>${inlineHTML(joined.replace(/\n/g, " "), ctx)}</blockquote>`;
    case "list":
      return listHTML(b.items, ctx);
    default: {
      const fig = clean(joined).match(FIGURE);
      const path = fig ? artifactPath(ctx.path, fig[2]) : null;
      if (fig && path && ctx.runId) {
        const n = ++ctx.figure;
        const marks = (joined.match(MARKS) || []).join("");
        const caption = fig[1] || fig[3] || "";
        return `<figure class="ms-fig" data-path="${esc(path)}"><div class="ms-fig-frame"><img src="${esc(artifactUrl(ctx.runId, path, ctx.version))}" alt="${esc(caption)}" loading="lazy"></div><figcaption><b>Figure ${n}.</b> ${inlineHTML(caption, ctx)}${marksHTML(marks)}</figcaption></figure>`;
      }
      return `<p>${inlineHTML(joined.replace(/\n/g, " "), ctx)}</p>`;
    }
  }
}

// The manuscript (with caret/selection marks) → [{key, html}], one per block, so the page only redraws the blocks that
// changed while it types (figures keep their loaded images).
export function renderBlocks(text, ctx = {}) {
  const c = { katex: null, runId: "", path: "", version: 0, ...ctx, figure: 0 };
  // a selection that starts in one block and ends in another is closed and reopened at the block boundary
  let inSel = false;
  return parseBlocks(text).map((b) => {
    const src = b.lines.join("\n");
    const startedIn = inSel;
    for (const ch of src) {
      if (ch === SEL_ON) inSel = true;
      else if (ch === SEL_OFF) inSel = false;
    }
    if (startedIn && !src.includes(SEL_ON)) b.lines[0] = SEL_ON + b.lines[0];
    const whole = startedIn && !hasMarks(src);
    const html = blockHTML(b, c);
    return { key: `${b.type}${c.figure}|${c.version}|${src}${whole ? "|sel" : ""}`, html, cls: whole ? "sel-block" : "" };
  });
}

// A line for the toolbar: "1 840 words · 3 equations · 2 figures · 5 references".
export function manuscriptStats(text) {
  const t = clean(text);
  const prose = t.replace(/\$\$[\s\S]*?\$\$/g, " ").replace(/\$[^$\n]+\$/g, " x ");
  return {
    words: words(prose.replace(/!\[[^\]]*\]\([^)]*\)/g, " ")),
    equations: (t.match(/\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]/g) || []).length,
    figures: (t.match(/^\s*!\[[^\]]*\]\([^)]+\)\s*$/gm) || []).length,
    references: parseReferences(t).size,
  };
}
