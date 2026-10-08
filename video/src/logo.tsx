// The Macrae logo as a small Hamiltonian system, rebuilt as vectors from ref/logo.png so the deep zoom stays
// sharp: orbit system, flow, dive into the core with the app revealed inside it, the pull-out and the end card.
// Geometry is measured from logo.png (720 px): ellipse fits, particle centres/radii, sampled colours.
import React from "react";
import { AbsoluteFill, Easing } from "remotion";
import { Cam, camAt, clamp01, FONT, lerp, pop, ramp } from "./lib";
import { WORDMARK_PATH } from "./wordmark";

// ---------------- geometry (logo.png pixels) ----------------
const C0 = { x: 359.5, y: 300 }; // orbit centre in logo.png
const S = 1.5; // logo.png px -> frame px at z = 1 (720 px logo -> 1080 px frame)
export const ORBIT_C = { x: 960, y: 380 }; // where the orbit centre sits in the frame
const TILT = (-28 * Math.PI) / 180; // major axes, measured
const Q = 0.574; // minor/major ratio of the orbit family

export const COL = {
  bg: "#0B0F26",
  amber: "#FFB547",
  peach: "#D9AC84",
  lilac: "#B4A4C2",
  blue: "#8E9BFF",
  ring: "#7A86E0",
  link: "#9C9CA4",
  star: "#ADAAAA",
  word: "#F2EDE3",
};

// 4 ellipses: semi-major a, ratio q, opacity (the outer two dimmer)
const RINGS = [
  { a: 67.6, q: 0.562, o: 0.95 },
  { a: 118.2, q: 0.571, o: 0.71 },
  { a: 168.9, q: 0.575, o: 0.535 },
  { a: 219.6, q: 0.577, o: 0.355 },
];
const CORE = { x: 359.2, y: 300, r: 14.4 };
const CORE_RING = 27.3;

type Kind = "amber" | "peach" | "lilac" | "blue";
type P = [number, number, number, Kind];
// Three arms of the particle chain, inner -> outer (centre x, centre y, radius, colour)
const ARMS: P[][] = [
  [
    [387.0, 309.9, 7.5, "amber"], [406.0, 299.9, 7.2, "amber"], [423.0, 283.1, 6.6, "peach"], [433.0, 262.7, 6.3, "peach"],
    [432.1, 241.5, 5.6, "peach"], [417.3, 223.8, 5.5, "lilac"], [387.6, 214.9, 5.0, "lilac"], [345.9, 217.0, 5.0, "lilac"],
    [295.7, 233.0, 4.5, "blue"], [244.2, 263.2, 4.5, "blue"], [198.9, 305.1, 4.1, "blue"], [164.5, 356.1, 3.5, "blue"],
  ],
  [
    [377.2, 279.5, 7.5, "amber"], [373.5, 271.0, 6.9, "amber"], [360.1, 264.9, 6.9, "amber"], [337.7, 266.0, 6.6, "peach"],
    [310.3, 275.8, 6.3, "peach"], [282.1, 295.0, 5.9, "peach"], [258.6, 320.9, 5.5, "lilac"], [246.1, 351.4, 5.5, "lilac"],
    [248.9, 381.0, 5.0, "lilac"], [269.9, 404.7, 4.6, "blue"], [309.7, 418.0, 4.1, "blue"], [365.2, 416.7, 4.1, "blue"],
    [433.0, 399.0, 3.7, "blue"],
  ],
  [
    [332.6, 304.9, 7.5, "amber"], [320.6, 316.6, 6.9, "amber"], [314.7, 331.6, 6.8, "amber"], [318.6, 346.0, 6.3, "peach"],
    [333.5, 356.6, 6.2, "peach"], [360.1, 360.0, 6.2, "peach"], [395.5, 353.2, 5.5, "lilac"], [435.0, 334.5, 5.5, "lilac"],
    [472.9, 305.9, 4.7, "lilac"], [501.5, 268.9, 4.5, "blue"], [514.7, 228.1, 4.5, "blue"], [508.0, 189.0, 4.1, "blue"],
    [479.2, 156.9, 4.1, "blue"],
  ],
];
const COMPANION: P = [370.4, 312.7, 7.5, "amber"]; // the small dot that sits on the core

// 6 stars from logo.png plus 2 for the wider 16:9 frame
const STARS: [number, number, number][] = [
  [215.9, 145.7, 1.9], [169.4, 164.6, 2.5], [190.5, 203.0, 1.5], [558.1, 397.1, 1.9], [541.2, 439.2, 2.5], [503.0, 454.0, 2.5],
  [-62, 420, 1.8], [786, 168, 2.0],
];

// Each particle as an orbit of its own: elliptical radius a, phase th, Kepler rate k ∝ a^-3/2
type Orb = { a: number; th: number; k: number; r: number; kind: Kind };
const toOrb = ([x, y, r, kind]: P): Orb => {
  const dx = x - C0.x;
  const dy = y - C0.y;
  const ux = dx * Math.cos(-TILT) - dy * Math.sin(-TILT);
  const uy = dx * Math.sin(-TILT) + dy * Math.cos(-TILT);
  const a = Math.hypot(ux, uy / Q);
  return { a, th: Math.atan2(uy / Q, ux), k: Math.pow(RINGS[0].a / a, 1.5), r, kind };
};
const ORBS = ARMS.map((arm) => arm.map(toOrb));
const COMP = toOrb(COMPANION);

// ---------------- warp(t): the flow ----------------
// F = Kepler phase (each particle advances k·F), eps = ring breathing, psi = precession.
// Returns exactly the logo at t = 0 and again at t = OUT_SETTLE (the bookend).
export const OUT_START = 26.6;
export const OUT_SETTLE = 27.2;
const W_SLOW = 0.05; // slow idle orbit
const SWEEP = 1.4; // Kepler sweep, grows quadratically from 2.6 s
const sweep = (t: number) => SWEEP * Math.pow(Math.max(0, t - 2.6) / 1.6, 2);
const F_OUT = W_SLOW * 4.6 + sweep(4.6); // where the pull-out unwinds from
const OMEGA = 2.2;

export const warp = (t: number) => {
  if (t < OUT_START) {
    const e = ramp(t, 2.6, 4.2);
    return { F: W_SLOW * t + sweep(t), eps: 0.04 * e, psi: (-8 * Math.PI * e) / 180, flow: ramp(t, 2.6, 3.0) };
  }
  const u = 1 - ramp(t, OUT_START, OUT_SETTLE, Easing.inOut(Easing.cubic));
  return { F: F_OUT * u + W_SLOW * (t - OUT_SETTLE), eps: 0.04 * u, psi: (-8 * Math.PI * u) / 180, flow: u };
};
type Warp = ReturnType<typeof warp>;

// orbit point (logo.png px) for elliptical radius a at phase th
const orbitPt = (a: number, q: number, th: number, w: Warp, t: number) => {
  const rr = 1 + w.eps * Math.sin(3 * th - OMEGA * t);
  const ex = a * Math.cos(th) * rr;
  const ey = q * a * Math.sin(th) * rr;
  const ang = TILT + w.psi;
  return { x: C0.x + ex * Math.cos(ang) - ey * Math.sin(ang), y: C0.y + ex * Math.sin(ang) + ey * Math.cos(ang) };
};
const orbPos = (o: Orb, w: Warp, t: number, dF = 0) => orbitPt(o.a, Q, o.th + o.k * (w.F - dF), w, t);

// ---------------- cameras ----------------
// z is the zoom about the frame; (x, y) is the frame point shown at the centre (frame px at z = 1).
const coreY = (z: number) => ORBIT_C.y + (540 - ORBIT_C.y) / z; // keeps the core fixed on screen
export const INTRO_CAM: Cam[] = [
  { t: 0, z: 1, x: 960, y: 540 },
  { t: 2.6, z: 1.04, x: 960, y: coreY(1.04) },
  { t: 4.2, z: 1.5, x: 960, y: coreY(1.5) },
  { t: 5.6, z: 60, x: 960, y: ORBIT_C.y },
];
const OUTRO_CAM: Cam[] = [
  { t: OUT_START, z: 60, x: 960, y: ORBIT_C.y },
  { t: OUT_SETTLE, z: 1, x: 960, y: 540 },
];
export const logoCam = (t: number) => (t < OUT_START ? camAt(t, INTRO_CAM) : camAt(t, OUTRO_CAM));

// logo.png px -> screen px
const project = (p: { x: number; y: number }, cam: Cam) => ({
  x: 960 + cam.z * (ORBIT_C.x + S * (p.x - C0.x) - cam.x),
  y: 540 + cam.z * (ORBIT_C.y + S * (p.y - C0.y) - cam.y),
});

// ---------------- dive(z): what the zoom does to the look ----------------
const mix = (a: string, b: string, x: number) => {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return `rgb(${pa.map((v, i) => Math.round(lerp(v, pb[i], clamp01(x)))).join(",")})`;
};
export const dive = (z: number, zPrev: number) => {
  const heat = clamp01(Math.log(z / 1.5) / Math.log(10)); // 0 at rest, 1 when white-hot
  return {
    core: heat < 0.5 ? mix(COL.amber, "#FFD99A", heat * 2) : mix("#FFD99A", "#FFFFFF", heat * 2 - 1),
    heat,
    size: z <= 1.5 ? z : 1.5 * Math.pow(z / 1.5, 0.18), // dots and strokes stop growing: they become streaks
    // fraction of the radius swept per exposure; only in the dive itself (the flow uses ghost trails instead)
    streak: clamp01(1 - Math.min(z, zPrev) / Math.max(z, zPrev)) * clamp01((z - 1.6) / 2),
    out: zPrev > z, // pulling out: streaks trail outwards
  };
};

// ---------------- page inside the core ----------------
const coreScreen = (cam: Cam) => project(CORE, cam);
const coreRadius = (cam: Cam) => CORE.r * S * cam.z;

// page scale while it grows out of the core: the page width matches the core's diameter at 4.9 s,
// then the push carries on into the page until it fills the frame (z 1.0) at 5.6 s
const REVEAL = [4.9, 5.6] as const;
const pageScaleIn = (t: number) => {
  const r0 = coreRadius(camAt(REVEAL[0], INTRO_CAM));
  const m0 = (2 * r0) / 1920;
  const e = ramp(t, REVEAL[0], REVEAL[1], Easing.out(Easing.cubic));
  return Math.exp(lerp(Math.log(m0), 0, e));
};

const PageInCore: React.FC<{ t: number; cam: Cam; page: React.ReactNode }> = ({ t, cam, page }) => {
  const intro = t < OUT_START;
  const c = coreScreen(cam);
  const rc = coreRadius(cam);
  const m = intro ? pageScaleIn(t) : cam.z / 60;
  const opacity = intro ? ramp(t, 4.9, 5.2) : 1;
  const white = intro ? 0 : ramp(t, 26.68, 26.95, Easing.in(Easing.quad));
  const amber = intro ? 0 : ramp(t, 26.95, OUT_SETTLE - 0.04);
  const rim = intro ? ramp(t, 4.9, 5.0) * (1 - ramp(t, 5.35, 5.6)) : ramp(t, OUT_START, OUT_START + 0.12) * (1 - ramp(t, 27.0, OUT_SETTLE));
  if (opacity <= 0) return null;
  return (
    <>
      <AbsoluteFill style={{ clipPath: `circle(${rc}px at ${c.x}px ${c.y}px)`, opacity }}>
        <AbsoluteFill style={{ background: "#fff" }} />
        <AbsoluteFill style={{ transformOrigin: "960px 540px", transform: `translate(${c.x - 960}px, ${c.y - 540}px) scale(${m})` }}>{page}</AbsoluteFill>
        {white > 0 && <AbsoluteFill style={{ background: "#fff", opacity: white }} />}
        {amber > 0 && <AbsoluteFill style={{ background: COL.amber, opacity: amber }} />}
      </AbsoluteFill>
      {rim > 0 && (
        <div
          style={{
            position: "absolute",
            left: c.x - rc,
            top: c.y - rc,
            width: 2 * rc,
            height: 2 * rc,
            borderRadius: "50%",
            border: `${Math.max(2, rc * 0.012)}px solid rgba(255, 217, 154, ${0.9 * rim})`,
            boxShadow: `0 0 ${18 + rc * 0.05}px ${4 + rc * 0.02}px rgba(255, 190, 110, ${0.65 * rim}), inset 0 0 ${10 + rc * 0.04}px rgba(255, 200, 130, ${0.55 * rim})`,
          }}
        />
      )}
    </>
  );
};

// ---------------- the orbit system ----------------
const ringPath = (ring: (typeof RINGS)[number], w: Warp, t: number, cam: Cam) => {
  const N = 240;
  let d = "";
  for (let i = 0; i <= N; i++) {
    const p = project(orbitPt(ring.a, ring.q, (i / N) * 2 * Math.PI, w, t), cam);
    d += `${i ? "L" : "M"}${p.x.toFixed(2)} ${p.y.toFixed(2)}`;
  }
  return d + "Z";
};

const appearAt = (j: number, n: number) => 0.2 + (j / (n - 1)) * 0.55; // pop along the chain, inner -> outer

const System: React.FC<{ t: number; cam: Cam; camPrev: Cam }> = ({ t, cam, camPrev }) => {
  const w = warp(t);
  const dv = dive(cam.z, camPrev.z);
  const c = coreScreen(cam);
  const sz = S * dv.size; // screen px per logo.png px for dots/strokes
  const breathe = 1 + 0.08 * Math.sin(2 * Math.PI * 0.5 * t);
  const glowR = CORE.r * S * (cam.z + 3.6 * Math.min(cam.z, 2.5) * breathe); // a halo of bounded width round the core
  const ignite = pop(t, 0.1, { damping: 11, stiffness: 170 });
  const flash = Math.exp(-Math.pow((t - 0.32) / 0.18, 2)) * (t > 0.1 ? 1 : 0);
  const thin = lerp(1, 0.4, w.flow); // connectors thin to hairlines in the flow
  const ghosts = w.flow > 0.02 ? [1, 2, 3] : [];

  // streak: a dot drawn as a short radial line, its length following the zoom speed
  const streakEnd = (p: { x: number; y: number }) => {
    if (dv.streak < 0.002) return p;
    const f = dv.out ? 1 + dv.streak : 1 - dv.streak;
    return { x: c.x + (p.x - c.x) * f, y: c.y + (p.y - c.y) * f };
  };
  const dot = (key: string, p: { x: number; y: number }, r: number, fill: string, opacity: number) => {
    const e = streakEnd(p);
    if (Math.hypot(e.x - p.x, e.y - p.y) < 0.6) return <circle key={key} cx={p.x} cy={p.y} r={r} fill={fill} opacity={opacity} />;
    return <line key={key} x1={p.x} y1={p.y} x2={e.x} y2={e.y} stroke={fill} strokeWidth={1.4 * r} strokeLinecap="round" opacity={opacity} />;
  };

  const onScreen = (p: { x: number; y: number }, pad = 200) => p.x > -pad && p.x < 1920 + pad && p.y > -pad && p.y < 1080 + pad;

  return (
    <svg width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
      <defs>
        <radialGradient id="glow" gradientUnits="userSpaceOnUse" cx={c.x} cy={c.y} r={glowR}>
          <stop offset="0" stopColor="#FFC56E" stopOpacity={0.55 + 0.35 * dv.heat} />
          <stop offset={Math.max(0.35, (CORE.r * S * cam.z) / glowR)} stopColor="#FFB347" stopOpacity={0.22 + 0.2 * dv.heat} />
          <stop offset="1" stopColor="#FFB347" stopOpacity={0} />
        </radialGradient>
      </defs>
      {/* stars */}
      {STARS.map(([x, y, r], i) => {
        const p = project({ x, y }, cam);
        if (!onScreen(p, 50)) return null;
        const o = ramp(t, 0.05 * i, 0.4 + 0.05 * i) * (0.75 + 0.25 * Math.sin(t * 1.7 + i * 2.1));
        return dot(`s${i}`, p, Math.max(0.8, r * sz * 0.8), COL.star, o);
      })}
      {/* orbits */}
      {RINGS.map((ring, i) => {
        // cull rings that have swept out past the frame
        if (cam.z * S * ring.a * ring.q > 2600) return null;
        const draw = ramp(t, 0.04 * i, 0.45 + 0.04 * i, Easing.out(Easing.cubic));
        return (
          <path
            key={i}
            d={ringPath(ring, w, t, cam)}
            fill="none"
            stroke={COL.ring}
            strokeOpacity={ring.o}
            strokeWidth={2.2 * sz * lerp(1, 0.8, w.flow)}
            pathLength={1}
            strokeDasharray="1 1"
            strokeDashoffset={1 - draw}
          />
        );
      })}
      {/* glow behind the core */}
      <circle cx={c.x} cy={c.y} r={glowR} fill="url(#glow)" opacity={ignite * (1 + 0.6 * flash)} />
      {/* ring round the core */}
      {cam.z * S * CORE_RING < 3000 && (
        <circle cx={c.x} cy={c.y} r={CORE_RING * S * cam.z} fill="none" stroke={COL.amber} strokeOpacity={0.6 * ignite} strokeWidth={1.9 * sz} />
      )}
      {/* connectors: ghosts first (trails), then the live chain */}
      {ghosts.map((g) =>
        ORBS.map((arm, a) =>
          arm.slice(1).map((o, j) => {
            const dF = g * 0.09;
            const p0 = project(orbPos(arm[j], w, t, dF), cam);
            const p1 = project(orbPos(o, w, t, dF), cam);
            return <line key={`g${g}-${a}-${j}`} x1={p0.x} y1={p0.y} x2={p1.x} y2={p1.y} stroke={COL.link} strokeWidth={0.7 * sz} strokeOpacity={(0.32 / g) * w.flow * (1 - dv.heat)} />;
          }),
        ),
      )}
      {ORBS.map((arm, a) =>
        arm.slice(1).map((o, j) => {
          const grow = ramp(t, appearAt(j + 1, arm.length) - 0.06, appearAt(j + 1, arm.length) + 0.12);
          if (grow <= 0) return null;
          const p0 = project(orbPos(arm[j], w, t), cam);
          const p1 = project(orbPos(o, w, t), cam);
          return (
            <line
              key={`l${a}-${j}`}
              x1={p0.x}
              y1={p0.y}
              x2={lerp(p0.x, p1.x, grow)}
              y2={lerp(p0.y, p1.y, grow)}
              stroke={COL.link}
              strokeWidth={1.4 * sz * thin}
              strokeOpacity={0.5 * (1 - dv.heat)}
              strokeLinecap="round"
            />
          );
        }),
      )}
      {/* particles */}
      {ORBS.map((arm, a) =>
        arm.map((o, j) => {
          const s = pop(t, appearAt(j, arm.length), { damping: 12, stiffness: 220 });
          if (s <= 0.001) return null;
          const p = project(orbPos(o, w, t), cam);
          if (!onScreen(p)) return null;
          return dot(`p${a}-${j}`, p, o.r * sz * s, COL[o.kind], 1);
        }),
      )}
      {dot("comp", project(orbPos(COMP, w, t), cam), COMP.r * sz * pop(t, 0.18, { damping: 12, stiffness: 220 }), dv.core, 1)}
      {/* the core */}
      <circle cx={c.x} cy={c.y} r={CORE.r * S * cam.z * ignite * (1 + 0.04 * (breathe - 1))} fill={dv.core} />
    </svg>
  );
};

// ---------------- wordmark + end-card text ----------------
const Wordmark: React.FC<{ cam: Cam; dy: number; opacity: number }> = ({ cam, dy, opacity }) => {
  if (opacity <= 0) return null;
  const a = cam.z * S;
  const bx = 960 + cam.z * (ORBIT_C.x - S * C0.x - cam.x);
  const by = 540 + cam.z * (ORBIT_C.y - S * C0.y - cam.y + dy);
  return (
    <svg width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
      <path d={WORDMARK_PATH} transform={`matrix(${a} 0 0 ${a} ${bx} ${by})`} fill={COL.word} fillRule="evenodd" opacity={opacity} />
    </svg>
  );
};

export const LOGO_BG = COL.bg;

// ---------------- the scenes ----------------
// Shots 1-3 (0-5.6 s) and 10 (26.6-30 s). `page` is the app frame drawn inside the core.
export const LogoScene: React.FC<{ t: number; page: React.ReactNode }> = ({ t, page }) => {
  const cam = logoCam(t);
  const camPrev = logoCam(t - 0.06);
  const intro = t < OUT_START;
  // wordmark: rises in (0.5-0.9), sinks out (3.2-3.7); end card rises again (27.3-27.7)
  const wIn = intro ? ramp(t, 0.5, 0.9, Easing.out(Easing.cubic)) : ramp(t, 27.3, 27.7, Easing.out(Easing.cubic));
  const wOut = intro ? ramp(t, 3.2, 3.7, Easing.in(Easing.cubic)) : 0;
  const tag = ramp(t, 27.7, 28.1, Easing.out(Easing.cubic));
  const credit = ramp(t, 28.0, 28.4, Easing.out(Easing.cubic));
  const showPage = intro ? t >= REVEAL[0] : t < OUT_SETTLE;
  return (
    <AbsoluteFill style={{ background: COL.bg, overflow: "hidden" }}>
      <AbsoluteFill style={{ background: `radial-gradient(1100px 760px at 50% 36%, rgba(60, 70, 150, 0.16), transparent 70%)` }} />
      <System t={t} cam={cam} camPrev={camPrev} />
      <Wordmark cam={cam} dy={14 * (1 - wIn) + 14 * wOut} opacity={wIn * (1 - wOut)} />
      {!intro && (
        <div style={{ position: "absolute", left: 0, right: 0, top: 838, textAlign: "center", fontFamily: FONT }}>
          <div style={{ fontSize: 40, fontWeight: 500, letterSpacing: -0.4, color: COL.word, opacity: tag, transform: `translateY(${(1 - tag) * 12}px)` }}>
            Ask the papers. Watch the work.
          </div>
          <div style={{ marginTop: 14, fontSize: 24, fontWeight: 450, letterSpacing: 0.2, color: "#9DA3C8", opacity: credit, transform: `translateY(${(1 - credit) * 10}px)` }}>
            Jungwirth group · IOCB Prague
          </div>
        </div>
      )}
      {showPage && <PageInCore t={t} cam={cam} page={page} />}
    </AbsoluteFill>
  );
};
