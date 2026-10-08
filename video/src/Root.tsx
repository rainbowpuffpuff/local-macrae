import React from "react";
import { CalculateMetadataFunction, Composition, getStaticFiles, staticFile } from "remotion";
import { AUDIO_FILES, Demo, DemoProps } from "./Demo";
import { DURATION, FPS, loadFonts } from "./lib";

loadFonts();

type Props = DemoProps;

// Which audio files exist in public/? Missing ones are skipped, so the video renders without them.
const calculateMetadata: CalculateMetadataFunction<Props> = async () => {
  const listed = new Set(getStaticFiles().map((f) => f.name));
  if (listed.size > 0) return { props: { audio: AUDIO_FILES.filter((f) => listed.has(f)) } };
  // no file list (e.g. Remotion Lambda): ask the server
  const found = await Promise.all(
    AUDIO_FILES.map(async (file) => {
      try {
        const res = await fetch(staticFile(file), { method: "HEAD" });
        return res.ok ? file : null;
      } catch {
        return null;
      }
    }),
  );
  return { props: { audio: found.filter((f): f is string => f !== null) } };
};

export const Root: React.FC = () => (
  <Composition
    id="MacraeDemo"
    component={Demo}
    fps={FPS}
    durationInFrames={DURATION * FPS}
    width={1920}
    height={1080}
    defaultProps={{ audio: [] }}
    calculateMetadata={calculateMetadata}
  />
);
