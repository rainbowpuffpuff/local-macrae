import React from "react";
import { AbsoluteFill, interpolate, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import { FPS } from "./lib";
import { AppScene, T, TRACE } from "./app";
import { LogoScene, OUT_START } from "./logo";

// Audio files the composition can use (paths in public/). Root.tsx checks which exist; missing ones are skipped,
// so the video renders before `npm run audio` has been run.
export const MUSIC = "music.mp3";

// [start second, clip] - one clip per line, see STORYBOARD.md "Voiceover script"
const VO: [number, string][] = [
  [0.4, "vo1"],
  [1.45, "vo2"],
  [5.9, "vo3"],
  [7.6, "vo-user"], // optional: a real group member asking the question (not generated)
  [9.8, "vo4a"],
  [13.5, "vo4b"],
  [15.55, "vo5"],
  [18.6, "vo6"],
  [21.0, "vo7"],
  [24.8, "vo8"],
  [27.75, "vo9"],
];

// click/whoosh from remotion.media (whoosh-rev is it reversed), the rest from ElevenLabs sound effects
export const SFX_FILES: Record<string, string> = {
  click: "sfx/click.wav",
  whoosh: "sfx/whoosh.wav",
  "whoosh-rev": "sfx/whoosh-rev.wav",
  shimmer: "sfx/shimmer.mp3",
  blip: "sfx/blip.mp3",
  tick: "sfx/tick.mp3",
  bright: "sfx/bright.mp3",
  "chime-a": "sfx/chime-a.mp3",
  "chime-b": "sfx/chime-b.mp3",
  done: "sfx/done.mp3",
};

const SFX: [number, string, number][] = [
  [4.25, "whoosh", 0.4],
  [5.05, "shimmer", 0.25],
  [T.talkClick, "click", 0.55],
  [T.live, "blip", 0.3],
  [T.tool, "tick", 0.1],
  [T.sources[0], "chime-a", 0.3],
  [T.sources[1], "chime-b", 0.3],
  [T.runClick, "click", 0.55],
  [16.8, "whoosh", 0.35],
  ...TRACE.map((r): [number, string, number] => (r.type === "calc" ? [r.at, "bright", 0.14] : [r.at, "tick", 0.12])),
  [T.done, "done", 0.35],
  [OUT_START, "whoosh-rev", 0.4],
];

// music bed: [second, volume] (STORYBOARD.md "Mix")
const MUSIC_ENV: [number, number][] = [
  [0, 0.28],
  [3.2, 0.28],
  [3.6, 0.75], // swell into the dive
  [5.6, 0.75],
  [5.85, 0.2], // under vo3
  [7.35, 0.2],
  [7.55, 0.3], // the silent question
  [9.7, 0.3],
  [9.9, 0.18], // under the answer
  [15.2, 0.18],
  [15.5, 0.22], // under the trace
  [26.4, 0.22],
  [26.6, 0.75], // the return and final hit
  [27.6, 0.75],
  [27.75, 0.55], // duck under "Macrae."
  [28.4, 0.55],
  [28.6, 0.7],
  [29.4, 0.7],
  [30, 0],
];

const at = (s: number) => Math.round(s * FPS);

export type DemoProps = { audio: string[] };
export const Demo: React.FC<DemoProps> = ({ audio }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const has = (file: string) => audio.includes(file);
  return (
    <AbsoluteFill style={{ background: "#fff" }}>
      {/* 1-3: logo, flow, dive into the core (the landing page appears inside it) */}
      {t < 5.6 && <LogoScene t={t} page={<AppScene t={t} />} />}
      {/* 4-9: the app */}
      {t >= 5.6 && t < OUT_START && <AppScene t={t} />}
      {/* 10: the page closes into the core, back out into the orbits, end card */}
      {t >= OUT_START && <LogoScene t={t} page={<AppScene t={t} />} />}

      {/* audio */}
      {has(MUSIC) && (
        <Audio
          src={staticFile(MUSIC)}
          volume={(f) =>
            interpolate(
              f / FPS,
              MUSIC_ENV.map((k) => k[0]),
              MUSIC_ENV.map((k) => k[1]),
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
            )
          }
        />
      )}
      {VO.filter(([, id]) => has(`vo/${id}.mp3`)).map(([s, id]) => (
        <Sequence key={id} from={at(s)} layout="none">
          <Audio src={staticFile(`vo/${id}.mp3`)} volume={1} />
        </Sequence>
      ))}
      {SFX.filter(([, id]) => has(SFX_FILES[id])).map(([s, id, v], i) => (
        <Sequence key={i} from={at(s)} durationInFrames={at(2)} layout="none">
          <Audio src={staticFile(SFX_FILES[id])} volume={v} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};

export const AUDIO_FILES = [MUSIC, ...VO.map(([, id]) => `vo/${id}.mp3`), ...Object.values(SFX_FILES)];
