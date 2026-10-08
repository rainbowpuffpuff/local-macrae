// Pure helpers for the page: no DOM, no network, so `node --test` can check them (web/tests/).
// Shapes follow CONTRACT.md: Task, Citation, Passage, RunSummary/Run, TraceEvent.

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- trace events ----------
export const EVENT_TYPES = {
  status: { icon: "•", label: "Status" },
  read: { icon: "📄", label: "Read" },
  search: { icon: "🔎", label: "Search" },
  calc: { icon: "🧮", label: "Calculation" },
  write: { icon: "✍️", label: "Wrote" },
  think: { icon: "💭", label: "Thinking" },
  cite: { icon: "🔖", label: "Cited" },
  result: { icon: "✅", label: "Result" },
  error: { icon: "⚠️", label: "Error" },
  plan: { icon: "🧭", label: "Plan" },
};

export function eventMeta(type) {
  return EVENT_TYPES[type] || EVENT_TYPES.status;
}

// Adds newly polled events to the ones we have: unique by seq, in seq order.
export function mergeEvents(existing, incoming) {
  const bySeq = new Map();
  for (const e of existing || []) if (e && Number.isFinite(Number(e.seq))) bySeq.set(Number(e.seq), e);
  let added = 0;
  for (const e of incoming || []) {
    if (!e || !Number.isFinite(Number(e.seq))) continue;
    if (!bySeq.has(Number(e.seq))) added++;
    bySeq.set(Number(e.seq), e);
  }
  const events = [...bySeq.values()].sort((a, b) => Number(a.seq) - Number(b.seq));
  const lastSeq = events.length ? Number(events[events.length - 1].seq) : null;
  return { events, lastSeq, added };
}

// How much the run did, for the counters above the timeline.
export function runStats(events) {
  const stats = { read: 0, search: 0, calc: 0, write: 0, error: 0, papers: 0 };
  const papers = new Set();
  for (const e of events || []) {
    if (e.type in stats) stats[e.type]++;
    if (e.citation && (e.type === "read" || e.type === "cite" || e.type === "search")) papers.add(paperKey(e.citation));
  }
  stats.papers = papers.size;
  return stats;
}

export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

// ---------- citations ----------
export function safeUrl(u) {
  if (typeof u !== "string") return "";
  const s = u.trim();
  return /^https?:\/\/[^\s"'<>]+$/i.test(s) ? s : "";
}

export function citationUrl(c) {
  if (!c) return "";
  const direct = safeUrl(c.url);
  if (direct) return direct;
  const doi = String(c.doi || "").trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i, "").replace(/^doi:/i, "");
  return /^10\.\d{4,9}\/\S+$/.test(doi) ? `https://doi.org/${doi.split("/").map(encodeURIComponent).join("/")}` : "";
}

// One paper (and page) is one source, however many times it shows up.
export function citationKey(c) {
  if (!c) return "";
  const id = String(c.doi || c.url || c.title || c.key || "").trim().toLowerCase();
  return `${id}#${c.page ?? ""}`;
}

export function dedupeCitations(list) {
  const seen = new Map();
  for (const c of list || []) {
    if (!c || typeof c !== "object") continue;
    const k = citationKey(c);
    if (k === "#") continue;
    if (!seen.has(k)) seen.set(k, c);
  }
  return [...seen.values()];
}

// The same paper, whatever the page: for "papers it used".
export function paperKey(c) {
  return c ? String(c.doi || c.url || c.title || c.key || "").trim().toLowerCase() : "";
}

// One card per paper, first mention wins.
export function collectCitations(events) {
  const seen = new Map();
  for (const e of events || []) {
    const c = e && e.citation;
    if (!c || typeof c !== "object") continue;
    const k = paperKey(c);
    if (k && !seen.has(k)) seen.set(k, c);
  }
  return [...seen.values()];
}

// "V. Košťál; P. Jungwirth; H. Martinez-Seara" → "Košťál et al."
export function shortAuthors(authors) {
  const list = (Array.isArray(authors) ? authors : String(authors || "").split(/;|\band\b/))
    .map((a) => String(a).trim())
    .filter(Boolean);
  if (!list.length) return "";
  const surname = (a) => {
    if (a.includes(",")) return a.split(",")[0].trim();
    const parts = a.split(/\s+/);
    return parts[parts.length - 1];
  };
  if (list.length === 1) return surname(list[0]);
  if (list.length === 2) return `${surname(list[0])} & ${surname(list[1])}`;
  return `${surname(list[0])} et al.`;
}

export function citationMeta(c) {
  const bits = [];
  const who = shortAuthors(c.authors);
  if (who) bits.push(who);
  if (c.journal) bits.push(c.journal);
  if (c.year && Number(c.year) > 1970) bits.push(String(c.year));
  if (c.page !== undefined && c.page !== null && c.page !== "") bits.push(`p. ${c.page}`);
  return bits.join(" · ");
}

// Parameters of the show_citations client tool. The LLM may send an object, an array, or JSON in a string.
export function normalizeCitations(params) {
  let v = params;
  for (let i = 0; i < 3 && typeof v === "string"; i++) {
    try { v = JSON.parse(v); } catch { return []; }
  }
  if (v && !Array.isArray(v) && typeof v === "object") {
    if ("citations" in v) return normalizeCitations(v.citations);
    if ("passages" in v) return normalizeCitations(v.passages);
    if (v.title || v.doi || v.url) v = [v];
    else return [];
  }
  if (!Array.isArray(v)) return [];
  return dedupeCitations(v.map((c) => (c && c.citation && typeof c.citation === "object" ? { ...c.citation, quote: c.citation.quote || c.text } : c)).filter((c) => c && typeof c === "object"));
}

// Parameters of the open_run client tool: {run_id}, {runId}, a bare id, or JSON in a string.
export function normalizeRunId(params) {
  let v = params;
  if (typeof v === "string") {
    const s = v.trim();
    if (s.startsWith("{")) {
      try { v = JSON.parse(s); } catch { return ""; }
    } else return /^[\w.:-]{1,200}$/.test(s) ? s : "";
  }
  if (v && typeof v === "object") return normalizeRunId(String(v.run_id ?? v.runId ?? v.id ?? ""));
  return "";
}

// ---------- text ----------
// Escaped text with **bold**, `code`, links, and [n] citation markers turned into buttons.
export function richText(s, refs = null) {
  const safe = esc(s).trim();
  const inline = (t) =>
    t
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g, (u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`)
      .replace(/\[(\d{1,3})\]/g, (m, n) =>
        !refs || refs.has(`[${n}]`) ? `<button type="button" class="ref" data-ref="[${n}]" aria-label="Source ${n}">${n}</button>` : m);
  return safe
    .split(/\n{2,}/)
    .filter((p) => p.trim())
    .map((p) => {
      const lines = p.split("\n");
      if (lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l))) {
        return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, ""))}</li>`).join("")}</ul>`;
      }
      return `<p>${inline(p).replace(/\n/g, "<br>")}</p>`;
    })
    .join("");
}

export function truncate(s, n) {
  const t = String(s ?? "");
  return t.length > n ? `${t.slice(0, Math.max(0, n - 1)).trimEnd()}…` : t;
}

// ---------- time ----------
export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(m % 60).padStart(2, "0")} min`;
}

export function formatOffset(t, t0) {
  if (!Number.isFinite(Number(t)) || !Number.isFinite(Number(t0))) return "";
  const d = Math.max(0, Number(t) - Number(t0));
  if (d < 60) return `+${d < 10 ? d.toFixed(1) : Math.round(d)} s`;
  const m = Math.floor(d / 60);
  return `+${m}:${String(Math.round(d % 60)).padStart(2, "0")}`;
}

export function relativeTime(ts, now = Date.now() / 1000) {
  const t = Number(ts);
  if (!Number.isFinite(t) || t <= 0) return "";
  const d = now - t;
  if (d < 45) return "just now";
  if (d < 3600) return `${Math.max(1, Math.round(d / 60))} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 172800) return "yesterday";
  return `${Math.round(d / 86400)} days ago`;
}

export function elapsed(run, now = Date.now() / 1000) {
  if (!run || !Number.isFinite(Number(run.started)) || !run.started) return null;
  const end = run.finished ? Number(run.finished) : now;
  return Math.max(0, end - Number(run.started));
}

// ---------- run status ----------
const STATES = {
  running: { cls: "running", label: "Running" },
  queued: { cls: "running", label: "Queued" },
  pending: { cls: "running", label: "Waiting" },
  ok: { cls: "ok", label: "Done" },
  done: { cls: "ok", label: "Done" },
  failed: { cls: "failed", label: "Failed" },
  error: { cls: "failed", label: "Failed" },
  cancelled: { cls: "cancelled", label: "Cancelled" },
  skipped: { cls: "cancelled", label: "Skipped" },
};

export function statusState(status) {
  const s = String(status || "").toLowerCase();
  return STATES[s] || { cls: "running", label: s ? s[0].toUpperCase() + s.slice(1) : "Starting" };
}

export function isFinal(status) {
  return ["ok", "done", "failed", "error", "cancelled"].includes(String(status || "").toLowerCase());
}

// ---------- tasks ----------
// Inputs to send with a start request: the form values, falling back to each input's default.
export function taskInputs(task, values = {}) {
  const out = {};
  for (const inp of (task && task.inputs) || []) {
    if (!inp || !inp.name) continue;
    const v = values[inp.name];
    const s = typeof v === "string" ? v.trim() : v;
    out[inp.name] = s === undefined || s === null || s === "" ? inp.default ?? "" : s;
  }
  return out;
}

// ---------- routing ----------
// The run view lives at /?run=<id> (and /?run=<id>&seq=<n> to point at one event, as the lessons' evidence links
// do); the Evolution panel at /?view=evolution. Any static server can serve the page.
export function parseRoute(search) {
  const q = new URLSearchParams(search || "");
  const run = normalizeRunId(q.get("run") || "");
  if (run) {
    const seq = q.get("seq");
    return seq !== null && /^\d{1,9}$/.test(seq) ? { view: "run", runId: run, seq: Number(seq) } : { view: "run", runId: run };
  }
  return q.get("view") === "evolution" ? { view: "evolution" } : { view: "home" };
}

export function routeHref(route) {
  if (route && route.view === "run" && route.runId) {
    const seq = route.seq !== undefined && route.seq !== null && /^\d{1,9}$/.test(String(route.seq)) ? `&seq=${route.seq}` : "";
    return `/?run=${encodeURIComponent(route.runId)}${seq}`;
  }
  return route && route.view === "evolution" ? "/?view=evolution" : "/";
}

// ---------- voice ----------
// The full payload of a server tool call, if the agent config sends it to the client.
export function parseToolResult(raw) {
  if (raw && typeof raw === "object") return raw;
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" ? v : null;
  } catch {
    return null;
  }
}

export const TOOL_LABELS = {
  search_papers: "🔎 Searched the papers",
  start_task: "▶️ Started a task",
  run_status: "⏱️ Checked the run",
  show_citations: "🔖 Showed sources",
  open_run: "🧭 Opened a run",
};

export function toolLabel(name) {
  return TOOL_LABELS[name] || `🛠️ ${String(name || "tool").replace(/_/g, " ")}`;
}

// Keeps the transcript bounded; merges a streamed agent message into the previous one with the same id.
export function pushTranscript(list, entry, max = 200) {
  const out = list.slice();
  const last = out[out.length - 1];
  if (entry.id && last && last.id === entry.id && last.role === entry.role) out[out.length - 1] = { ...last, ...entry };
  else out.push(entry);
  return out.length > max ? out.slice(out.length - max) : out;
}

// Health → the pill in the top bar.
// `seen` is what the page knows besides /api/health: `undefined` health means the first check hasn't answered yet.
// Any other /api call that succeeded (`reachable`) proves the backend is up, so a slow health check never leaves
// the pill on "Checking…" while runs and tasks are already loading (the run-methods-card.png bug).
export function healthLabel(h, { reachable = false } = {}) {
  if (h && h.ok) {
    const bits = ["Online"];
    if (Number.isFinite(Number(h.papers))) bits.push(plural(Number(h.papers), "paper"));
    if (h.modal) bits.push("Modal ready");
    return { cls: "live", text: bits.join(" · ") };
  }
  if (reachable) return { cls: "live", text: "Online" };
  if (h === undefined) return { cls: "", text: "Checking…" };
  return { cls: "err", text: "Agent offline" };
}

// ---------- run header ----------
// Run ids carry the task: mock `20261008181714-methods-card-1`, agent_runner `20261008-181714-methods-card-ab12`.
export function taskIdFromRunId(runId, tasks) {
  const id = String(runId || "");
  let best = "";
  for (const t of tasks || []) {
    const tid = t && String(t.id || "");
    if (tid && tid.length > best.length && new RegExp(`(^|[-_.])${tid.replace(/[^\w-]/g, "\\$&")}([-_.]|$)`).test(id)) best = tid;
  }
  return best;
}

// The outcome of a finished trace when state.json doesn't say (yet): an error that no result followed is a failure.
export function eventsOutcome(events) {
  let lastError = -1;
  let lastResult = -1;
  (events || []).forEach((e, i) => {
    if (e && e.type === "error") lastError = i;
    if (e && e.type === "result") lastResult = i;
  });
  return lastError > lastResult ? "failed" : "ok";
}

// Title and status for the run header, from whatever has arrived so far. The old page only filled these in from
// GET /api/runs/{id}, so while that call was slow or failing the header said "Loading the run…" and "Starting"
// even with a timeline full of events. Now the trace, the task list and what we knew when the run started
// (`hint`: {title, task_id}) all count, and `done` from the events endpoint settles the status.
export function runHeadline({ runId = "", info = null, events = [], tasks = [], hint = null, done = false, settled = false } = {}) {
  const evs = events || [];
  const taskById = (id) => (id ? (tasks || []).find((t) => t && t.id === id) : null);
  const taskId = (info && info.task_id) || (hint && hint.task_id) || taskIdFromRunId(runId, tasks);
  const task = taskById(taskId);
  let title = (info && info.title) || (hint && hint.title) || (task && task.title) || "";
  if (!title) title = evs.length || settled || info ? (taskId ? taskId.replace(/[-_]+/g, " ") : `Run ${runId}`) : "Opening the run…";
  let status = info && info.status ? String(info.status) : "";
  if (done && !isFinal(status)) status = eventsOutcome(evs);
  else if (!status && evs.length) status = "running";
  return { title, status, state: statusState(status), final: isFinal(status), taskId };
}

// ---------- chat ----------
// Sources for one Jarvis message: one per paper and page, numbered [1], [2]… unless the keys given are already
// a clean, unique set (the agent's show_citations keys match what it says out loud, so keep those).
export function numberCitations(list) {
  const uniq = dedupeCitations(list);
  const keys = uniq.map((c) => String(c.key || ""));
  const clean = keys.every((k) => /^\[\d{1,3}\]$/.test(k)) && new Set(keys).size === keys.length;
  return clean ? uniq : uniq.map((c, i) => ({ ...c, key: `[${i + 1}]` }));
}

// POST /api/search passages → a Jarvis answer: one bullet per passage, each ending in its [n].
export function passagesAnswer(passages, max = 4) {
  const list = (passages || []).filter((p) => p && p.text).slice(0, max);
  if (!list.length) return { text: "I couldn't find anything about that in the indexed papers. Try other words, or press the microphone and ask me.", citations: [] };
  const citations = [];
  const index = new Map();
  const lines = list.map((p) => {
    const c = p.citation && typeof p.citation === "object" ? p.citation : null;
    let ref = "";
    if (c) {
      const k = citationKey(c);
      if (!index.has(k)) {
        index.set(k, `[${citations.length + 1}]`);
        citations.push({ ...c, key: `[${citations.length + 1}]` });
      }
      ref = ` ${index.get(k)}`;
    }
    return `- ${truncate(String(p.text).replace(/\s+/g, " ").trim(), 300)}${ref}`;
  });
  const intro = `From the group's papers, the ${list.length === 1 ? "closest passage" : `${list.length} closest passages`}:`;
  return { text: `${intro}\n\n${lines.join("\n")}`, citations };
}

// Where sources the agent just showed belong: its latest message after the user's last turn (-1: none yet, so
// keep them for the next one).
export function citationTarget(thread) {
  for (let i = (thread || []).length - 1; i >= 0; i--) {
    const m = thread[i];
    if (m.role === "user") return -1;
    if (m.role === "jarvis" && !m.pending && !m.runId && !m.bad) return i;
  }
  return -1;
}

// "Košťál et al. 2026": the label on a collapsed source chip.
export function citationShort(c) {
  if (!c) return "";
  const who = shortAuthors(c.authors);
  const year = c.year && Number(c.year) > 1970 ? String(c.year) : "";
  return [who || truncate(c.title || c.doi || "Source", 28), year].filter(Boolean).join(" ");
}
