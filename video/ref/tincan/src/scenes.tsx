// Tin-can "connect the pairs" beat (6-8 s) and the closing end card.
import React from "react";
import { AbsoluteFill, AnimatedImage, staticFile } from "remotion";
import { FONT, pop, ramp } from "./lib";
import { PEOPLE } from "./app";

// GIF is 1000x500; its bots sit at x=148 / 848, y=248 (r≈70)
const G = { w: 1100, x: 410, y: 318 };
const bot = (gx: number) => ({ x: G.x + (gx * G.w) / 1000, y: G.y + (248 * G.w) / 1000 });

export const TinCanScene: React.FC<{ t: number }> = ({ t }) => {
  const l = t - 6; // local seconds
  const L = bot(148);
  const R = bot(848);
  const pair = (p: { x: number; y: number }, who: "carol" | "albina", grok: "carolsGrok" | "albinasGrok", delay: number) => {
    const s = pop(l, 0.1 + delay, { damping: 12, stiffness: 160 });
    const line = ramp(l, 0.3 + delay, 0.6 + delay);
    const label = ramp(l, 0.35 + delay, 0.6 + delay);
    return (
      <>
        <div style={{ position: "absolute", left: p.x - 150, width: 300, top: 150, textAlign: "center", fontSize: 34, fontWeight: 600, color: "#111", opacity: s }}>
          {PEOPLE[who].name}
        </div>
        <div style={{ position: "absolute", left: p.x - 48, top: 205, transform: `scale(${s})` }}>{PEOPLE[who].avatar(96)}</div>
        <div style={{ position: "absolute", left: p.x - 1.5, top: 305, width: 3, height: (p.y - 80 - 305) * line, background: "#c9c9c9", borderRadius: 2 }} />
        <div style={{ position: "absolute", left: p.x - 200, width: 400, top: p.y + 92, textAlign: "center", fontSize: 30, fontWeight: 600, color: "#111", opacity: label, transform: `translateY(${(1 - label) * 10}px)` }}>
          {PEOPLE[grok].name}
        </div>
      </>
    );
  };
  const tag = ramp(l, 0.75, 1.05);
  const glow = 0.5 + 0.5 * Math.sin(l * 7);
  return (
    <AbsoluteFill style={{ background: "#fff", fontFamily: FONT }}>
      <div style={{ position: "absolute", left: 958 - 70, top: G.y + 262 - 70, width: 140, height: 140, borderRadius: 140, background: "radial-gradient(#a78bfa, rgba(167,139,250,0) 70%)", opacity: 0.45 + 0.4 * glow * ramp(l, 0.5, 0.8) }} />
      <AnimatedImage src={staticFile("img/tincan.gif")} width={G.w} height={G.w / 2} style={{ position: "absolute", left: G.x, top: G.y, mixBlendMode: "multiply", filter: "brightness(1.04)" }} />
      {pair(L, "carol", "carolsGrok", 0)}
      {pair(R, "albina", "albinasGrok", 0.12)}
      <div style={{ position: "absolute", left: 0, right: 0, top: 900, textAlign: "center", fontSize: 44, fontWeight: 700, color: "#7c3aed", opacity: tag, transform: `translateY(${(1 - tag) * 14}px)` }}>
        One connected group
      </div>
    </AbsoluteFill>
  );
};

export const EndCard: React.FC<{ t: number }> = ({ t }) => {
  const l = t - 28;
  const a = pop(l, 0.12, { damping: 16, stiffness: 140 });
  const b = ramp(l, 0.45, 0.8);
  return (
    <AbsoluteFill style={{ background: "#fff", fontFamily: FONT, alignItems: "center", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 28, transform: `scale(${0.9 + 0.1 * a})`, opacity: a }}>
        <AnimatedImage src={staticFile("img/tincan.gif")} width={440} height={220} style={{ mixBlendMode: "multiply", filter: "brightness(1.04)" }} />
        <div style={{ fontSize: 150, fontWeight: 800, letterSpacing: -4, color: "#0b0b0b" }}>TinCan</div>
      </div>
      <div style={{ marginTop: 18, fontSize: 46, fontWeight: 500, color: "#3a3a3a", opacity: b, transform: `translateY(${(1 - b) * 12}px)` }}>
        Your people. Your bots. One conversation.
      </div>
    </AbsoluteFill>
  );
};
