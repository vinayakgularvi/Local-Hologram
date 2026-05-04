/** Must match backend `HOLUMINEX_PREFIX` (default `/holuminex`). */
export const HOLUMINEX_PREFIX = "/holuminex";

export function hx(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${HOLUMINEX_PREFIX}${p}`;
}

/**
 * `VITE_API_BASE` must be an API origin only (e.g. `http://127.0.0.1:6064`), not a path under
 * {@link HOLUMINEX_PREFIX}. If it already ends with `/holuminex`, strip it so callers that add
 * `hx("/api/...")` do not produce `/holuminex/holuminex/...` (Vite proxy misses → POST 405).
 */
export function resolveApiOrigin(baseRaw) {
  let b = String(baseRaw ?? "").trim();
  if (!b) return "";
  b = b.replace(/\/+$/, "");
  const hxp = HOLUMINEX_PREFIX.startsWith("/") ? HOLUMINEX_PREFIX : `/${HOLUMINEX_PREFIX}`;
  while (b.endsWith(hxp)) {
    b = b.slice(0, -hxp.length).replace(/\/+$/, "");
  }
  return b;
}
