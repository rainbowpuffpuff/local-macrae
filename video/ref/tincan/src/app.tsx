// Grok Bot desktop UI replica (colours sampled from the real app) + the scripted
// Marketplace -> Create group -> group chat interactions.
import React from "react";
import { AbsoluteFill, Easing, Img, interpolate, staticFile } from "remotion";
import { Cam, camAt, FONT, lerp, pop, ramp, typed } from "./lib";
import { BlobBot, Can, Defs, Level0 } from "./world";

export const W = 1280; // window size in "app points"
export const H = 800;
const SIDEBAR = 280;
const BASE = 1.2; // app points -> video pixels at zoom 1

const C = {
  sidebar: "#111111",
  chat: "#070707",
  bubble: "#262626",
  mine: "#5a5a5a",
  selected: "#313131",
  composer: "#2f2f2f",
  round: "#3c3c3c",
  btn: "#181818",
  border: "#2f2f2f",
  text: "#fcfcfc",
  sub: "#ababab",
  placeholder: "#6d6d6d",
  modal: "#181818",
  field: "#292929",
  add: "#222222",
  desc: "#a0a0a0",
  link: "#6aa6ff",
  purple: "#8b5cf6",
};

// ---------------- timeline (seconds) ----------------
export const T = {
  type1: [0.45, 1.05] as const, // "TinCan" in marketplace search
  results: 1.12,
  addClick: 2.4,
  toGroup: 2.8,
  type2: [3.25, 3.95] as const, // "TinCan team"
  checks: [4.12, 4.38, 4.64, 4.9],
  createClick: 5.45,
  whiteIn: 5.72, // tin-can scene covers 6.0 - 8.0
  chatIn: 7.95,
  compose: [14.2, 14.95] as const,
  send: 15.15,
  playClick: 17.55,
  cardZoom: [17.68, 19.05] as const,
};

type Who = "carol" | "carolsGrok" | "albina" | "albinasGrok";
type Msg = { from: Who | "me"; at: number; typing?: number; text?: string; thumbs?: boolean; video?: boolean };
export const MSGS: Msg[] = [
  { from: "carolsGrok", at: 8.55, typing: 8.2, text: "Group chats merged. Ready for the demo." },
  { from: "carol", at: 9.9, text: "@Albina's Grok, can you make the video?" },
  { from: "albinasGrok", at: 11.4, typing: 10.95, text: "On it. @Carol's Grok, send screenshots?" },
  { from: "carolsGrok", at: 12.9, typing: 12.45, text: "Sent 6 from Carol's Mac.", thumbs: true },
  { from: "me", at: 15.2, text: "Ship it 🚀" },
  { from: "albinasGrok", at: 16.85, typing: 15.85, video: true },
];

// ---------------- camera ----------------
const CAM: Cam[] = [
  { t: 0, z: 1.02, x: 640, y: 400 },
  { t: 0.55, z: 1.16, x: 640, y: 372 },
  { t: 1.15, z: 1.16, x: 640, y: 372 },
  { t: 2.0, z: 1.34, x: 760, y: 300 },
  { t: 2.75, z: 1.34, x: 760, y: 300 },
  { t: 3.3, z: 1.36, x: 640, y: 402 },
  { t: 5.25, z: 1.36, x: 640, y: 402 },
  { t: 6.0, z: 2.1, x: 640, y: 590 },
  // chat
  { t: 7.95, z: 1.0, x: 640, y: 400 },
  { t: 8.7, z: 1.6, x: 780, y: 520 },
  { t: 16.95, z: 1.6, x: 780, y: 520 },
  { t: 17.5, z: 1.8, x: 600, y: 590 },
];

// ---------------- small pieces ----------------
const Icon: React.FC<{ d: string; size?: number; color?: string; sw?: number; fill?: string }> = ({ d, size = 18, color = "#d6d6d6", sw = 1.8, fill = "none" }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);
const I = {
  panel: "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM9 5v14",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5",
  plus: "M12 5v14M5 12h14",
  share: "M12 3v12M7.5 7.5 12 3l4.5 4.5M5 13v6a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-6",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM6 11a6 6 0 0 0 12 0M12 17v4",
  wave: "M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4",
  cloud: "M12 12v7m-3-3 3 3 3-3M20 16.6A4.5 4.5 0 0 0 17.5 8h-1.3A7 7 0 1 0 4 14.9",
  x: "M6 6l12 12M18 6 6 18",
  chev: "M9 6l6 6-6 6",
  up: "M12 19V5M6 11l6-6 6 6",
  check: "M5 12.5 10 17.5 19 7",
  play: "M8 5v14l11-7z",
};

const BotAvatar: React.FC<{ shape: "circle" | "hex" | "pill" | "blob"; color: string; size: number }> = ({ shape, color, size }) => {
  const hex = Array.from({ length: 6 }, (_, i) => {
    const a = ((-90 + 60 * i) * Math.PI) / 180;
    return `${32 + 26 * Math.cos(a)},${32 + 26 * Math.sin(a)}`;
  }).join(" ");
  const eyes = shape === "pill" ? [40, 50] : [37, 47];
  return (
    <svg width={size} height={size} viewBox="0 0 64 64">
      {shape === "circle" && <circle cx={32} cy={32} r={30} fill={color} />}
      {shape === "hex" && <polygon points={hex} fill={color} stroke={color} strokeWidth={9} strokeLinejoin="round" />}
      {shape === "pill" && <rect x={1} y={13} width={62} height={38} rx={19} fill={color} />}
      {shape === "blob" && <path d="M34 6C50 7 61 18 60 34 59 51 45 59 30 58 13 57 3 46 4 31 5 16 18 5 34 6Z" fill={color} />}
      {eyes.map((x) => (
        <ellipse key={x} cx={x} cy={29} rx={2.8} ry={5} fill="#171717" transform={`rotate(10 ${x} 29)`} />
      ))}
    </svg>
  );
};
const Letter: React.FC<{ l: string; bg: string; size: number }> = ({ l, bg, size }) => (
  <div style={{ width: size, height: size, borderRadius: size, background: bg, color: "#fff", fontWeight: 600, fontSize: size * 0.46, display: "flex", alignItems: "center", justifyContent: "center" }}>
    {l}
  </div>
);

export const PEOPLE: Record<Who, { name: string; kind: string; avatar: (s: number) => React.ReactNode }> = {
  carol: { name: "Carol", kind: "Human", avatar: (s) => <Letter l="C" bg="#c49a6c" size={s} /> },
  carolsGrok: { name: "Carol's Grok", kind: "Bot", avatar: (s) => <BotAvatar shape="hex" color="#d98f4e" size={s} /> },
  albina: { name: "Albina", kind: "Human", avatar: (s) => <Letter l="A" bg={C.purple} size={s} /> },
  albinasGrok: { name: "Albina's Grok", kind: "Bot", avatar: (s) => <BotAvatar shape="circle" color="#ffffff" size={s} /> },
};

const Cursor: React.FC<{ x: number; y: number; down: number; opacity: number }> = ({ x, y, down, opacity }) => (
  <div style={{ position: "absolute", left: x - 3, top: y - 2, opacity, transform: `scale(${1 - 0.14 * down})`, transformOrigin: "3px 2px" }}>
    {down > 0 && <div style={{ position: "absolute", left: 3 - 16, top: 2 - 16, width: 32, height: 32, borderRadius: 32, background: "rgba(255,255,255,0.28)", transform: `scale(${0.5 + down})` }} />}
    <svg width={22} height={30} viewBox="0 0 22 30" style={{ filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.45))" }}>
      <path d="M2 2 L2 24 L7.5 18.5 L11 27 L14.5 25.5 L11 17.2 L18.5 17.2 Z" fill="#111" stroke="#fff" strokeWidth={1.6} strokeLinejoin="round" />
    </svg>
  </div>
);

// ---------------- sidebar ----------------
const ROWS: { name: string; sub: string; av: React.ReactNode; dot?: boolean }[] = [
  { name: "New Bot", sub: "Draft: weekly update", av: <BotAvatar shape="hex" color="#8a6440" size={32} /> },
  { name: "New Bot", sub: "Summarised 12 pull requests", av: <BotAvatar shape="pill" color="#7b7b7b" size={32} />, dot: true },
  { name: "New Bot", sub: "Stopped. Nothing else was running.", av: <BotAvatar shape="blob" color="#4a9e63" size={32} /> },
  { name: "Grok Bot", sub: "Stopped. There wasn’t a timed routine…", av: <BotAvatar shape="circle" color="#ffffff" size={32} /> },
  { name: "Chief of Staff", sub: "Draft: What can you", av: <BotAvatar shape="hex" color="#ee7a3a" size={32} /> },
];

const GroupAvatar = ({ size = 32 }) => (
  <div style={{ position: "relative", width: size, height: size }}>
    <div style={{ position: "absolute", left: 0, top: 0 }}>
      <Letter l="C" bg="#c49a6c" size={size * 0.66} />
    </div>
    <div style={{ position: "absolute", right: 0, bottom: 0, borderRadius: size, boxShadow: `0 0 0 2px ${C.selected}` }}>
      <Letter l="A" bg={C.purple} size={size * 0.66} />
    </div>
  </div>
);

const Sidebar: React.FC<{ t: number; group: boolean; preview: string }> = ({ t, group, preview }) => {
  const rows: typeof ROWS = group ? [{ name: "TinCan team", sub: preview, av: <GroupAvatar /> }, ...ROWS] : ROWS;
  const selected = group ? 0 : 3;
  return (
    <div style={{ position: "absolute", left: 0, top: 0, width: SIDEBAR, height: H, background: C.sidebar }}>
      <div style={{ position: "absolute", left: 17, top: 28, display: "flex", gap: 9 }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
          <div key={c} style={{ width: 13, height: 13, borderRadius: 13, background: c }} />
        ))}
      </div>
      <div style={{ position: "absolute", left: 150, top: 24, display: "flex", alignItems: "center", gap: 8 }}>
        <Icon d={I.panel} size={19} color="#9a9a9a" />
        {[I.search, I.plus].map((d) => (
          <div key={d} style={{ width: 36, height: 36, borderRadius: 36, background: C.btn, border: `1px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Icon d={d} size={18} color="#e8e8e8" />
          </div>
        ))}
      </div>
      <div style={{ position: "absolute", left: 12, right: 12, top: 78 }}>
        {rows.map((r, i) => (
          <div
            key={r.name + i}
            style={{
              height: 58,
              borderRadius: 10,
              background: i === selected ? C.selected : "transparent",
              display: "flex",
              alignItems: "center",
              padding: "0 10px",
              gap: 11,
              opacity: group && i === 0 ? pop(t, T.chatIn) : 1,
            }}
          >
            <div style={{ width: 32, display: "flex", justifyContent: "center" }}>{r.av}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: C.text, fontSize: 14.5, fontWeight: 500, lineHeight: "19px" }}>{r.name}</div>
              <div style={{ color: C.sub, fontSize: 13.5, lineHeight: "19px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.sub}</div>
            </div>
            {r.dot && <div style={{ width: 8, height: 8, borderRadius: 8, background: "#5e9df7" }} />}
          </div>
        ))}
      </div>
      <div style={{ position: "absolute", left: 16, right: 16, bottom: 16, display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 36, height: 36, borderRadius: 36, background: "#232323", border: `1px solid ${C.border}`, color: "#d0d0d0", fontSize: 13, display: "flex", alignItems: "center", justifyContent: "center" }}>AN</div>
        <div style={{ flex: 1, height: 36, borderRadius: 36, background: C.btn, border: `1px solid ${C.border}`, color: C.text, fontSize: 14.5, display: "flex", alignItems: "center", justifyContent: "center" }}>Marketplace</div>
        <div style={{ width: 36, height: 36, borderRadius: 36, background: "#fafafa", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon d={I.cloud} size={18} color="#111" sw={2} />
        </div>
      </div>
    </div>
  );
};

// ---------------- chat pane ----------------
const CHAT_X = SIDEBAR + 16; // column left
const CHAT_R = W - 16; // column right
const STACK_BOTTOM = H - 16 - 44 - 12; // bottom of the last message
const HEADER_H = 78;

const HeaderPill: React.FC<{ group: boolean }> = ({ group }) => (
  <div style={{ position: "absolute", left: SIDEBAR, right: 0, top: 23, display: "flex", justifyContent: "center" }}>
    <div style={{ height: 40, borderRadius: 40, background: C.btn, display: "flex", alignItems: "center", gap: 9, padding: group ? "0 16px 0 8px" : "0 18px 0 8px" }}>
      {group ? (
        <div style={{ display: "flex" }}>
          {(["carol", "carolsGrok", "albina", "albinasGrok"] as Who[]).map((w, i) => (
            <div key={w} style={{ marginLeft: i ? -6 : 0, borderRadius: 30, boxShadow: `0 0 0 2px ${C.btn}` }}>
              {PEOPLE[w].avatar(24)}
            </div>
          ))}
        </div>
      ) : (
        <BotAvatar shape="circle" color="#fff" size={26} />
      )}
      <span style={{ color: C.text, fontSize: 15.5, fontWeight: 500 }}>{group ? "TinCan team" : "Grok Bot"}</span>
      {group && <span style={{ color: C.sub, fontSize: 13.5 }}>4</span>}
    </div>
  </div>
);

const Bubble: React.FC<{ children: React.ReactNode; mine?: boolean }> = ({ children, mine }) => (
  <div
    style={{
      display: "inline-flex",
      alignItems: "center",
      height: 40,
      padding: "0 15px",
      borderRadius: 20,
      background: mine ? C.mine : C.bubble,
      color: C.text,
      fontSize: 15.5,
      whiteSpace: "pre",
    }}
  >
    <span>{children}</span>
  </div>
);

// "@Name" mentions in link blue
const Rich: React.FC<{ text: string }> = ({ text }) => (
  <>
    {text.split(/(@[A-Z][a-z]+'s Grok)/).map((part, i) =>
      part.startsWith("@") ? (
        <span key={i} style={{ color: C.link, fontWeight: 500 }}>
          {part}
        </span>
      ) : (
        <span key={i}>{part}</span>
      ),
    )}
  </>
);

const Dots: React.FC<{ t: number }> = ({ t }) => (
  <div style={{ display: "flex", gap: 5 }}>
    {[0, 1, 2].map((i) => (
      <div key={i} style={{ width: 7, height: 7, borderRadius: 7, background: "#9a9a9a", opacity: 0.35 + 0.65 * Math.max(0, Math.sin(t * 9 - i * 0.9)) }} />
    ))}
  </div>
);

const CARD = { w: 320, h: 180 };
const NAME_H = 23;
const GAP = 13;
const fullHeight = (m: Msg) => (m.from === "me" ? 40 : NAME_H + (m.video ? CARD.h : 40 + (m.thumbs ? 64 : 0)));

const VideoCard: React.FC<{ t: number }> = ({ t }) => {
  const chrome = 1 - ramp(t, T.playClick + 0.02, T.playClick + 0.2);
  return (
    <div style={{ width: CARD.w, height: CARD.h, borderRadius: 14 * chrome, overflow: "hidden", position: "relative", background: "#fff" }}>
      <div style={{ width: 1920, height: 1080, transform: `scale(${CARD.w / 1920})`, transformOrigin: "0 0" }}>
        <Level0 t={t} />
      </div>
      <div style={{ position: "absolute", inset: 0, opacity: chrome }}>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 44, background: "linear-gradient(transparent, rgba(0,0,0,0.6))" }} />
        <div style={{ position: "absolute", left: 12, bottom: 9, color: "#fff", fontSize: 13, fontWeight: 500 }}>tincan-demo.mp4</div>
        <div style={{ position: "absolute", right: 12, bottom: 9, color: "#fff", fontSize: 13 }}>0:30</div>
        <div style={{ position: "absolute", left: CARD.w / 2 - 26, top: CARD.h / 2 - 30, width: 52, height: 52, borderRadius: 52, background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon d={I.play} size={22} color="#fff" fill="#fff" sw={1} />
        </div>
      </div>
    </div>
  );
};

const THUMBS = [1, 2, 4, 5, 6, 3];
const MessageView: React.FC<{ m: Msg; t: number }> = ({ m, t }) => {
  if (m.from === "me")
    return (
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Bubble mine>{m.text}</Bubble>
      </div>
    );
  const p = PEOPLE[m.from];
  const typing = m.typing !== undefined && t < m.at;
  const show = pop(t, m.at);
  return (
    <div style={{ display: "flex", gap: 12 }}>
      <div style={{ paddingTop: 2 }}>{p.avatar(28)}</div>
      <div>
        <div style={{ height: 18, marginBottom: 5, fontSize: 13, color: "#8e8e8e", lineHeight: "18px" }}>
          <span style={{ color: "#e6e6e6", fontWeight: 600 }}>{p.name}</span> · {p.kind}
        </div>
        {typing ? (
          <Bubble>
            <Dots t={t} />
          </Bubble>
        ) : m.video ? (
          <div style={{ transform: `scale(${0.9 + 0.1 * show})`, transformOrigin: "0 0", opacity: show }}>
            <VideoCard t={t} />
          </div>
        ) : (
          <>
            <Bubble>
              <Rich text={m.text!} />
            </Bubble>
            {m.thumbs && (
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                {THUMBS.map((n, i) => {
                  const s = pop(t, m.at + 0.08 + i * 0.06);
                  return (
                    <Img
                      key={n}
                      src={staticFile(`img/shot${n}.png`)}
                      style={{ width: 88, height: 56, borderRadius: 8, objectFit: "cover", opacity: s, transform: `translateY(${(1 - s) * 10}px)`, border: "1px solid #333" }}
                    />
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

const GroupChat: React.FC<{ t: number }> = ({ t }) => {
  // bottom-anchored stack: each slot grows with a spring, pushing older messages up
  const slots = MSGS.map((m) => {
    const start = m.typing ?? m.at;
    const a = pop(t, start);
    const typingH = NAME_H + 40;
    const h = m.typing !== undefined ? lerp(typingH, fullHeight(m), pop(t, m.at)) : fullHeight(m);
    return a * (h + GAP);
  });
  let bottom = STACK_BOTTOM + GAP;
  const tops = slots.map(() => 0);
  for (let i = MSGS.length - 1; i >= 0; i--) {
    bottom -= slots[i];
    tops[i] = bottom;
  }
  return (
    <div style={{ position: "absolute", left: CHAT_X, width: CHAT_R - CHAT_X, top: HEADER_H, height: STACK_BOTTOM + 4 - HEADER_H, overflow: "hidden" }}>
      <div style={{ position: "absolute", left: 0, right: 0, top: tops[0] - HEADER_H - 44, textAlign: "center", color: "#6f6f6f", fontSize: 13, opacity: pop(t, T.chatIn + 0.2) }}>
        You created <b style={{ color: "#9a9a9a" }}>TinCan team</b> with Carol, Carol's Grok and Albina's Grok
      </div>
      {MSGS.map((m, i) => {
        const start = m.typing ?? m.at;
        if (t < start) return null;
        return (
          <div key={i} style={{ position: "absolute", left: 0, right: 0, top: tops[i] - HEADER_H, opacity: Math.min(1, pop(t, start) * 1.4) }}>
            <MessageView m={m} t={t} />
          </div>
        );
      })}
    </div>
  );
};

// the video card's rect in window points once everything has settled
export const CARD_RECT = (() => {
  const top = STACK_BOTTOM - fullHeight(MSGS[MSGS.length - 1]) + NAME_H;
  const left = CHAT_X + 28 + 12;
  return { x: left, y: top, w: CARD.w, h: CARD.h, cx: left + CARD.w / 2, cy: top + CARD.h / 2 };
})();

const OneToOne = () => (
  <div style={{ position: "absolute", left: CHAT_X, right: 16, top: HEADER_H, bottom: 80, display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: 10 }}>
    <div>
      <Bubble>Posting that to Carol now.</Bubble>
    </div>
    <div>
      <Bubble>
        Posted to Carol: “I love Carol” (<span style={{ color: C.link }}>issue #2</span>).
      </Bubble>
    </div>
    <div style={{ textAlign: "center", color: "#7d7d7d", fontSize: 13, margin: "14px 0" }}>Fri, Sep 18 2:46 PM</div>
    <div style={{ display: "flex", justifyContent: "flex-end" }}>
      <Bubble mine>stop doing the routine</Bubble>
    </div>
    <div>
      <Bubble>Stopping that now. Checking what’s still set to run.</Bubble>
    </div>
    <div>
      <Bubble>Stopped. There wasn’t a timed routine on the calendar.</Bubble>
    </div>
  </div>
);

const Composer: React.FC<{ t: number; group: boolean }> = ({ t, group }) => {
  const text = group && t < T.send ? typed("Ship it 🚀", t, ...T.compose) : "";
  return (
    <div
      style={{
        position: "absolute",
        left: CHAT_X,
        right: 16,
        bottom: 16,
        height: 44,
        borderRadius: 22,
        background: C.composer,
        border: "1px solid #3a3a3a",
        display: "flex",
        alignItems: "center",
        padding: "0 7px",
        gap: 10,
      }}
    >
      <div style={{ width: 30, height: 30, borderRadius: 30, background: C.round, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon d={I.plus} size={17} color="#d6d6d6" />
      </div>
      <div style={{ flex: 1, fontSize: 15.5, color: text ? C.text : C.placeholder, display: "flex", alignItems: "center" }}>
        {text || (group ? "Message TinCan team" : "Message Grok Bot")}
        {text && Math.floor(t * 2.2) % 2 === 0 && <span style={{ width: 1.5, height: 19, background: "#fff", marginLeft: 1 }} />}
      </div>
      <div style={{ width: 30, height: 30, borderRadius: 30, background: C.round, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon d={I.mic} size={16} color="#d6d6d6" />
      </div>
      <div style={{ width: 30, height: 30, borderRadius: 30, background: "#fafafa", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon d={text ? I.up : I.wave} size={17} color="#111" sw={2.2} />
      </div>
    </div>
  );
};

// ---------------- modals ----------------
const MK = { x: 240, y: 60, w: 800, h: 680 };
const CG = { x: 410, y: 128, w: 460, h: 540 };
export const ADD_BTN = { x: MK.x + MK.w - 35 - 16 - 30, y: MK.y + 160 + 36 };
const CHECK = (i: number) => ({ x: CG.x + CG.w - 28 - 11, y: CG.y + 190 + 26 + i * 58 });
const CREATE_BTN = { x: CG.x + CG.w / 2, y: CG.y + 452 + 23 };

const PluginRow: React.FC<{ icon: string; name: string; desc: string; by?: string }> = ({ icon, name, desc, by }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, height: 66 }}>
    <Img src={staticFile(`img/${icon}.png`)} style={{ width: 40, height: 40, borderRadius: 10 }} />
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ color: C.text, fontSize: 15.5, fontWeight: 500 }}>
        {name}
        {by && <span style={{ color: C.desc, fontWeight: 400, fontSize: 14 }}> {by}</span>}
      </div>
      <div style={{ color: C.desc, fontSize: 14.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{desc}</div>
    </div>
    <div style={{ background: C.add, color: C.text, fontSize: 14.5, fontWeight: 500, padding: "8px 15px", borderRadius: 20 }}>Add</div>
  </div>
);

const Section: React.FC<{ title: string; right?: string; rows: string[][] }> = ({ title, right, rows }) => (
  <div style={{ marginBottom: 16 }}>
    <div style={{ display: "flex", justifyContent: "space-between", color: C.text, fontSize: 16, fontWeight: 500, margin: "6px 0 8px" }}>
      {title}
      {right && <span style={{ color: C.desc, fontSize: 14, fontWeight: 400 }}>{right}</span>}
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", columnGap: 34 }}>
      {rows.map(([icon, name, desc, by]) => (
        <PluginRow key={name} icon={icon} name={name} desc={desc} by={by} />
      ))}
    </div>
  </div>
);

export const TinCanIcon: React.FC<{ size: number }> = ({ size }) => (
  <svg width={size} height={size} viewBox="0 0 440 440" style={{ borderRadius: size / 4, background: "#fff" }}>
    <Defs />
    <path d="M226 230 Q220 244 214 230" stroke="#8d6e44" strokeWidth={6} fill="none" />
    <BlobBot x={92} y={226} r={74} face={1} />
    <BlobBot x={348} y={226} r={74} face={-1} />
    <Can x={158} y={226} angle={0} len={60} dia={52} />
    <Can x={282} y={226} angle={180} len={60} dia={52} />
  </svg>
);

const Marketplace: React.FC<{ t: number }> = ({ t }) => {
  const q = typed("TinCan", t, ...T.type1);
  const res = ramp(t, T.results - 0.05, T.results + 0.18);
  const added = t >= T.addClick + 0.04;
  return (
    <div style={{ position: "absolute", left: MK.x, top: MK.y, width: MK.w, height: MK.h, background: C.modal, borderRadius: 20, border: "1px solid #2a2a2a", overflow: "hidden", boxShadow: "0 30px 80px rgba(0,0,0,0.6)" }}>
      <div style={{ position: "absolute", left: 45, top: 36, color: C.text, fontSize: 23, fontWeight: 500 }}>Marketplace</div>
      <div style={{ position: "absolute", right: 64, top: 36, display: "flex", alignItems: "center", gap: 10, color: C.desc, fontSize: 15.5 }}>
        <Img src={staticFile("img/installed.png")} style={{ width: 62, height: 22 }} />6 installed
        <Icon d={I.chev} size={16} color={C.desc} />
      </div>
      <div style={{ position: "absolute", right: 22, top: 18 }}>
        <Icon d={I.x} size={20} color="#cfcfcf" />
      </div>
      <div style={{ position: "absolute", left: 35, right: 35, top: 92, height: 34, borderRadius: 17, background: C.field, border: "1px solid #333", display: "flex", alignItems: "center", gap: 9, padding: "0 13px" }}>
        <Icon d={I.search} size={17} color="#a0a0a0" />
        <span style={{ color: q ? C.text : C.desc, fontSize: 15.5, flex: 1 }}>
          {q || "Search plugins and Bots"}
          {q && t < T.addClick && Math.floor(t * 2.2) % 2 === 0 && <span style={{ display: "inline-block", width: 1.5, height: 17, background: "#fff", marginLeft: 1, verticalAlign: -3 }} />}
        </span>
        {q && <Icon d={I.x} size={15} color="#a0a0a0" />}
      </div>
      <div style={{ position: "absolute", left: 45, right: 45, top: 146, opacity: 1 - res }}>
        <Section title="For you" rows={[["aws", "Amazon Location Service", "Because you use GitHub"], ["appwrite", "Appwrite", "Because you use GitHub"], ["aws", "AWS Amplify", "Because you use GitHub"], ["aws", "AWS Core", "Because you use GitHub"]]} />
        <Section title="Featured Plugins" right="View all" rows={[["gmail", "Gmail", "Search, read, draft, and manage email"], ["gcal", "Google Calendar", "Search events and schedule meetings"], ["gdrive", "Google Drive", "Search, read, create, and share files."], ["granola", "Granola", "Your meetings in your workflow."]]} />
        <Section title="Featured Bots" rows={[["eggbot", "dr eggbot", "Designs high-quality Grok Bots."], ["overheard", "Overheard", "Watches Reddit, Hacker News, news…"]]} />
      </div>
      {res > 0 && (
        <div style={{ position: "absolute", left: 35, right: 35, top: 146, opacity: res, transform: `translateY(${(1 - res) * 8}px)` }}>
          <div style={{ color: C.desc, fontSize: 14, margin: "6px 10px 10px" }}>Plugins and Bots</div>
          <div style={{ display: "flex", alignItems: "center", gap: 14, height: 72, padding: "0 16px 0 12px", borderRadius: 14, background: "#232323" }}>
            <TinCanIcon size={44} />
            <div style={{ flex: 1 }}>
              <div style={{ color: C.text, fontSize: 16, fontWeight: 600 }}>
                TinCan <span style={{ color: C.desc, fontWeight: 400, fontSize: 14 }}>by Albina &amp; Carol</span>
              </div>
              <div style={{ color: C.desc, fontSize: 14.5 }}>Group chats for people and bots</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, background: added ? "#fafafa" : "#2e2e2e", color: added ? "#111" : C.text, fontSize: 14.5, fontWeight: 600, padding: "8px 16px", borderRadius: 20 }}>
              {added && <Icon d={I.check} size={15} color="#111" sw={2.6} />}
              {added ? "Added" : "Add"}
            </div>
          </div>
          {[0, 1].map((i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 14, height: 66, padding: "0 16px 0 12px", opacity: 0.45 }}>
              <div style={{ width: 44, height: 44, borderRadius: 11, background: i ? "#5b4a86" : "#3a3a3a" }} />
              <div style={{ flex: 1 }}>
                <div style={{ width: 170 - i * 30, height: 10, borderRadius: 5, background: "#3a3a3a", marginBottom: 9 }} />
                <div style={{ width: 120, height: 8, borderRadius: 4, background: "#2e2e2e" }} />
              </div>
              <div style={{ background: C.add, color: C.text, fontSize: 14.5, padding: "8px 15px", borderRadius: 20 }}>Add</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const CreateGroup: React.FC<{ t: number }> = ({ t }) => {
  const name = typed("TinCan team", t, ...T.type2);
  const members: Who[] = ["carol", "carolsGrok", "albina", "albinasGrok"];
  const pressed = t >= T.createClick && t < T.createClick + 0.12;
  return (
    <div style={{ position: "absolute", left: CG.x, top: CG.y, width: CG.w, height: CG.h, background: C.modal, borderRadius: 20, border: "1px solid #2a2a2a", boxShadow: "0 30px 80px rgba(0,0,0,0.6)" }}>
      <div style={{ position: "absolute", left: 28, top: 26, color: C.text, fontSize: 20, fontWeight: 600 }}>Create a group</div>
      <div style={{ position: "absolute", right: 22, top: 24 }}>
        <Icon d={I.x} size={20} color="#cfcfcf" />
      </div>
      <div style={{ position: "absolute", left: 28, top: 76, color: "#9a9a9a", fontSize: 13, fontWeight: 500 }}>Group name</div>
      <div style={{ position: "absolute", left: 28, right: 28, top: 98, height: 40, borderRadius: 10, background: "#222", border: `1px solid ${name.length < 11 ? "#6b6b6b" : "#3a3a3a"}`, display: "flex", alignItems: "center", padding: "0 13px", color: C.text, fontSize: 15.5 }}>
        {name || <span style={{ color: C.placeholder }}>Name your group</span>}
        {t < T.type2[1] + 0.2 && Math.floor(t * 2.2) % 2 === 0 && <span style={{ width: 1.5, height: 18, background: "#fff", marginLeft: 1 }} />}
      </div>
      <div style={{ position: "absolute", left: 28, top: 160, color: "#9a9a9a", fontSize: 13, fontWeight: 500 }}>
        Add people and bots ({T.checks.filter((c) => t >= c).length})
      </div>
      {members.map((w, i) => {
        const on = pop(t, T.checks[i], { damping: 14, stiffness: 260 });
        return (
          <div key={w} style={{ position: "absolute", left: 28, right: 28, top: 190 + i * 58, height: 52, display: "flex", alignItems: "center", gap: 12 }}>
            {PEOPLE[w].avatar(34)}
            <div style={{ flex: 1 }}>
              <div style={{ color: C.text, fontSize: 15.5, fontWeight: 500 }}>{PEOPLE[w].name}</div>
              <div style={{ color: "#8a8a8a", fontSize: 12.5 }}>{PEOPLE[w].kind}{w === "albina" ? " · you" : w === "albinasGrok" ? " · yours" : ""}</div>
            </div>
            <div style={{ width: 22, height: 22, borderRadius: 7, border: `1.5px solid ${on > 0.05 ? C.purple : "#555"}`, background: on > 0.05 ? C.purple : "transparent", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ transform: `scale(${on})` }}>
                <Icon d={I.check} size={15} color="#fff" sw={3} />
              </div>
            </div>
          </div>
        );
      })}
      <div style={{ position: "absolute", left: 28, right: 28, top: 452, height: 46, borderRadius: 23, background: "#fafafa", color: "#111", fontSize: 16, fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center", transform: `scale(${pressed ? 0.97 : 1})` }}>
        Create group
      </div>
    </div>
  );
};

// ---------------- cursor path ----------------
type CK = { t: number; x: number; y: number };
const CURSOR: CK[] = [
  { t: 1.25, x: 700, y: 470 },
  { t: 2.2, x: ADD_BTN.x - 4, y: ADD_BTN.y + 4 },
  { t: 3.05, x: ADD_BTN.x - 4, y: ADD_BTN.y + 4 },
  { t: 3.9, x: CHECK(0).x + 60, y: CHECK(0).y - 40 },
  ...T.checks.map((c, i) => ({ t: c - 0.03, x: CHECK(i).x, y: CHECK(i).y })),
  { t: 5.33, x: CREATE_BTN.x + 30, y: CREATE_BTN.y },
];
const CLICKS = [T.addClick, ...T.checks, T.createClick, T.playClick];
const CURSOR2: CK[] = [
  { t: 17.0, x: CARD_RECT.cx + 170, y: CARD_RECT.cy + 120 },
  { t: 17.5, x: CARD_RECT.cx + 2, y: CARD_RECT.cy - 2 },
];
const along = (t: number, keys: CK[]) => {
  if (t <= keys[0].t) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t <= b.t) {
      const e = ramp(t, a.t, b.t, Easing.inOut(Easing.quad));
      return { t, x: lerp(a.x, b.x, e), y: lerp(a.y, b.y, e) };
    }
  }
  return keys[keys.length - 1];
};

// ---------------- the app scene ----------------
export const AppScene: React.FC<{ t: number }> = ({ t }) => {
  const group = t >= T.chatIn;
  // camera: UI keys, then the dive into the video card
  let cam = camAt(t, CAM);
  if (t > T.cardZoom[0]) {
    const zEnd = 1920 / (CARD.w * BASE);
    cam = camAt(t, [
      { t: T.cardZoom[0], z: 1.8, x: 600, y: 590 },
      { t: T.cardZoom[1], z: zEnd, x: CARD_RECT.cx, y: CARD_RECT.cy },
    ]);
  }
  const s = BASE * cam.z;

  const lastSeen = MSGS.filter((m) => t >= m.at).pop();
  const preview = lastSeen
    ? `${lastSeen.from === "me" ? "You" : PEOPLE[lastSeen.from].name}: ${lastSeen.video ? "tincan-demo.mp4" : lastSeen.text}`
    : "You created the group";

  const mk = 1 - ramp(t, T.toGroup, T.toGroup + 0.3);
  const cg = ramp(t, T.toGroup + 0.05, T.toGroup + 0.35);
  const backdrop = group ? 0 : 0.55 * (1 - ramp(t, 5.9, 6.0));
  const modalIn = pop(t, 0, { damping: 20, stiffness: 220 });

  const c1 = along(t, CURSOR);
  const c2 = along(t, CURSOR2);
  const firstPart = t < 7;
  const cur = firstPart ? c1 : c2;
  const curOpacity = firstPart ? ramp(t, 1.2, 1.4) * (1 - ramp(t, 5.6, 5.75)) : ramp(t, 16.95, 17.1) * (1 - ramp(t, 17.62, 17.72));
  const click = CLICKS.find((c) => t >= c && t < c + 0.2);
  const down = click === undefined ? 0 : Math.sin(((t - click) / 0.2) * Math.PI);

  return (
    <AbsoluteFill style={{ fontFamily: FONT, overflow: "hidden", background: "#0b0914" }}>
      <div style={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0", transform: `translate(960px, 540px) scale(${s}) translate(${-cam.x}px, ${-cam.y}px)` }}>
        {/* wallpaper */}
        <div
          style={{
            position: "absolute",
            left: -900,
            top: -600,
            width: W + 1800,
            height: H + 1200,
            background:
              "radial-gradient(900px 600px at 30% 20%, rgba(139,92,246,0.55), transparent 70%), radial-gradient(900px 700px at 85% 90%, rgba(56,108,255,0.35), transparent 70%), linear-gradient(135deg, #140c28, #1d1540 50%, #0b1026)",
          }}
        />
        {/* window */}
        <div style={{ position: "absolute", left: 0, top: 0, width: W, height: H, borderRadius: 14, overflow: "hidden", background: C.chat, boxShadow: "0 0 0 1px rgba(255,255,255,0.14), 0 40px 120px rgba(0,0,0,0.65)" }}>
          <Sidebar t={t} group={group} preview={preview} />
          <HeaderPill group={group} />
          <div style={{ position: "absolute", right: 24, top: 32 }}>
            <Icon d={I.share} size={19} color="#bdbdbd" />
          </div>
          {group ? <GroupChat t={t} /> : <OneToOne />}
          <Composer t={t} group={group} />
          {backdrop > 0 && <div style={{ position: "absolute", inset: 0, background: `rgba(0,0,0,${backdrop})` }} />}
          {!group && mk > 0 && (
            <div style={{ opacity: mk * modalIn, transform: `scale(${(0.96 + 0.04 * modalIn) * (0.98 + 0.02 * mk)})`, transformOrigin: "640px 400px" }}>
              <Marketplace t={t} />
            </div>
          )}
          {!group && cg > 0 && (
            <div style={{ opacity: cg, transform: `scale(${0.97 + 0.03 * cg})`, transformOrigin: "640px 400px" }}>
              <CreateGroup t={t} />
            </div>
          )}
        </div>
        {curOpacity > 0 && <Cursor x={cur.x} y={cur.y} down={down} opacity={curOpacity} />}
      </div>
      {/* brief light sweep on window reveal for chat scene */}
      {group && t < T.chatIn + 0.5 && <AbsoluteFill style={{ background: "#fff", opacity: 1 - ramp(t, T.chatIn, T.chatIn + 0.35) }} />}
      {t < 0.2 && <AbsoluteFill style={{ background: "#000", opacity: 1 - ramp(t, 0, 0.2) }} />}
      {interpolate(t, [0, 1], [0, 0]) > 0 && null}
    </AbsoluteFill>
  );
};
