import { playMp3WithLipSync } from "./audio-lipsync";
import {
  bumpMouth,
  setEmotion,
  startLipSyncLoop,
  stopLipSyncLoop,
  type Emotion,
} from "./avatar";
import { initPhotoMouthCanvas } from "./photo-mouth-canvas";

const SYSTEM_PROMPT =
  "You are a friendly, concise digital twin assistant. Keep answers short (2–5 sentences) unless asked for detail. Speak naturally for voice output.";

const input = document.getElementById("user-input") as HTMLTextAreaElement;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
const speakDirectBtn = document.getElementById("speak-direct-btn") as HTMLButtonElement;
const statusEl = document.getElementById("status") as HTMLParagraphElement;
const replyEl = document.getElementById("reply-text") as HTMLParagraphElement;

function setStatus(msg: string, isError = false): void {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

async function fetchReply(userText: string): Promise<string> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userText },
      ],
    }),
  });
  const data = (await res.json()) as { reply?: string; error?: string };
  if (!res.ok) {
    throw new Error(data.error ?? res.statusText);
  }
  if (typeof data.reply !== "string") {
    throw new Error("Invalid response");
  }
  return data.reply;
}

function speakWithLipSync(text: string, emotionWhileSpeaking: Emotion = "speaking"): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!window.speechSynthesis) {
      reject(new Error("Web Speech API not available"));
      return;
    }

    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1;
    u.pitch = 1;
    u.onboundary = (ev) => {
      if (ev.name === "word" || ev.charIndex >= 0) {
        bumpMouth(1);
      }
    };

    u.onstart = () => {
      setEmotion(emotionWhileSpeaking);
      startLipSyncLoop();
      let nudge = 0;
      const id = window.setInterval(() => {
        nudge += 1;
        if (nudge % 2 === 0) bumpMouth(0.45);
      }, 85);
      (u as SpeechSynthesisUtterance & { _nudge?: number })._nudge = id;
    };

    u.onend = () => {
      const id = (u as SpeechSynthesisUtterance & { _nudge?: number })._nudge;
      if (id) window.clearInterval(id);
      stopLipSyncLoop();
      setEmotion("neutral");
      resolve();
    };

    u.onerror = (e) => {
      const nid = (u as SpeechSynthesisUtterance & { _nudge?: number })._nudge;
      if (nid) window.clearInterval(nid);
      stopLipSyncLoop();
      setEmotion("neutral");
      reject(e.error ? new Error(String(e.error)) : new Error("speech error"));
    };

    window.speechSynthesis.speak(u);
  });
}

/** Prefer server MP3 + Web Audio lip sync; fall back to Web Speech + boundary bumps. */
async function speakAloud(text: string): Promise<void> {
  try {
    const r = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (r.ok) {
      const ct = r.headers.get("content-type") ?? "";
      if (ct.includes("mpeg") || ct.includes("audio")) {
        const blob = await r.blob();
        if (blob.size > 0) {
          setEmotion("speaking");
          await playMp3WithLipSync(blob);
          return;
        }
      }
    }
  } catch {
    /* use browser TTS */
  }
  await speakWithLipSync(text, "speaking");
}

async function onSend(): Promise<void> {
  const text = input.value.trim();
  if (!text) {
    setStatus("Type a message first.", true);
    return;
  }

  sendBtn.disabled = true;
  speakDirectBtn.disabled = true;
  setStatus("Thinking…");
  setEmotion("thinking");
  replyEl.textContent = "";

  try {
    const reply = await fetchReply(text);
    replyEl.textContent = reply;
    setStatus("Speaking…");
    await speakAloud(reply);
    setStatus("Done.");
    setEmotion("happy", "happy");
    window.setTimeout(() => setEmotion("neutral"), 1200);
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Request failed";
    setStatus(msg, true);
    setEmotion("neutral");
  } finally {
    sendBtn.disabled = false;
    speakDirectBtn.disabled = false;
  }
}

async function onSpeakOnly(): Promise<void> {
  const text = input.value.trim();
  if (!text) {
    setStatus("Type text to speak.", true);
    return;
  }
  sendBtn.disabled = true;
  speakDirectBtn.disabled = true;
  setStatus("Speaking…");
  try {
    await speakAloud(text);
    setStatus("Done.");
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Speech failed";
    setStatus(msg, true);
  } finally {
    sendBtn.disabled = false;
    speakDirectBtn.disabled = false;
  }
}

sendBtn.addEventListener("click", () => void onSend());
speakDirectBtn.addEventListener("click", () => void onSpeakOnly());

input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    void onSend();
  }
});

void initPhotoMouthCanvas()
  .then(() => {
    setEmotion("neutral");
    setStatus("Ready.");
  })
  .catch((e) => {
    console.error(e);
    setEmotion("neutral");
    setStatus("Ready.");
  });
