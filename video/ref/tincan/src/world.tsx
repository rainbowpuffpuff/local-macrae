// "Powers of ten" finale: bots -> people -> buildings, every level joined by its own
// tin-can string, then the buildings shrink into one city light on the real Earth (space.tsx).
// Each level is drawn on its own 1920x1080 canvas; level j sits inside level j+1 at
// offset A[j+1] and scale S[j+1].
import React from "react";
import { AbsoluteFill, Img, interpolate, interpolateColors, staticFile } from "remotion";
import { ramp } from "./lib";
import { Space, SPACE } from "./space";

type P = [number, number];

// ---------- shared drawing bits ----------

const STRING = "#8d6e44";

// Tin can: open end ("mouth") at (x, y), body runs along `angle` towards the string.
export const Can: React.FC<{ x: number; y: number; angle: number; len: number; dia: number }> = ({ x, y, angle, len, dia }) => {
  const sw = Math.max(dia * 0.035, 0.6);
  return (
    <g transform={`translate(${x} ${y}) rotate(${angle})`}>
      <rect x={0} y={-dia / 2} width={len} height={dia} fill="url(#canBody)" stroke="#1d1d1d" strokeWidth={sw} />
      {[0.14, 0.26, 0.38, 0.5, 0.62, 0.74, 0.86].map((r) => (
        <line key={r} x1={len * r} x2={len * r} y1={-dia / 2 + sw} y2={dia / 2 - sw} stroke="#8e8e8e" strokeWidth={sw * 0.8} />
      ))}
      <ellipse cx={len} cy={0} rx={dia * 0.13} ry={dia / 2} fill="#e6e6e6" stroke="#1d1d1d" strokeWidth={sw} />
      <circle cx={len} cy={0} r={dia * 0.05} fill="#1d1d1d" />
      <ellipse cx={0} cy={0} rx={dia * 0.16} ry={dia / 2} fill="#9c9c9c" stroke="#1d1d1d" strokeWidth={sw} />
    </g>
  );
};
const canEnd = (x: number, y: number, angle: number, len: number): P => [
  x + len * Math.cos((angle * Math.PI) / 180),
  y + len * Math.sin((angle * Math.PI) / 180),
];

const bez = (p0: P, c: P, p1: P, u: number): P => [
  (1 - u) * (1 - u) * p0[0] + 2 * (1 - u) * u * c[0] + u * u * p1[0],
  (1 - u) * (1 - u) * p0[1] + 2 * (1 - u) * u * c[1] + u * u * p1[1],
];

// String between two points with a signal pulse bouncing along it.
export const Line: React.FC<{ a: P; b: P; sag: number; w: number; t: number; period?: number; glow?: boolean; control?: P }> = ({
  a,
  b,
  sag,
  w,
  t,
  period = 1.3,
  glow,
  control,
}) => {
  const c: P = control ?? [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + sag];
  const leg = (t / period) % 2;
  const u = leg < 1 ? leg : 2 - leg;
  const [px, py] = bez(a, c, b, 0.06 + 0.88 * u);
  const ring = glow ? "#e9ddff" : "#8c8c8c";
  return (
    <g>
      <path d={`M${a[0]} ${a[1]} Q${c[0]} ${c[1]} ${b[0]} ${b[1]}`} fill="none" stroke={STRING} strokeWidth={w} strokeLinecap="round" />
      {glow && <circle cx={px} cy={py} r={w * 7} fill="#a78bfa" opacity={0.35} />}
      <circle cx={px} cy={py} r={w * 1.6} fill={glow ? "#ffffff" : "#6f6f6f"} />
      <circle cx={px} cy={py} r={w * 3.6} fill="none" stroke={ring} strokeWidth={w * 0.55} />
      <circle cx={px} cy={py} r={w * 6} fill="none" stroke={ring} strokeWidth={w * 0.45} opacity={0.6} />
    </g>
  );
};

// Round black bot from the TinCan GIF. face = 1 looks right, -1 looks left.
export const BlobBot: React.FC<{ x: number; y: number; r: number; face: 1 | -1 }> = ({ x, y, r, face }) => (
  <g>
    <circle cx={x} cy={y} r={r} fill="#0b0b0b" />
    {[
      [0.16, -0.4],
      [0.56, -0.5],
    ].map(([dx, dy], i) => (
      <rect
        key={i}
        x={x + face * dx * r - r * 0.085}
        y={y + dy * r - r * 0.16}
        width={r * 0.17}
        height={r * 0.32}
        rx={r * 0.085}
        fill="#fff"
        transform={`rotate(${face * 18} ${x + face * dx * r} ${y + dy * r})`}
      />
    ))}
  </g>
);

export const Defs = () => (
  <defs>
    <linearGradient id="canBody" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stopColor="#f4f4f4" />
      <stop offset="0.55" stopColor="#d2d2d2" />
      <stop offset="1" stopColor="#a9a9a9" />
    </linearGradient>
  </defs>
);

// ---------- level 0: two bots ----------

export const Level0: React.FC<{ t: number }> = ({ t }) => {
  const L = { x: 380, y: 560, r: 112 };
  const R = { x: 1540, y: 560, r: 112 };
  const mouthL: P = [L.x + L.r * 0.9, L.y];
  const mouthR: P = [R.x - R.r * 0.9, R.y];
  return (
    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{ overflow: "visible" }}>
      <Defs />
      <Line a={canEnd(...mouthL, 0, 104)} b={canEnd(...mouthR, 180, 104)} sag={46} w={4} t={t} />
      <BlobBot {...L} face={1} />
      <BlobBot {...R} face={-1} />
      <Can x={mouthL[0]} y={mouthL[1]} angle={0} len={104} dia={84} />
      <Can x={mouthR[0]} y={mouthR[1]} angle={180} len={104} dia={84} />
    </svg>
  );
};

// ---------- level 1: Carol & Albina ----------

const Person: React.FC<{ x: number; face: 1 | -1; skin: string; top: string; hair: string; long?: boolean }> = ({
  x,
  face: f,
  skin,
  top,
  hair,
  long,
}) => {
  const X = (dx: number) => x + f * dx;
  return (
    <g>
      {/* legs + shoes */}
      <rect x={x - 46} y={630} width={40} height={320} rx={18} fill="#2f3441" />
      <rect x={x + 6} y={630} width={40} height={320} rx={18} fill="#2f3441" />
      <ellipse cx={X(-8) - 18} cy={958} rx={36} ry={14} fill="#161616" />
      <ellipse cx={X(-8) + 34} cy={958} rx={36} ry={14} fill="#161616" />
      {/* back arm */}
      <path d={`M${X(-70)} 390 L${X(-92)} 600`} stroke={top} strokeWidth={40} strokeLinecap="round" />
      <circle cx={X(-93)} cy={620} r={20} fill={skin} />
      {long && <path d={`M${x - 70} 250 Q${x - 84} 400 ${X(-40)} 430 L${X(40)} 430 Q${x + 84} 400 ${x + 70} 250 Z`} fill={hair} />}
      {/* torso */}
      <path d={`M${x - 96} 400 Q${x - 96} 352 ${x - 50} 352 L${x + 50} 352 Q${x + 96} 352 ${x + 96} 400 L${x + 82} 660 L${x - 82} 660 Z`} fill={top} />
      <rect x={x - 17} y={316} width={34} height={46} rx={10} fill={skin} />
      {/* head + hair */}
      <circle cx={x} cy={262} r={64} fill={skin} />
      {long ? (
        <path d={`M${x - 68} 272 Q${x - 74} 186 ${x} 184 Q${x + 74} 186 ${x + 68} 272 Q${X(10)} 220 ${x - 68} 272 Z`} fill={hair} />
      ) : (
        <path
          d={`M${x - 70} 300 Q${x - 80} 190 ${x} 186 Q${x + 80} 190 ${x + 70} 300 L${X(40)} 300 Q${X(30)} 236 ${X(-20)} 224 Q${x - 40} 250 ${x - 44} 300 Z`}
          fill={hair}
        />
      )}
      {/* face */}
      <ellipse cx={X(16)} cy={264} rx={5} ry={7} fill="#2a2a2a" />
      <ellipse cx={X(42)} cy={264} rx={5} ry={7} fill="#2a2a2a" />
      <path d={`M${X(20)} 292 Q${X(32)} 302 ${X(44)} 292`} stroke="#2a2a2a" strokeWidth={4} fill="none" strokeLinecap="round" />
      {/* front arm holding the can to the mouth */}
      <path d={`M${X(70)} 392 L${X(126)} 462 L${X(88)} 318`} stroke={top} strokeWidth={40} strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <circle cx={X(86)} cy={314} r={20} fill={skin} />
    </g>
  );
};

export const Level1: React.FC<{ t: number }> = ({ t }) => {
  const cl: P = [498, 290];
  const ar: P = [1422, 290];
  return (
    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{ overflow: "visible" }}>
      <Defs />
      <Line a={canEnd(...cl, 0, 62)} b={canEnd(...ar, 180, 62)} sag={60} w={3.2} t={t + 0.4} />
      <Person x={430} face={1} skin="#e7b793" top="#c98a4b" hair="#4b2e20" />
      <Person x={1490} face={-1} skin="#f3d3bd" top="#8b5cf6" hair="#231815" long />
      <Can x={cl[0]} y={cl[1]} angle={0} len={62} dia={48} />
      <Can x={ar[0]} y={ar[1]} angle={180} len={62} dia={48} />
    </svg>
  );
};

// ---------- level 2: two buildings on a street ----------

export const Level2: React.FC<{ t: number }> = ({ t }) => {
  const cl: P = [640, 330];
  const cr: P = [1280, 300];
  const windows = (x0: number, y0: number, cols: number, rows: number, dx: number, dy: number, w: number, h: number, glass: string) =>
    Array.from({ length: cols * rows }, (_, i) => (
      <rect key={i} x={x0 + (i % cols) * dx} y={y0 + Math.floor(i / cols) * dy} width={w} height={h} rx={6} fill={glass} stroke="#ffffff" strokeWidth={6} />
    ));
  return (
    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{ overflow: "visible" }}>
      <Defs />
      {/* street */}
      <rect x={-600} y={945} width={3120} height={34} fill="#d9dde4" />
      <rect x={-600} y={979} width={3120} height={140} fill="#c3c9d3" />
      {/* old-town building, left */}
      <polygon points="60,262 360,110 660,262" fill="#c8553a" />
      <rect x={80} y={262} width={560} height={683} fill="#f2d68a" />
      <rect x={80} y={262} width={560} height={16} fill="#e2bf6c" />
      {windows(128, 320, 4, 5, 124, 118, 72, 88, "#bcd6ef")}
      <path d="M318 945 L318 860 Q360 820 402 860 L402 945 Z" fill="#8a5a3c" />
      {/* glass tower, right */}
      <rect x={1280} y={170} width={560} height={775} fill="#8fb1d9" />
      <rect x={1330} y={120} width={460} height={50} fill="#7fa2cc" />
      {Array.from({ length: 12 }, (_, i) => (
        <rect key={i} x={1280} y={220 + i * 60} width={560} height={6} fill="#a9c4e4" />
      ))}
      {Array.from({ length: 6 }, (_, i) => (
        <rect key={i} x={1360 + i * 80} y={170} width={5} height={775} fill="#7c9fca" />
      ))}
      <Line a={canEnd(...cl, 0, 90)} b={canEnd(...cr, 180, 90)} sag={70} w={4} t={t + 0.8} />
      <Can x={cl[0]} y={cl[1]} angle={0} len={90} dia={68} />
      <Can x={cr[0]} y={cr[1]} angle={180} len={90} dia={68} />
    </svg>
  );
};

// ---------- camera ----------

const LEVELS = [Level0, Level1, Level2];
// Level j-1 sits inside level j at: x_j = A[j] + S[j] * x_{j-1}.
// Level 3 is never drawn: it is screen space, where the middle of the buildings' string
// (960, 380) lands on the frame centre, which is where Prague sits on the 3D Earth.
const S = [1, 0.3, 0.17, 0.02];
const A: P[] = [
  [0, 0],
  [960 - 960 * 0.3, 965 - 672 * 0.3],
  [960 - 960 * 0.17, 945 - 965 * 0.17],
  [960 - 960 * 0.02, 540 - 380 * 0.02],
];

type Tf = { k: number; x: number; y: number };
const apply = (m: Tf, n: Tf): Tf => ({ k: m.k * n.k, x: m.k * n.x + m.x, y: m.k * n.y + m.y });
const up = (j: number): Tf => ({ k: S[j], x: A[j][0], y: A[j][1] }); // level j-1 -> level j
const down = (j: number): Tf => ({ k: 1 / S[j], x: -A[j][0] / S[j], y: -A[j][1] / S[j] }); // level j -> j-1

// u in [0, 3]: 0 = bots fill the frame, 3 = the buildings are a point of light.
const transforms = (u: number): Tf[] => {
  const i = Math.min(Math.floor(u), 2);
  const p = u - i;
  const s = S[i + 1];
  const f: P = [A[i + 1][0] / (1 - s), A[i + 1][1] / (1 - s)];
  const k = Math.pow(s, p);
  const out: Tf[] = [];
  out[i] = { k, x: f[0] * (1 - k), y: f[1] * (1 - k) };
  for (let j = i - 1; j >= 0; j--) out[j] = apply(out[j + 1], up(j + 1));
  for (let j = i + 1; j < LEVELS.length; j++) out[j] = apply(out[j - 1], down(j));
  return out;
};

// fade in when a level gets small enough to read, fade out when it becomes a speck
const IN: [number, number][] = [
  [99, 98],
  [2.3, 1.6],
  [2.4, 1.6],
];
const OUT: [number, number][] = [
  [0.03, 0.012],
  [0.02, 0.008],
  [0.1, 0.045],
];

export const worldU = (t: number, stages: number[]) => {
  // stages = [start, end of L0->L1, ...]; eased per stage but never fully stops
  if (t <= stages[0]) return 0;
  for (let i = 0; i < stages.length - 1; i++) {
    if (t <= stages[i + 1]) {
      const x = (t - stages[i]) / (stages[i + 1] - stages[i]);
      return i + 0.3 * x + 0.7 * x * x * (3 - 2 * x);
    }
  }
  return stages.length - 1;
};

export const World: React.FC<{ t: number; u: number }> = ({ t, u }) => {
  const tf = transforms(u);
  const bg = interpolateColors(u, [0, 2.2, 2.5, 2.75], ["#ffffff", "#ffffff", "#b9d4f0", "#000000"]);
  const sky = ramp(u, 2.55, 2.95);
  // the shrinking buildings become one warm point of light, which becomes Prague
  const dot = ramp(u, 2.45, 2.75) * (1 - ramp(t, SPACE.in + 0.2, SPACE.in + 0.5));
  return (
    <AbsoluteFill style={{ background: bg, overflow: "hidden" }}>
      {sky > 0 && (
        <Img
          src={staticFile("space/sky.jpg")}
          style={{
            position: "absolute",
            width: 2560,
            height: 1440,
            left: -320 - (t - 23) * 22,
            top: -180 + (t - 23) * 6,
            transform: `scale(${1.08 - (t - 23) * 0.012})`,
            opacity: sky,
          }}
        />
      )}
      {t >= SPACE.in - 0.05 && <Space t={t} />}
      {LEVELS.map((L, j) => {
        const m = tf[j];
        const o = Math.min(
          interpolate(m.k, [IN[j][1], IN[j][0]], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
          interpolate(m.k, [OUT[j][1], OUT[j][0]], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
        );
        if (o <= 0.001) return null;
        return (
          <div
            key={j}
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: 1920,
              height: 1080,
              transformOrigin: "0 0",
              transform: `translate(${m.x}px, ${m.y}px) scale(${m.k})`,
              opacity: o,
            }}
          >
            <L t={t} />
          </div>
        );
      }).reverse()}
      {dot > 0 && (
        <div
          style={{
            position: "absolute",
            left: 960 - 60,
            top: 540 - 60,
            width: 120,
            height: 120,
            borderRadius: 120,
            background: "radial-gradient(#fff 0%, #ffd79a 18%, rgba(255,196,107,0.35) 40%, rgba(255,196,107,0) 70%)",
            opacity: dot,
          }}
        />
      )}
    </AbsoluteFill>
  );
};
