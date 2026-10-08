// Generates voiceover clips + background music with ElevenLabs into public/.
// Run: npm run audio            (all)
//      npm run audio -- vo      (voiceover only)
//      npm run audio -- music   (music only)
import { writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const KEY = process.env.ELEVENLABS_API_KEY;
if (!KEY) throw new Error("ELEVENLABS_API_KEY missing (put it in .env)");
const only = process.argv[2];

const post = async (path, body) => {
  const res = await fetch(`https://api.elevenlabs.io${path}`, {
    method: "POST",
    headers: { "xi-api-key": KEY, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} ${res.status}: ${await res.text()}`);
  return Buffer.from(await res.arrayBuffer());
};

const duration = (file) =>
  Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file]).toString());

// Voiceover: one clip per beat so each lands exactly on its scene.
// previous_text/next_text keep the delivery consistent across clips.
const VOICE = "cgSgspJ2msm6clMCkdW9"; // Jessica – playful, bright, warm
const LINES = [
  ["vo1", "Your Grok Bot is brilliant. And alone."],
  ["vo2", "Add TinCan. Invite people and their bots."],
  ["vo3", "Every pair, connected."],
  ["vo4a", "Ask anyone's bot."],
  ["vo4b", "Bots hand work to each other."],
  ["vo4c", "You just steer."],
  ["vo5a", "That's how we made this video."],
  ["vo5b", "TinCan."],
];

if (!only || only === "vo") {
  for (const [i, [id, text]] of LINES.entries()) {
    const audio = await post(`/v1/text-to-speech/${VOICE}?output_format=mp3_44100_192`, {
      text,
      model_id: "eleven_multilingual_v2",
      previous_text: LINES.slice(0, i).map((l) => l[1]).join(" ") || undefined,
      next_text: LINES.slice(i + 1).map((l) => l[1]).join(" ") || undefined,
      voice_settings: { stability: 0.45, similarity_boost: 0.8, style: 0.35, use_speaker_boost: true },
    });
    const file = `public/vo/${id}.mp3`;
    writeFileSync(file, audio);
    console.log(file, duration(file).toFixed(2) + "s", "-", text);
  }
}

// Music: 30 s, light bed under the voiceover, swell for the zoom-out, final hit at ~28 s.
if (!only || only === "music") {
  // music_v2_5 plan schema ("chunks"; bracketed text = instrumental section tag).
  const chunk = (text, duration_ms, positive_styles, negative_styles) => ({
    text,
    duration_ms,
    positive_styles: ["118 BPM", "instrumental", ...positive_styles],
    negative_styles: ["vocals", "singing", ...negative_styles],
    context_adherence: "high",
  });
  const plan = {
    chunks: [
      chunk("[Demo Bed]", 19000, ["light bouncy plucky synths", "soft rhythmic claps", "warm sub bass", "playful indie-electronic groove", "fun and friendly", "sparse arrangement", "space for voiceover", "clean studio production"], ["heavy drums", "big riser"]),
      chunk("[Cosmic Build]", 9000, ["wide cinematic cosmic swell", "rising synth arpeggios", "punchy drums", "airy sweeping pads", "dramatic build and crescendo", "feeling of zooming out into space", "immersive stereo width"], []),
      chunk("[Final Hit]", 3000, ["one bright final synth hit on the downbeat", "long ringing reverb tail", "uplifting resolution chord", "sparkling high-end accent"], ["drums continue", "new melody"]),
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
  console.log(file, duration(file).toFixed(2) + "s");
}
