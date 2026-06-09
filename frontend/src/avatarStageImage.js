/** Avatar stage portrait canvas (matches Live hologram). */

export const AVATAR_STAGE_WIDTH = 2490;
export const AVATAR_STAGE_HEIGHT = 3840;
export const AVATAR_STAGE_PAD_COLOR = "#e4e2e2";

function loadImageFromFile(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Unable to load image."));
    };
    img.src = url;
  });
}

/** Fit image inside 2490×3840 with centered padding (left/right for tall sources). */
export async function fitImageToAvatarStage(file, outName = "avatar_source_2490x3840.png") {
  if (!file) throw new Error("No image file.");
  const img = await loadImageFromFile(file);
  const canvas = document.createElement("canvas");
  canvas.width = AVATAR_STAGE_WIDTH;
  canvas.height = AVATAR_STAGE_HEIGHT;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas is not supported.");
  ctx.fillStyle = AVATAR_STAGE_PAD_COLOR;
  ctx.fillRect(0, 0, AVATAR_STAGE_WIDTH, AVATAR_STAGE_HEIGHT);
  const scale = Math.min(
    AVATAR_STAGE_WIDTH / img.naturalWidth,
    AVATAR_STAGE_HEIGHT / img.naturalHeight,
  );
  const drawW = img.naturalWidth * scale;
  const drawH = img.naturalHeight * scale;
  const dx = (AVATAR_STAGE_WIDTH - drawW) / 2;
  const dy = (AVATAR_STAGE_HEIGHT - drawH) / 2;
  ctx.drawImage(img, dx, dy, drawW, drawH);
  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("Image export failed."))),
      "image/png",
    );
  });
  return new File([blob], outName, { type: "image/png" });
}
