/** Storage can be blocked by browser/company settings even on localhost. */
export function sessionToken(): string {
  const fromUrl =
    new URLSearchParams(location.hash.slice(1)).get("token") || "";
  let token = fromUrl;
  let persisted = false;
  try {
    if (fromUrl) sessionStorage.setItem("pawapo-token", fromUrl);
    const saved = sessionStorage.getItem("pawapo-token") || "";
    token = fromUrl || saved;
    persisted = !!token && saved === token;
  } catch {
    // Keep using the launch URL without requiring cookies or browser storage.
  }
  if (persisted) {
    try {
      history.replaceState(null, "", location.pathname + location.search);
    } catch {
      // Navigation restrictions must not prevent React from mounting.
    }
  }
  // When storage is blocked, retain the fragment for reload. It is never sent
  // as part of HTTP requests or included in startup diagnostics.
  return token;
}
