// Admin session handling - a single shared admin account
// (backend/config.py's ADMIN_USERNAME/ADMIN_PASSWORD) gates the
// whole dashboard: App.jsx renders nothing but <Login /> until a
// token from POST /admin/login is present AND verified against the
// backend (GET /admin/verify), so a stale/expired token left in
// localStorage from a previous session doesn't silently show a
// dashboard the backend would actually reject writes from.
//
// The token itself is just an opaque bearer string (backend/main.py
// issues and tracks it in-memory) - stored in localStorage so a page
// refresh doesn't force a re-login, cleared on logout or a 401 from
// the backend (session expired / backend restarted).

const TOKEN_KEY = "aegis_admin_token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // private-browsing / storage blocked - just means "not logged in"
  }
}

export function setToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Best-effort - login still works for this page load even if it
    // can't persist across a refresh.
  }
}

export function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing to do if storage is unavailable.
  }
}

// Wraps fetch, attaching `Authorization: Bearer <token>` when one is
// stored - use this (instead of the bare fetch) for every POST/PUT/
// DELETE call the dashboard makes, since those all hit
// require_admin-protected endpoints on the backend. Read-only GETs
// (health, cameras, alerts, footage, hotspots) don't need it - the
// backend leaves those open - but sending it anyway is harmless.
export async function authFetch(url, options = {}) {
  const token = getToken();

  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  return fetch(url, { ...options, headers });
}
