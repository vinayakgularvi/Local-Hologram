import { renderPhotoMouth } from "./photo-mouth-canvas";

export type Emotion = "neutral" | "thinking" | "speaking" | "happy";

const host = () => document.getElementById("avatar-root");
const emotionEl = () => document.getElementById("emotion-label");

export function setEmotion(e: Emotion, label?: string): void {
  const el = emotionEl();
  if (el) {
    el.textContent = label ?? e;
  }
  const root = host();
  if (!root) return;
  root.classList.toggle("thinking", e === "thinking");
  root.classList.toggle("speaking", e === "speaking");
}

function applyMouthCss(t: number): void {
  const v = Math.max(0, Math.min(1, t));
  host()?.style.setProperty("--mouth-open", String(v));
  renderPhotoMouth(v);
}

/** Direct mouth control (Web Audio RMS lip sync). */
export function setMouthOpen(t: number): void {
  applyMouthCss(t);
}

let mouthOpen = 0;
let raf = 0;

function tick(): void {
  mouthOpen *= 0.88;
  if (mouthOpen < 0.02) mouthOpen = 0;
  applyMouthCss(mouthOpen);
  raf = requestAnimationFrame(tick);
}

export function startLipSyncLoop(): void {
  if (raf) return;
  raf = requestAnimationFrame(tick);
}

export function stopLipSyncLoop(): void {
  if (raf) cancelAnimationFrame(raf);
  raf = 0;
  mouthOpen = 0;
  applyMouthCss(0);
}

/** Impulse when a word boundary fires (Web Speech API fallback). */
export function bumpMouth(strength = 1): void {
  mouthOpen = Math.min(1, mouthOpen + 0.48 * strength);
}
