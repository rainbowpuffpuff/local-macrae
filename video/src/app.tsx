// Macrae web UI replica (tokens, type and markup from web/style.css, web/index.html and the templates in
// web/app.js; layout from ref/frontend/*.png at 1920x1080) + the scripted timeline: landing, talking to
// Jarvis, the answer with citations, clicking a task, the run trace filling in, done.
// Page coordinates are CSS px on a 1920x1080 viewport, the same scale as the screenshots.
import React from "react";
import { AbsoluteFill, Easing } from "remotion";
import { Cam, camAt, EMOJI, FONT, lerp, MONO, pop, ramp } from "./lib";

// ---------------- tokens (web/style.css, light theme) ----------------
const C = {
  bg: "#ffffff",
  surface: "#ffffff",
  surface2: "#f4f4f4",
  surface3: "#ebebeb",
  hover: "rgba(0, 0, 0, .05)",
  border: "#e6e6e6",
  borderStrong: "#d4d4d4",
  text: "#0d0d0d",
  text2: "#5d5d5d",
  text3: "#8f8f8f",
  send: "#0d0d0d",
  accent: "#1d9bf0",
  ok: "#16a34a",
  shadow: "0 1px 2px rgba(0, 0, 0, .04), 0 4px 16px rgba(0, 0, 0, .06)",
};
const accentA = (a: number) => `rgba(29, 155, 240, ${a})`; // color-mix(accent a%, transparent)
const okA = (a: number) => `rgba(22, 163, 74, ${a})`;

// ---------------- content ----------------
export const AGENT = "Jarvis"; // the app says "Agent"; the film uses the product's voice name (STORYBOARD decision 4)
const PAPERS = 479; // PLACEHOLDER: "papers" from GET /api/health (here: entries in data/group_publications.json)
const QUESTION = "Why does the group scale charges?";

type Cite = { key: string; title: string; meta: string; quote: string; doi: string };
// PLACEHOLDER quotes and pages: replace with `python -m rag search` output once both PDFs are indexed.
export const CITES: Cite[] = [
  {
    key: "[1]",
    title: "Charge Scaling Manifesto: A Way of Reconciling the Inherently Macroscopic and Microscopic Natures of Molecular Simulations",
    meta: "Kirby & Jungwirth · J. Phys. Chem. Lett. · 2019 · p. 2",
    quote: "Scaling ionic charges by about 0.75 accounts for electronic polarization in a mean-field way.",
    doi: "10.1021/acs.jpclett.9b02652",
  },
  {
    key: "[2]",
    title: "A practical guide to biologically relevant molecular simulations with charge scaling for electronic polarization",
    meta: "Duboué-Dijon et al. · J. Chem. Phys. · 2020 · p. 4",
    quote: "Charge-scaled ion models bring ion pairing and binding to biomolecules in line with experiment.",
    doi: "10.1063/5.0017775",
  },
];

// Jarvis's answer, broken where it wraps in the 896 px home column (strings, line breaks, [n] chips)
type Seg = string | { ref: number } | "\n";
const ANSWER: Seg[] = [
  "Scaled charges capture the electronic polarization that standard, non-polarizable",
  "\n",
  "force fields miss ",
  { ref: 1 },
  ". Scaling ion charges by about 0.75 brings ion pairing and binding in line",
  "\n",
  "with experiment ",
  { ref: 2 },
  ".",
];
const segLen = (s: Seg) => (typeof s === "string" ? (s === "\n" ? 0 : Array.from(s).length) : 1);
const ANSWER_LEN = ANSWER.reduce((n, s) => n + segLen(s), 0);
// stream fraction at which each line of the answer starts
const LINE_STARTS = ANSWER.reduce<{ n: number; starts: number[] }>(
  (acc, s) => (s === "\n" ? { n: acc.n, starts: [...acc.starts, acc.n / ANSWER_LEN] } : { n: acc.n + segLen(s), starts: acc.starts }),
  { n: 0, starts: [0] },
).starts;

type Task = { id: string; icon: "flask" | "atom"; title: string; subtitle: string; prompt: string; lines: number; label: string; value: string; select?: boolean };
// The owner's video titles (STORYBOARD 5); prompts from tasks/tasks.json, truncated to 220 like app.js
const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);
const TASKS: Task[] = [
  {
    id: "methods-card",
    icon: "flask",
    title: "Methods card for a paper",
    subtitle: "Reads one paper's passages and writes a methods summary where every claim is cited",
    prompt: truncate(
      "Pulls the passages of one group paper from the paper index, then an agent on Modal writes a methods card (system, simulation and experimental methods, analysis, limitations) in which every claim cites its source as [n].",
      220,
    ),
    lines: 5,
    label: "DOI",
    value: "10.1021/acs.jctc.5c02051",
  },
  {
    id: "small-calc",
    icon: "atom",
    title: "Small calculation on Modal",
    subtitle: "Runs a tiny real computation on Modal and explains it with citations",
    prompt: truncate(
      "An agent on Modal installs PySCF, scans the distance between an ion and a water molecule with DFT, computes the counterpoise-corrected binding energy, compares it with full and charge-scaled (ECC) point charges, and explains the result citing the group's papers.",
      220,
    ),
    lines: 6,
    label: "Ion",
    value: "Na+",
    select: true,
  },
];

// ---------------- the trace (shot 8) ----------------
// PLACEHOLDER rows from the storyboard: before the final render, run the task once and copy titles, details
// and the duration verbatim from GET /api/runs/{id}/events. Only `at` (video seconds) is ours.
type EvType = "status" | "search" | "read" | "think" | "calc" | "write" | "result";
type Row = { at: number; type: EvType; title: string; detail?: string; cite?: number };
export const RUN_ID = "20261008181727-small-calc-1";
export const FINISHED_S = 112; // "Finished in 1 min 52 s"
export const TRACE: Row[] = [
  { at: 17.55, type: "status", title: "Started on Modal · sandbox ready" },
  { at: 18.2, type: "search", title: "Searched the papers: Na⁺ water binding, charge scaling", detail: "6 passages" },
  { at: 18.75, type: "read", title: "Read Kirby & Jungwirth 2019 · p. 2", detail: `“${CITES[0].quote}”`, cite: 0 },
  { at: 19.35, type: "read", title: "Read Duboué-Dijon et al. 2020 · p. 4", detail: `“${CITES[1].quote}”`, cite: 1 },
  { at: 20.05, type: "think", title: "Scan the Na⁺–O distance with DFT, then compare full and scaled point charges." },
  { at: 20.9, type: "calc", title: "Ran Na⁺–water scan (PySCF, 12 points, 38 s)", detail: "minimum at r(Na–O) = 2.25 Å" },
  { at: 21.6, type: "calc", title: "Binding energy, counterpoise-corrected: −23.4 kcal/mol" },
  { at: 22.3, type: "calc", title: "Point charges: full −31.2 · scaled ×0.75 −23.9 kcal/mol" },
  { at: 23.0, type: "write", title: "Wrote result.json" },
  { at: 23.4, type: "write", title: "Wrote explanation.md (cites [1] [2])" },
  { at: 24.1, type: "result", title: "Check passed: result.json has numbers · reward 1" },
];
const RUN_T0 = TRACE[0].at;
const RUN_T1 = TRACE[TRACE.length - 1].at;
export const SPEED = FINISHED_S / (RUN_T1 - RUN_T0); // the run is shown sped up by this factor
const runSeconds = (t: number) => Math.max(0, Math.min(FINISHED_S, (t - RUN_T0) * SPEED));

// formatDuration / formatOffset from web/core.js
const formatDuration = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
};
const formatOffset = (d: number) => {
  if (d < 60) return `+${d < 10 ? d.toFixed(1) : Math.round(d)} s`;
  return `+${Math.floor(d / 60)}:${String(Math.round(d % 60)).padStart(2, "0")}`;
};

// ---------------- timeline (seconds) ----------------
export const T = {
  health: 5.9,
  cards: 6.1,
  cursorIn: 6.2,
  atTalk: 6.85,
  talkClick: 7.0,
  connecting: 7.05,
  live: 7.35,
  chipsOut: 7.5,
  ask: [7.6, 9.0] as const,
  tool: 9.1,
  speak: 9.8,
  stream: [9.8, 13.2] as const,
  sources: [13.3, 13.45] as const,
  flash: [13.6, 14.6] as const,
  speakEnd: 15.2,
  cursor2: 15.25,
  hover: 16.1,
  runClick: 16.6,
  cardZoom: [16.75, 17.05] as const,
  runIn: 17.05,
  done: 24.4,
  banner: 24.6,
  overview: [24.5, 25.4] as const,
};
const WORDS = QUESTION.split(" ");
const wordAt = (i: number) => lerp(T.ask[0], T.ask[1] - 0.12, i / (WORDS.length - 1));

// ---------------- landing layout (page px) ----------------
const MAIN = { x: 284, w: 896 };
const SIDE = { x: 1236, w: 400 };
const HERO_Y = 98;
const TALK_Y = 407.6; // h1 124.8 + 16, kicker 38.4 + 12, lead 86.4 + 32
const TR_Y = TALK_Y + 64 + 12 + 22.4 + 12; // transcript top (518)
const TASKS_Y = 149.2;
const taskH = (task: Task) => 207 + 21 * task.lines; // measured: 312 with a 5-line prompt
const card2Y = TASKS_Y + taskH(TASKS[0]) + 12;
const RUN_BTN = { x: SIDE.x + SIDE.w - 17 - 35, y: card2Y + taskH(TASKS[1]) - 17 - 18 }; // "Run it" centre
const TALK_PT = { x: 336, y: 444 };

// ---------------- run view layout (page px) ----------------
const RMAIN = { x: 284, w: 932 };
const RSIDE = { x: 1256, w: 380 };
const RV = { back: 86, title: 126.4, meta: 176.2, steps: 215.4, stats: 258.6, sources: 353 };
const GRID_CARD_H = 162.4; // measured: 2-line title and meta, 3-line quote
const SOURCES_H = 20 + 10 + GRID_CARD_H + 18;
const rowH = (r: Row) => 73.6 + (r.detail ? (r.type === "calc" || r.type === "write" ? 46 : 28.4) : 0);
const readRows = TRACE.filter((r) => r.type === "read");

// ---------------- small pieces ----------------
const Emo: React.FC<{ c: string; size?: number }> = ({ c, size }) => <span style={{ fontFamily: EMOJI, fontSize: size, fontStyle: "normal" }}>{c}</span>;

// "Na⁺": the superscript plus, drawn the same way in every font
const Txt: React.FC<{ s: string }> = ({ s }) => (
  <>
    {s.split(/(⁺)/).map((p, i) =>
      p === "⁺" ? (
        <span key={i} style={{ fontSize: "0.68em", verticalAlign: "0.48em", lineHeight: 0 }}>
          +
        </span>
      ) : (
        <React.Fragment key={i}>{p}</React.Fragment>
      ),
    )}
  </>
);

const Mic: React.FC<{ size?: number; stroke?: boolean }> = ({ size = 22, stroke }) => (
  <svg viewBox="0 0 24 24" width={size} height={size}>
    <rect x={9} y={3} width={6} height={11} rx={3} fill={stroke ? "none" : "currentColor"} stroke={stroke ? "currentColor" : "none"} strokeWidth={1.8} />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" />
  </svg>
);

const ICONS = {
  flask: (
    <>
      <path d="M9 3h6M10 3v6.2L4.8 18a2 2 0 0 0 1.7 3h11a2 2 0 0 0 1.7-3L14 9.2V3" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinejoin="round" />
      <path d="M7.2 15h9.6" stroke="currentColor" strokeWidth={1.7} />
    </>
  ),
  atom: (
    <>
      <circle cx={12} cy={12} r={1.8} fill="currentColor" />
      <ellipse cx={12} cy={12} rx={9} ry={3.6} fill="none" stroke="currentColor" strokeWidth={1.5} />
      <ellipse cx={12} cy={12} rx={9} ry={3.6} fill="none" stroke="currentColor" strokeWidth={1.5} transform="rotate(60 12 12)" />
      <ellipse cx={12} cy={12} rx={9} ry={3.6} fill="none" stroke="currentColor" strokeWidth={1.5} transform="rotate(-60 12 12)" />
    </>
  ),
};

const Spinner: React.FC<{ t: number }> = ({ t }) => (
  <span
    style={{
      display: "inline-block",
      width: 12,
      height: 12,
      borderRadius: "50%",
      border: `2px solid ${C.borderStrong}`,
      borderTopColor: C.accent,
      transform: `rotate(${(t / 0.8) * 360}deg)`,
      flex: "none",
      boxSizing: "border-box",
    }}
  />
);

// .thinking: text with a moving shimmer
const Thinking: React.FC<{ t: number; children: React.ReactNode }> = ({ t, children }) => (
  <span
    style={{
      display: "inline-block",
      background: `linear-gradient(90deg, ${C.text3} 0%, ${C.text} 50%, ${C.text3} 100%)`,
      backgroundSize: "200% 100%",
      backgroundPosition: `${100 - (((t / 1.3) % 1) * 200)}% 0`,
      WebkitBackgroundClip: "text",
      backgroundClip: "text",
      color: "transparent",
    }}
  >
    {children}
  </span>
);

// The bear brand mark from index.html; the accent wave pulses while talking
const BrandMark: React.FC<{ wave: number }> = ({ wave }) => (
  <svg viewBox="0 0 48 40" width={34} height={28}>
    <path d="M40 8c3 3 3 9 0 12M44 4c5 5 5 15 0 20" fill="none" stroke={C.accent} strokeWidth={2.4} strokeLinecap="round" opacity={wave} />
    <circle cx={20} cy={23} r={10} fill={C.text} />
    <circle cx={8.5} cy={12.5} r={5} fill={C.text} opacity={0.82} />
    <circle cx={31.5} cy={12.5} r={5} fill={C.text} opacity={0.82} />
    <circle cx={16.5} cy={21} r={1.6} fill={C.bg} />
    <circle cx={23.5} cy={21} r={1.6} fill={C.bg} />
  </svg>
);

// tin-can cursor: black arrow, white stroke, press ring
const Cursor: React.FC<{ x: number; y: number; down: number; opacity: number }> = ({ x, y, down, opacity }) => (
  <div style={{ position: "absolute", left: x - 3, top: y - 2, opacity, transform: `scale(${1 - 0.14 * down})`, transformOrigin: "3px 2px" }}>
    {down > 0 && <div style={{ position: "absolute", left: 3 - 16, top: 2 - 16, width: 32, height: 32, borderRadius: 32, background: "rgba(29,155,240,0.22)", transform: `scale(${0.5 + down})` }} />}
    <svg width={22} height={30} viewBox="0 0 22 30" style={{ filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.35))" }}>
      <path d="M2 2 L2 24 L7.5 18.5 L11 27 L14.5 25.5 L11 17.2 L18.5 17.2 Z" fill="#111" stroke="#fff" strokeWidth={1.6} strokeLinejoin="round" />
    </svg>
  </div>
);

// ---------------- top bar ----------------
const TopBar: React.FC<{ t: number; talking: boolean }> = ({ t, talking }) => {
  const online = t >= T.health;
  const p = pop(t, T.health, { damping: 14, stiffness: 220 });
  const wave = talking ? 1 - 0.7 * (0.5 - 0.5 * Math.cos(((t - T.live) / 1.2) * 2 * Math.PI)) : 1;
  return (
    <div style={{ position: "absolute", left: 0, top: 0, width: 1920, height: 58 }}>
      {/* the bar's background runs past the page edges so wide shots read as a wider window */}
      <div style={{ position: "absolute", left: -2000, right: -2000, top: 0, height: 58, background: "rgba(255,255,255,.88)", backdropFilter: "blur(10px)", borderBottom: `1px solid ${C.border}` }} />
      <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", gap: 10, padding: "0 14px 0 16px" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
          <BrandMark wave={wave} />
          <span style={{ fontSize: 18, fontWeight: 650, letterSpacing: "-.01em" }}>Macrae</span>
        </span>
        <span style={{ fontSize: 13, color: C.text3, whiteSpace: "nowrap" }}>Jungwirth group · IOCB Prague</span>
        <span style={{ flex: 1 }} />
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            height: 30,
            padding: "0 12px",
            borderRadius: 999,
            border: `1px solid ${C.border}`,
            fontSize: 13,
            color: C.text2,
            whiteSpace: "nowrap",
            transform: online ? `scale(${0.96 + 0.04 * p})` : undefined,
          }}
        >
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: online ? C.ok : C.text3, boxShadow: online ? `0 0 0 ${3 * (1 - p)}px ${okA(0.25)}` : undefined }} />
          <span>{online ? `Online · ${PAPERS} papers · Modal ready` : "Checking…"}</span>
        </span>
        <span style={{ display: "grid", placeItems: "center", width: 34, height: 34, borderRadius: 10, color: C.text2 }}>
          <svg viewBox="0 0 24 24" width={19} height={19}>
            <path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round" />
          </svg>
        </span>
      </div>
    </div>
  );
};

// ---------------- voice panel ----------------
type VoiceState = { state: "idle" | "connecting" | "live"; speaking: boolean; level: number; press: number };

// a fake mic level, timed to the words of the (silent) question, and to Jarvis's speech
const hash = (i: number) => {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};
const micLevel = (t: number) => {
  let v = 0;
  WORDS.forEach((_, i) => {
    const d = t - wordAt(i) + 0.04;
    if (d < 0 || d > 0.6) return;
    v += (0.55 + 0.45 * hash(i)) * (d < 0.05 ? d / 0.05 : Math.exp(-(d - 0.05) / 0.13));
  });
  return Math.min(1, v * (0.85 + 0.15 * Math.sin(t * 41)));
};
const SPEECH: [number, number][] = [
  [9.85, 13.3],
  [13.5, 15.1],
];
const speechLevel = (t: number) => {
  const on = SPEECH.some(([a, b]) => t > a && t < b) ? 1 : 0;
  return on * (0.25 + 0.5 * Math.abs(Math.sin(t * 11.3)) * (0.5 + 0.5 * Math.sin(t * 4.1 + 1)));
};

const voiceAt = (t: number): VoiceState => {
  const press = t >= 6.95 && t < 7.1 ? Math.sin(((t - 6.95) / 0.15) * Math.PI) : 0;
  if (t < T.connecting) return { state: "idle", speaking: false, level: 0, press };
  if (t < T.live) return { state: "connecting", speaking: false, level: 0, press };
  const speaking = t >= T.speak && t < T.speakEnd;
  return { state: "live", speaking, level: speaking ? speechLevel(t) : t < T.tool ? micLevel(t) : 0.04 * (1 + Math.sin(t * 3)), press };
};

const TalkRow: React.FC<{ t: number; v: VoiceState }> = ({ t, v }) => {
  const live = v.state === "live";
  const liveIn = ramp(t, T.live, T.live + 0.2);
  const pulse = 0.5 - 0.5 * Math.cos(((t - T.connecting) / 1) * 2 * Math.PI); // @keyframes pulse, 1 s
  const ring =
    v.state === "connecting"
      ? { opacity: 1 - 0.65 * pulse, scale: 1 + 0.18 * pulse }
      : live
        ? { opacity: 0.9, scale: 1 + v.level * 0.45 }
        : { opacity: 0, scale: 1 };
  const orbBg = live ? (v.speaking ? C.accent : accentA(0.16)) : "rgba(255,255,255,.16)";
  const orbFg = live ? (v.speaking ? "#fff" : C.accent) : "#fff";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          alignItems: "center",
          gap: 14,
          height: 64,
          padding: "0 22px 0 10px",
          borderRadius: 999,
          background: live ? C.surface : C.send,
          color: live ? C.text : "#fff",
          border: live ? `1px solid ${C.borderStrong}` : "1px solid transparent",
          boxShadow: C.shadow,
          transform: `scale(${1 - 0.03 * v.press})`,
          opacity: v.state === "connecting" ? 0.6 + 0.4 * (1 - liveIn) : 1,
        }}
      >
        <span style={{ position: "relative", display: "grid", placeItems: "center", flex: "none", width: 46, height: 46, borderRadius: "50%", background: orbBg, color: orbFg }}>
          <Mic />
          <span
            style={{
              position: "absolute",
              inset: -4,
              borderRadius: "50%",
              border: `2px solid ${C.accent}`,
              opacity: ring.opacity,
              transform: `scale(${ring.scale})`,
            }}
          />
        </span>
        <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.25, minWidth: 0 }}>
          <b style={{ fontSize: 17, fontWeight: 600 }}>{live ? "End conversation" : v.state === "connecting" ? "Connecting…" : "Talk to the agent"}</b>
          <span style={{ fontSize: 13, opacity: 0.7, whiteSpace: "nowrap" }}>{live ? "Tap to hang up" : v.state === "connecting" ? "Allow the microphone if asked" : "Voice, with citations"}</span>
        </span>
      </div>
      {live && (
        <span
          style={{
            display: "grid",
            placeItems: "center",
            width: 40,
            height: 40,
            borderRadius: "50%",
            border: `1px solid ${C.borderStrong}`,
            color: C.text2,
            opacity: liveIn,
            transform: `scale(${0.8 + 0.2 * liveIn})`,
          }}
        >
          <Mic size={18} stroke />
        </span>
      )}
    </div>
  );
};

const statusText = (v: VoiceState) =>
  v.state === "connecting"
    ? "Connecting…"
    : v.state === "live"
      ? v.speaking
        ? "Speaking…"
        : "Listening…"
      : "Uses your microphone. Ask about methods and results, or ask it to run a task.";

// ---------------- citation card (.cite) ----------------
const CiteCard: React.FC<{ c: Cite; flash?: number; grid?: boolean; style?: React.CSSProperties }> = ({ c, flash = 0, grid, style }) => (
  <div
    style={{
      display: "flex",
      gap: 10,
      alignItems: "flex-start",
      border: `1px solid ${flash > 0 ? `rgba(29,155,240,${0.35 + 0.65 * flash})` : C.border}`,
      boxShadow: flash > 0 ? `0 0 0 3px ${accentA(0.22 * flash)}` : undefined,
      borderRadius: 14,
      padding: "10px 12px",
      background: C.surface,
      fontSize: 13,
      lineHeight: 1.4,
      height: grid ? GRID_CARD_H : undefined,
      overflow: "hidden",
      ...style,
    }}
  >
    <span style={{ font: `600 12px ${MONO}`, color: C.accent, flex: "none", minWidth: 22, paddingTop: 1 }}>{c.key}</span>
    <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
      <b style={{ fontWeight: 600, color: C.text, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{c.title}</b>
      <span style={{ color: C.text3, fontSize: 12 }}>{c.meta}</span>
      <span style={{ color: C.text2, fontSize: 12, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>“{c.quote}”</span>
      <span style={{ font: `11px ${MONO}`, color: C.text3, overflowWrap: "anywhere" }}>doi:{c.doi}</span>
    </span>
  </div>
);

// ---------------- transcript ----------------
const RefChip: React.FC<{ n: number; s: number }> = ({ n, s }) => (
  <span
    style={{
      display: "inline-grid",
      placeItems: "center",
      minWidth: 18,
      height: 18,
      padding: "0 4px",
      margin: "0 1px",
      borderRadius: 6,
      background: accentA(0.14),
      color: C.accent,
      font: `600 11px/1 ${FONT}`,
      verticalAlign: 2,
      transform: `scale(${s})`,
    }}
  >
    {n}
  </span>
);

const AnswerText: React.FC<{ t: number; wrap: boolean }> = ({ t, wrap }) => {
  const shown = Math.round(ANSWER_LEN * ramp(t, T.stream[0], T.stream[1], Easing.linear));
  let used = 0;
  const out: React.ReactNode[] = [];
  ANSWER.forEach((s, i) => {
    if (used >= shown && s !== "\n") return;
    if (s === "\n") {
      if (!wrap && used < shown) out.push(<br key={i} />);
      else if (wrap) out.push(" ");
      return;
    }
    if (typeof s === "string") {
      const chars = Array.from(s);
      out.push(<React.Fragment key={i}>{chars.slice(0, shown - used).join("")}</React.Fragment>);
      used += chars.length;
    } else {
      const at = T.stream[0] + ((used + 1) / ANSWER_LEN) * (T.stream[1] - T.stream[0]);
      out.push(<RefChip key={i} n={s.ref} s={pop(t, at, { damping: 12, stiffness: 260 })} />);
      used += 1;
    }
  });
  return <>{out}</>;
};

// grows a block's height with a short ease so new lines push the transcript open instead of jumping
const Grow: React.FC<{ t: number; at: number; h?: number; dur?: number; children: React.ReactNode; slide?: number }> = ({ t, at, h, dur = 0.25, children, slide = 3 }) => {
  if (t < at) return null;
  if (h === undefined) return <div style={{ flex: "none" }}>{children}</div>; // settled (side column): natural height
  const g = ramp(t, at, at + dur, Easing.out(Easing.cubic));
  return (
    <div style={{ height: h * g, opacity: g, transform: `translateY(${-slide * (1 - g)}px)`, flex: "none" }}>
      <div style={{ height: h }}>{children}</div>
    </div>
  );
};

const USER_H = 43.6;
const TOOL_H = 20.8;
const AGENT_H = (lines: number) => 19.2 + 2 + 25.6 * lines;
const CITE_H = 93.7; // measured: one-line titles at this width
const SOURCES_LINE_H = 22.4 + 8 + CITE_H * 2 + 8;

const Transcript: React.FC<{ t: number; w: number; side?: boolean }> = ({ t, w, side }) => {
  const chips = side ? 0 : 1 - ramp(t, T.chipsOut, T.chipsOut + 0.12);
  const asked = WORDS.filter((_, i) => t >= wordAt(i)).join(" ");
  const toolDone = t >= T.speak;
  // home column: the answer has explicit line breaks; lines open as the stream reaches them
  const lines = LINE_STARTS.reduce((n, f) => n + ramp(t, lerp(T.stream[0], T.stream[1], f), lerp(T.stream[0], T.stream[1], f) + 0.15, Easing.out(Easing.cubic)), 0);
  const agentH = side ? undefined : AGENT_H(lines);
  const flash = t >= T.flash[0] && t < T.flash[1] ? Math.min(1, (t - T.flash[0]) / 0.12, (T.flash[1] - t) / 0.3) : 0;
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        border: `1px solid ${C.border}`,
        borderRadius: 20,
        background: C.surface,
        padding: 16,
        minHeight: 140,
        width: w,
        boxShadow: C.shadow,
      }}
    >
      {chips > 0 && (
        <div style={{ opacity: chips, height: chips > 0.01 ? undefined : 0 }}>
          <p style={{ margin: "0 0 8px", color: C.text3, fontSize: 14 }}>Try asking</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {["Ions at the air/water interface", "Why scaled charges (ECC)?", "Calcium binding methods"].map((c) => (
              <span
                key={c}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  minHeight: 36,
                  padding: "6px 14px",
                  borderRadius: 999,
                  border: `1px solid ${C.borderStrong}`,
                  fontSize: 14,
                  color: C.text,
                  lineHeight: 1.3,
                }}
              >
                {c}
              </span>
            ))}
          </div>
        </div>
      )}
      {chips <= 0 && (
        <>
          <Grow t={t} at={side ? 0 : T.ask[0]} h={side ? undefined : USER_H}>
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <div
                style={{
                  maxWidth: "88%",
                  background: C.surface2,
                  border: `1px solid ${C.border}`,
                  borderRadius: "20px 20px 6px 20px",
                  padding: "8px 14px",
                  whiteSpace: "pre-wrap",
                  height: USER_H,
                }}
              >
                {side ? QUESTION : asked}
                {!side && t < T.ask[1] + 0.1 && <span style={{ display: "inline-block", width: 2, height: 18, marginLeft: 2, verticalAlign: -3, background: C.accent, opacity: Math.floor(t * 4) % 2 ? 0.2 : 1 }} />}
              </div>
            </div>
          </Grow>
          <Grow t={t} at={side ? 0 : T.tool} h={side ? undefined : TOOL_H}>
            <div style={{ fontSize: 13, color: C.text3, display: "flex", alignItems: "center", gap: 6, height: TOOL_H, lineHeight: 1.6 }}>
              {toolDone ? (
                <>
                  <Emo c="🔎" /> Searched the papers · 6 passages
                </>
              ) : (
                <>
                  <Spinner t={t} />
                  Searching the papers…
                </>
              )}
            </div>
          </Grow>
          <Grow t={t} at={side ? 0 : T.speak} h={agentH} dur={0.18}>
            <div>
              <span style={{ display: "block", fontSize: 12, color: C.text3, marginBottom: 2, lineHeight: 1.6 }}>{AGENT}</span>
              <div style={{ whiteSpace: side ? "normal" : "nowrap", lineHeight: 1.6 }}>
                <AnswerText t={side ? 99 : t} wrap={!!side} />
              </div>
            </div>
          </Grow>
          <Grow t={t} at={side ? 0 : T.sources[0]} h={side ? undefined : SOURCES_LINE_H} dur={0.3}>
            <div>
              <span style={{ color: C.text3, fontSize: 14, lineHeight: 1.6 }}>Sources</span>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                {CITES.map((c, i) => {
                  const s = side ? 1 : pop(t, T.sources[i], { damping: 13, stiffness: 210 });
                  return (
                    <div key={c.key} style={{ opacity: Math.min(1, s * 1.3), transform: `translateY(${(1 - s) * 14}px) scale(${0.96 + 0.04 * s})`, transformOrigin: "50% 0" }}>
                      <CiteCard c={c} flash={i === 0 && !side ? flash : 0} />
                    </div>
                  );
                })}
              </div>
            </div>
          </Grow>
        </>
      )}
    </div>
  );
};

const AskBox: React.FC<{ live: boolean }> = ({ live }) => (
  <>
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        gap: 8,
        background: C.surface,
        border: `1px solid ${C.borderStrong}`,
        borderRadius: 26,
        padding: "8px 8px 8px 18px",
        boxShadow: C.shadow,
      }}
    >
      <div style={{ flex: 1, minWidth: 0, color: C.text3, lineHeight: 1.5, padding: "6px 0" }}>{live ? "Type to the agent" : "Or type a question"}</div>
      <span style={{ display: "grid", placeItems: "center", flex: "none", width: 36, height: 36, borderRadius: "50%", background: C.send, color: "#fff", opacity: 0.25 }}>
        <svg viewBox="0 0 24 24" width={18} height={18}>
          <path d="M12 19V5m0 0-6 6m6-6 6 6" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    </div>
    <p style={{ margin: "-4px 0 0", fontSize: 12, color: C.text3, paddingLeft: 18, lineHeight: 1.6 }}>
      {live ? "Typed messages go to the agent too." : "Typed questions search the papers directly. During a conversation they go to the agent."}
    </p>
  </>
);

const VoicePanel: React.FC<{ t: number; w: number; side?: boolean }> = ({ t, w, side }) => {
  const v = voiceAt(t);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, width: w }}>
      <TalkRow t={t} v={v} />
      <p style={{ margin: 0, fontSize: 14, color: C.text3, minHeight: "1.4em", lineHeight: 1.6 }}>{statusText(v)}</p>
      <Transcript t={t} w={w} side={side} />
      <AskBox live={v.state === "live"} />
    </div>
  );
};

// ---------------- landing ----------------
const Hero: React.FC = () => (
  <div style={{ position: "absolute", left: MAIN.x, top: HERO_Y, width: MAIN.w }}>
    <h1 style={{ fontSize: 60, lineHeight: 1.04, letterSpacing: "-.03em", margin: "0 0 16px", fontWeight: 650, whiteSpace: "nowrap" }}>
      Ask the Jungwirth group's
      <br />
      papers.
    </h1>
    <p style={{ fontSize: 24, fontWeight: 600, letterSpacing: "-.01em", margin: "0 0 12px", lineHeight: 1.6 }}>Talk to an agent that has read them, and watch it work.</p>
    <p style={{ fontSize: 18, color: C.text2, margin: "0 0 32px", lineHeight: 1.6, whiteSpace: "nowrap" }}>
      It answers from the group's publications and cites the paper behind every claim.
      <br />
      Pick a task and it does real work on Modal: you see each paper it opens and
      <br />
      every calculation it runs, live.
    </p>
  </div>
);

const TaskCard: React.FC<{ task: Task; hover: number; press: number }> = ({ task, hover, press }) => (
  <div
    style={{
      height: taskH(task),
      border: `1px solid ${hover > 0 ? `rgb(${Math.round(lerp(230, 190, hover))},${Math.round(lerp(230, 190, hover))},${Math.round(lerp(230, 190, hover))})` : C.border}`,
      borderRadius: 20,
      background: C.surface,
      boxShadow: hover > 0 ? `0 1px 2px rgba(0,0,0,.05), 0 ${4 + 10 * hover}px ${16 + 18 * hover}px rgba(0,0,0,${0.06 + 0.06 * hover})` : C.shadow,
      padding: 16,
      display: "flex",
      flexDirection: "column",
      gap: 10,
      transform: `translateY(${-3 * hover}px)`,
    }}
  >
    <div style={{ display: "flex", gap: 12, alignItems: "flex-start", height: 57.9 }}>
      <span style={{ display: "grid", placeItems: "center", width: 40, height: 40, borderRadius: 12, background: C.surface3, color: C.text, flex: "none" }}>
        <svg viewBox="0 0 24 24" width={20} height={20}>
          {ICONS[task.icon]}
        </svg>
      </span>
      <div>
        <b style={{ display: "block", fontSize: 16, lineHeight: 1.3, fontWeight: 700 }}>{task.title}</b>
        <span style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden", fontSize: 13, color: C.text3, lineHeight: 1.35, marginTop: 2 }}>
          {task.subtitle}
        </span>
      </div>
    </div>
    <p style={{ margin: 0, fontSize: 14, color: C.text2, lineHeight: 1.5, height: 21 * task.lines, display: "-webkit-box", WebkitLineClamp: task.lines, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
      {task.prompt}
    </p>
    <div>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: C.text3, lineHeight: 1.6 }}>
        {task.label}
        <span
          style={{
            height: 36,
            padding: "0 12px",
            borderRadius: 12,
            border: `1px solid ${C.borderStrong}`,
            background: C.surface2,
            color: C.text,
            font: `13px ${MONO}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          {task.value}
          {task.select && (
            <svg viewBox="0 0 24 24" width={14} height={14}>
              <path d="M6 9l6 6 6-6" fill="none" stroke={C.text2} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </span>
      </label>
      <div style={{ display: "flex", alignItems: "center", gap: 10, height: 36 }}>
        <span style={{ color: C.text3, fontSize: 12 }}>Runs on Modal · traced</span>
        <span
          style={{
            marginLeft: "auto",
            display: "inline-flex",
            alignItems: "center",
            height: 36,
            padding: "0 16px",
            borderRadius: 999,
            fontSize: 14,
            fontWeight: 500,
            background: C.send,
            color: "#fff",
            opacity: hover > 0 && press === 0 ? 0.88 + 0.12 * (1 - hover) : 1,
            transform: `scale(${1 - 0.03 * press})`,
          }}
        >
          Run it
        </span>
      </div>
    </div>
  </div>
);

const Skeleton: React.FC<{ t: number }> = ({ t }) => (
  <div
    style={{
      height: 150,
      borderRadius: 20,
      background: `linear-gradient(90deg, ${C.surface2}, ${C.surface3}, ${C.surface2})`,
      backgroundSize: "200% 100%",
      backgroundPosition: `${100 - (((t / 1.6) % 1) * 200)}% 0`,
    }}
  />
);

const TasksPanel: React.FC<{ t: number }> = ({ t }) => {
  const hover = ramp(t, T.hover, T.hover + 0.18, Easing.out(Easing.cubic));
  const press = t >= T.runClick && t < T.runClick + 0.15 ? Math.sin(((t - T.runClick) / 0.15) * Math.PI) : 0;
  return (
    <div style={{ position: "absolute", left: SIDE.x, top: 120, width: SIDE.w }}>
      <h3 style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: ".06em", color: C.text3, margin: "0 0 10px", fontWeight: 600, lineHeight: 1.6 }}>Tasks it can run</h3>
      <div style={{ position: "relative" }}>
        {TASKS.map((task, i) => {
          const s = pop(t, T.cards + i * 0.12, { damping: 15, stiffness: 200 });
          return (
            <React.Fragment key={task.id}>
              {s < 1 && (
                <div style={{ position: "absolute", left: 0, right: 0, top: i * 162, opacity: 1 - Math.min(1, s * 2) }}>
                  <Skeleton t={t} />
                </div>
              )}
              {s > 0 && (
                <div style={{ position: "absolute", left: 0, right: 0, top: i ? card2Y - TASKS_Y : 0, opacity: Math.min(1, s * 1.5), transform: `translateY(${(1 - s) * 12}px) scale(${0.97 + 0.03 * s})` }}>
                  <TaskCard task={task} hover={i === 1 ? hover : 0} press={i === 1 ? press : 0} />
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};

const Landing: React.FC<{ t: number }> = ({ t }) => (
  <>
    <Hero />
    <div style={{ position: "absolute", left: MAIN.x, top: TALK_Y }}>
      <VoicePanel t={t} w={MAIN.w} />
    </div>
    <TasksPanel t={t} />
  </>
);

// ---------------- run view ----------------
const runDone = (t: number) => t >= T.done;

const statsAt = (t: number) => {
  const seen = TRACE.filter((r) => t >= r.at);
  const n = (type: EvType) => seen.filter((r) => r.type === type).length;
  return [
    { k: "read", icon: "📄", label: "papers read", n: n("read"), bumpAt: [...seen].reverse().find((r) => r.type === "read")?.at },
    { k: "search", icon: "🔎", label: "searches", n: n("search"), bumpAt: [...seen].reverse().find((r) => r.type === "search")?.at },
    { k: "calc", icon: "🧮", label: "calculations", n: n("calc"), bumpAt: [...seen].reverse().find((r) => r.type === "calc")?.at },
    { k: "write", icon: "✍", label: "files written", n: n("write"), bumpAt: [...seen].reverse().find((r) => r.type === "write")?.at },
  ];
};

// @keyframes bump { 30% { color: accent; transform: translateY(-2px) } } over .5 s
const bumpK = (t: number, at?: number) => {
  if (at === undefined || t < at || t > at + 0.5) return 0;
  const u = (t - at) / 0.5;
  return u < 0.3 ? u / 0.3 : (1 - u) / 0.7;
};

const ICON_FOR: Record<EvType, string> = { status: "", search: "🔎", read: "📄", think: "💭", calc: "🧮", write: "✍", result: "✅" };

const EvRow: React.FC<{ r: Row; t: number }> = ({ r, t }) => {
  const g = ramp(t, r.at, r.at + 0.3, Easing.out(Easing.cubic));
  const status = r.type === "status";
  const mono = r.type === "calc" || r.type === "write";
  const accentRing = r.type === "calc" || r.type === "search" || r.type === "read";
  const result = r.type === "result";
  return (
    <div style={{ display: "grid", gridTemplateColumns: "36px minmax(0, 1fr)", gap: 12, height: rowH(r), opacity: g, transform: `translateY(${8 * (1 - g)}px)` }}>
      <div
        style={{
          position: "relative",
          zIndex: 1,
          display: "grid",
          placeItems: "center",
          width: status ? 14 : 36,
          height: status ? 14 : 36,
          margin: status ? 11 : 0,
          borderRadius: "50%",
          background: status ? C.surface3 : result ? "#e8f6ed" : C.surface,
          border: `1px solid ${result ? okA(0.5) : accentRing ? (r.type === "calc" ? "#7fb9e3" : "#94c0de") : C.borderStrong}`,
          boxShadow: r.type === "calc" ? `0 0 0 3px ${accentA(0.1)}` : undefined,
          fontSize: 16,
          lineHeight: 1,
        }}
      >
        {!status && <Emo c={ICON_FOR[r.type]} size={16} />}
      </div>
      <div style={{ minWidth: 0, paddingTop: 6 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <span style={{ fontSize: 15, fontWeight: status || r.type === "think" ? 500 : 600, color: status || r.type === "think" ? C.text2 : C.text, flex: 1, minWidth: 0, lineHeight: 1.6, whiteSpace: "nowrap" }}>
            <Txt s={r.title} />
          </span>
          <span style={{ font: `11px ${MONO}`, color: C.text3, whiteSpace: "nowrap" }}>{formatOffset(runSeconds(r.at))}</span>
        </div>
        <div style={{ height: 25.6, lineHeight: 1.6 }}>
          <span style={{ display: "inline-block", font: `11px ${MONO}`, color: C.text3, marginTop: 1 }}>calc[0]</span>
        </div>
        {r.detail &&
          (mono ? (
            <div style={{ margin: "6px 0 0", font: `12px/1.5 ${MONO}`, background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 12, padding: "10px 12px", color: C.text2 }}>
              <Txt s={r.detail} />
            </div>
          ) : (
            <div style={{ margin: "6px 0 0", fontSize: 14, color: C.text2, lineHeight: 1.6, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              <Txt s={r.detail} />
            </div>
          ))}
      </div>
    </div>
  );
};

// where things sit in the run view at time t (page px, before scrolling)
const sourcesGrow = (t: number) => ramp(t, readRows[0].at, readRows[0].at + 0.35, Easing.out(Easing.cubic));
const timelineY = (t: number) => RV.sources + SOURCES_H * sourcesGrow(t);
const rowTop = (t: number, i: number) => timelineY(t) + TRACE.slice(0, i).reduce((h, r) => h + rowH(r), 0);
const timelineH = TRACE.reduce((h, r) => h + rowH(r), 0);
const END_H = 58;

// follow-scroll: keep the newest row about 70% down the frame (camera z 1.25 on (750, 520))
const FOLLOW_Y = 520 - 540 / 1.25 + (0.7 * 1080) / 1.25;
const scrollFor = (t: number, i: number) => Math.max(0, rowTop(t, i) + rowH(TRACE[i]) / 2 - FOLLOW_Y);
// overview: everything from the counters to the end banner
const OVERVIEW_TOP = RV.stats - 14;
const overviewBottom = () => rowTop(99, TRACE.length) + 4 + END_H + 24;
export const OVERVIEW_Z = Math.min(1, 1080 / (overviewBottom() - OVERVIEW_TOP + 58));
const OVERVIEW_SCROLL = Math.max(0, OVERVIEW_TOP - 58);

const scrollY = (t: number) => {
  let s = 0;
  TRACE.forEach((r, i) => {
    if (t < r.at - 0.05) return;
    s = lerp(s, scrollFor(t, i), ramp(t, r.at - 0.05, r.at + 0.45));
  });
  return lerp(s, OVERVIEW_SCROLL, ramp(t, T.overview[0], T.overview[1]));
};

const StatTile: React.FC<{ icon: string; label: string; n: number; bump: number }> = ({ icon, label, n, bump }) => (
  <div style={{ border: `1px solid ${C.border}`, borderRadius: 16, padding: "10px 14px", background: C.surface, height: 76.4 }}>
    <b
      style={{
        display: "block",
        fontSize: 24,
        fontWeight: 650,
        letterSpacing: "-.02em",
        lineHeight: 1.2,
        fontVariantNumeric: "tabular-nums",
        color: bump > 0 ? `rgb(${Math.round(lerp(13, 29, bump))},${Math.round(lerp(13, 155, bump))},${Math.round(lerp(13, 240, bump))})` : C.text,
        transform: `translateY(${-2 * bump}px)`,
      }}
    >
      {n}
    </b>
    <span style={{ fontSize: 13, color: C.text3, lineHeight: 1.6 }}>
      <Emo c={icon} /> {label}
    </span>
  </div>
);

const RunMain: React.FC<{ t: number }> = ({ t }) => {
  const done = runDone(t);
  const started = t >= RUN_T0;
  const stats = statsAt(t);
  const doneP = pop(t, T.done, { damping: 14, stiffness: 220 });
  const banner = ramp(t, T.banner, T.banner + 0.35, Easing.out(Easing.cubic));
  const tlY = timelineY(t);
  const last = TRACE.filter((r) => t >= r.at).length;
  const workY = rowTop(t, last);
  return (
    <>
      <div style={{ position: "absolute", left: RMAIN.x - 4, top: RV.back, display: "inline-flex", alignItems: "center", gap: 4, fontSize: 14, color: C.text2, padding: "4px 8px 4px 4px", lineHeight: 1.6 }}>
        <svg viewBox="0 0 24 24" width={16} height={16}>
          <path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        All tasks
      </div>
      <h2 style={{ position: "absolute", left: RMAIN.x, top: RV.title, margin: 0, fontSize: 38, lineHeight: 1.1, letterSpacing: "-.02em", fontWeight: 650, whiteSpace: "nowrap" }}>Small calculation on Modal</h2>
      <div style={{ position: "absolute", left: RMAIN.x, top: RV.meta, display: "flex", alignItems: "center", gap: 12, color: C.text2, height: 25.2 }}>
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            padding: "3px 9px",
            borderRadius: 999,
            lineHeight: 1.6,
            whiteSpace: "nowrap",
            color: done ? C.ok : C.accent,
            background: done ? okA(0.14) : accentA(0.14),
            transform: done ? `scale(${0.9 + 0.1 * doneP})` : undefined,
          }}
        >
          {done ? "Done" : started ? "Running" : "Starting"}
        </span>
        <span style={{ font: `12px ${MONO}` }}>{started ? formatDuration(runSeconds(t)) : ""}</span>
        <span style={{ font: `12px ${MONO}`, color: C.text3 }}>{RUN_ID}</span>
      </div>
      <div style={{ position: "absolute", left: RMAIN.x, top: RV.steps, display: "flex", gap: 6 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            font: `12px/1.6 ${MONO}`,
            color: C.text2,
            padding: "4px 10px",
            borderRadius: 10,
            border: `1px solid ${C.border}`,
            background: C.surface2,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: done ? C.ok : started ? C.accent : C.text3,
              opacity: done || !started ? 1 : 1 - 0.7 * (0.5 - 0.5 * Math.cos(((t - RUN_T0) / 1.2) * 2 * Math.PI)),
              transform: done ? `scale(${1 + 0.5 * (1 - doneP)})` : undefined,
            }}
          />
          calc[0]
          <span style={{ color: C.text3 }}>{done ? "agent · done · reward 1" : started ? "agent · running" : "agent · waiting"}</span>
        </span>
      </div>
      <div style={{ position: "absolute", left: RMAIN.x, top: RV.stats, width: RMAIN.w, display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }}>
        {stats.map((s) => (
          <StatTile key={s.k} icon={s.icon} label={s.label} n={s.n} bump={bumpK(t, s.bumpAt)} />
        ))}
      </div>
      {/* Papers it used */}
      {t >= readRows[0].at && (
        <div style={{ position: "absolute", left: RMAIN.x, top: RV.sources, width: RMAIN.w, height: SOURCES_H * sourcesGrow(t), overflow: "hidden", opacity: sourcesGrow(t) }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, height: 20, marginBottom: 10, fontSize: 12, textTransform: "uppercase", letterSpacing: ".06em", color: C.text3, fontWeight: 600 }}>
            <span>Papers it used</span>
            <span style={{ minWidth: 20, height: 20, padding: "0 6px", borderRadius: 10, background: C.surface3, color: C.text, fontSize: 11, lineHeight: "20px", textAlign: "center", fontWeight: 600, letterSpacing: 0 }}>
              {readRows.filter((r) => t >= r.at).length}
            </span>
            <span style={{ color: C.text3 }}>▾</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
            {readRows.map((r, i) => {
              const s = pop(t, r.at + 0.05, { damping: 13, stiffness: 200 });
              if (s <= 0) return <div key={i} />;
              return (
                <div key={i} style={{ opacity: Math.min(1, s * 1.4), transform: `translateY(${(1 - s) * -16}px)` }}>
                  <CiteCard c={CITES[r.cite!]} grid />
                </div>
              );
            })}
          </div>
        </div>
      )}
      {/* timeline */}
      <div style={{ position: "absolute", left: RMAIN.x, top: tlY, width: RMAIN.w }}>
        {last > 0 && <div style={{ position: "absolute", left: 17, top: 8, width: 2, height: Math.max(0, rowTop(t, last) - tlY - 16), background: C.border }} />}
        {TRACE.map((r, i) =>
          t >= r.at ? (
            <div key={i} style={{ position: "absolute", left: 0, right: 0, top: rowTop(t, i) - tlY }}>
              <EvRow r={r} t={t} />
            </div>
          ) : null,
        )}
      </div>
      {!done && (
        <div style={{ position: "absolute", left: RMAIN.x + 11, top: workY - 6, display: "flex", alignItems: "center", gap: 10, color: C.text3, fontSize: 14, lineHeight: 1.6 }}>
          <Spinner t={t} />
          <Thinking t={t}>{started ? "Working…" : "Waiting for the first trace event…"}</Thinking>
        </div>
      )}
      {banner > 0 && (
        <div
          style={{
            position: "absolute",
            left: RMAIN.x,
            top: rowTop(t, TRACE.length) + 4,
            width: RMAIN.w,
            height: END_H,
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "14px 16px",
            borderRadius: 16,
            border: `1px solid ${okA(0.45)}`,
            background: okA(0.08),
            fontSize: 15,
            opacity: banner,
            transform: `translateY(${(1 - banner) * 14}px)`,
          }}
        >
          <Emo c="✅" />
          <span>
            <b style={{ fontWeight: 650 }}>Finished in {formatDuration(FINISHED_S)}.</b> 2 papers · 3 calculations
          </span>
          <span
            style={{
              marginLeft: "auto",
              display: "inline-flex",
              alignItems: "center",
              height: 28,
              padding: "0 12px",
              borderRadius: 999,
              fontSize: 12,
              fontWeight: 500,
              border: `1px solid ${C.borderStrong}`,
            }}
          >
            Back to tasks
          </span>
        </div>
      )}
    </>
  );
};

const RunView: React.FC<{ t: number }> = ({ t }) => {
  const s = scrollY(t);
  return (
    <>
      <div style={{ position: "absolute", left: 0, top: -s, width: 1920 }}>
        <RunMain t={t} />
      </div>
      {/* the voice panel moved into #side-voice-slot (sticky) */}
      <div style={{ position: "absolute", left: RSIDE.x, top: Math.max(86 - s, 78) }}>
        <VoicePanel t={t} w={RSIDE.w} side />
      </div>
    </>
  );
};

// ---------------- cameras ----------------
const CARD2 = { x: SIDE.x + SIDE.w / 2, y: card2Y + taskH(TASKS[1]) / 2 };
const LAND_CAM: Cam[] = [
  { t: 5.6, z: 1, x: 960, y: 540 },
  { t: 6.4, z: 1, x: 960, y: 540 },
  { t: 7.0, z: 1.22, x: 760, y: 470 },
  { t: 7.4, z: 1.22, x: 760, y: 470 },
  { t: 9.8, z: 1.45, x: 730, y: 560 },
  { t: 13.3, z: 1.45, x: 730, y: 640 },
  { t: 13.9, z: 1.6, x: 620, y: 728 }, // the cards (storyboard: (600, 760), adjusted to the measured layout)
  { t: 15.2, z: 1.6, x: 620, y: 728 },
  { t: 15.9, z: 1.3, x: 1180, y: 470 },
  { t: T.cardZoom[0], z: 1.3, x: 1180, y: 470 },
  { t: T.cardZoom[1], z: 2.2, x: CARD2.x, y: CARD2.y },
];
const RUN_CAM: Cam[] = [
  { t: T.runIn, z: 1, x: 960, y: 540 },
  { t: 17.9, z: 1.25, x: 750, y: 520 },
  { t: T.overview[0], z: 1.25, x: 750, y: 520 },
  { t: T.overview[1], z: OVERVIEW_Z, x: 960, y: 540 / OVERVIEW_Z },
];
export const appCam = (t: number) => (t < T.runIn ? camAt(t, LAND_CAM, t > T.cardZoom[0] ? Easing.in(Easing.cubic) : undefined) : camAt(t, RUN_CAM));

// ---------------- cursor path ----------------
type CK = { t: number; x: number; y: number };
const along = (t: number, keys: CK[]) => {
  if (t <= keys[0].t) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t <= b.t) {
      const e = ramp(t, a.t, b.t, Easing.inOut(Easing.quad));
      // a slight arc, like a hand moving a mouse
      const arc = Math.sin(e * Math.PI) * 0.08 * Math.hypot(b.x - a.x, b.y - a.y);
      return { t, x: lerp(a.x, b.x, e), y: lerp(a.y, b.y, e) + arc };
    }
  }
  return keys[keys.length - 1];
};
const CURSOR1: CK[] = [
  { t: T.cursorIn, x: 1430, y: 980 },
  { t: T.atTalk, x: TALK_PT.x, y: TALK_PT.y },
];
const CURSOR2: CK[] = [
  { t: T.cursor2, x: 1010, y: 700 },
  { t: 16.45, x: RUN_BTN.x + 2, y: RUN_BTN.y + 2 },
];
const CLICKS = [T.talkClick, T.runClick];

// ---------------- the app scene ----------------
export const AppScene: React.FC<{ t: number }> = ({ t }) => {
  const run = t >= T.runIn;
  const cam = appCam(t);
  const talking = t >= T.live;
  const first = t < 12;
  const cur = first ? along(t, CURSOR1) : along(t, CURSOR2);
  const curOpacity = first ? ramp(t, T.cursorIn, T.cursorIn + 0.12) * (1 - ramp(t, 7.5, 7.7)) : ramp(t, T.cursor2, T.cursor2 + 0.15) * (1 - ramp(t, T.cardZoom[0], T.cardZoom[0] + 0.12));
  const click = CLICKS.find((c) => t >= c && t < c + 0.2);
  const down = click === undefined ? 0 : Math.sin(((t - click) / 0.2) * Math.PI);
  const dip = Math.max(0, 1 - Math.abs(t - T.runIn) / 0.06);
  const badge = ramp(t, 17.5, 17.8) * (1 - ramp(t, 24.45, 24.75));
  return (
    <AbsoluteFill style={{ fontFamily: FONT, color: C.text, fontSize: 16, lineHeight: 1.6, background: C.bg, overflow: "hidden", WebkitFontSmoothing: "antialiased" }}>
      <div style={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0", transform: `translate(960px, 540px) scale(${cam.z}) translate(${-cam.x}px, ${-cam.y}px)` }}>
        <div style={{ position: "absolute", left: -2000, top: -1000, width: 5920, height: 5000, background: C.bg }} />
        {run ? <RunView t={t} /> : <Landing t={t} />}
        <TopBar t={t} talking={talking} />
        {!run && curOpacity > 0 && <Cursor x={cur.x} y={cur.y} down={down} opacity={curOpacity} />}
      </div>
      {dip > 0 && <AbsoluteFill style={{ background: "#fff", opacity: dip }} />}
      {badge > 0 && (
        <div
          style={{
            position: "absolute",
            right: 44,
            bottom: 40,
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 22px 12px 18px",
            borderRadius: 999,
            background: "rgba(13,13,13,.86)",
            color: "#fff",
            fontSize: 26,
            fontWeight: 600,
            letterSpacing: "-.01em",
            opacity: badge,
            transform: `translateY(${(1 - badge) * 10}px)`,
            boxShadow: "0 8px 30px rgba(0,0,0,.2)",
          }}
        >
          <svg viewBox="0 0 28 16" width={34} height={19}>
            <path d="M1 1l12 7-12 7zM14 1l12 7-12 7z" fill="#fff" />
          </svg>
          sped up {Math.round(SPEED)}×
        </div>
      )}
    </AbsoluteFill>
  );
};
