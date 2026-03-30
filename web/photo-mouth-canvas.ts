/**
 * Photo on canvas: optional MediaPipe mouth/eye layout + audio lip warp + idle blinks.
 */
import { detectFaceLayout, getAiFaceParams, initFaceLandmarker } from "./face-landmarks";

let canvas: HTMLCanvasElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;
let frameEl: HTMLElement | null = null;
let cachedImg: HTMLImageElement | null = null;
let loaded = false;
let lastOpen = 0;
let ro: ResizeObserver | null = null;

/** Idle blink 0 = open eyes, 1 = closed */
let blinkAmt = 0;
let blinkRaf = 0;
let blinkScheduled = false;

function containRect(nw: number, nh: number, fw: number, fh: number): { dx: number; dy: number; dw: number; dh: number } {
  const ir = nw / nh;
  const fr = fw / fh;
  let dw: number;
  let dh: number;
  let dx: number;
  let dy: number;
  if (ir > fr) {
    dh = fh;
    dw = dh * ir;
    dx = (fw - dw) / 2;
    dy = 0;
  } else {
    dw = fw;
    dh = dw / ir;
    dx = 0;
    dy = (fh - dh) / 2;
  }
  return { dx, dy, dw, dh };
}

function readStretch(): number {
  const root = document.getElementById("avatar-root");
  if (!root) return 6.5;
  const v = parseFloat(getComputedStyle(root).getPropertyValue("--mouth-stretch").trim());
  return Number.isFinite(v) ? v : 6.5;
}

/** Manual fallback when AI face not available — fractions of drawn image rect */
function readMouthParamsManual(): { cx: number; cy: number; rx: number; ry: number } {
  const root = document.getElementById("avatar-root");
  const d = { cx: 0.5, cy: 0.12, rx: 0.032, ry: 0.014 };
  if (!root) return d;
  const s = getComputedStyle(root);
  const n = (name: string, fallback: number): number => {
    const v = parseFloat(s.getPropertyValue(name).trim());
    return Number.isFinite(v) ? v : fallback;
  };
  return {
    cx: n("--mouth-cx", d.cx),
    cy: n("--mouth-cy", d.cy),
    rx: n("--mouth-rx", d.rx),
    ry: n("--mouth-ry", d.ry),
  };
}

function decayBlink(): void {
  if (blinkAmt < 0.02) {
    blinkAmt = 0;
    blinkRaf = 0;
    return;
  }
  blinkAmt *= 0.86;
  paint();
  blinkRaf = requestAnimationFrame(decayBlink);
}

function triggerBlink(): void {
  blinkAmt = 1;
  if (!blinkRaf) blinkRaf = requestAnimationFrame(decayBlink);
}

function scheduleIdleBlinks(): void {
  if (blinkScheduled) return;
  blinkScheduled = true;
  const next = (): void => {
    window.setTimeout(() => {
      if (cachedImg && loaded) triggerBlink();
      next();
    }, 2000 + Math.random() * 4500);
  };
  next();
}

function paint(): void {
  if (!canvas || !ctx || !loaded || !cachedImg?.naturalWidth) return;
  const fw = frameEl?.clientWidth ?? 0;
  const fh = frameEl?.clientHeight ?? 0;
  if (fw < 2 || fh < 2) return;

  const nw = cachedImg.naturalWidth;
  const nh = cachedImg.naturalHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2.5);

  canvas.width = Math.round(fw * dpr);
  canvas.height = Math.round(fh * dpr);
  canvas.style.width = `${fw}px`;
  canvas.style.height = `${fh}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const { dx, dy, dw, dh } = containRect(nw, nh, fw, fh);

  ctx.fillStyle = "#1a1d26";
  ctx.fillRect(0, 0, fw, fh);
  ctx.drawImage(cachedImg, 0, 0, nw, nh, dx, dy, dw, dh);

  const v = lastOpen;
  const stretch = readStretch();

  const ai = getAiFaceParams();
  let mx: number;
  let my: number;
  let mrx: number;
  let mry: number;

  if (ai) {
    const m = ai.mouth;
    mx = dx + m.ncx * dw;
    my = dy + m.ncy * dh;
    mrx = m.nrx * dw;
    mry = m.nry * dh;
  } else {
    const m = readMouthParamsManual();
    mx = dx + m.cx * dw;
    my = dy + m.cy * dh;
    mrx = m.rx * dw;
    mry = m.ry * dh;
  }

  if (v >= 0.003) {
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(mx, my, Math.max(1, mrx), Math.max(1, mry), 0, 0, Math.PI * 2);
    ctx.clip();
    ctx.translate(mx, my);
    ctx.scale(1, 1 + v * stretch);
    ctx.translate(-mx, -my);
    ctx.drawImage(cachedImg, 0, 0, nw, nh, dx, dy, dw, dh);
    ctx.restore();
  }

  if (ai && blinkAmt > 0.02) {
    const alpha = Math.min(0.65, blinkAmt * 0.55);
    ctx.fillStyle = `rgba(32, 22, 20, ${alpha})`;
    for (const eye of [ai.leftEye, ai.rightEye]) {
      const ex = dx + eye.ncx * dw;
      const ey = dy + eye.ncy * dh;
      const er = eye.nr * dw;
      ctx.beginPath();
      ctx.ellipse(ex, ey, er * 1.05, er * 0.72, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

export function renderPhotoMouth(open: number): void {
  if (!loaded) return;
  lastOpen = Math.max(0, Math.min(1, open));
  paint();
}

export function initPhotoMouthCanvas(): Promise<void> {
  return new Promise((resolve, reject) => {
    frameEl = document.querySelector(".photo-avatar__frame");
    canvas = document.getElementById("photo-avatar-canvas") as HTMLCanvasElement | null;
    const imgEl = document.getElementById("photo-avatar-img") as HTMLImageElement | null;
    if (!frameEl || !canvas || !imgEl) {
      reject(new Error("photo frame, canvas, or img missing"));
      return;
    }
    ctx = canvas.getContext("2d");
    if (!ctx) {
      reject(new Error("2d context unavailable"));
      return;
    }

    const afterLoad = async (): Promise<void> => {
      cachedImg = imgEl;
      loaded = true;
      lastOpen = 0;

      const lmReady = await initFaceLandmarker();
      if (lmReady) {
        const ok = detectFaceLayout(imgEl);
        if (!ok) {
          console.warn("Local Hologram: no face detected — using manual mouth CSS vars");
        } else {
          scheduleIdleBlinks();
        }
      } else {
        console.warn("Local Hologram: MediaPipe Face Landmarker failed — using manual mouth CSS vars");
      }

      paint();
      if (ro) ro.disconnect();
      ro = new ResizeObserver(() => {
        paint();
      });
      ro.observe(frameEl!);
    };

    if (imgEl.complete) {
      if (imgEl.naturalWidth > 0) {
        void afterLoad().then(() => resolve()).catch(reject);
        return;
      }
      reject(new Error("photo failed to load"));
      return;
    }

    imgEl.onload = () => void afterLoad().then(() => resolve()).catch(reject);
    imgEl.onerror = () => reject(new Error("failed to load avatar photo"));
  });
}
