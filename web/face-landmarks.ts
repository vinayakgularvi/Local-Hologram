/**
 * MediaPipe Face Landmarker (browser): auto mouth + eye regions from the photo.
 * Falls back to CSS vars in photo-mouth-canvas if this fails to load or detect a face.
 */
import { FaceLandmarker, FilesetResolver } from "@mediapipe/tasks-vision";

const WASM_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/wasm";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

/** Outer lip contour — Face Mesh topology (indices < 478) */
const LIP_INDICES = [
  61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185, 95, 88, 178, 87, 14,
  317, 402, 318, 324, 308,
];

const LEFT_EYE_INDICES = [
  33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
];
const RIGHT_EYE_INDICES = [
  362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398,
];

export type AiFaceParams = {
  mouth: { ncx: number; ncy: number; nrx: number; nry: number };
  leftEye: { ncx: number; ncy: number; nr: number };
  rightEye: { ncx: number; ncy: number; nr: number };
};

let landmarker: FaceLandmarker | null = null;
let cached: AiFaceParams | null = null;

function bboxNorm(
  lm: { x: number; y: number }[],
  indices: number[],
): { minX: number; minY: number; maxX: number; maxY: number } {
  let minX = 1;
  let minY = 1;
  let maxX = 0;
  let maxY = 0;
  for (const i of indices) {
    const p = lm[i];
    if (!p) continue;
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  return { minX, minY, maxX, maxY };
}

export async function initFaceLandmarker(): Promise<boolean> {
  try {
    const filesetResolver = await FilesetResolver.forVisionTasks(WASM_CDN);
    for (const delegate of ["GPU", "CPU"] as const) {
      try {
        landmarker = await FaceLandmarker.createFromOptions(filesetResolver, {
          baseOptions: {
            modelAssetPath: MODEL_URL,
            delegate,
          },
          runningMode: "IMAGE",
          numFaces: 1,
          minFaceDetectionConfidence: 0.4,
          minFacePresenceConfidence: 0.4,
        });
        return true;
      } catch {
        landmarker = null;
      }
    }
  } catch (e) {
    console.warn("FaceLandmarker unavailable", e);
  }
  return false;
}

export function detectFaceLayout(img: HTMLImageElement): boolean {
  cached = null;
  if (!landmarker || !img.naturalWidth) return false;
  try {
    const result = landmarker.detect(img);
    const face = result.faceLandmarks?.[0];
    if (!face) return false;

    const lip = bboxNorm(face, LIP_INDICES);
    const ncx = (lip.minX + lip.maxX) / 2;
    const ncy = (lip.minY + lip.maxY) / 2;
    const nrx = (lip.maxX - lip.minX) / 2 + 0.008;
    const nry = (lip.maxY - lip.minY) / 2 + 0.004;

    const le = bboxNorm(face, LEFT_EYE_INDICES);
    const re = bboxNorm(face, RIGHT_EYE_INDICES);

    const lcx = (le.minX + le.maxX) / 2;
    const lcy = (le.minY + le.maxY) / 2;
    const lr = Math.max(le.maxX - le.minX, le.maxY - le.minY) / 2 + 0.005;

    const rcx = (re.minX + re.maxX) / 2;
    const rcy = (re.minY + re.maxY) / 2;
    const rr = Math.max(re.maxX - re.minX, re.maxY - re.minY) / 2 + 0.005;

    cached = {
      mouth: { ncx, ncy, nrx, nry },
      leftEye: { ncx: lcx, ncy: lcy, nr: lr },
      rightEye: { ncx: rcx, ncy: rcy, nr: rr },
    };
    return true;
  } catch (e) {
    console.warn("Face detection failed", e);
    return false;
  }
}

export function getAiFaceParams(): AiFaceParams | null {
  return cached;
}
