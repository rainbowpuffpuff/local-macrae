// HTML for the page's repeated pieces: source cards, trace rows, chat messages. Strings only (no DOM), so
// `node --test` can check them (web/tests/render.test.js). Everything from the network goes through esc().
import { esc, eventMeta, citationUrl, citationMeta, citationShort, richText, truncate, formatOffset, statusState, plural } from "./core.js";
import { eventCostText } from "./live.js";

// ---------- icons ----------
const ICONS = {
  flask: '<path d="M9 3h6M10 3v6.2L4.8 18a2 2 0 0 0 1.7 3h11a2 2 0 0 0 1.7-3L14 9.2V3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M7.2 15h9.6" stroke="currentColor" stroke-width="1.7"/>',
  atom: '<circle cx="12" cy="12" r="1.8" fill="currentColor"/><ellipse cx="12" cy="12" rx="9" ry="3.6" fill="none" stroke="currentColor" stroke-width="1.5"/><ellipse cx="12" cy="12" rx="9" ry="3.6" fill="none" stroke="currentColor" stroke-width="1.5" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="9" ry="3.6" fill="none" stroke="currentColor" stroke-width="1.5" transform="rotate(-60 12 12)"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 0 6.5 21H20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M8 8h8M8 11.5h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  card: '<rect x="3" y="4" width="18" height="16" rx="3" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M7 9h10M7 12.5h10M7 16h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  calc: '<rect x="5" y="3" width="14" height="18" rx="3" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M8.5 7.5h7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><g fill="currentColor"><circle cx="9" cy="12" r="1.1"/><circle cx="12" cy="12" r="1.1"/><circle cx="15" cy="12" r="1.1"/><circle cx="9" cy="16" r="1.1"/><circle cx="12" cy="16" r="1.1"/><circle cx="15" cy="16" r="1.1"/></g>',
  water: '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M9 14.5a3 3 0 0 0 3 3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  chart: '<path d="M4 20h16M7 16v-4M12 16V7M17 16v-7" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
  molecule: '<circle cx="7" cy="8" r="2.6" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="17" cy="7" r="2.2" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="13" cy="17" r="3" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M9.4 9.3l2.4 5M9.6 7.8l5.2-.6M15.9 9l-1.6 5.3" stroke="currentColor" stroke-width="1.5"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
};
ICONS.science = ICONS.flask;
ICONS.compute = ICONS.calc;
ICONS.calculator = ICONS.calc;
ICONS.paper = ICONS.book;
ICONS.methods = ICONS.card;

export const taskIcon = (name, size = 18) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">${ICONS[String(name || "").toLowerCase()] || ICONS.spark}</svg>`;

// Jarvis's avatar: the orbit mark, small.
export const JARVIS_AVATAR =
  '<span class="avatar" aria-hidden="true"><svg viewBox="-16 -16 32 32" width="18" height="18"><g transform="rotate(-22)" fill="none"><ellipse rx="13" ry="6.4" class="av-o1"/><ellipse rx="7.6" ry="3.7" class="av-o2"/></g><circle r="2.6" class="av-core"/></svg></span>';

// ---------- sources ----------
// A full source card: title, authors, journal, year, page, quote, DOI link.
export function citeHTML(c, i = 0) {
  const href = citationUrl(c);
  const key = c.key || `[${i + 1}]`;
  const num = key.replace(/[[\]]/g, "");
  return `<div class="cite" data-key="${esc(key)}">
    <span class="cite-key">${esc(num)}</span>
    <span class="cite-body"><b class="cite-title">${esc(c.title || c.doi || "Untitled source")}</b>
      ${c.authors ? `<span class="cite-authors">${esc(truncate(Array.isArray(c.authors) ? c.authors.join("; ") : c.authors, 160))}</span>` : ""}
      <span class="cite-meta">${esc(citationMeta({ ...c, authors: "" }))}</span>
      ${c.quote ? `<q>${esc(truncate(c.quote, 240))}</q>` : ""}
      ${href ? `<a class="cite-link" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${c.doi ? `doi:${esc(c.doi)}` : "Open the paper"} ↗</a>` : c.doi ? `<span class="cite-link">doi:${esc(c.doi)}</span>` : ""}
    </span></div>`;
}

// The numbered chips under a Jarvis answer; the open ones show their card below.
export function sourcesHTML(msg) {
  const list = msg.citations || [];
  if (!list.length) return "";
  const open = new Set(msg.open || []);
  return `<div class="sources-row">
      <span class="sources-label">${esc(plural(list.length, "source"))}</span>
      ${list.map((c, i) => {
        const key = c.key || `[${i + 1}]`;
        return `<button type="button" class="src-chip${open.has(key) ? " on" : ""}" data-key="${esc(key)}" aria-expanded="${open.has(key)}" title="${esc(c.title || "")}"><span class="n">${esc(key.replace(/[[\]]/g, ""))}</span>${esc(citationShort(c))}</button>`;
      }).join("")}
      ${list.length > 1 ? `<button type="button" class="src-all linklike" data-all="${open.size === list.length ? "close" : "open"}">${open.size === list.length ? "Hide all" : "Show all"}</button>` : ""}
    </div>
    ${list.filter((c, i) => open.has(c.key || `[${i + 1}]`)).map((c) => citeHTML(c)).join("")}`;
}

// ---------- chat messages ----------
const MIC = '<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';

// run: {title, cls, label, plan?, cost?} for a message that links to a run (from the page's run state): the planner's
// decision and the cost so far show under the title once they are known.
export function runChipHTML(runId, run) {
  const st = run && run.cls ? run : { cls: "running", label: "Running" };
  const sub = [run && run.plan, run && run.cost].filter(Boolean).join(" · ");
  return `<a class="run-chip" href="/?run=${encodeURIComponent(runId)}" data-run="${esc(runId)}">
    <span class="dot ${esc(st.cls)}"></span><span class="rc-main"><span class="rc-title">${esc((run && run.title) || "Open the run")}</span>${sub ? `<span class="rc-sub">${esc(sub)}</span>` : ""}</span>
    <span class="state ${esc(st.cls)}">${esc(st.label)}</span><span class="rc-open">Trace →</span></a>`;
}

export function messageHTML(m, { runs = new Map() } = {}) {
  const id = esc(m.id || "");
  switch (m.role) {
    case "user":
      return `<div class="msg user" data-id="${id}"><div class="bubble">${esc(m.text)}</div>${m.via === "voice" ? `<span class="via">${MIC} spoken</span>` : ""}</div>`;
    case "jarvis": {
      const head = `<div class="speaker">${JARVIS_AVATAR}<b>Jarvis</b>${m.via === "voice" ? `<span class="via">${MIC} voice</span>` : ""}</div>`;
      if (m.pending) return `<div class="msg jarvis pending" data-id="${id}">${head}<div class="typing"><span></span><span></span><span></span><em>${esc(m.text || "Thinking…")}</em></div></div>`;
      // [n] becomes a button when this message has source n, or for any n while its sources haven't arrived yet.
      const keys = (m.citations || []).map((c, i) => c.key || `[${i + 1}]`);
      const body = richText(m.text || "", keys.length ? new Set(keys) : null);
      return `<div class="msg jarvis${m.bad ? " bad" : ""}" data-id="${id}">${head}<div class="text">${body}</div>
        ${m.runId ? runChipHTML(m.runId, runs.get(m.runId)) : ""}
        ${m.evoLink ? `<a class="run-chip evo-chip" href="/?view=evolution" data-open-tab="evolution"><span aria-hidden="true">📈</span><span class="rc-main"><span class="rc-title">Run over run</span><span class="rc-sub">time, cost and what it learned, per task</span></span><span class="rc-open">Evolution →</span></a>` : ""}
        ${m.citations && m.citations.length ? `<div class="sources">${sourcesHTML(m)}</div>` : ""}</div>`;
    }
    case "tool":
      return `<div class="tool-line${m.err ? " err" : ""}" data-id="${id}">${m.pending ? '<span class="spinner"></span>' : ""}${esc(m.text)}</div>`;
    case "note":
      return `<div class="note-line${m.bad ? " bad" : ""}" data-id="${id}">${esc(m.text)}</div>`;
    default:
      return "";
  }
}

// ---------- trace ----------
// Rows render visible: no opacity-0 entrance animation (an animation that never ran left the old timeline blank).
export function eventHTML(e, t0) {
  const known = eventMeta(e.type) !== eventMeta("status") || e.type === "status";
  const type = known ? e.type : "status";
  const m = eventMeta(type);
  // A plan event's reasons (or its decision as JSON) are on the plan card above the timeline; the row is the headline.
  const detail = type === "plan" ? "" : String(e.detail || "").trim();
  const prose = type === "think" || type === "result" || type === "plan";
  const small = [e.step, eventCostText(e, t0)].filter(Boolean).join(" · ");
  return `<li class="ev ev-${esc(type)}" data-seq="${esc(e.seq)}">
    <div class="ev-icon" aria-hidden="true">${m.icon}</div>
    <div class="ev-body">
      <div class="ev-top"><span class="ev-title"><span class="sr-only">${esc(m.label)}: </span>${esc(e.title || m.label)}</span><span class="ev-time">${esc(formatOffset(e.t, t0))}</span></div>
      ${small ? `<span class="ev-step">${esc(small)}</span>` : ""}
      ${detail ? `<div class="ev-detail${prose ? " text" : ""}">${prose ? richText(detail, new Set()) : esc(detail)}</div>` : ""}
      ${e.citation && typeof e.citation === "object" ? citeHTML(e.citation) : ""}
    </div></li>`;
}

export function stepHTML(s) {
  const ss = statusState(s.status);
  const bits = [s.kind, ss.label.toLowerCase()];
  if (s.reward !== null && s.reward !== undefined && s.reward !== "") bits.push(`reward ${Number(s.reward).toFixed(2).replace(/\.?0+$/, "") || 0}`);
  if (Number(s.attempt) > 1) bits.push(`attempt ${s.attempt}`);
  const tip = [s.account && `account ${s.account}`, s.error].filter(Boolean).join(" · ");
  return `<span class="step" title="${esc(tip)}"><span class="dot ${ss.cls}"></span>${esc(s.key)}<span class="sub">${esc(bits.filter(Boolean).join(" · "))}</span></span>`;
}
