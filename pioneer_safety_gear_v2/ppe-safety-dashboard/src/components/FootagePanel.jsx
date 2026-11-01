import { useEffect, useState } from "react";
import {
  Video,
  Flame,
  Cloud,
  AlertTriangle,
  Trash2,
  RefreshCw,
  HardDrive,
  MapPin,
  Clock,
} from "lucide-react";

import { authFetch } from "../lib/auth";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const PAGE_SIZE = 24;

const FLAG_DEFS = [
  { key: "has_violation", label: "Violation", icon: AlertTriangle, color: "yellow" },
  { key: "has_fire", label: "Fire", icon: Flame, color: "red" },
  { key: "has_smoke", label: "Smoke", icon: Cloud, color: "orange" },
];

const FLAG_STYLES = {
  yellow: "bg-yellow-950/30 border-yellow-900 text-yellow-400",
  red: "bg-red-950/30 border-red-900 text-red-400",
  orange: "bg-orange-950/30 border-orange-900 text-orange-400",
};

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return "--";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatTimeRange(startTime, endTime) {
  const start = new Date(startTime * 1000);
  const end = new Date(endTime * 1000);

  const dateLabel = start.toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
  });

  const timeFmt = { hour: "2-digit", minute: "2-digit", second: "2-digit" };

  return `${dateLabel} · ${start.toLocaleTimeString(undefined, timeFmt)} – ${end.toLocaleTimeString(undefined, timeFmt)}`;
}

// Continuous, CCTV-style recorded coverage - browse/search/play back
// every clip any enabled camera has recorded (backend/recording.py).
// Self-contained data fetching, like the rest of this dashboard's
// panels - just needs the camera roster (for the filter dropdown and
// showing a friendly location/zone label alongside each clip).
export default function FootagePanel({ cameras }) {
  const [segments, setSegments] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [hasMore, setHasMore] = useState(false);

  const [cameraFilter, setCameraFilter] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const [flagFilters, setFlagFilters] = useState({});

  const [expandedId, setExpandedId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState(null);

  const buildParams = (offset) => {
    const params = new URLSearchParams();

    if (cameraFilter) params.set("camera_id", cameraFilter);
    if (dateFilter) params.set("date", dateFilter);

    for (const [key, value] of Object.entries(flagFilters)) {
      if (value) params.set(key, "true");
    }

    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(offset));

    return params;
  };

  const load = async (offset = 0, append = false) => {
    if (!append) setLoading(true);
    setLoadError(null);

    try {
      const res = await fetch(`${API_BASE}/footage?${buildParams(offset)}`);
      if (!res.ok) throw new Error("Failed to load footage");

      const data = await res.json();
      const page = data.segments ?? [];

      setSegments((prev) => (append ? [...prev, ...page] : page));
      setHasMore(offset + page.length < data.total);
    } catch {
      setLoadError("Could not load footage - is the backend running?");
    } finally {
      setLoading(false);
    }
  };

  const loadStats = async () => {
    try {
      const res = await fetch(`${API_BASE}/footage/stats`);
      if (!res.ok) return;
      setStats(await res.json());
    } catch {
      // Leave last-known stats in place on a transient hiccup.
    }
  };

  useEffect(() => {
    load(0, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraFilter, dateFilter, flagFilters]);

  useEffect(() => {
    loadStats();
  }, []);

  // Auto-refresh - the in-progress card's own elapsed-time label goes
  // stale between refreshes, and a page left open should still pick
  // up a segment finishing (in_progress flipping off) or a new one
  // starting, without waiting on the next filter change.
  useEffect(() => {
    const interval = setInterval(() => {
      load(0, false);
      loadStats();
    }, 30000);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraFilter, dateFilter, flagFilters]);

  const toggleFlag = (key) => {
    setFlagFilters((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const clearFilters = () => {
    setCameraFilter("");
    setDateFilter("");
    setFlagFilters({});
  };

  const deleteSegment = async (id) => {
    setDeletingId(id);
    setDeleteError(null);

    try {
      const res = await authFetch(`${API_BASE}/footage/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });

      const data = await res.json();

      if (data.error) {
        setDeleteError(data.error);
        return;
      }

      setSegments((prev) => prev.filter((s) => s.id !== id));
      loadStats();
    } catch {
      setDeleteError("Delete failed - is the backend running?");
    } finally {
      setDeletingId(null);
    }
  };

  const anyFilterActive = cameraFilter || dateFilter || Object.values(flagFilters).some(Boolean);

  return (
    <section>
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Video size={19} className="text-teal-400" />
          <div>
            <h2 className="text-lg font-display font-semibold text-white tracking-tight">
              Footage
            </h2>
            <p className="text-sm text-zinc-500">
              Continuous recorded coverage from every camera - browse, search, play back
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {stats && (
            <span className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-zinc-700 text-zinc-400 bg-zinc-800/50">
              <HardDrive size={12} />
              {formatBytes(stats.total_bytes)} stored
            </span>
          )}

          <button
            onClick={() => {
              load(0, false);
              loadStats();
            }}
            className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-zinc-700 text-zinc-400 hover:text-teal-400 hover:border-teal-900 transition-colors"
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select
          value={cameraFilter}
          onChange={(e) => setCameraFilter(e.target.value)}
          className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-teal-600"
        >
          <option value="">All cameras</option>
          {cameras.map((c) => (
            <option key={c.camera_id} value={c.camera_id}>
              {c.camera_id} &middot; {c.location}
            </option>
          ))}
        </select>

        <input
          type="date"
          value={dateFilter}
          onChange={(e) => setDateFilter(e.target.value)}
          className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-teal-600"
        />

        {FLAG_DEFS.map(({ key, label, icon: Icon, color }) => (
          <button
            key={key}
            onClick={() => toggleFlag(key)}
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
              flagFilters[key]
                ? FLAG_STYLES[color]
                : "border-zinc-800 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            <Icon size={12} />
            {label}
          </button>
        ))}

        {anyFilterActive && (
          <button
            onClick={clearFilters}
            className="text-xs text-zinc-500 hover:text-white px-2"
          >
            Clear filters
          </button>
        )}
      </div>

      {deleteError && (
        <p className="text-xs text-red-400 mb-3">{deleteError}</p>
      )}

      {loading ? (
        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-8 text-center text-sm text-zinc-500">
          Loading footage...
        </div>
      ) : loadError ? (
        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-8 text-center text-sm text-red-400">
          {loadError}
        </div>
      ) : segments.length === 0 ? (
        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-8 text-center">
          <Video className="mx-auto text-zinc-600 mb-2" size={28} />
          <p className="text-sm text-zinc-400">No footage matches these filters yet.</p>
          <p className="text-xs text-zinc-600 mt-1">
            A clip appears as soon as its camera starts recording - if
            nothing shows here, check that at least one camera is
            enabled and online.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {segments.map((seg) => {
              // A segment still being written can't be safely played
              // (its container isn't finalized until the recorder
              // closes it) or deleted (the file is open) - it still
              // shows up immediately though, with a live badge, so
              // "nothing in the dashboard yet" never happens just
              // because a segment hasn't rotated out.
              const isExpanded = !seg.in_progress && expandedId === seg.id;

              return (
                <div
                  key={seg.id}
                  className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden"
                >
                  <div className="relative aspect-video bg-zinc-950">
                    {seg.in_progress && (
                      <span className="absolute top-2 left-2 z-10 flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md bg-red-950/70 border border-red-800 text-red-400">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                        REC
                      </span>
                    )}

                    {isExpanded ? (
                      // eslint-disable-next-line jsx-a11y/media-has-caption
                      <video
                        src={`${API_BASE}/footage_media/${seg.file_path}`}
                        controls
                        autoPlay
                        className="w-full h-full object-contain bg-black"
                      />
                    ) : seg.thumbnail_path ? (
                      <button
                        onClick={() => !seg.in_progress && setExpandedId(seg.id)}
                        className={`w-full h-full ${seg.in_progress ? "cursor-default" : ""}`}
                        title={seg.in_progress ? "Recording - playable once this segment finishes" : "Play clip"}
                      >
                        <img
                          src={`${API_BASE}/footage_media/${seg.thumbnail_path}`}
                          alt={`Thumbnail for ${seg.camera_id} clip`}
                          className="w-full h-full object-cover"
                          loading="lazy"
                        />
                      </button>
                    ) : (
                      <button
                        onClick={() => !seg.in_progress && setExpandedId(seg.id)}
                        className={`w-full h-full flex items-center justify-center text-zinc-700 ${seg.in_progress ? "cursor-default" : ""}`}
                        title={seg.in_progress ? "Recording - playable once this segment finishes" : "Play clip"}
                      >
                        <Video size={28} />
                      </button>
                    )}
                  </div>

                  <div className="p-4">
                    <p className="text-sm font-semibold text-white flex items-center gap-1.5">
                      <MapPin size={13} className="text-zinc-500" />
                      {seg.camera_id} &middot; {seg.location}
                      <span className="text-zinc-500 font-normal">({seg.zone})</span>
                    </p>

                    <p className="text-xs text-zinc-500 mt-1 flex items-center gap-1.5">
                      <Clock size={12} />
                      {seg.in_progress ? (
                        <>Recording &middot; {formatDuration(seg.duration_seconds)} so far</>
                      ) : (
                        <>
                          {formatTimeRange(seg.start_time, seg.end_time)} &middot;{" "}
                          {formatDuration(seg.duration_seconds)}
                        </>
                      )}
                    </p>

                    <div className="flex items-center justify-between mt-3">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {FLAG_DEFS.filter(({ key }) => seg[key]).map(
                          ({ key, label, icon: Icon, color }) => (
                            <span
                              key={key}
                              className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-md border ${FLAG_STYLES[color]}`}
                            >
                              <Icon size={11} />
                              {label}
                            </span>
                          )
                        )}
                      </div>

                      {!seg.in_progress && (
                        <button
                          onClick={() => deleteSegment(seg.id)}
                          disabled={deletingId === seg.id}
                          className="flex items-center gap-1 text-xs text-zinc-500 hover:text-red-400 disabled:opacity-50"
                          title="Delete this clip"
                        >
                          <Trash2 size={12} />
                          {deletingId === seg.id ? "..." : "Delete"}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {hasMore && (
            <div className="flex justify-center mt-5">
              <button
                onClick={() => load(segments.length, true)}
                className="text-xs px-4 py-2 rounded-lg border border-zinc-800 text-zinc-400 hover:text-white hover:border-teal-900 transition-colors"
              >
                Load more
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
