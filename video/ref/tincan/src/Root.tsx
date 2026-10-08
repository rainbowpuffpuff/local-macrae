import React from "react";
import { Composition } from "remotion";
import { Demo } from "./Demo";
import { DURATION, FPS } from "./lib";

export const Root: React.FC = () => (
  <Composition id="TinCanDemo" component={Demo} fps={FPS} durationInFrames={DURATION * FPS} width={1920} height={1080} />
);
