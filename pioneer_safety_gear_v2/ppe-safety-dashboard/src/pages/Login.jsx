import { useState } from "react";
import { Lock, Radar, ShieldAlert, User } from "lucide-react";

import { setToken } from "../lib/auth";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Gates the entire dashboard (see App.jsx) - nothing behind this
// screen renders until POST /admin/login succeeds. A single shared
// admin account (backend/config.py's ADMIN_USERNAME/ADMIN_PASSWORD -
// defaults to admin/aegis123, meant to be changed before any real
// deployment).
export default function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!username.trim() || !password) {
      setError("Enter both a username and password.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.detail || "Invalid username or password.");
        return;
      }

      setToken(data.token);
      onLogin(data.token);
    } catch {
      setError("Could not reach the backend - is it running?");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center gap-3 mb-8">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-teal-500 to-amber-500 flex items-center justify-center">
            <Radar size={26} className="text-zinc-950" strokeWidth={2.5} />
          </div>

          <div className="text-center">
            <h1 className="font-display font-bold text-white text-xl tracking-tight">
              Aegis SiteWatch
            </h1>
            <p className="text-sm text-zinc-500">Admin sign-in</p>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 space-y-4"
        >
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">
              Username
            </label>
            <div className="relative">
              <User
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
              />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                className="w-full bg-zinc-950 border border-zinc-800 rounded-xl pl-9 pr-3 py-2.5 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-teal-600"
                placeholder="admin"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">
              Password
            </label>
            <div className="relative">
              <Lock
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
              />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full bg-zinc-950 border border-zinc-800 rounded-xl pl-9 pr-3 py-2.5 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-teal-600"
                placeholder="••••••••"
              />
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-950/30 border border-red-900 rounded-xl px-3 py-2.5">
              <ShieldAlert size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-teal-600 hover:bg-teal-500 disabled:opacity-60 disabled:cursor-not-allowed text-white text-sm font-medium rounded-xl py-2.5 transition-colors"
          >
            {submitting ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="text-center text-xs text-zinc-600 mt-6">
          Site Vigilance AI &middot; Aegis Vigilance Platform v5.0
        </p>
      </div>
    </div>
  );
}
