/** LiveTalking avatar_id — server-backed so all devices share the same selection. */

export const SELECTED_AVATAR_STORAGE_KEY = "hologram_selected_avatar_id";

function apiUrl(path) {
  const fromEnv = import.meta.env.VITE_API_BASE;
  const base = fromEnv ? String(fromEnv).replace(/\/$/, "") : "";
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}

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

function applySelectedAvatarIdLocal(avatarId) {
  const id = String(avatarId || "").trim();
  if (!id) return "";
  const prev = getSelectedAvatarId();
  try {
    localStorage.setItem(SELECTED_AVATAR_STORAGE_KEY, id);
  } catch {
    /* ignore quota / private mode */
  }
  if (typeof window !== "undefined" && id !== prev) {
    window.dispatchEvent(
      new CustomEvent("hologram-avatar-selected", {
        detail: { avatarId: id, videoUrl: `/${id}.mp4` },
      }),
    );
  }
  return id;
}

/** Pull active avatar from server (other device may have changed it). */
export async function syncSelectedAvatarFromServer() {
  try {
    const res = await fetch(apiUrl("/api/avatar/selected"), {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return getSelectedAvatarId();
    const data = await res.json();
    const id = String(data.avatar_id || "").trim();
    if (id) return applySelectedAvatarIdLocal(id);
    return getSelectedAvatarId();
  } catch {
    return getSelectedAvatarId();
  }
}

/** Set active avatar on server + this browser. */
export async function setSelectedAvatarId(avatarId) {
  const id = String(avatarId || "").trim();
  if (!id) return;
  try {
    const res = await fetch(apiUrl("/api/avatar/selected"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ avatar_id: id }),
    });
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      const saved = String(data.avatar_id || id).trim() || id;
      applySelectedAvatarIdLocal(saved);
      return;
    }
  } catch {
    /* fall through to local-only */
  }
  applySelectedAvatarIdLocal(id);
}
