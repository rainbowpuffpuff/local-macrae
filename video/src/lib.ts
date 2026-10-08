import { continueRender, delayRender, Easing, interpolate, spring, staticFile } from "remotion";

export const FPS = 60;
export const DURATION = 30; // seconds

export const lerp = (a: number, b: number, x: number) => a + (b - a) * x;
export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

// 0..1 between t0 and t1 (clamped, eased)
export const ramp = (t: number, t0: number, t1: number, easing = Easing.inOut(Easing.cubic)) =>
  interpolate(t, [t0, t1], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing });

// Snappy spring starting at t0 (0 before t0)
export const pop = (t: number, t0: number, config = { damping: 18, stiffness: 190 }) =>
  t < t0 ? 0 : spring({ frame: (t - t0) * FPS, fps: FPS, config });

// Text typed out between t0 and t1 (emoji-safe)
export const typed = (text: string, t: number, t0: number, t1: number) => {
  const chars = Array.from(text);
  return chars.slice(0, Math.round(chars.length * ramp(t, t0, t1, Easing.linear))).join("");
};

// Camera keyframes: z = zoom, (x, y) = point shown at frame centre.
// Between keys the zoom is exponential and the centre follows the zoom's fixed point,
// so moves read as a real push-in / pull-out instead of a slide.
export type Cam = { t: number; z: number; x: number; y: number };
export const camAt = (t: number, keys: Cam[], easing = Easing.inOut(Easing.cubic)): Cam => {
  if (t <= keys[0].t) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t > b.t) continue;
    const e = ramp(t, a.t, b.t, easing);
    const z = a.z * Math.pow(b.z / a.z, e);
    const w = Math.abs(b.z - a.z) < 1e-6 ? e : (1 - a.z / z) / (1 - a.z / b.z);
    return { t, z, x: lerp(a.x, b.x, w), y: lerp(a.y, b.y, w) };
  }
  return keys[keys.length - 1];
};

// One bundled font set, so every render machine draws the same glyphs (public/fonts, all OFL).
export const FONT = '"Inter", "Noto Color Emoji", sans-serif';
export const MONO = '"JetBrains Mono", "Noto Color Emoji", monospace';
export const EMOJI = '"Noto Color Emoji"';

const FACES: [string, string, FontFaceDescriptors][] = [
  ["Inter", "fonts/InterVariable.woff2", { weight: "100 900", style: "normal" }],
  ["Inter", "fonts/InterVariable-Italic.woff2", { weight: "100 900", style: "italic" }],
  ["JetBrains Mono", "fonts/JetBrainsMono-Variable.woff2", { weight: "100 800", style: "normal" }],
  ["Noto Color Emoji", "fonts/NotoColorEmoji-subset.woff2", { weight: "400", style: "normal" }],
];

let fontsRequested = false;
export const loadFonts = () => {
  if (fontsRequested || typeof document === "undefined") return;
  fontsRequested = true;
  const handle = delayRender("Loading fonts");
  Promise.all(
    FACES.map(async ([family, file, desc]) => {
      const face = new FontFace(family, `url(${staticFile(file)}) format("woff2")`, desc);
      document.fonts.add(await face.load());
    }),
  )
    .then(() => continueRender(handle))
    .catch((err) => {
      console.error(err);
      continueRender(handle);
    });
};
