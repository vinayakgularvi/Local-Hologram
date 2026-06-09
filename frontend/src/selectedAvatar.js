/** Persisted LiveTalking avatar_id used in WebRTC /offer and /session/avatar. */

export const SELECTED_AVATAR_STORAGE_KEY = "hologram_selected_avatar_id";

export function getSelectedAvatarId() {
  try {
    return String(localStorage.getItem(SELECTED_AVATAR_STORAGE_KEY) || "").trim();
  } catch {
    return "";
  }
}

/** Public MP4 saved as frontend/public/{avatar_id}.mp4 → served at /{avatar_id}.mp4 */
export function getSelectedAvatarVideoUrl() {
  const id = getSelectedAvatarId();
  return id ? `/${id}.mp4` : "";
}

export function setSelectedAvatarId(avatarId) {
  const id = String(avatarId || "").trim();
  if (!id) return;
  try {
    localStorage.setItem(SELECTED_AVATAR_STORAGE_KEY, id);
  } catch {
    /* ignore quota / private mode */
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("hologram-avatar-selected", {
        detail: { avatarId: id, videoUrl: `/${id}.mp4` },
      }),
    );
  }
}
