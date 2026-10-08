// The voice session: ElevenLabs Conversational AI through @elevenlabs/client, loaded from jsDelivr on first use.
// The Worker hands out a signed URL (GET /voice/signed-url) so the API key never reaches the browser.
// Audio worklets load from the same pinned CDN path, so the CSP needs no blob: or data: scripts.

const SDK_VERSION = "1.27.0";
const CDN = `https://cdn.jsdelivr.net/npm/@elevenlabs/client@${SDK_VERSION}`;
const SDK_URL = `${CDN}/dist/lib.iife.js`;
const SDK_SRI = "sha384-ekuWfdL0BkeVAWv24yeCWtzVwYDQd4pCkPL5TKVDLmwDBeVxS2cTwo0wfsUNaFkJ";
const WORKLETS = {
  rawAudioProcessor: `${CDN}/worklets/rawAudioProcessor.js`,
  audioConcatProcessor: `${CDN}/worklets/audioConcatProcessor.js`,
};

let sdkPromise = null;

export function loadSdk() {
  if (window.ElevenLabsClient?.Conversation) return Promise.resolve(window.ElevenLabsClient);
  if (sdkPromise) return sdkPromise;
  sdkPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SDK_URL;
    s.integrity = SDK_SRI;
    s.crossOrigin = "anonymous";
    s.async = true;
    s.onload = () => (window.ElevenLabsClient?.Conversation ? resolve(window.ElevenLabsClient) : reject(new Error("the voice library loaded but has no Conversation")));
    s.onerror = () => reject(new Error("could not load the voice library from cdn.jsdelivr.net"));
    document.head.appendChild(s);
  }).catch((err) => {
    sdkPromise = null;
    throw err;
  });
  return sdkPromise;
}

// Warm the CDN cache when the user is about to click.
export function preloadSdk() {
  loadSdk().catch(() => {});
}

async function fetchSignedUrl() {
  let res;
  try {
    res = await fetch("/voice/signed-url", { headers: { accept: "application/json" }, cache: "no-store" });
  } catch {
    throw new Error("Can't reach this site's server for a voice session.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.signed_url) {
    if (res.status === 503) throw new Error("Voice isn't set up on this deployment yet. You can still type below.");
    if (res.status === 429) throw new Error("Too many voice sessions from here. Try again in a minute.");
    throw new Error(data.error || `Voice session refused (${res.status}).`);
  }
  return data.signed_url;
}

function micError(err) {
  const name = err && err.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone blocked. Allow it for this site in the browser, or type below.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone found. You can type below.";
  if (name === "NotReadableError") return "The microphone is busy in another app.";
  return null;
}

// handlers: onStatus(state, text), onMode(mode), onMessage({role, text, id}), onTool({name, phase, isError, result}),
//           onLevel(0..1), clientTools {name: fn}
export class Voice {
  constructor(handlers) {
    this.h = handlers;
    this.conv = null;
    this.state = "idle";
    this.muted = false;
    this.raf = 0;
  }

  get live() {
    return this.state === "live" && !!this.conv;
  }

  set(state, text) {
    this.state = state;
    this.h.onStatus?.(state, text);
  }

  async start() {
    if (this.state === "connecting" || this.live) return;
    this.set("connecting", "Connecting…");
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Voice needs a secure (https) page and a browser with microphone support.");
      // Ask for the mic first: a clear prompt now beats a silent failure inside the SDK.
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
      } catch (err) {
        throw new Error(micError(err) || `Microphone error: ${err.message || err}`);
      }
      const [sdk, signedUrl] = await Promise.all([loadSdk(), fetchSignedUrl()]);
      if (this.state !== "connecting") return; // stopped while we waited
      this.conv = await sdk.Conversation.startSession({
        signedUrl,
        connectionType: "websocket",
        workletPaths: WORKLETS,
        clientTools: this.h.clientTools || {},
        onConnect: () => this.set("live", "Listening. Ask away."),
        onDisconnect: (d) => this.ended(d),
        onError: (message) => this.h.onError?.(String(message || "voice error")),
        onModeChange: ({ mode }) => {
          this.h.onMode?.(mode);
          if (this.live) this.h.onStatus?.("live", mode === "speaking" ? "Speaking…" : this.muted ? "Muted" : "Listening…");
        },
        onMessage: (m) => {
          const role = m.role === "user" || m.source === "user" ? "user" : "agent";
          if (m.message) this.h.onMessage?.({ role, text: m.message, id: m.response_id || m.event_id });
        },
        onAgentToolRequest: (t) => this.h.onTool?.({ name: t.tool_name, phase: "request" }),
        onAgentToolResponse: (t) =>
          this.h.onTool?.({ name: t.tool_name, phase: "response", isError: !!t.is_error, result: t.full_tool_result }),
      });
      if (this.state === "connecting") this.set("live", "Listening. Ask away.");
      this.muted = false;
      this.meter();
    } catch (err) {
      const conv = this.conv;
      this.conv = null;
      if (conv) conv.endSession().catch(() => {});
      this.set("error", (err && err.message) || "Could not start the voice session.");
    }
  }

  ended(details) {
    cancelAnimationFrame(this.raf);
    this.h.onLevel?.(0);
    this.conv = null;
    if (this.state === "idle") return;
    if (details && details.reason === "error") this.set("error", `The call dropped: ${details.message || "connection error"}.`);
    else if (details && details.reason === "agent") this.set("idle", "The agent ended the conversation.");
    else this.set("idle", "Conversation ended.");
  }

  async stop() {
    const conv = this.conv;
    this.conv = null;
    cancelAnimationFrame(this.raf);
    this.set("idle", "Conversation ended.");
    this.h.onLevel?.(0);
    if (conv) await conv.endSession().catch(() => {});
  }

  toggleMute() {
    if (!this.live) return this.muted;
    this.muted = !this.muted;
    this.conv.setMicMuted(this.muted);
    this.h.onStatus?.("live", this.muted ? "Muted. The agent can't hear you." : "Listening…");
    return this.muted;
  }

  sendText(text) {
    if (!this.live) return false;
    this.conv.sendUserMessage(text);
    return true;
  }

  // Tells the agent what happened on the page without making it speak.
  context(text) {
    if (!this.live) return;
    try {
      this.conv.sendContextualUpdate(text);
    } catch {}
  }

  meter() {
    const tick = () => {
      if (!this.live) return;
      let level = 0;
      try {
        level = Math.max(this.conv.getOutputVolume?.() || 0, this.muted ? 0 : this.conv.getInputVolume?.() || 0);
      } catch {}
      this.h.onLevel?.(Math.min(1, level * 1.6));
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }
}
