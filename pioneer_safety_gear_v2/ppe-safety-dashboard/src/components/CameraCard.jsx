import { useState } from "react";
import {
  Cloud,
  Flame,
  MapPin,
  Activity,
  Wifi,
  WifiOff,
  Users,
} from "lucide-react";

const ITEM_LABELS = {
  helmet: "Helmet",
  vest: "Vest",
  gloves: "Gloves",
  boots: "Boots",
  goggles: "Goggles",
};

// Configurable via ppe-safety-dashboard/.env (VITE_API_BASE) so the
// dashboard can point at a backend on another machine on the network -
// falls back to localhost for local single-machine demos.
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

/*
 * snapshot comes straight from the backend's per-camera payload
 * (GET /cameras + GET/WS /detections):
 *
 * {
 *   camera_id, location, zone, enabled, online, fps,
 *   workers: [{ worker_id, items: {helmet, vest, gloves, boots}, compliant, violations }],
 *   compliance_percent, violation_count,
 *   detections: [...], watchedItems: ["helmet", ...]
 * }
 */
const HAZARD_LABELS = {
  fire: "Fire",
  smoke: "Smoke",
};

export default function CameraCard({ snapshot }) {
  const [streamError, setStreamError] = useState(false);
  const [loading, setLoading] = useState(true);

  const enabled = snapshot?.enabled !== false;
  const cameraOnline = enabled && snapshot?.online && !streamError;
  const watchedItems = snapshot?.watchedItems ?? [];
  const watchedHazards = snapshot?.watchedHazards ?? [];

  const detections = snapshot?.detections ?? [];

  const fireDetection = detections.find(
    (d) => d.violation && d.class_name === "fire"
  );
  const smokeDetection = detections.find(
    (d) => d.violation && d.class_name === "smoke"
  );

  const isFire = Boolean(fireDetection);
  const isSmoke = Boolean(smokeDetection);

  const workerCount = snapshot?.worker_count ?? 0;
  const compliancePercent = snapshot?.compliance_percent;
  const violationCount = snapshot?.violation_count ?? 0;

  return (
    <div
      className={`bg-zinc-900 border rounded-2xl overflow-hidden transition-all duration-300 ${
        isFire
          ? "border-red-500/60 shadow-lg shadow-red-500/10"
          : isSmoke
            ? "border-orange-500/60 shadow-lg shadow-orange-500/10"
            : "border-zinc-800"
      }`}
    >
      {/* CAMERA FEED */}

      <div className="h-52 bg-black relative overflow-hidden">
        {enabled && !streamError ? (
          <>
            <img
              src={`${API_BASE}/video_feed/${snapshot.camera_id}`}
              alt={`${snapshot.camera_id} live AI camera feed`}
              className="w-full h-full object-cover"
              onLoad={() => setLoading(false)}
              onError={() => {
                setLoading(false);
                setStreamError(true);
              }}
            />

            {loading && (
              <div className="absolute inset-0 bg-zinc-950 flex items-center justify-center">
                <div className="text-center">
                  <div className="w-8 h-8 border-2 border-zinc-600 border-t-teal-500 rounded-full animate-spin mx-auto mb-3" />
                  <p className="text-sm text-zinc-400">
                    Connecting to camera...
                  </p>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="h-full flex items-center justify-center px-4">
            <div className="text-center">
              <WifiOff size={32} className="text-red-400 mx-auto mb-3" />
              <p className="text-zinc-300 text-sm font-medium">
                {enabled ? "Camera Offline" : "Not Configured"}
              </p>
              <p className="text-zinc-500 text-xs mt-1 break-words">
                {enabled
                  ? (snapshot?.lastError ?? "Unable to receive video stream - retrying...")
                  : "Enable this camera in cameras.json"}
              </p>
            </div>
          </div>
        )}

        <div className="absolute top-3 right-3">
          <div
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg backdrop-blur-sm ${
              cameraOnline ? "bg-zinc-950/90" : "bg-red-950/90"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                cameraOnline ? "bg-green-500 animate-pulse" : "bg-red-500"
              }`}
            />
            <span
              className={`text-xs font-semibold ${
                cameraOnline ? "text-green-400" : "text-red-400"
              }`}
            >
              {cameraOnline ? "LIVE" : "OFFLINE"}
            </span>
          </div>
        </div>

        {(isFire || isSmoke) && (
          <div
            className={`absolute top-3 left-3 px-3 py-1.5 rounded-lg text-xs font-bold text-white shadow-lg ${
              isFire ? "bg-red-600" : "bg-orange-600"
            }`}
          >
            {isFire ? "🔥 FIRE DETECTED" : "💨 SMOKE DETECTED"}
          </div>
        )}

        {(fireDetection || smokeDetection) && (
          <div className="absolute bottom-3 right-3 bg-zinc-950/90 backdrop-blur-sm px-3 py-2 rounded-lg text-xs">
            <span className="text-zinc-400">Confidence </span>
            <span className="text-white font-semibold">
              {Math.round(
                (fireDetection ?? smokeDetection).confidence * 100
              )}
              %
            </span>
          </div>
        )}

        {cameraOnline && (
          <div className="absolute bottom-3 left-3 flex items-center gap-2 bg-zinc-950/90 backdrop-blur-sm px-3 py-2 rounded-lg">
            <Activity size={13} className="text-teal-400" />
            <span className="text-xs text-teal-300">AI DETECTION</span>
          </div>
        )}
      </div>

      {/* CAMERA INFO */}

      <div className="p-4">
        <div className="flex justify-between gap-4">
          <div>
            <h3 className="font-semibold text-white">{snapshot.camera_id}</h3>
            <p className="text-xs text-zinc-500 flex items-center gap-1 mt-1">
              <MapPin size={12} />
              {snapshot.location}
            </p>
          </div>

          <div
            className={`flex items-center gap-2 text-xs pt-0.5 ${
              cameraOnline ? "text-green-400" : "text-red-400"
            }`}
          >
            {cameraOnline ? <Wifi size={13} /> : <WifiOff size={13} />}
            {cameraOnline ? "ONLINE" : "OFFLINE"}
          </div>
        </div>

        <div className="mt-2 flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] uppercase tracking-wide text-zinc-600 font-semibold">
            Watching:
          </span>
          {watchedItems.length === 0 ? (
            <span className="text-[10px] text-zinc-600 italic">
              no PPE items selected
            </span>
          ) : (
            watchedItems.map((item) => (
              <span
                key={item}
                className="text-[10px] font-medium bg-zinc-800 text-zinc-300 px-2 py-0.5 rounded-full"
              >
                {ITEM_LABELS[item] ?? item}
              </span>
            ))
          )}
        </div>

        <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] uppercase tracking-wide text-zinc-600 font-semibold">
            Hazards:
          </span>
          {watchedHazards.length === 0 ? (
            <span className="text-[10px] text-zinc-600 italic">
              none selected
            </span>
          ) : (
            watchedHazards.map((hazard) => (
              <span
                key={hazard}
                className="text-[10px] font-medium bg-red-950/40 text-red-300 px-2 py-0.5 rounded-full"
              >
                {HAZARD_LABELS[hazard] ?? hazard}
              </span>
            ))
          )}
        </div>

        {cameraOnline && workerCount > 0 && (
          <div className="mt-3 flex items-center justify-between rounded-lg px-3 py-2 bg-zinc-800/50">
            <div className="flex items-center gap-2">
              <Users size={14} className="text-teal-400" />
              <span className="text-xs text-zinc-300">
                {workerCount} worker{workerCount === 1 ? "" : "s"} detected
              </span>
            </div>

            {compliancePercent !== null && compliancePercent !== undefined && (
              <span
                className={`text-xs font-semibold ${
                  compliancePercent === 100 ? "text-green-400" : "text-yellow-400"
                }`}
              >
                {compliancePercent}% compliant
              </span>
            )}
          </div>
        )}

        {(isFire || isSmoke) && (
          <div
            className={`mt-3 flex items-center justify-between rounded-lg px-3 py-2 ${
              isFire ? "bg-red-950/40" : "bg-orange-950/40"
            }`}
          >
            <div className="flex items-center gap-2">
              {isFire ? (
                <Flame size={16} className="text-red-400" />
              ) : (
                <Cloud size={16} className="text-orange-400" />
              )}
              <span className="text-xs text-zinc-300">
                {isFire ? "FIRE" : "SMOKE"} INCIDENT
              </span>
            </div>
            <span
              className={`text-xs font-semibold ${
                isFire ? "text-red-400" : "text-orange-400"
              }`}
            >
              {isFire ? "CRITICAL" : "HIGH"}
            </span>
          </div>
        )}

        {cameraOnline && !isFire && !isSmoke && violationCount === 0 && (
          <div className="mt-3 flex items-center gap-2 bg-green-950/30 rounded-lg px-3 py-2">
            <span className="w-2 h-2 bg-green-500 rounded-full" />
            <span className="text-xs text-green-400">
              No active safety violations
            </span>
          </div>
        )}

        {cameraOnline && !isFire && !isSmoke && violationCount > 0 && (
          <div className="mt-3 flex items-center gap-2 bg-yellow-950/30 rounded-lg px-3 py-2">
            <span className="w-2 h-2 bg-yellow-500 rounded-full" />
            <span className="text-xs text-yellow-400">
              {violationCount} PPE violation{violationCount === 1 ? "" : "s"}{" "}
              active
            </span>
          </div>
        )}

        {!cameraOnline && (
          <div className="mt-3 flex items-center gap-2 bg-red-950/30 rounded-lg px-3 py-2">
            <span className="w-2 h-2 bg-red-500 rounded-full" />
            <span className="text-xs text-red-400">
              Camera connection unavailable
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
