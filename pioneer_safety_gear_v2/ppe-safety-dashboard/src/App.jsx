import { useEffect, useState } from "react";

import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import { authFetch, clearToken, getToken } from "./lib/auth";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Gates the whole dashboard behind admin login (see Login.jsx and
// backend/main.py's require_admin) - nothing in Dashboard renders
// until a token is present AND verified against the backend, so a
// stale token from a previous session (backend restarted since,
// session expired) doesn't show a dashboard whose write actions
// would then just fail with 401s.
function App() {
  // null while the initial GET /admin/verify check is in flight, so
  // the login screen doesn't flash for someone who's already logged
  // in and just refreshed the page.
  const [authenticated, setAuthenticated] = useState(null);

  useEffect(() => {
    let mounted = true;

    const verify = async () => {
      const token = getToken();

      if (!token) {
        if (mounted) setAuthenticated(false);
        return;
      }

      try {
        const res = await authFetch(`${API_BASE}/admin/verify`);

        if (!mounted) return;

        setAuthenticated(res.ok);

        if (!res.ok) clearToken();
      } catch {
        // Backend unreachable right now - don't force a re-login just
        // because of a transient network hiccup; Dashboard's own
        // health polling already surfaces "Backend Offline".
        if (mounted) setAuthenticated(true);
      }
    };

    verify();

    return () => {
      mounted = false;
    };
  }, []);

  const handleLogout = async () => {
    try {
      await authFetch(`${API_BASE}/admin/logout`, { method: "POST" });
    } catch {
      // Best-effort - clear the local session either way.
    }

    clearToken();
    setAuthenticated(false);
  };

  if (authenticated === null) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <p className="text-sm text-zinc-500">Loading...</p>
      </div>
    );
  }

  if (!authenticated) {
    return <Login onLogin={() => setAuthenticated(true)} />;
  }

  return <Dashboard onLogout={handleLogout} />;
}

export default App;
