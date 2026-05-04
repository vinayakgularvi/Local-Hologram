/** Must match backend `HOLUMINEX_PREFIX` (default `/holuminex`). */
export const HOLUMINEX_PREFIX = "/holuminex";

export function hx(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${HOLUMINEX_PREFIX}${p}`;
}
