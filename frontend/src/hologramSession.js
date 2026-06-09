/** LiveTalking WebRTC session id (from /offer). RAG voice-turn uses the same id server-side. */

export const HOLOGRAM_SESSION_STORAGE_KEY = "hologram_session_id";

export function getHologramSessionId() {
  try {
    const stored = String(localStorage.getItem(HOLOGRAM_SESSION_STORAGE_KEY) || "").trim();
    if (stored && stored !== "0") return stored;
  } catch {
    /* ignore quota / private mode */
  }
  return "";
}

export function rememberHologramSessionId(id) {
  const s = String(id || "").trim();
  if (!s || s === "0") return getHologramSessionId();
  try {
    localStorage.setItem(HOLOGRAM_SESSION_STORAGE_KEY, s);
  } catch {
    /* ignore */
  }
  return s;
}

/** Always use LiveTalking sessionid from /offer (UUID). */
export function resolveHologramSessionId(offerSessionId) {
  return rememberHologramSessionId(offerSessionId);
}

/** Optional sessionid on reconnect offer; LiveTalking may still assign a new UUID. */
export function hologramSessionIdPayload() {
  const sid = getHologramSessionId();
  if (!sid || sid === "0") return null;
  return { sessionid: sid };
}
