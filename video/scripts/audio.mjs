// Generates the voiceover clips, the music and the sound effects into public/ (STORYBOARD.md: "Voiceover script",
// "Music brief", "SFX"). Needs ELEVENLABS_API_KEY in .env (see .env.example); click/whoosh come from remotion.media.
// Run: npm run audio            (all)
//      npm run audio -- vo      (voiceover only)
//      npm run audio -- music   (music only)
//      npm run audio -- sfx     (sound effects only)
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

// .env without --env-file, so Node 18 works too
if (existsSync(".env")) {
  for (const line of readFileSync(".env", "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
const only = process.argv[2];
const KEY = process.env.ELEVENLABS_API_KEY;
const needKey = () => {
  if (!KEY) throw new Error("ELEVENLABS_API_KEY missing (put it in .env, see .env.example)");
};

const post = async (path, body) => {
  needKey();
  const res = await fetch(`https://api.elevenlabs.io${path}`, {
    method: "POST",
    headers: { "xi-api-key": KEY, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} ${res.status}: ${await res.text()}`);
  return Buffer.from(await res.arrayBuffer());
};

const hasFfmpeg = (() => {
  try {
    execFileSync("ffprobe", ["-version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
})();
const duration = (file) =>
  hasFfmpeg ? Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file]).toString()) : NaN;

for (const dir of ["public/vo", "public/sfx"]) mkdirSync(dir, { recursive: true });

// ---------------- voiceover ----------------
// Voice "Jarvis (video)". One clip per line so each lands exactly on its beat; previous_text/next_text keep the
// delivery consistent. The TTS text respells what the model mispronounces; the screen keeps the real spelling:
// Jungwirth = "YOONG-virt", 2019 = "twenty nineteen". Set RESPELL=0 to send the real spelling instead.
const VOICE = process.env.ELEVENLABS_VOICE_ID || "wNrcUqZN35sbTl2vAQU2";
const respell = process.env.RESPELL !== "0";
const J = respell ? "Yoong-virt" : "Jungwirth";
// [id, text as spoken, target length in s] (vo-user is not generated: record a real group member if wanted)
const LINES = [
  ["vo1", "I'm Jarvis.", 0.9],
  ["vo2", `I've read the ${J} group's papers.`, 2.0],
  ["vo3", "Ask me anything about them.", 1.4],
  ["vo4a", "Scaled charges capture electronic polarization that standard force fields miss.", 3.6],
  ["vo4b", `Kirby and ${J}, twenty nineteen.`, 1.7],
  ["vo5", "Pick a task, and I run it on Modal.", 1.9],
  ["vo6", "You see every paper I read…", 1.6],
  ["vo7", "…and every number I calculate.", 1.8],
  ["vo8", "Done. All of it, traced.", 1.7],
  ["vo9", "Macrae.", 0.8],
];

if (!only || only === "vo") {
  needKey();
  const over = [];
  for (const [i, [id, text, target]] of LINES.entries()) {
    const audio = await post(`/v1/text-to-speech/${VOICE}?output_format=mp3_44100_192`, {
      text,
      model_id: "eleven_multilingual_v2",
      previous_text: LINES.slice(0, i).map((l) => l[1]).join(" ") || undefined,
      next_text: LINES.slice(i + 1).map((l) => l[1]).join(" ") || undefined,
      voice_settings: { stability: 0.5, similarity_boost: 0.8, style: 0.25, use_speaker_boost: true },
    });
    const file = `public/vo/${id}.mp3`;
    writeFileSync(file, audio);
    const d = duration(file);
    if (d > target) over.push(`${id} ${d.toFixed(2)}s > ${target}s`);
    console.log(file, Number.isNaN(d) ? "" : `${d.toFixed(2)}s (target ≤ ${target}s)`, "-", text);
  }
  // Fit rule (STORYBOARD.md): shorten the line rather than speeding it up
  if (over.length) console.warn(`\nOver target, shorten these lines:\n  ${over.join("\n  ")}`);
}

// ---------------- music ----------------
// 30 s at 100 BPM in D major: orbit intro with a bloom on the 5.4 s reveal, a light demo bed, a computation
// pulse for the trace, and one bright hit at 27.2 s that rings out.
if (!only || only === "music") {
  // music_v2_5 plan schema ("chunks"; bracketed text = instrumental section tag), as in the tin-can video
  const NEG = ["vocals", "singing", "EDM drop", "dubstep", "trailer braams", "heavy distorted drums", "big lead melody"];
  const chunk = (text, duration_ms, positive_styles, negative_styles = []) => ({
    text,
    duration_ms,
    positive_styles: ["100 BPM", "D major, add9 and sus2 voicings", "instrumental", "scientific, warm, quietly confident, curious", ...positive_styles],
    negative_styles: [...NEG, "corporate", "epic trailer", ...negative_styles],
    context_adherence: "high",
  });
  const plan = {
    chunks: [
      chunk("[Orbit Intro]", 5400, [
        "weightless ambient pad",
        "glassy celesta arpeggio circling like orbits",
        "soft sub drone",
        "slow rise, swelling from 3.4 seconds",
        "reverse cymbal and airy riser in the last 2 seconds",
        "soft bright bloom chord on the final downbeat",
        "wide stereo",
      ], ["drums"]),
      chunk("[Demo Bed]", 12000, [
        "light warm curious electronic",
        "plucked synth arpeggio",
        "felt piano chords",
        "muted kick on 1 and 3",
        "brushed shaker",
        "sparse, clear mid-range for voiceover, no lead",
        "thins out in the middle, small lift at the end",
      ]),
      chunk("[Trace Pulse]", 9200, [
        "same key and tempo",
        "ticking hi-hats",
        "pulsing 16th-note sequencer",
        "gentle steady build in density",
        "leaves room for voiceover",
        "holds, then pulls back for a beat at the end",
      ]),
      chunk("[Return and Final Hit]", 3400, [
        "0.6 second reverse swell into one bright warm chord hit",
        "chimes and celesta on the hit",
        "long reverb tail ringing out to the end",
      ], ["drums after the hit", "new melody"]),
    ],
  };
  const file = "public/music.mp3";
  writeFileSync(
    file,
    await post("/v1/music?output_format=mp3_44100_192", {
      composition_plan: plan,
      model_id: process.env.MUSIC_MODEL || "music_v2_5",
      respect_sections_durations: true,
    }),
  );
  console.log(file, Number.isNaN(duration(file)) ? "" : `${duration(file).toFixed(2)}s`);
}

// ---------------- sound effects ----------------
const REMOTION_MEDIA = {
  "sfx/click.wav": "https://remotion.media/mouse-click.wav",
  "sfx/whoosh.wav": "https://remotion.media/whoosh.wav",
};
// [file, prompt, seconds]
const GENERATED = [
  ["sfx/shimmer.mp3", "soft bright magical shimmer, glassy chime bloom swelling gently, clean, airy", 1.5],
  ["sfx/blip.mp3", "soft clean digital connect blip, short friendly UI sound", 0.5],
  ["sfx/tick.mp3", "very short soft UI tick, subtle muted click, clean", 0.5],
  ["sfx/bright.mp3", "short bright crisp digital blip, glassy UI sound, a little higher than a tick", 0.5],
  ["sfx/chime-a.mp3", "single soft glassy notification chime, high note, clean, short", 0.8],
  ["sfx/chime-b.mp3", "single soft glassy notification chime, slightly lower note, clean, short", 0.8],
  ["sfx/done.mp3", "warm success chime, two ascending soft bell notes, clean UI sound", 1.2],
];

if (!only || only === "sfx") {
  for (const [file, url] of Object.entries(REMOTION_MEDIA)) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} ${res.status}`);
    writeFileSync(`public/${file}`, Buffer.from(await res.arrayBuffer()));
    console.log(`public/${file}`, "(remotion.media)");
  }
  if (hasFfmpeg) {
    execFileSync("ffmpeg", ["-v", "error", "-y", "-i", "public/sfx/whoosh.wav", "-af", "areverse", "public/sfx/whoosh-rev.wav"]);
    console.log("public/sfx/whoosh-rev.wav (reversed whoosh)");
  } else console.warn("ffmpeg not found: skipped public/sfx/whoosh-rev.wav (the pull-out plays without it)");
  needKey();
  for (const [file, text, seconds] of GENERATED) {
    writeFileSync(`public/${file}`, await post("/v1/sound-generation?output_format=mp3_44100_192", { text, duration_seconds: seconds, prompt_influence: 0.6 }));
    console.log(`public/${file}`, "-", text);
  }
}
