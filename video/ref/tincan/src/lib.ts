import { Easing, interpolate, spring } from "remotion";

export const FPS = 60;
export const DURATION = 30; // seconds

export const lerp = (a: number, b: number, x: number) => a + (b - a) * x;

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
export const camAt = (t: number, keys: Cam[]): Cam => {
  if (t <= keys[0].t) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t > b.t) continue;
    const e = ramp(t, a.t, b.t);
    const z = a.z * Math.pow(b.z / a.z, e);
    const w = Math.abs(b.z - a.z) < 1e-6 ? e : (1 - a.z / z) / (1 - a.z / b.z);
    return { t, z, x: lerp(a.x, b.x, w), y: lerp(a.y, b.y, w) };
  }
  return keys[keys.length - 1];
};

export const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif';
