import React from "react";
import { AbsoluteFill, Easing, interpolate, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import { FPS, ramp } from "./lib";
import { AppScene, MSGS, T } from "./app";
import { EndCard, TinCanScene } from "./scenes";
import { World, worldU } from "./world";

const STAGES = [19.9, 21.3, 22.55, 23.6]; // zoom-out: bots -> people -> buildings -> city light
const END = 28.0;

// [start second, file, volume]
const VO: [number, string][] = [
  [0.1, "vo1"],
  [2.2, "vo2"],
  [6.2, "vo3"],
  [9.95, "vo4a"],
  [11.45, "vo4b"],
  [14.55, "vo4c"],
  [17.2, "vo5a"],
  [19.1, "vo5b"],
];
const typeTicks = (t0: number, t1: number, n: number) => Array.from({ length: n }, (_, i) => t0 + ((t1 - t0) * i) / n);
const SFX: [number, string, number][] = [
  [0.02, "blip", 0.35],
  ...typeTicks(...T.type1, 6).map((s): [number, string, number] => [s, "tick", 0.1]),
  [T.addClick, "click", 0.55],
  [T.toGroup, "blip", 0.3],
  ...typeTicks(...T.type2, 11).map((s): [number, string, number] => [s, "tick", 0.08]),
  ...T.checks.map((s): [number, string, number] => [s, "click", 0.4]),
  [T.createClick, "click", 0.55],
  [5.75, "whoosh", 0.35],
  ...MSGS.filter((m) => m.from !== "me").map((m, i): [number, string, number] => [m.at, i % 2 ? "chime-b" : "chime-a", 0.3]),
  ...typeTicks(...T.compose, 9).map((s): [number, string, number] => [s, "tick", 0.08]),
  [T.send, "send", 0.4],
  [T.playClick, "click", 0.55],
  [T.cardZoom[0] + 0.1, "whoosh", 0.4],
];

const at = (s: number) => Math.round(s * FPS);

export const Demo: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const u = worldU(t, STAGES);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {(t < 6.05 || (t >= 7.9 && t < 19.1)) && <AppScene t={t} />}
      {/* UI -> white -> tin-can beat -> white -> chat (no muddy cross-fades) */}
      {t >= T.whiteIn && t < T.chatIn + 0.01 && (
        <AbsoluteFill style={{ background: "#fff", opacity: ramp(t, T.whiteIn, T.whiteIn + 0.22) }}>
          <AbsoluteFill style={{ opacity: ramp(t, 5.9, 6.08) * (1 - ramp(t, 7.78, T.chatIn)) }}>
            <TinCanScene t={Math.max(t, 6)} />
          </AbsoluteFill>
        </AbsoluteFill>
      )}
      {t >= 19.02 && t < END + 0.4 && (
        <AbsoluteFill style={{ opacity: ramp(t, 19.02, 19.08) }}>
          <World t={t} u={u} />
        </AbsoluteFill>
      )}
      {t >= END - 0.12 && (
        <AbsoluteFill style={{ clipPath: `circle(${2300 * ramp(t, END - 0.12, END + 0.22, Easing.in(Easing.cubic))}px at 960px 560px)` }}>
          <EndCard t={t} />
        </AbsoluteFill>
      )}

      {/* audio */}
      <Audio
        src={staticFile("music.mp3")}
        volume={(f) => {
          const s = f / FPS;
          return interpolate(s, [0, 0.4, 19.0, 20.0, 29.4, 30], [0, 0.2, 0.2, 0.72, 0.72, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        }}
      />
      {VO.map(([s, id]) => (
        <Sequence key={id} from={at(s)} layout="none">
          <Audio src={staticFile(`vo/${id}.mp3`)} volume={1} />
        </Sequence>
      ))}
      {SFX.map(([s, id, v], i) => (
        <Sequence key={i} from={at(s)} durationInFrames={at(1.5)} layout="none">
          <Audio src={staticFile(`sfx/${id}.wav`)} volume={v} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
