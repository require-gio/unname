/**
 * Tauri serves static files; using a server adapter would break the app (spec §9.6).
 * Without SSR there is no rendering outside the device – matching the promise that
 * nothing leaves the device.
 */
export const ssr = false;
export const prerender = false;
