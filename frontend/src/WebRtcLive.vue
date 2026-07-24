<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import {
  getSelectedAvatarId,
  SELECTED_AVATAR_STORAGE_KEY,
  syncSelectedAvatarFromServer,
} from "./selectedAvatar.js";
import {
  getHologramSessionId,
  hologramSessionIdPayload,
  resolveHologramSessionId,
} from "./hologramSession.js";

const busy = ref(false);
/** True once the remote video is actually rendering (not only SDP done). */
const videoReady = ref(false);
const started = ref(false);
const proxyConfigured = ref(null);
const sessionId = ref(getHologramSessionId() || "0");
const webrtcError = ref("");

const videoEl = ref(null);
/** Tracks whether WebRTC answer playback is active for end-of-playback timing. */
const voiceStreamOverlayActive = ref(false);
const audioEl = ref(null);

function onHologramAvatarSelected(ev) {
  const avatarId = String(ev?.detail?.avatarId || getSelectedAvatarId() || "").trim();
  if (!avatarId) return;
  lastPolledSelectedAvatarId = avatarId;
  applySelectedAvatarToCurrentSession(avatarId);
}

function onHologramAvatarStorage(ev) {
  if (ev.key !== SELECTED_AVATAR_STORAGE_KEY || !ev.newValue) return;
  lastPolledSelectedAvatarId = ev.newValue;
  applySelectedAvatarToCurrentSession(ev.newValue);
}

/** @type {RTCPeerConnection | null} */
let pc = null;

let recInstance = null;
/** Browser STT for sub-200ms interim while server Whisper runs (hybrid mode). */
let interimRecInstance = null;
let browserInterimText = "";
/** @type {{ blob: Blob, index: number }[]} */
let parallelSliceQueue = [];
let parallelSliceInFlight = 0;
/** @type {MediaRecorder | null} */
let mediaRecorder = null;
/** @type {MediaStream | null} */
let micCaptureStream = null;
/** @type {BlobPart[]} */
let micRecordedChunks = [];
/** @type {number | null} */
let micMaxRecordTimer = null;
/** @type {number | null} */
let micSilenceRaf = null;
let micLastSoundMs = 0;
let micRecordStartedMs = 0;
let micHasDetectedSpeech = false;
let micLastRms = 0;
/** @type {number | null} */
let liveTranscribeTimer = null;
let liveTranscribeInFlight = false;
let liveTranscribeSeq = 0;
/** @type {Promise<void>[]} */
let parallelSliceJobs = [];
/** @type {{ index: number, text: string }[]} */
let parallelSliceParts = [];
let parallelSliceSendIndex = 0;
/** @type {Blob | null} */
let webmHeaderChunk = null;
/** @type {Blob[]} */
let pendingSliceChunks = [];
let parallelSliceFlushTimer = null;
let liveTranscriptText = "";
let sttFirstChunkLatencyMs = null;
let sttChunkCount = 0;
let lastLiveTranscribeBytes = 0;
let lastLiveTranscribeAtMs = 0;
let lastLiveTranscribeApiMs = null;

const transcribeConfigured = ref(false);
/** @type {import('vue').Ref<'parakeet' | 'whisper'>} */
const transcribeBackend = ref("parakeet");
const micListening = ref(false);
const voiceThinking = ref(false);
const ENV_MIC_LANG_RAW = String(import.meta.env.VITE_VOICE_DEFAULT_LANG || "en-US").trim();
/** Voice pipeline API (default: TTS → humanaudio; does not use /api/voice-turn). */
const VOICE_PIPELINE_API = String(
  import.meta.env.VITE_VOICE_PIPELINE_API || "/api/voice-stream"
).trim();
/** BCP 47–ish tag from env, or "auto" for navigator.language. */
const DEFAULT_MIC_LANG = (() => {
  const r = ENV_MIC_LANG_RAW;
  if (!r) return "en-US";
  if (r.toLowerCase() === "auto") return "auto";
  if (/^[a-z]{2,3}(-[a-zA-Z0-9]+)*$/i.test(r)) return r;
  return "en-US";
})();
const micLangOptions = [
  { value: "auto", label: "Auto (browser default)" },
  // Chinese (Mandarin + dialects)
  { value: "zh-CN", label: "Chinese Mandarin (Mainland)" },
  { value: "zh-TW", label: "Chinese Mandarin (Taiwan)" },
  { value: "zh-HK", label: "Chinese (Hong Kong)" },
  { value: "yue-HK", label: "Chinese Cantonese (Hong Kong)" },
  { value: "wuu-CN", label: "Chinese Wu / Shanghainese" },
  { value: "nan-TW", label: "Chinese Min Nan (Taiwanese)" },
  // English (multiple accents)
  { value: "en-US", label: "English (US)" },
  { value: "en-GB", label: "English (UK)" },
  { value: "en-AU", label: "English (Australia)" },
  { value: "en-IN", label: "English (India)" },
  { value: "en-CA", label: "English (Canada)" },
  { value: "en-NZ", label: "English (New Zealand)" },
  // Japanese / Korean / Russian / Italian
  { value: "ja-JP", label: "Japanese" },
  { value: "ko-KR", label: "Korean" },
  { value: "ru-RU", label: "Russian" },
  { value: "it-IT", label: "Italian" },
  // German (6 variants)
  { value: "de-DE", label: "German (Germany)" },
  { value: "de-AT", label: "German (Austria)" },
  { value: "de-CH", label: "German (Switzerland)" },
  { value: "de-BE", label: "German (Belgium)" },
  { value: "de-LU", label: "German (Luxembourg)" },
  { value: "de-LI", label: "German (Liechtenstein)" },
  // French
  { value: "fr-FR", label: "French (France)" },
  { value: "fr-CA", label: "French (Canada)" },
  { value: "fr-CH", label: "French (Switzerland)" },
  { value: "fr-BE", label: "French (Belgium)" },
  // Portuguese
  { value: "pt-PT", label: "Portuguese (Portugal)" },
  { value: "pt-BR", label: "Portuguese (Brazil)" },
  // Spanish
  { value: "es-ES", label: "Spanish (Spain)" },
  { value: "es-MX", label: "Spanish (Mexico)" },
  { value: "es-US", label: "Spanish (US)" },
];
const selectedMicLang = ref(
  micLangOptions.some((o) => o.value === DEFAULT_MIC_LANG) ? DEFAULT_MIC_LANG : "en-US"
);
/** Parsed <receipt> JSON from last voice turn — shown as live bill on the stage. */
const liveBill = ref(null);
/** Shown at top of stage when <orderdone> is present; live bill is cleared. */
const orderPlacedMessage = ref("");
let orderPlacedHideTimer = null;
let captionHideTimer = null;

/** Holuminex Cafe menu (transparent panel on hologram stage). */
const CAFE_MENU = [
  {
    id: "coffee",
    title: "Coffee & Espresso",
    items: [
      { id: "espresso", name: "Espresso", price: 3.5 },
      { id: "americano", name: "Americano", price: 4.0 },
      { id: "latte", name: "Latte", price: 5.5 },
      { id: "mocha", name: "Mocha", price: 6.0 },
      { id: "indian-filter-coffee", name: "Indian Filter Coffee", price: 8.0 },
    ],
  },
  {
    id: "signature",
    title: "Signature Drinks",
    items: [
      {
        id: "lavender-oat-latte",
        name: "Lavender Oat Latte",
        desc: "Espresso, Lavender, Oat Milk",
        price: 7.0,
      },
      {
        id: "honey-blossom",
        name: "Honey Blossom Cold Brew",
        desc: "Honey, Vanilla Cold Foam",
        price: 6.5,
      },
      {
        id: "matcha-rose",
        name: "Matcha Rose Latte",
        desc: "Ceremonial Matcha, Rose Water, Steamed Milk",
        price: 7.25,
      },
    ],
  },
  {
    id: "brunch",
    title: "All-Day Brunch",
    items: [
      {
        id: "acai-bowl",
        name: "Acai Berry Bowl",
        desc: "Granola, Fruit, Coconut, Honey",
        price: 13.0,
      },
      {
        id: "sourdough-chicken-sandwich",
        name: "Sourdough Chicken Sandwich",
        desc: "Crispy chicken, lettuce, tomato, onion, cheese, toasted sourdough",
        price: 18.0,
      },
      { id: "veg-sandwich", name: "Veg Sandwich", price: 12.0 },
    ],
  },
  {
    id: "pastries",
    title: "Bites & Pastries",
    items: [
      { id: "almond-croissant", name: "Almond Croissant", price: 5.0 },
      { id: "blueberry-muffin", name: "Blueberry Muffin", price: 4.5 },
      { id: "house-granola", name: "House Granola", price: 6.0 },
      { id: "vegan-brownie", name: "Vegan Brownie", price: 5.5 },
    ],
  },
];

const MENU_HERO_ITEMS = [
  {
    id: "sourdough-chicken-sandwich",
    label: "Sourdough Chicken Sandwich",
    src: "/menu-items/SourdoughChickenSandwich.png",
  },
  { id: "latte", label: "Latte", src: "/menu-items/Latte.png" },
  { id: "indian-filter-coffee", label: "Indian Filter Coffee", src: "/menu-items/IndianFilterCoffee.png" },
  { id: "croissant", label: "Croissant", src: "/menu-items/Croissant.png" },
];

function normalizeMenuImageName(name) {
  return String(name || "")
    .trim()
    .toLowerCase()
    .replace(/['']/g, "")
    .replace(/\s+/g, " ");
}

/** Default PNG per menu item id (add files under public/menu-items/). */
const MENU_ITEM_SRC_BY_ID = {
  espresso: "/menu-items/Latte.png",
  americano: "/menu-items/Latte.png",
  latte: "/menu-items/Latte.png",
  mocha: "/menu-items/Latte.png",
  "indian-filter-coffee": "/menu-items/IndianFilterCoffee.png",
  "lavender-oat-latte": "/menu-items/Latte.png",
  "honey-blossom": "/menu-items/OrangeJuice.png",
  "matcha-rose": "/menu-items/Latte.png",
  "acai-bowl": "/menu-items/Croissant.png",
  "sourdough-chicken-sandwich": "/menu-items/SourdoughChickenSandwich.png",
  "veg-sandwich": "/menu-items/SourdoughChickenSandwich.png",
  "almond-croissant": "/menu-items/Croissant.png",
  "blueberry-muffin": "/menu-items/Croissant.png",
  "house-granola": "/menu-items/Croissant.png",
  "vegan-brownie": "/menu-items/Croissant.png",
};

function defaultMenuImageSrcForItem(item, sectionId) {
  if (item?.id && MENU_ITEM_SRC_BY_ID[item.id]) return MENU_ITEM_SRC_BY_ID[item.id];
  if (sectionId === "coffee" || sectionId === "signature") return "/menu-items/Latte.png";
  if (sectionId === "brunch") return "/menu-items/SourdoughChickenSandwich.png";
  if (sectionId === "pastries") return "/menu-items/Croissant.png";
  return "/menu-items/Croissant.png";
}

/** Every cafe menu name → image; built from CAFE_MENU + RAG aliases. */
const MENU_IMAGE_BY_NORMALIZED_NAME = (() => {
  const map = new Map();
  const assign = (name, id, src) => {
    map.set(normalizeMenuImageName(name), { id, label: name, src });
  };
  for (const h of MENU_HERO_ITEMS) {
    assign(h.label, h.id, h.src);
  }
  for (const section of CAFE_MENU) {
    for (const item of section.items) {
      const src = defaultMenuImageSrcForItem(item, section.id);
      assign(item.name, item.id, src);
    }
  }
  // RAG / LLM aliases (not on printed menu but common in answers)
  assign(
    "Smoked Chicken Burger",
    "smoked-chicken-burger",
    "/menu-items/SourdoughChickenSandwich.png"
  );
  assign("Chicken Burger", "chicken-burger", "/menu-items/SourdoughChickenSandwich.png");
  assign("Smoked Chicken Sandwich", "smoked-chicken-sandwich", "/menu-items/SourdoughChickenSandwich.png");
  return map;
})();

/** Hero strip above the text menu (static PNGs). */
const MENU_HERO_IMAGES_ENABLED = false;
/** Voice show_image featured item photos with 3D animation. */
const SHOW_IMAGE_ENABLED = true;

/** When non-empty, menu + hero strip hidden; item images shown with 3D animation. */
const featuredMenuImages = ref([]);

function resolveMenuImageByKeyword(key) {
  if (!key) return null;
  if (/\b(burger|sandwich|sourdough)\b/.test(key) || (/\bchicken\b/.test(key) && /\b(smoked|grilled|crispy|juicy)\b/.test(key))) {
    return {
      id: "sourdough-chicken-sandwich",
      label: "Sourdough Chicken Sandwich",
      src: "/menu-items/SourdoughChickenSandwich.png",
    };
  }
  if (/\b(filter coffee|indian coffee)\b/.test(key)) {
    return {
      id: "indian-filter-coffee",
      label: "Indian Filter Coffee",
      src: "/menu-items/IndianFilterCoffee.png",
    };
  }
  if (/\b(coffee|espresso|latte|mocha|americano|cappuccino|brew|matcha|cold brew)\b/.test(key)) {
    return { id: "latte", label: "Latte", src: "/menu-items/Latte.png" };
  }
  if (/\b(croissant|muffin|granola|brownie|pastry|bowl|acai)\b/.test(key)) {
    return { id: "croissant", label: "Croissant", src: "/menu-items/Croissant.png" };
  }
  if (/\b(juice|orange)\b/.test(key)) {
    return { id: "orange-juice", label: "Orange Juice", src: "/menu-items/OrangeJuice.png" };
  }
  return null;
}

function resolveMenuImageByName(name) {
  const displayLabel = String(name || "").trim();
  const key = normalizeMenuImageName(displayLabel);
  if (!key) return null;

  const withLabel = (img) => (img ? { ...img, label: displayLabel || img.label } : null);

  const exact = MENU_IMAGE_BY_NORMALIZED_NAME.get(key);
  if (exact) return withLabel(exact);

  for (const [menuKey, img] of MENU_IMAGE_BY_NORMALIZED_NAME) {
    if (menuKey.includes(key) || key.includes(menuKey)) return withLabel(img);
  }

  const keyTokens = new Set(key.split(" ").filter((t) => t.length > 2));
  let best = null;
  let bestScore = 0;
  for (const [menuKey, img] of MENU_IMAGE_BY_NORMALIZED_NAME) {
    let score = 0;
    for (const t of menuKey.split(" ")) {
      if (t.length > 2 && keyTokens.has(t)) score += 1;
    }
    if (score > bestScore) {
      bestScore = score;
      best = img;
    }
  }
  if (best && bestScore >= 2) return withLabel(best);

  return withLabel(resolveMenuImageByKeyword(key));
}

function cleanShowImageInner(raw) {
  let s = String(raw || "").trim();
  if (!s) return "";
  if (s.startsWith("```")) {
    const lines = s.split("\n");
    if (lines[0]?.startsWith("```")) lines.shift();
    if (lines.length && lines[lines.length - 1].trim() === "```") lines.pop();
    s = lines.join("\n").trim();
  }
  return s;
}

function parseShowImageInner(inner) {
  const s = cleanShowImageInner(inner);
  if (!s) return null;
  const candidates = [s];
  if (s.includes("{{") || s.includes("}}")) {
    candidates.push(s.replace(/\{\{/g, "{").replace(/\}\}/g, "}"));
  }
  for (const cand of candidates) {
    try {
      const obj = JSON.parse(cand);
      if (obj && typeof obj === "object" && Array.isArray(obj.items)) {
        const items = obj.items
          .filter((it) => it && typeof it === "object" && String(it.name || "").trim())
          .map((it) => ({ name: String(it.name).trim() }));
        if (items.length) return { items };
      }
    } catch {
      /* plain name fallback below */
    }
  }
  return { items: [{ name: s }] };
}

function parseShowImageFromAnswer(raw) {
  const text = String(raw || "");
  const closedRe = /<show_image>\s*([\s\S]*?)\s*<\/show_image>/gi;
  let lastInner = null;
  for (const m of text.matchAll(closedRe)) {
    const inner = (m[1] || "").trim();
    if (inner) lastInner = inner;
  }
  if (!lastInner) {
    const looseRe = /<show_image>\s*([\s\S]*?)$/gi;
    const loose = looseRe.exec(text);
    if (loose?.[1]?.trim()) lastInner = loose[1].trim();
  }
  if (!lastInner) return null;
  return parseShowImageInner(lastInner);
}

function normalizeShowImagePayload(payload) {
  if (!payload) return null;
  if (typeof payload === "string") {
    const s = payload.trim();
    if (!s) return null;
    try {
      return parseShowImageInner(s);
    } catch {
      return { items: [{ name: s }] };
    }
  }
  if (typeof payload === "object" && Array.isArray(payload.items)) {
    const items = payload.items
      .filter((it) => it && String(it.name || "").trim())
      .map((it) => ({ name: String(it.name).trim() }));
    return items.length ? { items } : null;
  }
  return null;
}

function resolveMenuImagesFromPayload(payload) {
  const norm = normalizeShowImagePayload(payload);
  if (!norm?.items?.length) return [];
  const out = [];
  const seen = new Set();
  for (const it of norm.items) {
    const img = resolveMenuImageByName(it.name);
    if (!img || seen.has(img.id)) continue;
    seen.add(img.id);
    out.push(img);
  }
  return out;
}

function applyShowImageFromVoiceTurn(data) {
  if (!SHOW_IMAGE_ENABLED) {
    featuredMenuImages.value = [];
    return;
  }
  const answer = String(data?.answer || "");
  const hasShowImageTag = /<show_image\b/i.test(answer);
  const payload =
    normalizeShowImagePayload(data?.show_image) ||
    (hasShowImageTag ? parseShowImageFromAnswer(answer) : null);
  const images = resolveMenuImagesFromPayload(payload);
  featuredMenuImages.value = images;
  if (images.length) {
    console.info("[show_image] showing", images.map((i) => i.label));
  } else if (hasShowImageTag) {
    console.warn("[show_image] <show_image> present but no menu PNG matched", {
      show_image: data?.show_image,
      answer_tail: answer.slice(-200),
    });
  }
}

function clearFeaturedMenuImage() {
  featuredMenuImages.value = [];
}

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
const MIC_MAX_RECORD_MS = Math.max(
  5000,
  Number.parseInt(import.meta.env.VITE_MIC_MAX_RECORD_MS || "45000", 10) || 45000
);
const MIC_MIN_RECORD_BEFORE_SILENCE_MS = Math.max(
  600,
  Number.parseInt(import.meta.env.VITE_MIC_MIN_RECORD_MS || "800", 10) || 800
);
/** Shorter recordings are not sent to transcribe (avoids tap-to-cancel hallucinations). */
const MIC_MIN_TRANSCRIBE_MS = Math.max(
  700,
  Number.parseInt(import.meta.env.VITE_MIC_MIN_TRANSCRIBE_MS || "900", 10) || 900
);
const MIC_MIN_TRANSCRIBE_BYTES = Math.max(
  1200,
  Number.parseInt(import.meta.env.VITE_MIC_MIN_TRANSCRIBE_BYTES || "2400", 10) || 2400
);
const MIC_SILENCE_RMS_THRESHOLD = Math.max(
  0.008,
  Number.parseFloat(import.meta.env.VITE_MIC_SILENCE_RMS || "0.014") || 0.014
);
/** Live chunked transcribe: poll interval while recording (0 = disabled). */
const TRANSCRIBE_LIVE_CHUNK_MS = Math.max(
  0,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_LIVE_CHUNK_MS || "2500", 10) || 2500
);
const TRANSCRIBE_LIVE_MIN_MS = Math.max(
  800,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_LIVE_MIN_MS || "1500", 10) || 1500
);
const TRANSCRIBE_LIVE_MIN_BYTES = Math.max(
  2000,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_LIVE_MIN_BYTES || "8000", 10) || 8000
);
const TRANSCRIBE_CHUNK_LENGTH_S = Math.max(
  0,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_CHUNK_LENGTH_S || "10", 10) || 10
);
/** Smaller Whisper windows for live partial requests (mode=chunked). */
const TRANSCRIBE_LIVE_CHUNK_LENGTH_S = Math.max(
  0,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_LIVE_CHUNK_LENGTH_S || "5", 10) || 5
);
const TRANSCRIBE_MODE = (() => {
  const m = String(import.meta.env.VITE_TRANSCRIBE_MODE || "chunked").trim().toLowerCase();
  return m === "sequential" ? "sequential" : "chunked";
})();
const TRANSCRIBE_LIVE_ENABLED =
  TRANSCRIBE_LIVE_CHUNK_MS > 0 &&
  String(import.meta.env.VITE_TRANSCRIBE_LIVE_ENABLED ?? "1").trim().toLowerCase() !== "0";
/** Parallel live STT: MediaRecorder emits every N ms; each slice POSTs to /api/transcribe in parallel. */
const TRANSCRIBE_SLICE_MS = Math.max(
  20,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_SLICE_MS || "20", 10) || 20
);
const TRANSCRIBE_PARALLEL_SLICES =
  String(import.meta.env.VITE_TRANSCRIBE_PARALLEL_SLICES ?? "1").trim().toLowerCase() !== "0";
const TRANSCRIBE_PARALLEL_ENABLED = TRANSCRIBE_PARALLEL_SLICES && TRANSCRIBE_SLICE_MS > 0;
const TRANSCRIBE_SLICE_MIN_BYTES = Math.max(
  1,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_SLICE_MIN_BYTES || "200", 10) || 200
);
/** Batch N ms of recorder slices per API call (0 = one POST per 20ms timeslice). */
const TRANSCRIBE_SLICE_BATCH_MS = Math.max(
  0,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_SLICE_BATCH_MS || "0", 10) || 0
);
/** Min record time before first slice POST (0 = send from first 20ms chunk after header). */
const TRANSCRIBE_SLICE_MIN_RECORD_MS = Math.max(
  0,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_SLICE_MIN_RECORD_MS || "0", 10) || 0
);
/** Max concurrent slice POSTs (0 = unlimited). */
const TRANSCRIBE_SLICE_MAX_IN_FLIGHT = Math.max(
  0,
  Number.parseInt(import.meta.env.VITE_TRANSCRIBE_SLICE_MAX_IN_FLIGHT || "16", 10) || 16
);
/** Instant interim via browser Speech API while Whisper slices run (Chromium). */
const STT_BROWSER_INTERIM =
  String(
    import.meta.env.VITE_STT_BROWSER_INTERIM ??
      (TRANSCRIBE_PARALLEL_ENABLED ? "1" : "0")
  )
    .trim()
    .toLowerCase() !== "0";
/** Skip Hologram proxy for slices when CORS allows (saves one hop). */
const TRANSCRIBE_DIRECT_URL = String(import.meta.env.VITE_TRANSCRIBE_DIRECT_URL || "").trim();
const TRANSCRIBE_LIVE_INTERVAL_ENABLED = TRANSCRIBE_LIVE_ENABLED && !TRANSCRIBE_PARALLEL_ENABLED;
const MIC_RECORDER_TIMESLICE_MS = TRANSCRIBE_PARALLEL_ENABLED ? TRANSCRIBE_SLICE_MS : 250;
/** Whisper often returns these on silence / very short cancel taps. */
const PHANTOM_TRANSCRIPT_RE =
  /^(thank\s*you|thanks|thank\s*you\.|thanks\.|ok|okay|bye|goodbye|you|the|\.+)$/i;
/** How long the avatar caption stays visible after transcript text appears. */
const CAPTION_HIDE_MS = Math.max(
  800,
  Number.parseInt(import.meta.env.VITE_CAPTION_HIDE_MS || "2000", 10) || 2000
);
/** Mic on/off beep length (ms) — tap, record start, record stop. */
const MIC_TONE_TAP_MS = Math.max(
  40,
  Number.parseInt(import.meta.env.VITE_MIC_TONE_TAP_MS || "80", 10) || 80
);
const MIC_TONE_ON_MS = Math.max(
  120,
  Number.parseInt(import.meta.env.VITE_MIC_TONE_ON_MS || "380", 10) || 380
);
const MIC_TONE_OFF_MS = Math.max(
  120,
  Number.parseInt(import.meta.env.VITE_MIC_TONE_OFF_MS || "340", 10) || 340
);
const MIC_PULSE_SEC = Math.max(
  0.8,
  Number.parseFloat(import.meta.env.VITE_MIC_PULSE_SEC || "2.2") || 2.2
);
/** First video currentTime bump (often stale frames — not lip-synced avatar yet). */
const VIDEO_STREAM_TICK_SEC = 0.02;
/** Min audio timeline advance after voice-turn before we count WebRTC audio as playing. */
const WEBRTC_AUDIO_PLAY_MIN_DELTA_SEC = Math.max(
  0.12,
  Number.parseFloat(import.meta.env.VITE_WEBRTC_AUDIO_PLAY_MIN_DELTA_SEC || "0.35") || 0.35
);
/** Min timeline advance before we treat avatar as lip-synced and playing. */
const LIP_SYNC_AVATAR_MIN_DELTA_SEC = Math.max(
  0.08,
  Number.parseFloat(import.meta.env.VITE_LIP_SYNC_AVATAR_MIN_DELTA_SEC || "0.15") || 0.15
);
let silenceTimer = null;
let voiceSessionCancelled = false;
let toneAudioCtx = null;
/** @type {{ turnId: number, requestSentMs: number, baselineTime: number, videoBaselineTime: number } | null} */
let webrtcTtfaPending = null;
let webrtcTtfaTimeout = null;
let webrtcTtfaPollId = null;
/** @type {{ turnId: number, audioFirstMs: number, videoBaselineTime: number, audioBaselineTime: number, micTapStartMs: number, voiceTurnCompleteMs: number, sttTranscriptReadyMs: number, videoStreamFirstAt: number | null, strictAudioAt: number | null, lipSyncAvatarAt: number | null, realPlaybackAt: number | null } | null} */
let lipSyncPending = null;
let lipSyncPollId = null;
let lipSyncTimeout = null;
/** Hides voice-stream overlay when answer audio finishes (JSON returns before playback ends). */
let answerEndWatchId = null;
let answerEndMaxTimerId = null;
let answerEndAudioEventsBound = false;
let answerAudioCtx = null;
/** @type {AnalyserNode | null} */
let answerAudioAnalyser = null;
const ANSWER_END_STALL_MS = 650;
const ANSWER_END_NEVER_HEARD_MS = 90_000;
const ANSWER_END_MAX_MS = 180_000;
const ANSWER_AUDIO_RMS_MIN = 0.012;
/** Voice input request (recognition start) → final transcript (STT latency). */
let voiceInputRequestMs = 0;
let sttSessionStartMs = 0;
let sttFinalTranscriptMs = 0;
let micTapStartMs = 0;
/** @type {{ turnId: number, sttMs: number | null, ragMs: number | null, micTapStartMs: number, voiceTurnCompleteMs: number, clientVoiceTurnMs: number | null } | null} */
let pendingVoiceAnalytics = null;

const speechRecCtor = computed(() => {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
});

const micAvailable = computed(
  () => transcribeConfigured.value || !!speechRecCtor.value
);
const useServerTranscribe = computed(() => transcribeConfigured.value);

function resolveMicLang() {
  if (selectedMicLang.value !== "auto") return selectedMicLang.value;
  if (typeof navigator === "undefined") return "en-US";
  const preferred = Array.isArray(navigator.languages) ? navigator.languages[0] : null;
  return preferred || navigator.language || "en-US";
}

function showVoiceStreamOverlay() {
  if (voiceStreamOverlayActive.value) return;
  voiceStreamOverlayActive.value = true;
}

function hideVoiceStreamOverlay() {
  if (!voiceStreamOverlayActive.value) return;
  voiceStreamOverlayActive.value = false;
}

function teardownAnswerAudioAnalyser() {
  answerAudioAnalyser = null;
  if (answerAudioCtx) {
    void answerAudioCtx.close().catch(() => {});
    answerAudioCtx = null;
  }
}

function ensureAnswerAudioAnalyser() {
  const audio = audioEl.value;
  if (!audio || answerAudioAnalyser) return answerAudioAnalyser;
  const stream = audio.srcObject;
  if (!(stream instanceof MediaStream)) return null;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    answerAudioCtx = new AC();
    const src = answerAudioCtx.createMediaStreamSource(stream);
    answerAudioAnalyser = answerAudioCtx.createAnalyser();
    answerAudioAnalyser.fftSize = 256;
    src.connect(answerAudioAnalyser);
    return answerAudioAnalyser;
  } catch {
    return null;
  }
}

function measureAnswerAudioRms() {
  const analyser = ensureAnswerAudioAnalyser();
  if (!analyser) return 0;
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i += 1) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

function clearAnswerOverlayMaxTimer() {
  if (answerEndMaxTimerId != null) {
    window.clearTimeout(answerEndMaxTimerId);
    answerEndMaxTimerId = null;
  }
}

function scheduleAnswerOverlayMaxMs(ms) {
  clearAnswerOverlayMaxTimer();
  const budget = Number(ms);
  if (!Number.isFinite(budget) || budget <= 0) return;
  answerEndMaxTimerId = window.setTimeout(() => {
    answerEndMaxTimerId = null;
    finishAnswerPlaybackEndWatch("max-estimate");
  }, Math.min(ANSWER_END_MAX_MS, budget));
}

function clearAnswerPlaybackEndWatch() {
  if (answerEndWatchId != null) {
    window.clearInterval(answerEndWatchId);
    answerEndWatchId = null;
  }
  clearAnswerOverlayMaxTimer();
  teardownAnswerAudioAnalyser();
}

function bindAnswerEndAudioEvents() {
  const audio = audioEl.value;
  if (!audio || answerEndAudioEventsBound) return;
  answerEndAudioEventsBound = true;
  const onEndSignal = () => {
    if (!voiceStreamOverlayActive.value || answerEndWatchId == null) return;
    finishAnswerPlaybackEndWatch("audio-pause-or-ended");
  };
  audio.addEventListener("pause", onEndSignal);
  audio.addEventListener("ended", onEndSignal);
}

function finishAnswerPlaybackEndWatch(reason) {
  if (!answerEndWatchId && !voiceStreamOverlayActive.value) return;
  clearAnswerPlaybackEndWatch();
  console.info("[voice-stream] overlay off:", reason);
  hideVoiceStreamOverlay();
}

function ensureAnswerPlaybackEndWatch(baselineSec, maxMs = null) {
  if (answerEndWatchId != null) return;
  bindAnswerEndAudioEvents();
  scheduleAnswerOverlayMaxMs(maxMs ?? ANSWER_END_MAX_MS);
  const baseline = Number(baselineSec);
  if (!Number.isFinite(baseline) || baseline < 0) return;

  let sawAnswerAudio = false;
  let lastSpeechAt = 0;
  let lastAudioT = -1;
  let stallMs = 0;
  const startedAt = performance.now();

  answerEndWatchId = window.setInterval(() => {
    const now = performance.now();
    if (now - startedAt > ANSWER_END_MAX_MS) {
      finishAnswerPlaybackEndWatch("max-duration");
      return;
    }
    if (!sawAnswerAudio && now - startedAt > ANSWER_END_NEVER_HEARD_MS) {
      finishAnswerPlaybackEndWatch("no-answer-audio");
      return;
    }

    const audio = audioEl.value;
    if (!audio) return;

    const rms = measureAnswerAudioRms();
    const timelinePlaying = webRtcStrictAudioPlaying(baseline);
    if (timelinePlaying || rms >= ANSWER_AUDIO_RMS_MIN) {
      sawAnswerAudio = true;
      showVoiceStreamOverlay();
      stallMs = 0;
      lastAudioT = audio.currentTime;
      if (rms >= ANSWER_AUDIO_RMS_MIN) lastSpeechAt = now;
      return;
    }

    if (!sawAnswerAudio) return;

    if (audio.paused) {
      finishAnswerPlaybackEndWatch("paused");
      return;
    }

    const t = audio.currentTime;
    if (lastAudioT >= 0 && Math.abs(t - lastAudioT) < 0.01) {
      stallMs += 16;
    } else {
      stallMs = 0;
      lastAudioT = t;
    }
    if (stallMs >= ANSWER_END_STALL_MS) {
      finishAnswerPlaybackEndWatch("audio-stall");
      return;
    }
    if (lastSpeechAt > 0 && now - lastSpeechAt >= ANSWER_END_STALL_MS) {
      finishAnswerPlaybackEndWatch("answer-silence");
    }
  }, 16);
}

/** Hide voice-stream overlay and stop answer playback watch. */
function deactivateWebRtcStage() {
  clearAnswerPlaybackEndWatch();
  hideVoiceStreamOverlay();
}

function answerPlaybackBaseline() {
  if (lipSyncPending && Number.isFinite(lipSyncPending.audioBaselineTime)) {
    return lipSyncPending.audioBaselineTime;
  }
  if (webrtcTtfaPending && Number.isFinite(webrtcTtfaPending.baselineTime)) {
    return webrtcTtfaPending.baselineTime;
  }
  const audio = audioEl.value;
  return audio && Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
}

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

let webrtcStatusPollId = null;
let lastPolledSelectedAvatarId = "";

function applyWebrtcStatusPayload(d) {
  if (!d || typeof d !== "object") return;
  proxyConfigured.value = Boolean(d.signaling_proxy_configured);
  transcribeConfigured.value = Boolean(d.transcribe_configured);
  if (d.transcribe_backend === "whisper") {
    transcribeBackend.value = "whisper";
  } else if (
    d.transcribe_backend === "parakeet" ||
    d.transcribe_backend === "openai" ||
    d.transcribe_backend === "nvidia" ||
    d.transcribe_backend === "nemo"
  ) {
    transcribeBackend.value = "parakeet";
  }
  const remoteAvatar = String(d.selected_avatar_id || "").trim();
  if (remoteAvatar && remoteAvatar !== lastPolledSelectedAvatarId) {
    lastPolledSelectedAvatarId = remoteAvatar;
    if (remoteAvatar !== getSelectedAvatarId()) {
      void syncSelectedAvatarFromServer().then((id) => {
        if (id) applySelectedAvatarToCurrentSession(id);
      });
    }
  }
}

async function refreshWebrtcStatus() {
  try {
    const res = await fetch(signalingUrl("/api/webrtc"));
    if (!res.ok) throw new Error(String(res.status));
    applyWebrtcStatusPayload(await res.json());
  } catch {
    proxyConfigured.value = false;
    transcribeConfigured.value = false;
  }
}

void refreshWebrtcStatus();

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

async function applySessionAvatar(avatarId, sessionid) {
  const id = String(avatarId || "").trim();
  const sid = String(sessionid || "").trim();
  if (!id || !sid || sid === "0") return;
  try {
    const res = await fetch(signalingUrl("/session/avatar"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ sessionid: sid, avatar_id: id, interrupt: true }),
    });
    if (!res.ok) {
      const t = await res.text();
      console.warn("[session/avatar] failed:", t.slice(0, 200) || res.status);
    }
  } catch (e) {
    console.warn("[session/avatar] error:", e);
  }
}

function applySelectedAvatarToCurrentSession(avatarId) {
  const id = String(avatarId || "").trim();
  const sid = String(sessionId.value || getHologramSessionId() || "").trim();
  if (!id || !started.value || !sid || sid === "0") return;
  void applySessionAvatar(id, sid);
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

  const avatarId = getSelectedAvatarId();
  const payload = { sdp: local.sdp, type: local.type };
  if (avatarId) payload.avatar = avatarId;
  const sessionPayload = hologramSessionIdPayload();
  if (sessionPayload) Object.assign(payload, sessionPayload);

  const res = await fetch(signalingUrl("/offer"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t.slice(0, 400) || `Offer failed (${res.status})`);
  }
  const answer = await res.json();
  if (answer.sessionid !== undefined && answer.sessionid !== null) {
    sessionId.value = resolveHologramSessionId(answer.sessionid);
  }
  await pc.setRemoteDescription(answer);
  if (avatarId) {
    await applySessionAvatar(avatarId, sessionId.value);
  }
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
        bindVideoPlaybackListener();
      } else if (evt.track.kind === "audio" && audioEl.value) {
        audioEl.value.srcObject = stream;
        bindWebRtcTtfaAudioListener();
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
  deactivateWebRtcStage();
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
  sessionId.value = getHologramSessionId() || "0";
}

function bindWebRtcTtfaAudioListener() {
  const audio = audioEl.value;
  if (!audio || audio.dataset.ttfaBound === "1") return;
  audio.dataset.ttfaBound = "1";
  audio.addEventListener("playing", () => {
    if (!webrtcTtfaPending && !lipSyncPending) return;
    const baseline =
      webrtcTtfaPending?.baselineTime ?? lipSyncPending?.audioBaselineTime ?? 0;
    if (
      webRtcStrictAudioPlaying(baseline) ||
      measureAnswerAudioRms() >= ANSWER_AUDIO_RMS_MIN
    ) {
      showVoiceStreamOverlay();
    }
    if (!webrtcTtfaPending) return;
    onWebRtcAudioPlayingForTtfa();
  });
}

function webRtcStrictAudioPlaying(baselineSec) {
  const audio = audioEl.value;
  if (!audio || audio.paused || audio.readyState < 2) return false;
  const baseline = Number(baselineSec);
  if (!Number.isFinite(baseline) || baseline < 0) return false;
  return audio.currentTime > baseline + WEBRTC_AUDIO_PLAY_MIN_DELTA_SEC;
}

function onWebRtcAudioPlayingForTtfa() {
  if (!webrtcTtfaPending) return;
  const { turnId, requestSentMs, videoBaselineTime, baselineTime } = webrtcTtfaPending;
  if (!turnId || turnId <= 0) return;
  if (!webRtcStrictAudioPlaying(baselineTime)) return;
  webrtcTtfaPending = null;
  if (webrtcTtfaTimeout != null) {
    window.clearTimeout(webrtcTtfaTimeout);
    webrtcTtfaTimeout = null;
  }
  if (webrtcTtfaPollId != null) {
    window.clearInterval(webrtcTtfaPollId);
    webrtcTtfaPollId = null;
  }
  const now = performance.now();
  const ttsMs = now - requestSentMs;
  const pending =
    pendingVoiceAnalytics && pendingVoiceAnalytics.turnId === turnId
      ? pendingVoiceAnalytics
      : null;
  const micTap =
    pending && pending.micTapStartMs > 0
      ? pending.micTapStartMs
      : micTapStartMs > 0
        ? micTapStartMs
        : 0;
  const micToFirstAudioMs =
    micTap > 0 ? Math.max(0, now - micTap) : null;
  const clientVoiceTurnMs =
    pending && pending.clientVoiceTurnMs != null ? pending.clientVoiceTurnMs : null;
  void reportWebrtcFirstVoiceMs(turnId, ttsMs, {
    micToFirstAudioMs,
    clientVoiceTurnMs,
  });
  scheduleLipSyncLatency(
    turnId,
    now,
    videoBaselineTime,
    micTap,
    requestSentMs,
    baselineTime
  );
  ensureAnswerPlaybackEndWatch(baselineTime);
}

function clearLipSyncPending() {
  lipSyncPending = null;
  if (lipSyncPollId != null) {
    window.clearInterval(lipSyncPollId);
    lipSyncPollId = null;
  }
  if (lipSyncTimeout != null) {
    window.clearTimeout(lipSyncTimeout);
    lipSyncTimeout = null;
  }
}

function bindVideoPlaybackListener() {
  const v = videoEl.value;
  if (!v || v.dataset.playbackTtfaBound === "1") return;
  v.dataset.playbackTtfaBound = "1";
  v.addEventListener("playing", () => {
    tryMarkLipSyncAvatar(performance.now());
  });
}

function videoDeltaSec() {
  if (!lipSyncPending || !videoEl.value) return 0;
  return Math.max(0, videoEl.value.currentTime - lipSyncPending.videoBaselineTime);
}

function tryMarkStrictWebRtcAudio(now) {
  if (!lipSyncPending || lipSyncPending.strictAudioAt != null) return false;
  if (!webRtcStrictAudioPlaying(lipSyncPending.audioBaselineTime)) return false;
  lipSyncPending.strictAudioAt = now;
  return true;
}

/** Real playback = audible audio + lip-sync video; use the later timestamp. */
function tryCompleteRealWebRtcPlayback(now) {
  if (!lipSyncPending || lipSyncPending.realPlaybackAt != null) return false;
  const { strictAudioAt, lipSyncAvatarAt } = lipSyncPending;
  if (!strictAudioAt || !lipSyncAvatarAt) return false;
  lipSyncPending.realPlaybackAt = Math.max(strictAudioAt, lipSyncAvatarAt);
  return true;
}

/** Earliest video timeline tick (may still be old frames). */
function tryMarkVideoStreamFirst(now) {
  if (!lipSyncPending || lipSyncPending.videoStreamFirstAt != null) return;
  const video = videoEl.value;
  if (!video || video.paused || video.readyState < 2) return;
  if (videoDeltaSec() <= VIDEO_STREAM_TICK_SEC) return;
  lipSyncPending.videoStreamFirstAt = now;
}

/**
 * Lip-synced avatar actually playing: enough new video timeline + decoded frames.
 * This is later than the first 20ms tick — captures the gap users still feel.
 */
function tryMarkLipSyncAvatar(now) {
  if (!lipSyncPending || lipSyncPending.lipSyncAvatarAt != null) return false;
  const video = videoEl.value;
  if (!video || video.paused || video.readyState < 3) return false;
  if (videoDeltaSec() < LIP_SYNC_AVATAR_MIN_DELTA_SEC) return false;
  tryMarkVideoStreamFirst(now);
  lipSyncPending.lipSyncAvatarAt = now;
  return true;
}

function scheduleLipSyncLatency(
  turnId,
  audioFirstMs,
  videoBaselineTime,
  micTapStartMs = 0,
  voiceTurnCompleteMs = 0,
  audioBaselineAtComplete = 0
) {
  clearLipSyncPending();
  if (!turnId || !Number.isFinite(audioFirstMs)) return;
  bindVideoPlaybackListener();
  const v = videoEl.value;
  const baseline =
    Number.isFinite(videoBaselineTime) && videoBaselineTime >= 0
      ? videoBaselineTime
      : v && Number.isFinite(v.currentTime)
        ? v.currentTime
        : 0;
  const audioBaseline =
    Number.isFinite(audioBaselineAtComplete) && audioBaselineAtComplete >= 0
      ? audioBaselineAtComplete
      : 0;
  lipSyncPending = {
    turnId,
    audioFirstMs,
    videoBaselineTime: baseline,
    audioBaselineTime: audioBaseline,
    micTapStartMs: Number.isFinite(micTapStartMs) && micTapStartMs > 0 ? micTapStartMs : 0,
    voiceTurnCompleteMs:
      Number.isFinite(voiceTurnCompleteMs) && voiceTurnCompleteMs > 0
        ? voiceTurnCompleteMs
        : 0,
    sttTranscriptReadyMs:
      sttFinalTranscriptMs > 0 ? sttFinalTranscriptMs : 0,
    videoStreamFirstAt: null,
    strictAudioAt: null,
    lipSyncAvatarAt: null,
    realPlaybackAt: null,
  };
  lipSyncPollId = window.setInterval(() => {
    if (!lipSyncPending || !videoEl.value) return;
    const now = performance.now();
    tryMarkVideoStreamFirst(now);
    tryMarkStrictWebRtcAudio(now);
    tryMarkLipSyncAvatar(now);
    if (tryCompleteRealWebRtcPlayback(now)) {
      onLipSyncPlaybackStarted();
    }
  }, 16);
  lipSyncTimeout = window.setTimeout(() => {
    if (lipSyncPending?.realPlaybackAt != null) {
      onLipSyncPlaybackStarted();
    } else if (lipSyncPending?.lipSyncAvatarAt != null || lipSyncPending?.strictAudioAt != null) {
      onLipSyncPlaybackStarted();
    } else {
      clearLipSyncPending();
      clearMicTapTiming();
      deactivateWebRtcStage();
    }
  }, 120000);
}

/** Lip-sync / first-audio metrics only — WebRTC stays up until answer playback ends. */
function onLipSyncPlaybackStarted() {
  if (!lipSyncPending) return;
  const p = lipSyncPending;
  const audioBaseline = p.audioBaselineTime;
  clearLipSyncPending();
  clearMicTapTiming();
  ensureAnswerPlaybackEndWatch(audioBaseline);
  onLipSyncVideoReady(p);
}

function onLipSyncVideoReady(p) {
  if (!p) return;
  const {
    turnId,
    audioFirstMs,
    micTapStartMs,
    voiceTurnCompleteMs,
    strictAudioAt,
    lipSyncAvatarAt,
    videoStreamFirstAt,
    realPlaybackAt,
    sttTranscriptReadyMs,
  } = p;
  const avatarAt = lipSyncAvatarAt ?? performance.now();
  const streamFirstAt = videoStreamFirstAt ?? avatarAt;
  const strictAudio = strictAudioAt ?? audioFirstMs;
  const lipMs = Math.max(0, avatarAt - strictAudio);
  const micToLipMs = micTapStartMs > 0 ? Math.max(0, avatarAt - micTapStartMs) : null;
  const sttToLipSyncMs =
    sttTranscriptReadyMs > 0 ? Math.max(0, avatarAt - sttTranscriptReadyMs) : null;
  const anchorMs = voiceTurnCompleteMs > 0 ? voiceTurnCompleteMs : 0;
  const videoStreamFirstMs =
    anchorMs > 0 ? Math.max(0, streamFirstAt - anchorMs) : null;
  const lipSyncAvatarPlayMs = anchorMs > 0 ? Math.max(0, avatarAt - anchorMs) : null;
  const webrtcRealPlaybackMs =
    anchorMs > 0 && realPlaybackAt != null
      ? Math.max(0, realPlaybackAt - anchorMs)
      : null;
  const streamStartToAvatarMs = Math.max(0, avatarAt - streamFirstAt);
  void reportLipSyncLatencyMs(turnId, lipMs, {
    micToLipSyncMs: micToLipMs,
    sttToLipSyncMs,
    videoStreamFirstMs,
    lipSyncAvatarPlayMs,
    streamStartToAvatarMs,
    webrtcRealPlaybackMs,
  });
}

function clearWebRtcTtfaPending() {
  webrtcTtfaPending = null;
  if (webrtcTtfaTimeout != null) {
    window.clearTimeout(webrtcTtfaTimeout);
    webrtcTtfaTimeout = null;
  }
  if (webrtcTtfaPollId != null) {
    window.clearInterval(webrtcTtfaPollId);
    webrtcTtfaPollId = null;
  }
  clearLipSyncPending();
}

function scheduleWebRtcTtfa(turnId, requestSentMs) {
  clearWebRtcTtfaPending();
  if (!turnId || !Number.isFinite(requestSentMs)) return;
  _startWebRtcTtfaPoll(turnId, requestSentMs);
}

/** Start watching WebRTC audio before voice-turn JSON returns (stream /human may already be speaking). */
function beginVoiceTurnTtfaWatch(requestSentMs) {
  clearWebRtcTtfaPending();
  if (!Number.isFinite(requestSentMs)) return;
  _startWebRtcTtfaPoll(0, requestSentMs);
}

function attachVoiceTurnTtfaTurnId(turnId) {
  if (!turnId || !webrtcTtfaPending) return;
  if (webrtcTtfaPending.turnId === 0) {
    webrtcTtfaPending.turnId = turnId;
    if (webRtcStrictAudioPlaying(webrtcTtfaPending.baselineTime)) {
      onWebRtcAudioPlayingForTtfa();
    }
  }
}

function _startWebRtcTtfaPoll(turnId, requestSentMs) {
  bindWebRtcTtfaAudioListener();
  const audio = audioEl.value;
  const baselineTime = audio && Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
  const video = videoEl.value;
  const videoBaselineTime =
    video && Number.isFinite(video.currentTime) ? video.currentTime : 0;
  webrtcTtfaPending = { turnId, requestSentMs, baselineTime, videoBaselineTime };
  webrtcTtfaPollId = window.setInterval(() => {
    if (!webrtcTtfaPending || !audioEl.value) return;
    if (webRtcStrictAudioPlaying(webrtcTtfaPending.baselineTime)) {
      onWebRtcAudioPlayingForTtfa();
    }
  }, 16);
  webrtcTtfaTimeout = window.setTimeout(() => {
    clearWebRtcTtfaPending();
    clearMicTapTiming();
    if (!answerEndWatchId) {
      deactivateWebRtcStage();
    }
  }, 120000);
}

function computeTimeToFirstVoiceMs(webrtcMs, sttMs, ragMs) {
  const webrtc = Number(webrtcMs);
  if (!Number.isFinite(webrtc) || webrtc < 0) return null;
  const stt = Number.isFinite(sttMs) && sttMs >= 0 ? sttMs : 0;
  const rag = Number.isFinite(ragMs) && ragMs >= 0 ? ragMs : 0;
  return Math.round((stt + rag + webrtc) * 10) / 10;
}

function clearMicTapTiming() {
  micTapStartMs = 0;
  voiceInputRequestMs = 0;
  sttSessionStartMs = 0;
  sttFinalTranscriptMs = 0;
}

async function reportWebrtcFirstVoiceMs(turnId, ttsMs, extra = {}) {
  const pending =
    pendingVoiceAnalytics && pendingVoiceAnalytics.turnId === turnId
      ? pendingVoiceAnalytics
      : null;
  const roundedTts = Math.round(ttsMs * 10) / 10;
  const timeToFirstVoice = computeTimeToFirstVoiceMs(
    roundedTts,
    pending?.sttMs,
    pending?.ragMs
  );
  pendingVoiceAnalytics = null;
  try {
    const body = {
      turn_id: turnId,
      webrtc_first_voice_ms: roundedTts,
      tts_latency_ms: roundedTts,
    };
    if (timeToFirstVoice != null) {
      body.time_to_first_voice_ms = timeToFirstVoice;
    }
    const micAudio = extra.micToFirstAudioMs;
    if (micAudio != null && Number.isFinite(micAudio) && micAudio >= 0) {
      body.mic_to_first_audio_ms = Math.round(micAudio * 10) / 10;
    }
    const clientVt = extra.clientVoiceTurnMs ?? pending?.clientVoiceTurnMs;
    if (clientVt != null && Number.isFinite(clientVt) && clientVt >= 0) {
      body.client_voice_turn_ms = Math.round(clientVt * 10) / 10;
    }
    const res = await fetch(signalingUrl("/api/analytics/webrtc-first-voice"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      console.warn("[analytics] webrtc-first-voice failed", res.status);
    }
  } catch (e) {
    console.warn("[analytics] webrtc-first-voice", e);
  }
}

async function reportLipSyncLatencyMs(turnId, lipSyncMs, extra = {}) {
  try {
    const body = {
      turn_id: turnId,
      lip_sync_latency_ms: Math.round(lipSyncMs * 10) / 10,
    };
    const micLip = extra.micToLipSyncMs;
    if (micLip != null && Number.isFinite(micLip) && micLip >= 0) {
      body.mic_to_lip_sync_ms = Math.round(micLip * 10) / 10;
    }
    const sttLip = extra.sttToLipSyncMs;
    if (sttLip != null && Number.isFinite(sttLip) && sttLip >= 0) {
      body.stt_to_lip_sync_ms = Math.round(sttLip * 10) / 10;
    }
    const streamFirst = extra.videoStreamFirstMs;
    if (streamFirst != null && Number.isFinite(streamFirst) && streamFirst >= 0) {
      body.video_stream_first_ms = Math.round(streamFirst * 10) / 10;
    }
    if (extra.lipSyncAvatarPlayMs != null && Number.isFinite(extra.lipSyncAvatarPlayMs)) {
      body.lip_sync_avatar_play_ms = Math.round(extra.lipSyncAvatarPlayMs * 10) / 10;
    }
    if (extra.streamStartToAvatarMs != null && Number.isFinite(extra.streamStartToAvatarMs)) {
      body.stream_start_to_avatar_ms = Math.round(extra.streamStartToAvatarMs * 10) / 10;
    }
    const realPb = extra.webrtcRealPlaybackMs;
    if (realPb != null && Number.isFinite(realPb) && realPb >= 0) {
      body.webrtc_real_playback_ms = Math.round(realPb * 10) / 10;
    }
    const res = await fetch(signalingUrl("/api/analytics/lip-sync-latency"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      console.warn("[analytics] lip-sync-latency failed", res.status);
    }
  } catch (e) {
    console.warn("[analytics] lip-sync-latency", e);
  }
}

/** Queue TTS on LiveTalking; do not await (avatar starts generating while UI updates). */
function liveTalkingSessionIdPayload() {
  const sid = String(sessionId.value || getHologramSessionId() || "").trim();
  if (!sid || sid === "0") return null;
  return { sessionid: sid };
}

/** Stop LiveTalking speech when user taps mic to interrupt. */
async function interruptAvatarSpeech() {
  deactivateWebRtcStage();
  clearWebRtcTtfaPending();
  pendingVoiceAnalytics = null;

  const payload = liveTalkingSessionIdPayload();
  if (!payload) {
    console.warn("[interrupt_talk] skipped — no WebRTC sessionid (connect hologram first)");
    return false;
  }

  try {
    const res = await fetch(signalingUrl("/interrupt_talk"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.text();
      console.warn("[interrupt_talk] failed", res.status, body.slice(0, 300));
      return false;
    }
    console.info("[interrupt_talk] ok", payload);
    return true;
  } catch (e) {
    console.warn("[interrupt_talk]", e);
    return false;
  }
}

function postHuman(text) {
  const t = text.trim();
  if (!t) return;
  const payload = liveTalkingSessionIdPayload();
  if (!payload) return;
  ensureAnswerPlaybackEndWatch(
    answerPlaybackBaseline(),
    Math.min(ANSWER_END_MAX_MS, Math.max(8000, t.length * 85))
  );
  void fetch(signalingUrl("/human"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    keepalive: true,
    body: JSON.stringify({
      text: t,
      type: "echo",
      interrupt: true,
      ...payload,
    }),
  })
    .then(async (res) => {
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body.slice(0, 400) || `Send failed (${res.status})`);
      }
    })
    .catch((e) => {
      webrtcError.value = e instanceof Error ? e.message : String(e);
    });
}

function clearSilenceTimer() {
  if (silenceTimer !== null) {
    window.clearTimeout(silenceTimer);
    silenceTimer = null;
  }
}

function stopMicSilenceMonitor() {
  if (micSilenceRaf != null) {
    cancelAnimationFrame(micSilenceRaf);
    micSilenceRaf = null;
  }
}

function releaseMicCapture() {
  stopMicSilenceMonitor();
  if (micMaxRecordTimer != null) {
    window.clearTimeout(micMaxRecordTimer);
    micMaxRecordTimer = null;
  }
  if (micCaptureStream) {
    for (const track of micCaptureStream.getTracks()) {
      track.stop();
    }
    micCaptureStream = null;
  }
  mediaRecorder = null;
  micRecordedChunks = [];
}

function micRecordDurationMs() {
  return micRecordStartedMs > 0 ? Math.max(0, performance.now() - micRecordStartedMs) : 0;
}

function resetLiveTranscribeState() {
  liveTranscriptText = "";
  sttFirstChunkLatencyMs = null;
  sttChunkCount = 0;
  lastLiveTranscribeBytes = 0;
  lastLiveTranscribeAtMs = 0;
  lastLiveTranscribeApiMs = null;
  parallelSliceJobs = [];
  parallelSliceParts = [];
  parallelSliceSendIndex = 0;
  webmHeaderChunk = null;
  pendingSliceChunks = [];
  parallelSliceQueue = [];
  parallelSliceInFlight = 0;
  stopBrowserInterimStt();
  if (parallelSliceFlushTimer != null) {
    window.clearTimeout(parallelSliceFlushTimer);
    parallelSliceFlushTimer = null;
  }
  liveTranscribeSeq++;
  liveTranscribeInFlight = false;
}

function joinParallelTranscriptParts(parts) {
  const sorted = [...parts].sort((a, b) => a.index - b.index);
  let out = "";
  for (const p of sorted) {
    const piece = String(p.text || "").trim();
    if (!piece) continue;
    if (!out) {
      out = piece;
      continue;
    }
    if (piece.startsWith(out) || out.endsWith(piece)) {
      out = piece.length > out.length ? piece : out;
    } else if (!out.endsWith(piece) && !piece.startsWith(out.split(/\s+/).pop() || "")) {
      out = `${out} ${piece}`;
    }
  }
  return out.replace(/\s+/g, " ").trim();
}

function refreshInterimCaption() {
  const whisper = joinParallelTranscriptParts(parallelSliceParts) || liveTranscriptText;
  const text = (whisper || browserInterimText || "").trim();
  if (text) interimTranscript.value = text;
}

function refreshLiveInterimFromParallel() {
  const joined = joinParallelTranscriptParts(parallelSliceParts);
  if (!joined) {
    refreshInterimCaption();
    return;
  }
  liveTranscriptText = joined;
  refreshInterimCaption();
}

function stopBrowserInterimStt() {
  if (interimRecInstance) {
    try {
      interimRecInstance.stop();
    } catch {
      /* ignore */
    }
    interimRecInstance = null;
  }
  browserInterimText = "";
}

function startBrowserInterimStt() {
  if (!STT_BROWSER_INTERIM || !speechRecCtor.value) return;
  stopBrowserInterimStt();
  const SR = speechRecCtor.value;
  interimRecInstance = new SR();
  interimRecInstance.lang = resolveMicLang();
  interimRecInstance.continuous = true;
  interimRecInstance.interimResults = true;
  interimRecInstance.maxAlternatives = 1;
  browserInterimText = "";
  interimRecInstance.onresult = (event) => {
    if (voiceSessionCancelled || !micListening.value) return;
    let interim = "";
    let finals = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const piece = String(event.results[i][0]?.transcript || "");
      if (event.results[i].isFinal) finals += piece;
      else interim += piece;
    }
    if (finals.trim()) {
      browserInterimText = `${browserInterimText} ${finals}`.trim().replace(/\s+/g, " ");
    }
    const line = `${browserInterimText} ${interim}`.trim().replace(/\s+/g, " ");
    if (line) {
      if (sttFirstChunkLatencyMs == null && micTapStartMs > 0) {
        sttFirstChunkLatencyMs = Math.round((performance.now() - micTapStartMs) * 10) / 10;
      }
      interimTranscript.value = liveTranscriptText || line;
    }
  };
  interimRecInstance.onerror = () => {
    /* keep server path; browser interim is optional */
  };
  interimRecInstance.onend = () => {
    if (voiceSessionCancelled || !micListening.value || !interimRecInstance) return;
    try {
      interimRecInstance.start();
    } catch {
      /* ignore */
    }
  };
  try {
    interimRecInstance.start();
  } catch {
    interimRecInstance = null;
  }
}

function flushParallelSlicePending() {
  if (parallelSliceFlushTimer != null) {
    window.clearTimeout(parallelSliceFlushTimer);
    parallelSliceFlushTimer = null;
  }
  if (!pendingSliceChunks.length) return;
  const mimeType = mediaRecorder?.mimeType || "audio/webm";
  const parts = webmHeaderChunk
    ? [webmHeaderChunk, ...pendingSliceChunks]
    : [...pendingSliceChunks];
  pendingSliceChunks = [];
  const blob = new Blob(parts, { type: mimeType });
  const index = parallelSliceSendIndex++;
  queueParallelSliceTranscribe(blob, index);
}

/** One 20ms MediaRecorder chunk → Parakeet POST (header + single cluster). */
function enqueueParallelSliceTranscribe(chunk) {
  if (!chunk?.size || !webmHeaderChunk) return;
  if (TRANSCRIBE_SLICE_BATCH_MS > 0) {
    pendingSliceChunks.push(chunk);
    scheduleParallelSliceBatchFlush();
    return;
  }
  const mimeType = mediaRecorder?.mimeType || "audio/webm";
  const blob = new Blob([webmHeaderChunk, chunk], { type: mimeType });
  if (shouldSkipTranscribeBlob(blob, { slice: true })) return;
  if (!micHasDetectedSpeech && micRecordDurationMs() < TRANSCRIBE_SLICE_MIN_RECORD_MS) return;
  const index = parallelSliceSendIndex++;
  queueParallelSliceTranscribe(blob, index);
}

function pumpParallelSliceQueue() {
  const max = TRANSCRIBE_SLICE_MAX_IN_FLIGHT;
  while (parallelSliceQueue.length && (max === 0 || parallelSliceInFlight < max)) {
    const item = parallelSliceQueue.shift();
    if (!item) break;
    parallelSliceInFlight += 1;
    const job = runParallelSliceJob(item.blob, item.index).finally(() => {
      parallelSliceInFlight -= 1;
      pumpParallelSliceQueue();
    });
    parallelSliceJobs.push(job);
  }
}

function queueParallelSliceTranscribe(blob, index) {
  if (shouldSkipTranscribeBlob(blob, { slice: true })) return;
  if (!micHasDetectedSpeech && micRecordDurationMs() < TRANSCRIBE_SLICE_MIN_RECORD_MS) return;
  parallelSliceQueue.push({ blob, index });
  pumpParallelSliceQueue();
}

async function runParallelSliceJob(blob, index) {
  const seq = liveTranscribeSeq;
  try {
    const { text, latencyMs } = await transcribeMicBlob(blob, { slice: true });
    if (seq !== liveTranscribeSeq || voiceSessionCancelled) return;
    if (!text || isPhantomTranscript(text, blob, TRANSCRIBE_SLICE_MS)) return;
    parallelSliceParts.push({ index, text });
    sttChunkCount += 1;
    lastLiveTranscribeAtMs = performance.now();
    lastLiveTranscribeApiMs = latencyMs;
    refreshLiveInterimFromParallel();
    if (sttFirstChunkLatencyMs == null && micTapStartMs > 0) {
      sttFirstChunkLatencyMs = Math.round((performance.now() - micTapStartMs) * 10) / 10;
    }
    console.info("[parallel-transcribe slice]", {
      index,
      api_ms: latencyMs,
      chars: text.length,
      joined_chars: liveTranscriptText.length,
      in_flight: parallelSliceInFlight,
      queued: parallelSliceQueue.length,
    });
  } catch (e) {
    console.warn("[parallel-transcribe slice]", index, e);
  }
}

function scheduleParallelSliceBatchFlush() {
  if (TRANSCRIBE_SLICE_BATCH_MS <= 0) {
    flushParallelSlicePending();
    return;
  }
  if (parallelSliceFlushTimer != null) {
    window.clearTimeout(parallelSliceFlushTimer);
  }
  parallelSliceFlushTimer = window.setTimeout(() => {
    parallelSliceFlushTimer = null;
    flushParallelSlicePending();
  }, TRANSCRIBE_SLICE_BATCH_MS);
}

function warmTranscribeConnection() {
  fetch(signalingUrl("/api/health"), { method: "GET" }).catch(() => {});
}

function stopLiveTranscribeLoop() {
  if (liveTranscribeTimer != null) {
    window.clearInterval(liveTranscribeTimer);
    liveTranscribeTimer = null;
  }
}

function buildMicBlobSoFar() {
  const mimeType = mediaRecorder?.mimeType || "audio/webm";
  return new Blob(micRecordedChunks, { type: mimeType });
}

function flushMicRecorderData() {
  if (mediaRecorder?.state === "recording") {
    try {
      mediaRecorder.requestData();
    } catch {
      /* ignore */
    }
  }
}

async function tickLiveTranscribe() {
  if (
    !TRANSCRIBE_LIVE_ENABLED ||
    !micListening.value ||
    voiceSessionCancelled ||
    !mediaRecorder ||
    liveTranscribeInFlight
  ) {
    return;
  }
  const recordMs = micRecordDurationMs();
  if (!micHasDetectedSpeech || recordMs < TRANSCRIBE_LIVE_MIN_MS) return;
  flushMicRecorderData();
  const blob = buildMicBlobSoFar();
  if (shouldSkipTranscribeBlob(blob, { slice: false }) || blob.size < TRANSCRIBE_LIVE_MIN_BYTES) return;
  const now = performance.now();
  if (
    blob.size <= lastLiveTranscribeBytes &&
    now - lastLiveTranscribeAtMs < TRANSCRIBE_LIVE_CHUNK_MS * 0.85
  ) {
    return;
  }
  liveTranscribeInFlight = true;
  const seq = liveTranscribeSeq;
  try {
    const { text, latencyMs } = await transcribeMicBlob(blob, { live: true });
    if (seq !== liveTranscribeSeq || voiceSessionCancelled || !micListening.value) return;
    sttChunkCount += 1;
    lastLiveTranscribeBytes = blob.size;
    lastLiveTranscribeAtMs = performance.now();
    lastLiveTranscribeApiMs = latencyMs;
    if (text && !isPhantomTranscript(text, blob, recordMs)) {
      liveTranscriptText = text;
      interimTranscript.value = text;
      if (sttFirstChunkLatencyMs == null && micTapStartMs > 0) {
        sttFirstChunkLatencyMs =
          Math.round((performance.now() - micTapStartMs) * 10) / 10;
      }
      console.info("[live-transcribe chunk]", {
        chunk: sttChunkCount,
        api_ms: latencyMs,
        chars: text.length,
      });
    }
  } catch (e) {
    console.warn("[live-transcribe chunk]", e);
  } finally {
    liveTranscribeInFlight = false;
  }
}

function startLiveTranscribeLoop() {
  stopLiveTranscribeLoop();
  if (!TRANSCRIBE_LIVE_INTERVAL_ENABLED) return;
  liveTranscribeTimer = window.setInterval(() => {
    void tickLiveTranscribe();
  }, TRANSCRIBE_LIVE_CHUNK_MS);
}

function normalizeSttMetrics(stt) {
  if (stt == null) return {};
  if (typeof stt === "number") {
    return Number.isFinite(stt) && stt >= 0 ? { finalApiMs: stt } : {};
  }
  return stt && typeof stt === "object" ? stt : {};
}

/** Skip transcribe when there is no meaningful audio (empty blob, silence, or no speech yet). */
function shouldSkipTranscribeBlob(blob, opts = {}) {
  if (!blob?.size) return true;
  const minBytes = opts.slice ? TRANSCRIBE_SLICE_MIN_BYTES : MIC_MIN_TRANSCRIBE_BYTES;
  if (blob.size < minBytes) return true;
  if (opts.slice) {
    if (!micHasDetectedSpeech) return true;
    if (micLastRms < MIC_SILENCE_RMS_THRESHOLD) return true;
  } else if (!micHasDetectedSpeech) {
    return true;
  }
  return false;
}

function isPhantomTranscript(text, blob, recordMs) {
  const t = String(text || "").trim();
  if (!t) return true;
  if (recordMs > 0 && recordMs < MIC_MIN_TRANSCRIBE_MS) return true;
  if (blob && blob.size > 0 && blob.size < MIC_MIN_TRANSCRIBE_BYTES) return true;
  if (PHANTOM_TRANSCRIPT_RE.test(t)) return true;
  if (t.length <= 12 && recordMs > 0 && recordMs < MIC_MIN_RECORD_BEFORE_SILENCE_MS * 2) {
    return true;
  }
  return false;
}

/** Mic tap to cancel: stop capture without transcribe or voice-turn. */
function abortMicCapture() {
  voiceSessionCancelled = true;
  clearSilenceTimer();
  stopMicSilenceMonitor();
  stopLiveTranscribeLoop();
  resetLiveTranscribeState();
  if (micMaxRecordTimer != null) {
    window.clearTimeout(micMaxRecordTimer);
    micMaxRecordTimer = null;
  }
  micRecordedChunks = [];
  if (mediaRecorder) {
    mediaRecorder.onstop = null;
    if (mediaRecorder.state !== "inactive") {
      try {
        mediaRecorder.stop();
      } catch {
        /* ignore */
      }
    }
    mediaRecorder = null;
  }
  releaseMicCapture();
  micListening.value = false;
  playMicTone("off");
  clearCaptionHideTimer();
  finalTranscript.value = "";
  interimTranscript.value = "";
  clearMicTapTiming();
  voiceSessionCancelled = false;
}

function transcribeLanguageForTag(tag) {
  const raw = String(tag || "en-US").trim();
  const t =
    raw.toLowerCase() === "auto" && typeof navigator !== "undefined" && navigator.language
      ? navigator.language.toLowerCase()
      : raw.toLowerCase();
  if (t.startsWith("zh") || t.startsWith("yue") || t.startsWith("wuu") || t.startsWith("nan")) {
    return "chinese";
  }
  if (t.startsWith("ja")) return "japanese";
  if (t.startsWith("ko")) return "korean";
  if (t.startsWith("ru")) return "russian";
  if (t.startsWith("it")) return "italian";
  if (t.startsWith("es")) return "spanish";
  if (t.startsWith("fr")) return "french";
  if (t.startsWith("de")) return "german";
  return "english";
}

function resolveTranscribeChunkLengthS(opts = {}) {
  if (opts.slice) return null;
  if (TRANSCRIBE_MODE !== "chunked") return null;
  if (opts.live && TRANSCRIBE_LIVE_CHUNK_LENGTH_S > 0) {
    return TRANSCRIBE_LIVE_CHUNK_LENGTH_S;
  }
  return TRANSCRIBE_CHUNK_LENGTH_S > 0 ? TRANSCRIBE_CHUNK_LENGTH_S : 10;
}

function transcribePostUrl(opts = {}) {
  if (opts.slice && TRANSCRIBE_DIRECT_URL) {
    return TRANSCRIBE_DIRECT_URL;
  }
  return signalingUrl("/api/transcribe");
}

async function transcribeMicBlob(blob, opts = {}) {
  if (shouldSkipTranscribeBlob(blob, opts)) {
    return { text: "", latencyMs: 0, chunks: [], skipped: true };
  }
  const fd = new FormData();
  const ext = blob.type.includes("webm") ? "webm" : blob.type.includes("ogg") ? "ogg" : "wav";
  fd.append("file", blob, `mic.${ext}`);
  fd.append("language", transcribeLanguageForTag(resolveMicLang()));
  if (opts.slice) {
    fd.append("slice", "1");
    if (transcribeBackend.value === "whisper") {
      fd.append("mode", "sequential");
    }
  } else if (transcribeBackend.value === "whisper") {
    fd.append("mode", TRANSCRIBE_MODE);
    const chunkLen = resolveTranscribeChunkLengthS(opts);
    if (chunkLen != null) {
      fd.append("chunk_length_s", String(chunkLen));
    }
  }
  const reqStartMs = performance.now();
  const res = await fetch(transcribePostUrl(opts), {
    method: "POST",
    headers: { Accept: "application/json" },
    body: fd,
  });
  const latencyMs = Math.max(0, performance.now() - reqStartMs);
  if (!res.ok) {
    const body = await res.text();
    if (opts.slice && (res.status === 422 || res.status === 400)) {
      return { text: "", latencyMs: Math.round(latencyMs * 10) / 10, chunks: [] };
    }
    throw new Error(body.slice(0, 400) || `Transcribe failed (${res.status})`);
  }
  const data = await res.json();
  const serverMs = Number(data.latency_ms);
  return {
    text: String(data.text || "").trim(),
    latencyMs:
      Number.isFinite(serverMs) && serverMs >= 0
        ? Math.round(serverMs * 10) / 10
        : Math.round(latencyMs * 10) / 10,
    chunks: Array.isArray(data.chunks) ? data.chunks : [],
  };
}

function scheduleStopAfter(delayMs) {
  clearSilenceTimer();
  silenceTimer = window.setTimeout(() => {
    silenceTimer = null;
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      try {
        mediaRecorder.stop();
      } catch {
        /* ignore */
      }
    } else if (recInstance) {
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
    const durSec =
      kind === "tap"
        ? MIC_TONE_TAP_MS / 1000
        : kind === "on"
          ? MIC_TONE_ON_MS / 1000
          : MIC_TONE_OFF_MS / 1000;
    const attack = Math.min(0.02, durSec * 0.12);
    const releaseStart = Math.max(attack + 0.02, durSec * 0.55);
    const stopAt = now + durSec;
    if (kind === "tap") {
      osc.frequency.setValueAtTime(1200, now);
    } else if (kind === "on") {
      osc.frequency.setValueAtTime(980, now);
    } else {
      osc.frequency.setValueAtTime(620, now);
    }
    const peak = kind === "tap" ? 0.32 : kind === "on" ? 0.24 : 0.2;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(peak, now + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + releaseStart);
    osc.stop(stopAt);
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

/** Browser speech recognition only (not used for server /api/transcribe). */
function computeSttLatencyMs() {
  const endMs = sttFinalTranscriptMs > 0 ? sttFinalTranscriptMs : performance.now();
  const startMs =
    sttSessionStartMs > 0
      ? sttSessionStartMs
      : voiceInputRequestMs > 0
        ? voiceInputRequestMs
        : 0;
  if (startMs > 0) {
    return Math.max(0, endMs - startMs);
  }
  return null;
}

async function runVoicePipeline(userText, stt = null) {
  const t = userText.trim();
  if (!t) return;
  const sttMetrics = normalizeSttMetrics(stt);
  const sttLatencyMs = sttMetrics.finalApiMs;
  voiceThinking.value = true;
  webrtcError.value = "";
  clearWebRtcTtfaPending();
  pendingVoiceAnalytics = null;
  const requestSentMs = performance.now();
  beginVoiceTurnTtfaWatch(requestSentMs);
  const sid = String(sessionId.value || getHologramSessionId() || "").trim();
  const payload = { text: t };
  if (sid && sid !== "0") payload.sessionid = sid;
  if (sttLatencyMs != null && Number.isFinite(sttLatencyMs) && sttLatencyMs >= 0) {
    payload.stt_latency_ms = Math.round(sttLatencyMs * 10) / 10;
  }
  if (
    sttMetrics.firstChunkMs != null &&
    Number.isFinite(sttMetrics.firstChunkMs) &&
    sttMetrics.firstChunkMs >= 0
  ) {
    payload.stt_first_chunk_latency_ms = Math.round(sttMetrics.firstChunkMs * 10) / 10;
  }
  if (
    sttMetrics.chunkCount != null &&
    Number.isFinite(sttMetrics.chunkCount) &&
    sttMetrics.chunkCount >= 0
  ) {
    payload.stt_chunk_count = Math.round(sttMetrics.chunkCount);
  }
  try {
    const res = await fetch(signalingUrl(VOICE_PIPELINE_API), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(body.slice(0, 400) || `Ollama step failed (${res.status})`);
    }
    const data = await res.json();
    const voiceTurnCompleteMs = performance.now();
    const clientVoiceTurnMs = Math.max(0, voiceTurnCompleteMs - requestSentMs);

    const answer = String(data.answer || "").trim();
    applyShowImageFromVoiceTurn(data);
    let speakText = String(data.speak_text ?? "").trim();
    if (!speakText) {
      speakText = stripReceiptForSpeech(answer);
    }
    const spoken = speakText ? stripReceiptForSpeech(speakText) : "";
    const humanDispatched = Boolean(data.human_dispatched);
    const streamHuman = Boolean(data.rag?.stream_human);
    const expectsWebRtcPlayback = Boolean(spoken) || humanDispatched || streamHuman;
    const streamLat = data.rag?.stream_dispatch_latency;
    const latSummary = streamLat?.summary;
    console.info(`[${VOICE_PIPELINE_API}] complete — TTS→humanaudio + WebRTC (latency ms)`, {
      human_dispatched: humanDispatched,
      stream_human: streamHuman,
      dispatch_mode: data.rag?.dispatch_mode,
      stream_human_unit: data.rag?.stream_human_unit,
      stream_human_words_per_chunk: data.rag?.stream_human_words_per_chunk,
      rag_latency_ms: data.rag?.rag_latency_ms,
      rag_first_chunk_enqueued_ms:
        latSummary?.first_chunk_enqueued_ms ??
        data.rag?.rag_first_chunk_ms ??
        data.rag?.rag_first_sentence_ms,
      first_chunk_completed_ms: latSummary?.first_chunk_completed_ms,
      first_chunk_tts_ms: latSummary?.first_chunk_tts_ms,
      first_chunk_humanaudio_ms: latSummary?.first_chunk_humanaudio_ms,
      first_chunk_total_ms: latSummary?.first_chunk_total_ms,
      avg_total_ms: latSummary?.avg_total_ms,
      sum_total_ms: latSummary?.sum_total_ms,
      human_chunk_count: data.rag?.human_chunk_count ?? data.rag?.human_sentence_count,
      stream_chunks: streamLat?.chunks,
      speak_chars: spoken.length,
      total_request_ms: data.total_request_ms,
      stt_latency_ms: sttLatencyMs,
      sessionid: sid || null,
    });

    if (spoken) {
      const useLegacyHuman =
        VOICE_PIPELINE_API.includes("voice-turn") && !humanDispatched && !streamHuman;
      if (useLegacyHuman) {
        if (!sid || sid === "0") {
          console.warn(
            `[${VOICE_PIPELINE_API}] no valid WebRTC sessionid — connect hologram first`
          );
        } else {
          postHuman(spoken);
        }
      }
      const turnId = Number(data.analytics_turn_id);
      const ragStreamMs = Number(data.rag?.rag_latency_ms);
      const serverMs = Number(data.total_request_ms);
      let middleMs = null;
      if (Number.isFinite(ragStreamMs)) {
        middleMs = ragStreamMs;
      } else if (Number.isFinite(serverMs)) {
        middleMs = serverMs;
      }
      if (Number.isFinite(turnId) && turnId > 0) {
        attachVoiceTurnTtfaTurnId(turnId);
        pendingVoiceAnalytics = {
          turnId,
          sttMs: Number.isFinite(sttLatencyMs) ? sttLatencyMs : null,
          ragMs: middleMs,
          micTapStartMs: micTapStartMs > 0 ? micTapStartMs : 0,
          voiceTurnCompleteMs,
          clientVoiceTurnMs: Math.round(clientVoiceTurnMs * 10) / 10,
        };
      }
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
    const hasShowImage = SHOW_IMAGE_ENABLED && featuredMenuImages.value.length > 0;
    if (!speakText && !receipt?.items?.length && orderNum == null && !hasShowImage) {
      throw new Error("Model returned an empty reply.");
    }
    if (!expectsWebRtcPlayback) {
      clearWebRtcTtfaPending();
      pendingVoiceAnalytics = null;
      clearMicTapTiming();
      deactivateWebRtcStage();
    } else {
      const chunkCount = Number(
        data.rag?.human_chunk_count ?? data.rag?.human_sentence_count
      );
      const sumTotalMs = Number(latSummary?.sum_total_ms);
      let maxOverlayMs = Number.isFinite(sumTotalMs) && sumTotalMs > 0 ? sumTotalMs + 5000 : null;
      if (!maxOverlayMs && spoken.length) {
        const chunks = Number.isFinite(chunkCount) && chunkCount > 0 ? chunkCount : 1;
        maxOverlayMs = Math.min(
          ANSWER_END_MAX_MS,
          Math.max(12000, spoken.length * 85 + chunks * 2500)
        );
      }
      ensureAnswerPlaybackEndWatch(answerPlaybackBaseline(), maxOverlayMs);
    }
  } catch (e) {
    clearWebRtcTtfaPending();
    pendingVoiceAnalytics = null;
    clearMicTapTiming();
    clearAnswerPlaybackEndWatch();
    deactivateWebRtcStage();
    webrtcError.value = e instanceof Error ? e.message : String(e);
  } finally {
    voiceThinking.value = false;
  }
}

function stopMicInternal({ cancel }) {
  if (cancel) {
    if (mediaRecorder) {
      abortMicCapture();
      return;
    }
    voiceSessionCancelled = true;
    clearMicTapTiming();
    clearSilenceTimer();
    if (recInstance) {
      try {
        recInstance.stop();
      } catch {
        /* ignore */
      }
      return;
    }
    releaseMicCapture();
    micListening.value = false;
    finalTranscript.value = "";
    interimTranscript.value = "";
    voiceSessionCancelled = false;
    return;
  }
  voiceSessionCancelled = false;
  clearSilenceTimer();
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    try {
      mediaRecorder.stop();
    } catch {
      releaseMicCapture();
      micListening.value = false;
    }
    return;
  }
  if (recInstance) {
    try {
      recInstance.stop();
    } catch {
      /* ignore */
    }
  } else {
    releaseMicCapture();
    micListening.value = false;
    if (cancel) {
      finalTranscript.value = "";
      interimTranscript.value = "";
    }
  }
}

function startMicSilenceMonitor(analyser) {
  stopMicSilenceMonitor();
  micHasDetectedSpeech = false;
  micLastRms = 0;
  micLastSoundMs = performance.now();
  const buf = new Uint8Array(analyser.fftSize);
  const tick = () => {
    if (!micListening.value || !mediaRecorder) return;
    analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / buf.length);
    micLastRms = rms;
    if (rms > MIC_SILENCE_RMS_THRESHOLD) {
      if (!micHasDetectedSpeech && TRANSCRIBE_PARALLEL_ENABLED) {
        if (TRANSCRIBE_SLICE_BATCH_MS > 0) flushParallelSlicePending();
        else pumpParallelSliceQueue();
      }
      micHasDetectedSpeech = true;
      micLastSoundMs = performance.now();
    } else if (
      micHasDetectedSpeech &&
      performance.now() - micLastSoundMs >= SILENCE_MS &&
      performance.now() - micRecordStartedMs >= MIC_MIN_RECORD_BEFORE_SILENCE_MS
    ) {
      stopMicInternal({ cancel: false });
      return;
    }
    micSilenceRaf = requestAnimationFrame(tick);
  };
  micSilenceRaf = requestAnimationFrame(tick);
}

async function onMediaRecorderStop() {
  if (voiceSessionCancelled) {
    abortMicCapture();
    return;
  }
  stopLiveTranscribeLoop();
  micListening.value = false;
  playMicTone("off");
  const recordMs = micRecordDurationMs();
  const mimeType = mediaRecorder?.mimeType || "audio/webm";
  const blob = new Blob(micRecordedChunks, { type: mimeType });
  const savedFirstChunkMs = sttFirstChunkLatencyMs;
  const savedChunkCount = sttChunkCount;
  releaseMicCapture();
  if (
    shouldSkipTranscribeBlob(blob, { slice: false }) ||
    recordMs < MIC_MIN_TRANSCRIBE_MS
  ) {
    resetLiveTranscribeState();
    clearMicTapTiming();
    return;
  }
  try {
    if (TRANSCRIBE_PARALLEL_ENABLED) {
      flushParallelSlicePending();
      pumpParallelSliceQueue();
    }
    if (TRANSCRIBE_PARALLEL_ENABLED && parallelSliceJobs.length) {
      await Promise.allSettled(parallelSliceJobs);
    } else if (liveTranscribeInFlight) {
      await new Promise((r) => window.setTimeout(r, 120));
    }
    const sinceLiveMs = performance.now() - lastLiveTranscribeAtMs;
    const parallelJoined = joinParallelTranscriptParts(parallelSliceParts);
    const browserFallback = `${browserInterimText}`.trim();
    const canReuseParallel =
      TRANSCRIBE_PARALLEL_ENABLED &&
      parallelJoined &&
      !isPhantomTranscript(parallelJoined, blob, recordMs);
    const canReuseBrowser =
      !canReuseParallel &&
      TRANSCRIBE_PARALLEL_ENABLED &&
      browserFallback &&
      !isPhantomTranscript(browserFallback, blob, recordMs);
    const canReuseLive =
      !canReuseParallel &&
      liveTranscriptText &&
      sinceLiveMs < 800 &&
      lastLiveTranscribeBytes >= blob.size * 0.9 &&
      lastLiveTranscribeApiMs != null &&
      !isPhantomTranscript(liveTranscriptText, blob, recordMs);
    let text = "";
    let latencyMs = null;
    let finalChunkCount = savedChunkCount;
    if (canReuseParallel) {
      text = parallelJoined;
      latencyMs = lastLiveTranscribeApiMs;
      finalChunkCount = savedChunkCount;
    } else if (canReuseBrowser) {
      text = browserFallback;
      latencyMs = sttFirstChunkLatencyMs;
      finalChunkCount = savedChunkCount;
    } else if (canReuseLive) {
      text = liveTranscriptText;
      latencyMs = lastLiveTranscribeApiMs;
      finalChunkCount = savedChunkCount;
    } else {
      const result = await transcribeMicBlob(blob);
      text = result.text;
      latencyMs = result.latencyMs;
      finalChunkCount = savedChunkCount + 1;
    }
    if (text && !isPhantomTranscript(text, blob, recordMs)) {
      sttFinalTranscriptMs = performance.now();
      showCaptionThenHide(text);
      void runVoicePipeline(text, {
        finalApiMs: latencyMs,
        firstChunkMs: savedFirstChunkMs,
        chunkCount: finalChunkCount,
      });
    } else {
      finalTranscript.value = "";
      interimTranscript.value = "";
      clearMicTapTiming();
    }
  } catch (e) {
    finalTranscript.value = "";
    interimTranscript.value = "";
    webrtcError.value = e instanceof Error ? e.message : String(e);
    clearMicTapTiming();
  } finally {
    resetLiveTranscribeState();
  }
}

async function startServerMicCapture() {
  const tapMs = performance.now();
  micTapStartMs = tapMs;
  voiceInputRequestMs = tapMs;
  sttSessionStartMs = tapMs;
  sttFinalTranscriptMs = 0;
  voiceSessionCancelled = false;
  clearCaptionHideTimer();
  resetLiveTranscribeState();
  finalTranscript.value = "";
  interimTranscript.value = "";
  micCaptureStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      channelCount: 1,
    },
  });
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
  mediaRecorder = mime
    ? new MediaRecorder(micCaptureStream, { mimeType: mime })
    : new MediaRecorder(micCaptureStream);
  micRecordedChunks = [];
  mediaRecorder.ondataavailable = (ev) => {
    if (!ev.data?.size) return;
    micRecordedChunks.push(ev.data);
    if (!TRANSCRIBE_PARALLEL_ENABLED) return;
    if (!webmHeaderChunk) {
      webmHeaderChunk = ev.data;
      return;
    }
    enqueueParallelSliceTranscribe(ev.data);
  };
  mediaRecorder.onstop = () => {
    void onMediaRecorderStop();
  };
  mediaRecorder.onerror = () => {
    webrtcError.value = "Microphone recording failed.";
    stopMicInternal({ cancel: true });
  };
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const source = ctx.createMediaStreamSource(micCaptureStream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
    micRecordStartedMs = performance.now();
    mediaRecorder.start(MIC_RECORDER_TIMESLICE_MS);
    micListening.value = true;
    playMicTone("on");
    startMicSilenceMonitor(analyser);
    startLiveTranscribeLoop();
    warmTranscribeConnection();
    startBrowserInterimStt();
    micMaxRecordTimer = window.setTimeout(() => {
      micMaxRecordTimer = null;
      if (micListening.value) stopMicInternal({ cancel: false });
    }, MIC_MAX_RECORD_MS);
  } catch (e) {
    releaseMicCapture();
    throw e;
  }
}

async function toggleMic() {
  webrtcError.value = "";
  clearFeaturedMenuImage();
  clearCaptionHideTimer();
  if (!started.value) {
    webrtcError.value = "Connecting… try again in a moment.";
    return;
  }
  if (!micAvailable.value) {
    webrtcError.value = "Voice input is not available.";
    return;
  }
  if (micListening.value) {
    stopMicInternal({ cancel: true });
    return;
  }

  pendingVoiceAnalytics = null;
  clearMicTapTiming();
  await interruptAvatarSpeech();

  if (useServerTranscribe.value) {
    void startServerMicCapture().catch((e) => {
      webrtcError.value = e instanceof Error ? e.message : String(e);
      releaseMicCapture();
      micListening.value = false;
      clearMicTapTiming();
    });
    return;
  }

  if (!speechRecCtor.value) {
    webrtcError.value = "Voice input needs a Chromium-based browser.";
    return;
  }

  voiceSessionCancelled = false;
  clearCaptionHideTimer();
  const SR = speechRecCtor.value;
  recInstance = new SR();
  recInstance.lang = resolveMicLang();
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
      if (sttFinalTranscriptMs <= 0) {
        sttFinalTranscriptMs = performance.now();
      }
      scheduleStopAfter(FINAL_RESULT_STOP_MS);
    } else if (hasText) {
      scheduleSilenceStop();
    }
  };

  recInstance.onstart = () => {
    sttSessionStartMs = performance.now();
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
      clearMicTapTiming();
      return;
    }
    const combined = `${finalTranscript.value} ${interimTranscript.value}`.trim();
    if (combined && !isPhantomTranscript(combined, null, micRecordDurationMs())) {
      showCaptionThenHide(combined);
      void runVoicePipeline(combined, null);
    } else {
      finalTranscript.value = "";
      interimTranscript.value = "";
      clearMicTapTiming();
    }
  };

  try {
    const tapMs = performance.now();
    micTapStartMs = tapMs;
    voiceInputRequestMs = tapMs;
    sttSessionStartMs = 0;
    sttFinalTranscriptMs = 0;
    recInstance.start();
    micListening.value = true;
    playMicTone("on");
  } catch (e) {
    webrtcError.value = e instanceof Error ? e.message : String(e);
    recInstance = null;
    micListening.value = false;
  }
}

function onMicPointerDown() {
  playMicTone("tap");
}

function clearCaptionHideTimer() {
  if (captionHideTimer != null) {
    window.clearTimeout(captionHideTimer);
    captionHideTimer = null;
  }
}

/** Show heard/transcribed text on the avatar caption, then clear after a short delay. */
function showCaptionThenHide(text) {
  clearCaptionHideTimer();
  const t = String(text || "").trim();
  if (!t) {
    finalTranscript.value = "";
    interimTranscript.value = "";
    return;
  }
  finalTranscript.value = t;
  interimTranscript.value = "";
  captionHideTimer = window.setTimeout(() => {
    captionHideTimer = null;
    finalTranscript.value = "";
    interimTranscript.value = "";
  }, CAPTION_HIDE_MS);
}

const CAPTION_STATUS_PHRASES = new Set([
  "listening…",
  "listening...",
  "transcribing…",
  "transcribing...",
  "recording…",
  "recording...",
]);

const liveCaption = computed(() => {
  const parts = [finalTranscript.value, interimTranscript.value]
    .map((s) => String(s || "").trim())
    .filter((s) => s && !CAPTION_STATUS_PHRASES.has(s.toLowerCase()));
  return parts.join(" ").trim();
});

function stripReceiptForSpeech(raw) {
  return String(raw || "")
    .replace(/<receipt>\s*[\s\S]*?\s*<\/receipt>/gi, "")
    .replace(/<orderdone>\s*[\s\S]*?\s*<\/orderdone>/gi, "")
    .replace(/<show_image>\s*[\s\S]*?\s*<\/show_image>/gi, "")
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
  void syncSelectedAvatarFromServer().then((id) => {
    const avatarId = id || getSelectedAvatarId();
    if (avatarId) {
      lastPolledSelectedAvatarId = avatarId;
      applySelectedAvatarToCurrentSession(avatarId);
    }
  });
  webrtcStatusPollId = window.setInterval(() => void refreshWebrtcStatus(), 8000);
  window.addEventListener("hologram-avatar-selected", onHologramAvatarSelected);
  window.addEventListener("storage", onHologramAvatarStorage);
  void connect();
});

onUnmounted(() => {
  if (webrtcStatusPollId != null) {
    window.clearInterval(webrtcStatusPollId);
    webrtcStatusPollId = null;
  }
  window.removeEventListener("hologram-avatar-selected", onHologramAvatarSelected);
  window.removeEventListener("storage", onHologramAvatarStorage);
  if (orderPlacedHideTimer != null) {
    window.clearTimeout(orderPlacedHideTimer);
    orderPlacedHideTimer = null;
  }
  clearCaptionHideTimer();
  stopMicInternal({ cancel: true });
  clearWebRtcTtfaPending();
  disconnect();
});
</script>

<template>
  <section class="panel">
    <nav class="panel-nav" aria-label="App pages">
      <router-link class="panel-nav__link" to="/avatar">Studio</router-link>
      <router-link class="panel-nav__link" to="/analytics">Analytics</router-link>
      <router-link class="panel-nav__link" to="/video-rag">Video RAG</router-link>
    </nav>

    <div class="media-stack">
      <div
        class="video-wrap"
        :class="{
          'video-wrap--featured-image': SHOW_IMAGE_ENABLED && featuredMenuImages.length > 0,
        }"
      >
        <video ref="videoEl" class="video video--webrtc" autoplay playsinline />
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

        <div
          v-if="SHOW_IMAGE_ENABLED && featuredMenuImages.length"
          class="menu-featured"
          :class="{
            'menu-featured--multi': featuredMenuImages.length > 1,
          }"
          role="group"
          aria-label="Featured menu items"
        >
          <div class="menu-featured__grid">
            <article
              v-for="(img, fIdx) in featuredMenuImages"
              :key="img.id"
              class="menu-featured__card"
              :style="{
                '--featured-delay': `${fIdx * 0.1}s`,
                '--featured-3d-phase': `${fIdx * 0.35}s`,
              }"
            >
              <div class="menu-featured__stage">
                <img
                  class="menu-featured__img"
                  :src="img.src"
                  alt=""
                  width="480"
                  height="480"
                  loading="lazy"
                  decoding="async"
                />
              </div>
            </article>
          </div>
        </div>

        <aside
          v-show="!featuredMenuImages.length"
          class="cafe-menu"
          aria-label="Holuminex Cafe menu"
        >
          <div class="cafe-menu__center">
            <div v-if="MENU_HERO_IMAGES_ENABLED" class="cafe-menu__heroes" aria-hidden="true">
              <div
                v-for="(hero, hIdx) in MENU_HERO_ITEMS"
                :key="hero.id"
                class="cafe-menu__hero"
                :style="{
                  '--hero-delay': `${0.05 + hIdx * 0.09}s`,
                  '--hero-float-delay': `${hIdx * 0.55}s`,
                }"
              >
                <img
                  class="cafe-menu__hero-img"
                  :src="hero.src"
                  :alt="hero.label"
                  width="160"
                  height="160"
                  loading="lazy"
                  decoding="async"
                />
              </div>
            </div>
            <div class="cafe-menu__box">
              <div class="cafe-menu__panel">
                <section
                  v-for="(section, sIdx) in CAFE_MENU"
                  :key="section.id"
                  class="cafe-menu__section"
                  :style="{ '--section-delay': `${0.08 + sIdx * 0.07}s` }"
                >
                  <h3 class="cafe-menu__section-title">{{ section.title }}</h3>
                  <ul class="cafe-menu__list">
                    <li
                      v-for="item in section.items"
                      :key="item.id"
                      class="cafe-menu__row"
                    >
                      <div class="cafe-menu__row-main">
                        <span class="cafe-menu__name">{{ item.name }}</span>
                        <span class="cafe-menu__leader" aria-hidden="true" />
                        <span class="cafe-menu__price">${{ formatBillMoney(item.price) }}</span>
                      </div>
                      <p v-if="item.desc" class="cafe-menu__desc">{{ item.desc }}</p>
                      <p v-if="item.note" class="cafe-menu__note">{{ item.note }}</p>
                    </li>
                  </ul>
                </section>
              </div>
            </div>
          </div>
        </aside>

        <div class="caption" aria-live="polite">
          {{ liveCaption || "\u00a0" }}
        </div>

        <button
          type="button"
          class="mic-fab"
          :class="{ 'mic-fab--on': micListening }"
          :style="micListening ? { '--mic-pulse-sec': `${MIC_PULSE_SEC}s` } : undefined"
          :disabled="!started || !micAvailable"
          :aria-pressed="micListening"
          :aria-label="micListening ? 'Listening — tap to cancel' : 'Microphone off — tap to speak'"
          :title="
            !micAvailable
              ? 'Voice input unavailable'
              : micListening && useServerTranscribe
                ? 'Recording… tap to cancel'
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

.lang-picker {
  position: absolute;
  top: 3rem;
  right: 0.65rem;
  z-index: 7;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-width: min(18rem, 42vw);
  padding: 0.35rem 0.45rem;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(15, 23, 42, 0.12);
  box-shadow: 0 2px 9px rgba(0, 0, 0, 0.08);
}

.lang-picker__label {
  font-size: 0.72rem;
  font-weight: 700;
  color: #334155;
}

.lang-picker__select {
  border: 1px solid rgba(148, 163, 184, 0.72);
  border-radius: 8px;
  background: #fff;
  color: #0f172a;
  font-size: 0.78rem;
  padding: 0.3rem 0.38rem;
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
  border: clamp(2px, 0.14cqw, 5px) solid #d8d8d8;
  box-shadow: none;
}

.video {
  display: block;
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center center;
}

/* WebRTC stream is the only video layer on stage. */
.video--webrtc {
  z-index: 1;
  opacity: 1;
  visibility: visible;
  pointer-events: none;
}

.video-rail {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 1;
  width: clamp(2rem, 9.65cqw, 15rem);
  background: transparent;
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

@keyframes cafe-menu-in {
  from {
    opacity: 0;
    transform: translateY(18px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes cafe-menu-section-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes cafe-menu-hero-pop {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.86);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes cafe-menu-hero-float {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-8px);
  }
}

@keyframes cafe-menu-border-flow {
  0% {
    background-position: 0% 50%;
  }
  50% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0% 50%;
  }
}

@keyframes menu-featured-3d-in {
  from {
    opacity: 0;
    transform: rotateY(-14deg) rotateX(8deg) translateY(18px) scale(0.88);
  }
  to {
    opacity: 1;
    transform: rotateY(-4deg) rotateX(3deg) translateY(0) scale(1);
  }
}

@keyframes menu-featured-3d-idle {
  0%,
  100% {
    transform: rotateY(-5deg) rotateX(2deg) translateY(0) scale(1);
  }
  25% {
    transform: rotateY(5deg) rotateX(-2deg) translateY(-4px) scale(1.02);
  }
  50% {
    transform: rotateY(7deg) rotateX(3deg) translateY(-2px) scale(1.03);
  }
  75% {
    transform: rotateY(-3deg) rotateX(2deg) translateY(-3px) scale(1.01);
  }
}

.video-wrap--featured-image {
  overflow: visible;
}

.menu-featured {
  position: absolute;
  left: clamp(1.25rem, 5.5cqw, 8rem);
  right: clamp(1.25rem, 5.5cqw, 8rem);
  top: clamp(6rem, 18cqh, 14rem);
  bottom: max(3.5rem, 9cqh, env(safe-area-inset-bottom));
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  background: transparent;
  border: none;
  box-shadow: none;
  perspective: clamp(900px, 120cqw, 1400px);
  perspective-origin: 50% 55%;
}

.menu-featured__grid {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: clamp(0.45rem, 1.35cqw, 1.15rem);
  width: 100%;
  max-width: 100%;
  max-height: 100%;
  overflow: visible;
  padding: clamp(0.75rem, 2cqh, 1.5rem) clamp(0.5rem, 1.2cqw, 1rem);
  background: transparent;
  border: none;
  box-shadow: none;
}

.menu-featured__card {
  --featured-delay: 0s;
  --featured-3d-phase: 0s;
  flex: 0 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: clamp(0.15rem, 0.4cqw, 0.3rem);
  padding: clamp(0.35rem, 1cqh, 0.75rem);
  background: transparent;
  overflow: visible;
}

.menu-featured__stage {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  box-shadow: none;
  transform-style: preserve-3d;
  transform-origin: 50% 62%;
  animation:
    menu-featured-3d-in 0.75s cubic-bezier(0.22, 1, 0.36, 1) var(--featured-delay) both,
    menu-featured-3d-idle 5.5s ease-in-out calc(0.75s + var(--featured-3d-phase)) infinite;
  will-change: transform;
  overflow: visible;
}

.menu-featured__img {
  position: relative;
  z-index: 1;
  display: block;
  width: auto;
  max-width: min(100%, 36rem);
  max-height: min(100%, 48cqh, 54vh);
  height: auto;
  object-fit: contain;
  object-position: center center;
  background: transparent;
  /* PNGs on black: knock out backdrop, avatar shows through (hologram look) */
  mix-blend-mode: screen;
  transform: translateZ(24px);
  transform-style: preserve-3d;
  filter: none;
  box-shadow: none;
}

.menu-featured__caption {
  margin: 0;
  max-width: clamp(5rem, 18cqw, 11rem);
  font-size: clamp(0.52rem, 1.05cqw, 0.75rem);
  font-weight: 700;
  line-height: 1.2;
  text-align: center;
  color: #fff;
  text-shadow:
    0 1px 3px rgba(0, 0, 0, 0.55),
    0 0 12px rgba(0, 0, 0, 0.35);
}

.menu-featured--multi .menu-featured__stage {
  width: auto;
  max-width: clamp(5.5rem, 19cqw, 11.5rem);
}

.menu-featured--multi .menu-featured__img {
  width: auto;
  max-width: 100%;
  max-height: min(100%, 22cqh, 26vh);
}

.cafe-menu {
  position: absolute;
  left: clamp(2rem, 9.65cqw, 15rem);
  right: clamp(2rem, 9.65cqw, 15rem);
  bottom: max(3.35rem, 8.5cqh, env(safe-area-inset-bottom));
  z-index: 4;
  display: flex;
  justify-content: center;
  pointer-events: none;
  animation: cafe-menu-in 0.65s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.cafe-menu__center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: clamp(0.35rem, 0.9cqw, 0.7rem);
  width: 100%;
  max-width: min(98cqw, 100%);
}

.cafe-menu__heroes {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  flex-wrap: nowrap;
  gap: clamp(0.5rem, 2cqw, 1.5rem);
  width: 100%;
  padding: 0 clamp(0.25rem, 0.6cqw, 0.5rem);
  pointer-events: none;
}

.cafe-menu__hero {
  --hero-delay: 0s;
  --hero-float-delay: 0s;
  flex: 0 1 auto;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  min-width: clamp(3.5rem, 14cqw, 8rem);
  max-width: clamp(4.5rem, 18cqw, 10rem);
  animation:
    cafe-menu-hero-pop 0.55s cubic-bezier(0.22, 1, 0.36, 1) var(--hero-delay) both,
    cafe-menu-hero-float 3.4s ease-in-out var(--hero-float-delay) infinite;
}

.cafe-menu__hero-img {
  display: block;
  width: 100%;
  height: clamp(3.25rem, 14cqw, 7.5rem);
  object-fit: contain;
  filter: drop-shadow(0 8px 16px rgba(0, 0, 0, 0.22));
}

.cafe-menu__box {
  --menu-border-width: clamp(2px, 0.35cqw, 3px);
  position: relative;
  isolation: isolate;
  display: block;
  width: 100%;
  padding: clamp(0.55rem, 1.25cqw, 0.9rem) clamp(0.65rem, 1.5cqw, 1.1rem);
  border: none;
  border-radius: clamp(8px, 1.1cqw, 14px);
  background: rgba(15, 23, 42, 0.14);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.12);
  pointer-events: auto;
}

.cafe-menu__box::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: var(--menu-border-width);
  background: linear-gradient(
    90deg,
    rgba(167, 139, 250, 0.55),
    rgba(233, 213, 255, 1),
    rgba(196, 181, 253, 0.95),
    rgba(139, 92, 246, 0.7),
    rgba(233, 213, 255, 1),
    rgba(167, 139, 250, 0.55)
  );
  background-size: 280% 100%;
  animation: cafe-menu-border-flow 3.2s ease-in-out infinite;
  pointer-events: none;
  z-index: -1;
  -webkit-mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
}

.cafe-menu__panel {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: clamp(0.45rem, 1.1cqw, 0.85rem) clamp(0.55rem, 1.35cqw, 1rem);
  width: 100%;
  max-height: min(48cqh, 54vh);
  overflow-x: hidden;
  overflow-y: auto;
  padding: clamp(0.1rem, 0.3cqw, 0.25rem) clamp(0.15rem, 0.4cqw, 0.35rem);
  background: transparent;
  border: none;
  box-shadow: none;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  scrollbar-color: rgba(196, 181, 253, 0.55) transparent;
}

.cafe-menu__panel::-webkit-scrollbar {
  width: 4px;
}

.cafe-menu__panel::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: rgba(196, 181, 253, 0.55);
}

.cafe-menu__section {
  --section-delay: 0s;
  min-width: 0;
  margin: 0;
  animation: cafe-menu-section-in 0.5s ease var(--section-delay) both;
}

.cafe-menu__section-title {
  margin: 0 0 clamp(0.28rem, 0.55cqw, 0.45rem);
  padding: clamp(0.28rem, 0.55cqw, 0.42rem) clamp(0.65rem, 1.45cqw, 1rem);
  border-radius: clamp(6px, 0.75cqw, 10px);
  font-size: clamp(0.62rem, 1.38cqw, 0.95rem);
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  text-align: center;
  color: #fff;
  background: linear-gradient(120deg, rgba(233, 213, 255, 0.92) 0%, rgba(196, 181, 253, 0.88) 100%);
  text-shadow: 0 1px 2px rgba(91, 33, 182, 0.2);
}

.cafe-menu__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.cafe-menu__row {
  margin-bottom: clamp(0.22rem, 0.45cqw, 0.32rem);
}

.cafe-menu__row-main {
  display: flex;
  align-items: baseline;
  gap: 0.25rem;
}

.cafe-menu__name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: clamp(0.68rem, 1.48cqw, 1rem);
  font-weight: 700;
  color: #fff;
  line-height: 1.25;
  word-break: break-word;
  text-shadow:
    0 1px 2px rgba(0, 0, 0, 0.55),
    0 0 10px rgba(0, 0, 0, 0.35);
}

.cafe-menu__leader {
  flex: 1 1 auto;
  min-width: 0.35rem;
  margin: 0 0.15rem;
  border-bottom: 1px dotted rgba(255, 255, 255, 0.45);
  transform: translateY(-0.15em);
}

.cafe-menu__price {
  flex: 0 0 auto;
  font-size: clamp(0.68rem, 1.45cqw, 0.98rem);
  font-weight: 800;
  color: #fff;
  font-variant-numeric: tabular-nums;
  text-shadow:
    0 1px 2px rgba(0, 0, 0, 0.55),
    0 0 10px rgba(0, 0, 0, 0.35);
}

.cafe-menu__desc,
.cafe-menu__note {
  margin: 0.1rem 0 0;
  font-size: clamp(0.55rem, 1.15cqw, 0.82rem);
  line-height: 1.3;
  color: rgba(255, 255, 255, 0.88);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.45);
}

.cafe-menu__note {
  font-style: italic;
  color: rgba(233, 213, 255, 0.95);
}

@media (prefers-reduced-motion: reduce) {
  .cafe-menu,
  .cafe-menu__section,
  .cafe-menu__hero,
  .menu-featured,
  .menu-featured__stage,
  .menu-featured__card {
    animation: none;
  }

  .menu-featured__stage {
    transform: none;
  }

  .menu-featured__img {
    transform: none;
  }

  .cafe-menu__box::before {
    animation: none;
    background-position: 50% 50%;
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

.video-loading__logo {
  width: clamp(2rem, 3.6cqw, 3.5rem);
  height: clamp(2rem, 3.6cqw, 3.5rem);
  animation: spin-logo 1.1s linear infinite;
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

@keyframes spin-logo {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.start-overlay {
  position: absolute;
  inset: 0;
  z-index: 7;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: clamp(1rem, 2.4cqw, 1.8rem);
  background: rgba(8, 10, 18, 0.96);
  backdrop-filter: blur(8px);
}

.start-overlay__card {
  width: min(92%, 28rem);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.65rem;
  border-radius: 18px;
  padding: clamp(0.9rem, 2.1cqw, 1.4rem);
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(148, 163, 184, 0.35);
  box-shadow: 0 14px 35px rgba(0, 0, 0, 0.22);
}

.start-overlay__logo {
  width: 3rem;
  height: 3rem;
  animation: spin-logo 1.35s linear infinite;
}

.start-overlay__title {
  margin: 0.1rem 0 0;
  font-size: clamp(1rem, 2.2cqw, 1.4rem);
  font-weight: 800;
  color: #0f172a;
}

.start-overlay__sub {
  margin: 0;
  text-align: center;
  color: #475569;
  font-size: clamp(0.75rem, 1.4cqw, 0.9rem);
}

.start-overlay__btn {
  width: 100%;
  border: none;
  border-radius: 999px;
  padding: 0.62rem 0.95rem;
  margin-top: 0.2rem;
  font-size: 0.92rem;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(120deg, #0d9488, #2563eb);
  cursor: pointer;
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
  animation: mic-pulse var(--mic-pulse-sec, 2.2s) ease-in-out infinite;
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
