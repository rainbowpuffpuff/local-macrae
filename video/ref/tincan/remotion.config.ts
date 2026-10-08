import { Config } from "@remotion/cli/config";

Config.setEntryPoint("src/index.ts");
Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(95);
Config.setCrf(16);
Config.setChromiumOpenGlRenderer("angle"); // WebGL for the 3D finale (space.tsx)
