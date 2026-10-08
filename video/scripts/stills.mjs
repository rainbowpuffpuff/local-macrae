// Render review stills: node scripts/stills.mjs 1 3.5 5 6.8 8.6 13.9 16.5 21.5 25.2 28.5  (seconds)
// SCALE=1 for full-size stills (default 0.5).
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { mkdirSync } from "node:fs";

const DEFAULT = [1.0, 3.5, 5.0, 6.8, 8.6, 13.9, 16.5, 21.5, 25.2, 28.5]; // STORYBOARD.md "Review stills"
const seconds = process.argv.length > 2 ? process.argv.slice(2).map(Number) : DEFAULT;

const serveUrl = await bundle({ entryPoint: "src/index.ts" });
const composition = await selectComposition({ serveUrl, id: "MacraeDemo" });
mkdirSync("out/stills", { recursive: true });
for (const s of seconds) {
  const output = `out/stills/t${s.toFixed(2)}.jpg`;
  await renderStill({ serveUrl, composition, frame: Math.round(s * composition.fps), output, imageFormat: "jpeg", jpegQuality: 85, scale: Number(process.env.SCALE ?? 0.5) });
  console.log(output);
}
