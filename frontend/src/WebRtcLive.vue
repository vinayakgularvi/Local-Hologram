<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";

const busy = ref(false);
/** True once the remote video is actually rendering (not only SDP done). */
const videoReady = ref(false);
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
/** Parsed <receipt> JSON from last voice turn — shown as live bill on the stage. */
const liveBill = ref(null);
/** Shown at top of stage when <orderdone> is present; live bill is cleared. */
const orderPlacedMessage = ref("");
let orderPlacedHideTimer = null;
const finalTranscript = ref("");
const interimTranscript = ref("");
const WEBRTC_ICE_GATHER_TIMEOUT_MS = Math.max(
  80,
  Number.parseInt(import.meta.env.VITE_WEBRTC_ICE_GATHER_TIMEOUT_MS || "250", 10) || 250
);
const WEBRTC_STUN_URL = String(import.meta.env.VITE_WEBRTC_STUN_URL || "stun:stun.l.google.com:19302").trim();

/** Silence after last speech before auto-stopping recognition (ms) */
const SILENCE_MS = Math.max(
  250,
  Number.parseInt(import.meta.env.VITE_VOICE_SILENCE_MS || "550", 10) || 550
);
/** Once we get a final chunk, stop quickly to reduce turn latency. */
const FINAL_RESULT_STOP_MS = Math.max(
  120,
  Number.parseInt(import.meta.env.VITE_VOICE_FINAL_STOP_MS || "220", 10) || 220
);
let silenceTimer = null;
let voiceSessionCancelled = false;
let toneAudioCtx = null;

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
      return `http://${host}:8080`;
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

function waitIceGatheringFast(conn) {
  if (conn.iceGatheringState === "complete") {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let done = false;
    const timer = window.setTimeout(() => {
      finish();
    }, WEBRTC_ICE_GATHER_TIMEOUT_MS);
    const finish = () => {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      conn.removeEventListener("icegatheringstatechange", onState);
      conn.removeEventListener("icecandidate", onCandidate);
      resolve();
    };
    const onState = () => {
      if (conn.iceGatheringState === "complete") {
        finish();
      }
    };
    const onCandidate = (ev) => {
      const cand = ev?.candidate?.candidate || "";
      // Host candidate is typically available first on local/LAN and enables fast offer send.
      if (cand.includes(" typ host ")) {
        finish();
      }
    };
    conn.addEventListener("icegatheringstatechange", onState);
    conn.addEventListener("icecandidate", onCandidate);
  });
}

async function negotiate() {
  if (!pc) throw new Error("Peer connection not created");
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitIceGatheringFast(pc);
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

function markVideoReadyOnce() {
  videoReady.value = true;
}

async function connect() {
  webrtcError.value = "";
  videoReady.value = false;
  if (proxyConfigured.value === false) {
    webrtcError.value = "Server is not configured for WebRTC signaling.";
    return;
  }
  busy.value = true;
  try {
    const config = {
      sdpSemantics: "unified-plan",
      iceServers: WEBRTC_STUN_URL ? [{ urls: [WEBRTC_STUN_URL] }] : [],
    };
    pc = new RTCPeerConnection(config);
    pc.addEventListener("track", (evt) => {
      const stream = evt.streams[0];
      if (!stream) return;
      if (evt.track.kind === "video" && videoEl.value) {
        try {
          // Hint decoder for detail-first quality where supported (helps with 4K-like streams).
          evt.track.contentHint = "detail";
        } catch {
          /* ignore unsupported browsers */
        }
        const v = videoEl.value;
        v.srcObject = stream;
        const onReady = () => {
          markVideoReadyOnce();
        };
        v.addEventListener("playing", onReady, { once: true });
        v.addEventListener("loadeddata", onReady, { once: true });
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
    videoReady.value = false;
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
  videoReady.value = false;
  if (orderPlacedHideTimer != null) {
    window.clearTimeout(orderPlacedHideTimer);
    orderPlacedHideTimer = null;
  }
  orderPlacedMessage.value = "";
  liveBill.value = null;
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

function scheduleStopAfter(delayMs) {
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
  }, Math.max(80, delayMs));
}

function scheduleSilenceStop() {
  scheduleStopAfter(SILENCE_MS);
}

function playMicTone(kind) {
  if (typeof window === "undefined") return;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  const emit = () => {
    if (!toneAudioCtx || toneAudioCtx.state !== "running") return;
    const now = toneAudioCtx.currentTime;
    const osc = toneAudioCtx.createOscillator();
    const gain = toneAudioCtx.createGain();
    osc.type = kind === "tap" ? "square" : "triangle";
    if (kind === "tap") {
      osc.frequency.setValueAtTime(1200, now);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.32, now + 0.006);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.055);
      osc.stop(now + 0.065);
    } else if (kind === "on") {
      osc.frequency.setValueAtTime(980, now);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.24, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.14);
      osc.stop(now + 0.16);
    } else {
      osc.frequency.setValueAtTime(620, now);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.2, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);
      osc.stop(now + 0.14);
    }
    osc.connect(gain);
    gain.connect(toneAudioCtx.destination);
    osc.start(now);
    osc.onended = () => {
      try {
        osc.disconnect();
        gain.disconnect();
      } catch {
        /* ignore */
      }
    };
  };
  try {
    if (!toneAudioCtx) {
      toneAudioCtx = new AC();
    }
    if (toneAudioCtx.state === "suspended") {
      void toneAudioCtx.resume().then(() => {
        emit();
      });
      return;
    }
    emit();
  } catch {
    /* ignore audio tone failures */
  }
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
    console.log("[voice-turn] response", data);
    const answer = String(data.answer || "").trim();
    let speakText = String(data.speak_text ?? "").trim();
    if (!speakText) {
      speakText = stripReceiptForSpeech(answer);
    }
    let receipt = data.receipt && typeof data.receipt === "object" ? data.receipt : null;
    if (!receipt?.items?.length && answer) {
      receipt = tryParseReceiptFromAnswer(answer);
    }
    let orderNum =
      data.order_done && typeof data.order_done.number === "number" ? data.order_done.number : null;
    if (orderNum == null && answer) {
      orderNum = tryParseOrderDoneFromAnswer(answer);
    }
    if (orderNum != null) {
      liveBill.value = null;
      orderPlacedMessage.value = `Your order no. ${orderNum} placed.`;
      if (orderPlacedHideTimer != null) {
        window.clearTimeout(orderPlacedHideTimer);
      }
      orderPlacedHideTimer = window.setTimeout(() => {
        orderPlacedHideTimer = null;
        orderPlacedMessage.value = "";
      }, 7000);
    } else if (receipt?.items?.length) {
      liveBill.value = { items: receipt.items };
    }
    if (!speakText && !receipt?.items?.length && orderNum == null) {
      throw new Error("Model returned an empty reply.");
    }
    if (speakText) {
      await postHuman(speakText);
    }
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
  // Single utterance mode returns final results faster than long continuous mode.
  recInstance.continuous = false;
  recInstance.interimResults = true;
  recInstance.maxAlternatives = 1;
  finalTranscript.value = "";
  interimTranscript.value = "";

  recInstance.onresult = (event) => {
    let interim = "";
    let hasText = false;
    let sawFinal = false;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const piece = event.results[i][0].transcript;
      if (piece.trim()) hasText = true;
      if (event.results[i].isFinal) {
        sawFinal = true;
        finalTranscript.value += `${piece.trim()} `;
      } else {
        interim += piece;
      }
    }
    interimTranscript.value = interim;
    if (sawFinal) {
      scheduleStopAfter(FINAL_RESULT_STOP_MS);
    } else if (hasText) {
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
    playMicTone("off");
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

function onMicPointerDown() {
  playMicTone("tap");
}

const liveCaption = computed(() => {
  return `${finalTranscript.value} ${interimTranscript.value}`.trim();
});

function stripReceiptForSpeech(raw) {
  return String(raw || "")
    .replace(/<receipt>\s*[\s\S]*?\s*<\/receipt>/gi, "")
    .replace(/<orderdone>\s*[\s\S]*?\s*<\/orderdone>/gi, "")
    .trim();
}

function tryParseOrderDoneFromAnswer(raw) {
  const re = /<orderdone>\s*([\s\S]*?)\s*<\/orderdone>/gi;
  let lastInner = null;
  let m;
  const s = String(raw || "");
  while ((m = re.exec(s)) !== null) {
    lastInner = m[1] ?? "";
  }
  if (lastInner === null) return null;
  const inner = String(lastInner).trim();
  if (!inner) return 42;
  const digits = inner.replace(/\D/g, "");
  if (!digits) return 42;
  const n = parseInt(digits, 10);
  return Number.isFinite(n) && n > 0 ? n : 42;
}

function tryParseReceiptFromAnswer(raw) {
  const merged = [];
  for (const m of String(raw || "").matchAll(/<receipt>\s*([\s\S]*?)\s*<\/receipt>/gi)) {
    const inner = (m[1] || "").trim();
    if (!inner) continue;
    try {
      const obj = JSON.parse(inner);
      if (obj && typeof obj === "object" && Array.isArray(obj.items)) {
        merged.push(...obj.items);
      }
    } catch {
      /* ignore */
    }
  }
  return merged.length ? { items: merged } : null;
}

const liveBillItems = computed(() => {
  const items = liveBill.value?.items;
  return Array.isArray(items) ? items : [];
});

const liveBillTotal = computed(() =>
  liveBillItems.value.reduce((sum, it) => {
    const n = Number(it?.price);
    const c = Number(it?.count);
    const price = Number.isFinite(n) ? n : 0;
    const count = Number.isFinite(c) && c > 0 ? c : 1;
    return sum + price * count;
  }, 0)
);

function formatBillMoney(n) {
  const x = Number(n);
  if (!Number.isFinite(x)) return "—";
  return x.toFixed(2);
}

onMounted(() => {
  void connect();
});

onUnmounted(() => {
  if (orderPlacedHideTimer != null) {
    window.clearTimeout(orderPlacedHideTimer);
    orderPlacedHideTimer = null;
  }
  disconnect();
});
</script>

<template>
  <section class="panel">
    <nav class="panel-nav" aria-label="App pages">
      <router-link class="panel-nav__link" to="/avatar">Studio</router-link>
      <router-link class="panel-nav__link" to="/analytics">Analytics</router-link>
    </nav>
    <div class="media-stack">
      <div class="video-wrap">
        <div
          v-if="!videoReady && !webrtcError"
          class="video-loading"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <div class="video-loading__track" aria-hidden="true">
            <div class="video-loading__fill" />
          </div>
          <span class="video-loading__label">{{ busy ? "Connecting…" : "Loading video…" }}</span>
        </div>

        <video ref="videoEl" class="video" autoplay playsinline />
        <div class="video-rail video-rail--left" aria-hidden="true" />
        <div class="video-rail video-rail--right" aria-hidden="true" />
        <audio ref="audioEl" class="sr-only" autoplay />

        <div
          v-if="orderPlacedMessage"
          class="order-placed-banner"
          role="status"
          aria-live="polite"
        >
          {{ orderPlacedMessage }}
        </div>

        <aside
          v-if="liveBillItems.length"
          class="live-bill"
          aria-label="Live order summary"
        >
          <div class="live-bill__head">
            <span class="live-bill__badge" aria-hidden="true">🧾</span>
            <div>
              <h3 class="live-bill__title">Your order</h3>
              <p class="live-bill__sub">Running total</p>
            </div>
          </div>
          <ul class="live-bill__lines">
            <li v-for="(it, idx) in liveBillItems" :key="idx" class="live-bill__line">
              <span class="live-bill__name">{{ it.name ?? "Item" }}</span>
              <span class="live-bill__meta">
                <span class="live-bill__qty">×{{ it.count ?? 1 }}</span>
                <span class="live-bill__price">${{ formatBillMoney(it.price) }}</span>
              </span>
            </li>
          </ul>
          <div class="live-bill__total" role="status">
            <span>Total</span>
            <span class="live-bill__total-amt">${{ formatBillMoney(liveBillTotal) }}</span>
          </div>
        </aside>

        <aside class="menu-overlay" aria-label="Holuminex Cafe menu">
          <div class="menu-overlay__inner">
            <div class="menu-banner">
              <span class="menu-banner__bean" aria-hidden="true">☕</span>
              <div>
                <h2 class="menu-banner__title">Holuminex Cafe</h2>
                <p class="menu-banner__meta">Seattle · All day</p>
              </div>
            </div>
            <figure class="menu-figure">
              <img
                src="/menu-holuminex.png"
                alt="Holuminex Cafe menu"
                width="640"
                height="360"
                loading="lazy"
              />
            </figure>
          </div>
        </aside>

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
          @pointerdown="onMicPointerDown"
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

    <div v-if="webrtcError" class="bottom-bar">
      <p class="err" role="alert">{{ webrtcError }}</p>
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

.panel-nav {
  position: absolute;
  top: 0.65rem;
  right: 0.65rem;
  z-index: 6;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  justify-content: flex-end;
}

.panel-nav__link {
  font-size: clamp(0.65rem, 1.6cqw, 0.82rem);
  font-weight: 600;
  text-decoration: none;
  padding: 0.28em 0.65em;
  border-radius: 999px;
  color: #0f172a;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(15, 23, 42, 0.12);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.panel-nav__link.router-link-active {
  border-color: rgba(0, 200, 200, 0.45);
  color: #0d9488;
}

.media-stack {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

/* Fixed design resolution 2490 × 3840 — UI scales with container (cqw/cqh) */
.video-wrap {
  container-type: size;
  container-name: stage;
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
  width: 80%;
  height: 100%;
  object-fit: cover;
  object-position: center center;
}

.video-rail {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 1;
  /* ~240px at 2490px wide */
  width: clamp(2rem, 9.65cqw, 15rem);
  background: #e4e2e2;
  pointer-events: none;
}

.video-rail--left {
  left: 0;
}

.video-rail--right {
  right: 0;
}

/* Top center: order confirmed when model sends <orderdone>…</orderdone> */
.order-placed-banner {
  position: absolute;
  left: 50%;
  top: max(0.5rem, env(safe-area-inset-top));
  transform: translateX(-50%);
  z-index: 5;
  max-width: min(88cqw, 96%);
  padding: clamp(0.38rem, 1cqw, 0.62rem) clamp(0.7rem, 1.85cqw, 1.15rem);
  border-radius: clamp(10px, 1.2cqw, 14px);
  font-size: clamp(0.56rem, 1.38cqw, 0.9rem);
  font-weight: 800;
  letter-spacing: 0.035em;
  text-align: center;
  color: #fff;
  background: linear-gradient(120deg, #0d9488, #059669);
  box-shadow:
    0 4px 22px rgba(13, 148, 136, 0.42),
    0 0 0 1px rgba(255, 255, 255, 0.28) inset;
  pointer-events: none;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.22);
}

/* Top-right on the live video: running bill from <receipt>…</receipt> */
.live-bill {
  position: absolute;
  top: max(0.5rem, env(safe-area-inset-top));
  right: max(0.45rem, env(safe-area-inset-right));
  z-index: 4;
  width: min(40cqw, 13.5rem);
  max-width: calc(100% - 22cqw);
  max-height: min(52cqh, 70vh);
  overflow: hidden auto;
  padding: clamp(0.4rem, 1cqw, 0.65rem) clamp(0.45rem, 1.1cqw, 0.75rem);
  border-radius: clamp(10px, 1.15cqw, 14px);
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(15, 23, 42, 0.14);
  box-shadow:
    0 4px 18px rgba(0, 0, 0, 0.12),
    0 0 0 1px rgba(255, 255, 255, 0.5) inset;
  pointer-events: auto;
  -webkit-overflow-scrolling: touch;
}

.live-bill__head {
  display: flex;
  align-items: flex-start;
  gap: clamp(0.25rem, 0.55cqw, 0.45rem);
  margin-bottom: clamp(0.35rem, 0.85cqw, 0.55rem);
  padding-bottom: clamp(0.3rem, 0.7cqw, 0.45rem);
  border-bottom: 1px solid rgba(148, 163, 184, 0.45);
}

.live-bill__badge {
  font-size: clamp(0.85rem, 2cqw, 1.1rem);
  line-height: 1;
  margin-top: 0.06em;
}

.live-bill__title {
  margin: 0;
  font-size: clamp(0.58rem, 1.35cqw, 0.82rem);
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #0f172a;
}

.live-bill__sub {
  margin: 0.08rem 0 0;
  font-size: clamp(0.48rem, 1.05cqw, 0.65rem);
  color: #64748b;
}

.live-bill__lines {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: clamp(0.28rem, 0.65cqw, 0.45rem);
}

.live-bill__line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: clamp(0.52rem, 1.2cqw, 0.78rem);
  line-height: 1.35;
  color: #1e293b;
}

.live-bill__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.live-bill__meta {
  flex-shrink: 0;
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  font-variant-numeric: tabular-nums;
}

.live-bill__qty {
  font-size: 0.92em;
  color: #64748b;
}

.live-bill__price {
  font-weight: 700;
  color: #0f766e;
}

.live-bill__total {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: clamp(0.4rem, 0.95cqw, 0.6rem);
  padding-top: clamp(0.35rem, 0.8cqw, 0.5rem);
  border-top: 2px solid rgba(13, 148, 136, 0.35);
  font-size: clamp(0.55rem, 1.25cqw, 0.82rem);
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #0f172a;
}

.live-bill__total-amt {
  font-size: 1.08em;
  color: #0d9488;
  font-variant-numeric: tabular-nums;
}

@keyframes menu-card-entrance {
  from {
    opacity: 0;
    transform: translate3d(-14%, 18px, 0) scale(0.94);
  }
  to {
    opacity: 1;
    transform: translate3d(0, 0, 0) scale(1);
  }
}

@keyframes menu-card-glow {
  0%,
  100% {
    box-shadow:
      0 8px 28px rgba(0, 0, 0, 0.12),
      0 0 0 0 rgba(13, 148, 136, 0);
  }
  50% {
    box-shadow:
      0 14px 36px rgba(0, 0, 0, 0.14),
      0 0 28px rgba(13, 148, 136, 0.14);
  }
}

@keyframes menu-banner-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes menu-figure-in {
  from {
    opacity: 0;
    transform: scale(0.97);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

@keyframes menu-bean-wiggle {
  0%,
  100% {
    transform: rotate(0deg);
  }
  30% {
    transform: rotate(-10deg);
  }
  60% {
    transform: rotate(8deg);
  }
}

.menu-overlay {
  position: absolute;
  top: auto;
  bottom: max(0.45rem, env(safe-area-inset-bottom));
  right: max(0.35rem, env(safe-area-inset-left));
  z-index: 2;
  width: min(42cqw, 92vw);
  max-height: min(62cqh, 78vh);
  overflow: hidden auto;
  border-radius: clamp(10px, 1.2cqw, 16px);
  background: rgba(255, 255, 255, 0.93);
  border: 1px solid rgba(15, 23, 42, 0.12);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.12);
  pointer-events: auto;
  -webkit-overflow-scrolling: touch;
  transform-origin: left bottom;
  animation:
    menu-card-entrance 0.68s cubic-bezier(0.22, 1, 0.36, 1) both,
    menu-card-glow 4.5s ease-in-out 0.72s infinite;
  will-change: transform, opacity;
}

.menu-overlay__inner {
  padding: clamp(0.35rem, 0.9cqw, 0.65rem);
}

.menu-banner {
  display: flex;
  align-items: center;
  gap: clamp(0.25rem, 0.6cqw, 0.5rem);
  padding: clamp(0.25rem, 0.55cqw, 0.45rem) clamp(0.35rem, 0.8cqw, 0.55rem);
  border-radius: clamp(8px, 1cqw, 12px);
  background: linear-gradient(120deg, #0d9488, #14b8a6);
  color: #fff;
  margin-bottom: clamp(0.25rem, 0.6cqw, 0.45rem);
  animation: menu-banner-in 0.55s ease backwards;
  animation-delay: 0.1s;
}

.menu-banner__bean {
  font-size: clamp(0.85rem, 2cqw, 1.15rem);
  line-height: 1;
  display: inline-block;
  transform-origin: 60% 70%;
  animation: menu-bean-wiggle 3.2s ease-in-out 0.85s infinite;
}

.menu-banner__title {
  margin: 0;
  font-size: clamp(0.62rem, 1.45cqw, 0.88rem);
  letter-spacing: 0.03em;
}

.menu-banner__meta {
  margin: 0.05rem 0 0;
  font-size: clamp(0.52rem, 1.1cqw, 0.68rem);
  opacity: 0.95;
}

.menu-figure {
  margin: 0;
  border-radius: clamp(6px, 0.8cqw, 10px);
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.35);
  animation: menu-figure-in 0.6s ease backwards;
  animation-delay: 0.22s;
}

.menu-figure img {
  display: block;
  width: 100%;
  height: auto;
  transition: transform 0.35s ease;
}

.menu-overlay:hover .menu-figure img {
  transform: scale(1.02);
}

@media (prefers-reduced-motion: reduce) {
  .menu-overlay {
    animation: none;
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.12);
  }

  .menu-banner,
  .menu-figure {
    animation: none;
  }

  .menu-banner__bean {
    animation: none;
  }

  .menu-figure img {
    transition: none;
  }

  .menu-overlay:hover .menu-figure img {
    transform: none;
  }
}

.video-loading {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: clamp(0.5rem, 1.25cqh, 1.5rem);
  background: rgba(228, 226, 226, 0.94);
  backdrop-filter: blur(8px);
}

.video-loading__label {
  font-size: clamp(1rem, 1.45cqw, 2.25rem);
  font-weight: 600;
  color: #333;
}

.video-loading__track {
  width: min(58cqw, 92%);
  height: max(4px, 0.22cqw);
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.video-loading__fill {
  height: 100%;
  width: 42%;
  border-radius: 999px;
  background: linear-gradient(90deg, #00b4b4, #6b52d8);
  animation: video-load-slide 1.35s ease-in-out infinite;
}

@media (prefers-reduced-motion: reduce) {
  .video-loading__fill {
    animation: none;
    width: 100%;
    opacity: 0.85;
  }
}

@keyframes video-load-slide {
  0% {
    transform: translateX(-115%);
  }
  100% {
    transform: translateX(310%);
  }
}

.caption {
  position: absolute;
  left: 50%;
  top: 0;
  transform: translateX(-50%);
  z-index: 3;
  box-sizing: border-box;
  /* stay inside rails + safe horizontal inset */
  width: min(88cqw, calc(100% - 21cqw));
  margin: 0;
  padding: calc(1.1cqh + env(safe-area-inset-top)) clamp(1rem, 3cqw, 3rem)
    clamp(0.65cqh, 1.25rem, 2rem);
  min-height: clamp(2.5rem, 4cqh, 5rem);
  max-height: min(30cqh, 40vh);
  overflow-y: auto;
  /* ~54px at 2490-wide stage */
  font-size: clamp(1.125rem, 2.18cqw, 3.5rem);
  line-height: 1.38;
  font-weight: 600;
  letter-spacing: 0.015em;
  text-align: center;
  color: #fff;
  text-shadow:
    0 0 1px rgba(0, 0, 0, 0.95),
    0 0 clamp(8px, 0.65cqw, 18px) rgba(0, 0, 0, 0.75),
    0 clamp(1px, 0.12cqw, 4px) clamp(2px, 0.35cqw, 8px) rgba(0, 0, 0, 0.9),
    0 clamp(2px, 0.2cqw, 10px) clamp(8px, 0.55cqw, 20px) rgba(0, 0, 0, 0.55);
  overflow-wrap: anywhere;
  pointer-events: none;
  -webkit-font-smoothing: antialiased;
}

.mic-fab {
  position: absolute;
  top: 27%;
  right: max(1rem, env(safe-area-inset-right), 3.5cqw);
  bottom: auto;
  transform: translateY(-50%);
  z-index: 3;
  /* ~170–180px on 2490-wide canvas */
  width: clamp(3.25rem, 7.2cqw, 11.5rem);
  height: clamp(3.25rem, 7.2cqw, 11.5rem);
  min-width: 3rem;
  min-height: 3rem;
  padding: 0;
  border: max(1px, 0.06cqw) solid #e0e0e0;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #333;
  background: #fff;
  box-shadow:
    0 clamp(2px, 0.15cqw, 8px) clamp(10px, 0.65cqw, 18px) rgba(0, 0, 0, 0.08),
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
  box-shadow: 0 clamp(3px, 0.2cqw, 10px) clamp(14px, 0.85cqw, 22px) rgba(0, 0, 0, 0.1);
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
    0 0 0 max(2px, 0.1cqw) rgba(0, 200, 200, 0.35),
    0 clamp(3px, 0.2cqw, 8px) clamp(14px, 0.85cqw, 24px) rgba(0, 180, 180, 0.15);
  animation: mic-pulse 1.4s ease-in-out infinite;
}

@keyframes mic-pulse {
  50% {
    box-shadow:
      0 0 0 max(3px, 0.14cqw) rgba(0, 200, 200, 0.2),
      0 clamp(4px, 0.25cqw, 10px) clamp(16px, 1cqw, 26px) rgba(0, 180, 180, 0.12);
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
  /* Match 2490×3840 typography when bar is full viewport width */
  padding: clamp(0.5rem, calc(100vw * 28 / 2490), 1.35rem) clamp(0.75rem, calc(100vw * 56 / 2490), 2.25rem)
    calc(0.55rem + env(safe-area-inset-bottom));
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

.err {
  margin: 0;
  font-size: clamp(0.9rem, calc(100vw * 30 / 2490), 1.85rem);
  line-height: 1.35;
  color: #b00020;
}
</style>
