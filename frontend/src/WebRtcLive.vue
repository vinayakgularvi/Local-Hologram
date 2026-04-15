<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";

const busy = ref(false);
const started = ref(false);
const proxyConfigured = ref(null);
const sessionId = ref("0");
const webrtcError = ref("");

const videoEl = ref(null);
const audioEl = ref(null);

/** @type {RTCPeerConnection | null} */
let pc = null;

let recInstance = null;

const micListening = ref(false);
const voiceThinking = ref(false);
const finalTranscript = ref("");
const interimTranscript = ref("");

/** Silence after last speech before auto-stopping recognition (ms) */
const SILENCE_MS = 1500;
let silenceTimer = null;
let voiceSessionCancelled = false;

const speechRecCtor = computed(() => {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
});

const micAvailable = computed(() => !!speechRecCtor.value);

function apiOrigin() {
  const fromEnv = import.meta.env.VITE_API_BASE;
  if (fromEnv) return String(fromEnv).replace(/\/$/, "");
  if (typeof window !== "undefined") {
    const port = window.location.port;
    const host = window.location.hostname;
    if (port === "5173" || port === "4173") {
      return `http://${host}:8000`;
    }
  }
  return "";
}

function signalingUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  const base = apiOrigin();
  return base ? `${base}${p}` : p;
}

fetch(signalingUrl("/api/webrtc"))
  .then((r) => r.json())
  .then((d) => {
    proxyConfigured.value = Boolean(d.signaling_proxy_configured);
  })
  .catch(() => {
    proxyConfigured.value = false;
  });

function waitIceGatheringComplete(conn) {
  if (conn.iceGatheringState === "complete") {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const onState = () => {
      if (conn.iceGatheringState === "complete") {
        conn.removeEventListener("icegatheringstatechange", onState);
        resolve();
      }
    };
    conn.addEventListener("icegatheringstatechange", onState);
  });
}

async function negotiate() {
  if (!pc) throw new Error("Peer connection not created");
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitIceGatheringComplete(pc);
  const local = pc.localDescription;
  if (!local) throw new Error("Missing local description");

  const res = await fetch(signalingUrl("/offer"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ sdp: local.sdp, type: local.type }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t.slice(0, 400) || `Offer failed (${res.status})`);
  }
  const answer = await res.json();
  if (answer.sessionid !== undefined && answer.sessionid !== null) {
    sessionId.value = String(answer.sessionid);
  }
  await pc.setRemoteDescription(answer);
}

async function connect() {
  webrtcError.value = "";
  if (proxyConfigured.value === false) {
    webrtcError.value = "Server is not configured for WebRTC signaling.";
    return;
  }
  busy.value = true;
  try {
    const config = {
      sdpSemantics: "unified-plan",
      iceServers: [{ urls: ["stun:stun.l.google.com:19302"] }],
    };
    pc = new RTCPeerConnection(config);
    pc.addEventListener("track", (evt) => {
      const stream = evt.streams[0];
      if (!stream) return;
      if (evt.track.kind === "video" && videoEl.value) {
        videoEl.value.srcObject = stream;
      } else if (evt.track.kind === "audio" && audioEl.value) {
        audioEl.value.srcObject = stream;
      }
    });
    pc.addEventListener("connectionstatechange", () => {
      if (pc && (pc.connectionState === "failed" || pc.connectionState === "closed")) {
        webrtcError.value = `Connection ${pc.connectionState}`;
      }
    });
    await negotiate();
    started.value = true;
  } catch (e) {
    webrtcError.value = e instanceof Error ? e.message : String(e);
    if (pc) {
      pc.close();
      pc = null;
    }
  } finally {
    busy.value = false;
  }
}

function disconnect() {
  stopMicInternal({ cancel: true });
  started.value = false;
  if (videoEl.value) videoEl.value.srcObject = null;
  if (audioEl.value) audioEl.value.srcObject = null;
  if (pc) {
    pc.close();
    pc = null;
  }
  sessionId.value = "0";
}

async function postHuman(text) {
  const t = text.trim();
  if (!t) return;
  webrtcError.value = "";
  try {
    const res = await fetch(signalingUrl("/human"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: t,
        type: "echo",
        interrupt: true,
        sessionid: String(sessionId.value),
      }),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(body.slice(0, 400) || `Send failed (${res.status})`);
    }
  } catch (e) {
    webrtcError.value = e instanceof Error ? e.message : String(e);
  }
}

function clearSilenceTimer() {
  if (silenceTimer !== null) {
    window.clearTimeout(silenceTimer);
    silenceTimer = null;
  }
}

function scheduleSilenceStop() {
  clearSilenceTimer();
  silenceTimer = window.setTimeout(() => {
    silenceTimer = null;
    if (recInstance) {
      try {
        recInstance.stop();
      } catch {
        /* ignore */
      }
    }
  }, SILENCE_MS);
}

async function runVoicePipeline(userText) {
  const t = userText.trim();
  if (!t) return;
  voiceThinking.value = true;
  webrtcError.value = "";
  try {
    const res = await fetch(signalingUrl("/api/voice-turn"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ text: t }),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(body.slice(0, 400) || `Ollama step failed (${res.status})`);
    }
    const data = await res.json();
    const answer = String(data.answer || "").trim();
    if (!answer) {
      throw new Error("Model returned an empty reply.");
    }
    await postHuman(answer);
  } catch (e) {
    webrtcError.value = e instanceof Error ? e.message : String(e);
  } finally {
    voiceThinking.value = false;
  }
}

function stopMicInternal({ cancel }) {
  voiceSessionCancelled = cancel;
  clearSilenceTimer();
  if (recInstance) {
    try {
      recInstance.stop();
    } catch {
      /* ignore */
    }
  } else {
    micListening.value = false;
    if (cancel) {
      finalTranscript.value = "";
      interimTranscript.value = "";
    }
  }
}

function toggleMic() {
  webrtcError.value = "";
  if (!started.value) {
    webrtcError.value = "Connecting… try again in a moment.";
    return;
  }
  if (!speechRecCtor.value) {
    webrtcError.value = "Voice input needs a Chromium-based browser.";
    return;
  }
  if (micListening.value) {
    stopMicInternal({ cancel: true });
    return;
  }

  voiceSessionCancelled = false;
  const SR = speechRecCtor.value;
  recInstance = new SR();
  recInstance.lang = (typeof navigator !== "undefined" && navigator.language) || "en-US";
  recInstance.continuous = true;
  recInstance.interimResults = true;
  finalTranscript.value = "";
  interimTranscript.value = "";

  recInstance.onresult = (event) => {
    let interim = "";
    let hasText = false;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const piece = event.results[i][0].transcript;
      if (piece.trim()) hasText = true;
      if (event.results[i].isFinal) {
        finalTranscript.value += `${piece.trim()} `;
      } else {
        interim += piece;
      }
    }
    interimTranscript.value = interim;
    if (hasText) {
      scheduleSilenceStop();
    }
  };

  recInstance.onspeechstart = () => {
    clearSilenceTimer();
  };

  recInstance.onspeechend = () => {
    scheduleSilenceStop();
  };

  recInstance.onerror = (ev) => {
    const err = ev.error || "recognition_error";
    webrtcError.value = String(err);
    stopMicInternal({ cancel: true });
  };

  recInstance.onend = () => {
    clearSilenceTimer();
    recInstance = null;
    micListening.value = false;
    if (voiceSessionCancelled) {
      voiceSessionCancelled = false;
      finalTranscript.value = "";
      interimTranscript.value = "";
      return;
    }
    const combined = `${finalTranscript.value} ${interimTranscript.value}`.trim();
    finalTranscript.value = "";
    interimTranscript.value = "";
    if (combined) {
      void runVoicePipeline(combined);
    }
  };

  try {
    recInstance.start();
    micListening.value = true;
  } catch (e) {
    webrtcError.value = e instanceof Error ? e.message : String(e);
    recInstance = null;
    micListening.value = false;
  }
}

const liveCaption = computed(() => {
  if (voiceThinking.value) {
    return "Getting reply…";
  }
  return `${finalTranscript.value} ${interimTranscript.value}`.trim();
});

onMounted(() => {
  void connect();
});

onUnmounted(() => {
  disconnect();
});
</script>

<template>
  <section class="panel">
    <div class="media-stack">
      <div class="video-wrap">
        <video ref="videoEl" class="video" autoplay playsinline />
        <div class="video-rail video-rail--left" aria-hidden="true" />
        <div class="video-rail video-rail--right" aria-hidden="true" />
        <audio ref="audioEl" class="sr-only" autoplay />

        <div class="caption" aria-live="polite">
          {{ liveCaption || "\u00a0" }}
        </div>

        <button
          type="button"
          class="mic-fab"
          :class="{ 'mic-fab--on': micListening }"
          :disabled="!started || !micAvailable"
          :aria-pressed="micListening"
          :aria-label="micListening ? 'Listening — tap to cancel' : 'Microphone off — tap to speak'"
          :title="
            !micAvailable
              ? 'Voice input unavailable'
              : micListening
                ? 'Listening… tap to cancel'
                : 'Tap to speak'
          "
          @click="toggleMic"
        >
          <svg
            class="mic-svg"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
            focusable="false"
          >
            <path
              class="mic-svg__capsule"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"
            />
            <path
              class="mic-svg__arc"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M19 10v2a7 7 0 0 1-14 0v-2"
            />
            <path
              class="mic-svg__stand"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M12 19v3M8 22h8"
            />
            <path
              v-if="!micListening"
              class="mic-svg__slash"
              stroke="currentColor"
              stroke-width="2.25"
              stroke-linecap="round"
              d="M4.5 4.5L19.5 19.5"
            />
          </svg>
        </button>
      </div>
    </div>

    <div v-if="webrtcError || busy" class="bottom-bar">
      <p v-if="busy && !webrtcError" class="status" role="status">Connecting…</p>
      <p v-if="webrtcError" class="err" role="alert">{{ webrtcError }}</p>
    </div>
  </section>
</template>

<style scoped>
/* Design canvas: portrait 2490 × 3840 (scales down to fit viewport) */
.panel {
  position: relative;
  flex: 1;
  min-height: 0;
  width: 100%;
  max-width: none;
  display: block;
  background: #e4e2e2;
}

.media-stack {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.video-wrap {
  position: relative;
  flex: none;
  aspect-ratio: 2490 / 3840;
  width: min(2490px, 100vw, calc(100dvh * 2490 / 3840));
  height: auto;
  background: #e4e2e2;
  overflow: hidden;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.06);
}

.video {
  display: block;
  position: absolute;
  inset: 0;
  z-index: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center center;
}

.video-rail {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 1;
  width: clamp(4.25rem, 20vw, 15rem);
  background: #e4e2e2;
  pointer-events: none;
}

.video-rail--left {
  left: 0;
}

.video-rail--right {
  right: 0;
}

.caption {
  position: absolute;
  left: 50%;
  top: 0;
  transform: translateX(-50%);
  z-index: 3;
  box-sizing: border-box;
  width: min(40rem, calc(100% - min(7.5rem, 14vw) - 2rem));
  margin: 0;
  padding: calc(0.9rem + env(safe-area-inset-top)) 1rem 0.75rem;
  min-height: 2.75rem;
  max-height: 36vh;
  overflow-y: auto;
  font-size: clamp(1.2rem, 4.5vw, 1.85rem);
  line-height: 1.42;
  font-weight: 600;
  letter-spacing: 0.01em;
  text-align: center;
  color: #fff;
  text-shadow:
    0 0 1px rgba(0, 0, 0, 0.95),
    0 0 14px rgba(0, 0, 0, 0.75),
    0 1px 3px rgba(0, 0, 0, 0.9),
    0 2px 12px rgba(0, 0, 0, 0.55);
  overflow-wrap: anywhere;
  pointer-events: none;
  -webkit-font-smoothing: antialiased;
}

.mic-fab {
  position: absolute;
  top: 27%;
  right: max(1rem, env(safe-area-inset-right));
  bottom: auto;
  transform: translateY(-50%);
  z-index: 3;
  width: min(7.5rem, 14vw);
  height: min(7.5rem, 14vw);
  min-width: 2.75rem;
  min-height: 2.75rem;
  padding: 0;
  border: 1px solid #e0e0e0;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #333;
  background: #fff;
  box-shadow:
    0 2px 14px rgba(0, 0, 0, 0.08),
    0 0 0 1px rgba(255, 255, 255, 0.9) inset;
  transition:
    transform 0.15s ease,
    background 0.2s ease,
    box-shadow 0.2s ease,
    border-color 0.2s ease;
}

.mic-fab:hover:not(:disabled) {
  transform: translateY(-50%) scale(1.05);
  border-color: #ccc;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.1);
}

.mic-fab:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.mic-fab--on {
  color: #008b8b;
  border-color: rgba(0, 200, 200, 0.55);
  background: rgba(0, 200, 200, 0.1);
  box-shadow:
    0 0 0 2px rgba(0, 200, 200, 0.35),
    0 4px 18px rgba(0, 180, 180, 0.15);
  animation: mic-pulse 1.4s ease-in-out infinite;
}

@keyframes mic-pulse {
  50% {
    box-shadow:
      0 0 0 3px rgba(0, 200, 200, 0.2),
      0 4px 22px rgba(0, 180, 180, 0.12);
  }
}

.mic-svg {
  width: 52%;
  height: 52%;
  display: block;
  overflow: visible;
}

.bottom-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 4;
  padding: 0.5rem 0.75rem calc(0.5rem + env(safe-area-inset-bottom));
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(10px);
  border-top: 1px solid rgba(0, 0, 0, 0.06);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.status {
  margin: 0;
  font-size: 0.85rem;
  color: #444;
}

.err {
  margin: 0;
  font-size: 0.85rem;
  color: #b00020;
}
</style>
