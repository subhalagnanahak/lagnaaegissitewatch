import { useEffect, useRef, useState } from "react";
import {
  ScanFace,
  UserPlus,
  Users,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Camera,
  CameraOff,
  Upload,
  Trash2,
  RefreshCw,
  Clock,
  X,
} from "lucide-react";

import { authFetch } from "../lib/auth";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Every PPE item the gate can require - mirrors backend/config.py's
// ALL_WATCHABLE_PPE_ITEMS core set (goggles/mask/ear_protection/
// face_shield are watchable but not part of the PS06-required four,
// same distinction the Settings page's watched-items picker makes).
const GATE_ITEM_CHOICES = ["helmet", "vest", "gloves", "boots", "goggles"];

const DECISION_STYLES = {
  ALLOWED: { label: "ALLOWED", color: "text-green-400 bg-green-950/40 border-green-900", Icon: CheckCircle2 },
  DENIED: { label: "DENIED", color: "text-red-400 bg-red-950/40 border-red-900", Icon: XCircle },
  UNKNOWN_FACE: { label: "UNRECOGNIZED", color: "text-amber-400 bg-amber-950/40 border-amber-900", Icon: HelpCircle },
  NO_FACE: { label: "NO FACE DETECTED", color: "text-zinc-400 bg-zinc-800/50 border-zinc-700", Icon: HelpCircle },
  FACE_ID_UNAVAILABLE: { label: "FACE ID UNAVAILABLE", color: "text-zinc-400 bg-zinc-800/50 border-zinc-700", Icon: HelpCircle },
};

function decisionBadge(decision) {
  const style = DECISION_STYLES[decision] || DECISION_STYLES.NO_FACE;
  const Icon = style.Icon;

  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-lg border ${style.color}`}>
      <Icon size={13} />
      {style.label}
    </span>
  );
}

// Manual photo-upload fallback - kept for a site with no DVR/RTSP
// gate camera configured (GATE_CAMERA_SOURCE unset in backend/.env),
// or for the moment the DVR connection drops. POST /gate/scan accepts
// any uploaded image this way, independent of the live feed below.
// On a phone this opens the device's own camera app; on desktop it's
// an ordinary file picker.
function FileFallbackButton({ label, onFile, multiple = false }) {
  const inputRef = useRef(null);

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="user"
        multiple={multiple}
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          if (files.length) onFile(multiple ? files : files[0]);
          e.target.value = "";
        }}
      />
      <button
        onClick={() => inputRef.current?.click()}
        className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-white"
      >
        <Upload size={12} />
        {label}
      </button>
    </>
  );
}

// Live view of the gate's DVR/CCTV channel. This is deliberately NOT
// a browser webcam - a DVR channel (e.g. one of a Hikvision hybrid
// DVR's 4 analog/IP channels) only ever exists as a network stream,
// never as a local device a browser's getUserMedia() can see. So the
// BACKEND pulls it over RTSP (gate_camera.py) and re-serves it here
// as a plain MJPEG multipart stream - an <img> pointed at that
// endpoint renders a live, continuously-updating feed natively, no
// JavaScript required to keep it refreshing. Multiple of these can be
// on screen at once (Scan section + Enroll section both show it) with
// no camera-contention issue, since every viewer just reads the same
// already-open backend connection.
function DvrFeed({ connected }) {
  if (!connected) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-zinc-500 text-xs text-center px-4">
        <CameraOff size={22} />
        Gate camera not connected
      </div>
    );
  }

  return (
    <img
      src={`${API_BASE}/gate/camera/feed`}
      alt="Gate camera live feed"
      className="w-full h-full object-cover"
    />
  );
}

export default function EntryGatePanel() {
  const [gateStatus, setGateStatus] = useState(null);
  const [workers, setWorkers] = useState([]);
  const [log, setLog] = useState([]);

  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState(null);
  const [scanError, setScanError] = useState("");

  const [enrollName, setEnrollName] = useState("");
  const [enrollDept, setEnrollDept] = useState("");
  const [enrollItems, setEnrollItems] = useState(new Set(["helmet", "vest", "gloves", "boots"]));
  // Each entry is { blob, url } - url is an object URL for the thumbnail
  // preview, since a captured/grabbed Blob has no name/thumbnail of
  // its own the way a file-picker File does. Revoked on removal and
  // on unmount below.
  const [enrollPhotos, setEnrollPhotos] = useState([]);
  const [enrolling, setEnrolling] = useState(false);
  const [enrollError, setEnrollError] = useState("");
  const [grabbing, setGrabbing] = useState(false);

  useEffect(() => {
    // Release all preview object URLs when the panel unmounts.
    return () => {
      enrollPhotos.forEach((p) => URL.revokeObjectURL(p.url));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addEnrollPhoto = (blob) => {
    setEnrollPhotos((prev) => [...prev, { blob, url: URL.createObjectURL(blob) }]);
  };

  const removeEnrollPhoto = (index) => {
    setEnrollPhotos((prev) => {
      const target = prev[index];
      if (target) URL.revokeObjectURL(target.url);
      return prev.filter((_, i) => i !== index);
    });
  };

  const clearEnrollPhotos = () => {
    setEnrollPhotos((prev) => {
      prev.forEach((p) => URL.revokeObjectURL(p.url));
      return [];
    });
  };

  const refresh = async () => {
    try {
      const [statusRes, workersRes, logRes] = await Promise.all([
        fetch(`${API_BASE}/gate/status`),
        fetch(`${API_BASE}/workers`),
        fetch(`${API_BASE}/gate/log?limit=30`),
      ]);
      setGateStatus(await statusRes.json());
      setWorkers((await workersRes.json()).workers || []);
      setLog((await logRes.json()).entries || []);
    } catch {
      // Backend unreachable - leave previous state showing rather
      // than clearing it, same as the rest of this dashboard.
    }
  };

  useEffect(() => {
    refresh();
    // A bit faster than the rest of the dashboard's polling - the
    // live DVR connection status line benefits from staying current.
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  // Manual photo-upload path - posts a File/Blob straight to
  // /gate/scan. Used by FileFallbackButton when there's no DVR
  // configured or its connection is down.
  const runScan = async (file) => {
    setScanning(true);
    setScanError("");
    setScanResult(null);

    try {
      const formData = new FormData();
      formData.append("image", file);

      const res = await fetch(`${API_BASE}/gate/scan`, { method: "POST", body: formData });
      const data = await res.json();

      if (data.error) {
        setScanError(data.error);
      } else {
        setScanResult(data);
        refresh();
      }
    } catch {
      setScanError("Scan failed - is the backend running?");
    } finally {
      setScanning(false);
    }
  };

  // DVR path (the normal path once GATE_CAMERA_SOURCE is set) - asks
  // the backend to grab the current live RTSP frame itself and scan
  // it. No image ever has to leave or enter the browser.
  const runDvrScan = async () => {
    setScanning(true);
    setScanError("");
    setScanResult(null);

    try {
      const res = await fetch(`${API_BASE}/gate/camera/capture`, { method: "POST" });
      const data = await res.json();

      if (data.error) {
        setScanError(data.error);
      } else {
        setScanResult(data);
        refresh();
      }
    } catch {
      setScanError("Scan failed - is the backend running?");
    } finally {
      setScanning(false);
    }
  };

  // Grabs one still frame from the live DVR feed to use as an
  // enrollment photo - the same gate camera channel as the scan
  // above, not a browser webcam.
  const grabEnrollPhoto = async () => {
    setGrabbing(true);
    setEnrollError("");

    try {
      const res = await fetch(`${API_BASE}/gate/camera/snapshot.jpg?t=${Date.now()}`);

      if (!res.ok) {
        setEnrollError("Could not grab a frame from the gate camera - check it's connected above.");
        return;
      }

      const blob = await res.blob();
      addEnrollPhoto(blob);
    } catch {
      setEnrollError("Could not reach the backend to grab a photo.");
    } finally {
      setGrabbing(false);
    }
  };

  const toggleEnrollItem = (item) => {
    setEnrollItems((prev) => {
      const next = new Set(prev);
      if (next.has(item)) next.delete(item);
      else next.add(item);
      return next;
    });
  };

  const submitEnrollment = async () => {
    if (!enrollName.trim()) {
      setEnrollError("Name is required.");
      return;
    }

    if (enrollPhotos.length === 0) {
      setEnrollError("At least one clear, front-facing photo is required.");
      return;
    }

    setEnrolling(true);
    setEnrollError("");

    try {
      const formData = new FormData();
      formData.append("name", enrollName.trim());
      formData.append("department", enrollDept.trim());
      formData.append("required_items", Array.from(enrollItems).join(","));
      enrollPhotos.forEach((p, i) => formData.append("photos", p.blob, `gate-capture-${i + 1}.jpg`));

      const res = await authFetch(`${API_BASE}/workers/enroll`, { method: "POST", body: formData });
      const data = await res.json();

      if (data.error) {
        setEnrollError(data.error);
      } else {
        setEnrollName("");
        setEnrollDept("");
        clearEnrollPhotos();
        refresh();
      }
    } catch {
      setEnrollError("Enrollment failed - is the backend running?");
    } finally {
      setEnrolling(false);
    }
  };

  const removeWorker = async (workerId) => {
    try {
      await authFetch(`${API_BASE}/workers/${encodeURIComponent(workerId)}`, { method: "DELETE" });
      refresh();
    } catch {
      // Non-fatal - the roster just won't update until the next refresh.
    }
  };

  const faceAvailable = gateStatus?.face_id?.available;
  const cameraStatus = gateStatus?.camera; // { configured, connected, auto_scan, error }
  const cameraConnected = !!cameraStatus?.connected;

  return (
    <div className="space-y-8">
      {gateStatus && !faceAvailable && (
        <div className="bg-amber-950/20 border border-amber-900 rounded-2xl p-5 text-sm text-amber-300">
          <p className="font-semibold mb-1">Face ID isn't set up yet</p>
          <p className="text-amber-400/90">
            {gateStatus.face_id.reason} Enrollment and gate scans will stay
            disabled until the two small model files are downloaded - see
            backend/models/README.md (one-time setup, fully offline after
            that).
          </p>
        </div>
      )}

      {/* ---- GATE CAMERA (DVR) STATUS ----
           The gate camera is a DVR/CCTV channel, read server-side by
           gate_camera.py - either over RTSP, or by polling the DVR's
           ISAPI JPEG-snapshot endpoint when the RTSP stream itself is
           unusable (see backend/DVR_SETUP.md). Not a browser webcam,
           so there's nothing to pick here, just whether the
           configured feed is actually connected. */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4 flex flex-wrap items-center gap-3">
        <Camera size={16} className={cameraConnected ? "text-teal-400 flex-shrink-0" : "text-zinc-600 flex-shrink-0"} />
        <div className="flex-1 min-w-[220px]">
          <p className="text-sm text-white">
            {!cameraStatus?.configured
              ? "No gate camera configured"
              : cameraConnected
                ? `Gate camera connected${cameraStatus.auto_scan ? " - auto-scanning" : ""}`
                : "Gate camera configured but not connected"}
          </p>
          <p className="text-xs text-zinc-500">
            {!cameraStatus?.configured
              ? "Set GATE_CAMERA_SOURCE in backend/.env to your DVR's RTSP (or ISAPI snapshot) URL - see backend/DVR_SETUP.md. The photo-upload option below still works either way."
              : cameraStatus.error || "Reading from the DVR camera feed."}
          </p>
        </div>
        <button
          onClick={refresh}
          className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-white"
        >
          <RefreshCw size={13} />
          Refresh
        </button>
      </div>

      {/* ---- SCAN ---- */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <ScanFace size={19} className="text-teal-400" />
          <div>
            <h2 className="text-lg font-display font-semibold text-white tracking-tight">
              Entry Gate Scan
            </h2>
            <p className="text-sm text-zinc-500">
              Face match + PPE check before entry - requires:{" "}
              {(gateStatus?.required_items || []).join(", ") || "..."}
            </p>
          </div>
        </div>

        <div className="relative bg-black rounded-xl overflow-hidden w-full max-w-sm aspect-[4/3]">
          <DvrFeed connected={cameraConnected} />
        </div>

        <div className="flex items-center gap-3 mt-3">
          <button
            onClick={runDvrScan}
            disabled={scanning || !cameraConnected}
            className="flex items-center gap-2 bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white text-sm font-medium rounded-xl px-4 py-2.5 transition-colors"
          >
            <ScanFace size={15} />
            {scanning ? "Scanning..." : "Capture & Scan"}
          </button>
        </div>

        <div className="mt-2">
          <FileFallbackButton label="No DVR? Upload a photo instead" onFile={runScan} />
        </div>

        {scanError && (
          <div className="bg-red-950/30 border border-red-900 text-red-400 text-sm rounded-xl p-4 mt-3">
            {scanError}
          </div>
        )}

        {scanResult && (
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 mt-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <p className="text-white font-medium">
                  {scanResult.name || "Unknown"}
                  {scanResult.department && (
                    <span className="text-zinc-500 text-sm"> · {scanResult.department}</span>
                  )}
                </p>
                {scanResult.match_score != null && (
                  <p className="text-xs text-zinc-500 mt-0.5">
                    Match confidence: {Math.round(scanResult.match_score * 100)}%
                  </p>
                )}
              </div>
              {decisionBadge(scanResult.decision)}
            </div>

            {scanResult.missing_items?.length > 0 && (
              <p className="text-sm text-red-400">
                Missing: {scanResult.missing_items.map((i) => i.replace("_", " ")).join(", ")}
              </p>
            )}
          </div>
        )}
      </section>

      {/* ---- ENROLL ---- */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <UserPlus size={19} className="text-teal-400" />
          <div>
            <h2 className="text-lg font-display font-semibold text-white tracking-tight">
              Enroll Worker
            </h2>
            <p className="text-sm text-zinc-500">
              One clear, front-facing photo (more = a more robust match)
            </p>
          </div>
        </div>

        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 space-y-4 max-w-xl">
          <div className="grid grid-cols-2 gap-3">
            <input
              value={enrollName}
              onChange={(e) => setEnrollName(e.target.value)}
              placeholder="Full name"
              className="bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-teal-600"
            />
            <input
              value={enrollDept}
              onChange={(e) => setEnrollDept(e.target.value)}
              placeholder="Department (optional)"
              className="bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-teal-600"
            />
          </div>

          <div>
            <p className="text-xs text-zinc-500 mb-2">Required PPE at this gate</p>
            <div className="flex flex-wrap gap-2">
              {GATE_ITEM_CHOICES.map((item) => (
                <button
                  key={item}
                  onClick={() => toggleEnrollItem(item)}
                  className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                    enrollItems.has(item)
                      ? "bg-teal-600 border-teal-600 text-white"
                      : "bg-zinc-800 border-zinc-700 text-zinc-400 hover:text-white"
                  }`}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="relative bg-black rounded-xl overflow-hidden w-full max-w-sm aspect-[4/3]">
              <DvrFeed connected={cameraConnected} />
            </div>

            <div className="flex items-center gap-3 mt-3">
              <button
                onClick={grabEnrollPhoto}
                disabled={grabbing || !cameraConnected}
                className="flex items-center gap-2 bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white text-sm font-medium rounded-xl px-4 py-2.5 transition-colors"
              >
                <Camera size={15} />
                {grabbing ? "Grabbing..." : `Add Photo${enrollPhotos.length ? ` (${enrollPhotos.length})` : ""}`}
              </button>
            </div>

            <div className="flex items-center gap-3 mt-2">
              <FileFallbackButton
                label="No DVR? Upload photos instead"
                multiple
                onFile={(files) => files.forEach(addEnrollPhoto)}
              />
              {enrollPhotos.length > 0 && (
                <button
                  onClick={clearEnrollPhotos}
                  className="text-xs text-zinc-500 hover:text-red-400"
                >
                  Clear all
                </button>
              )}
            </div>

            {enrollPhotos.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-3">
                {enrollPhotos.map((p, i) => (
                  <div key={p.url} className="relative w-16 h-16 rounded-lg overflow-hidden border border-zinc-700">
                    <img src={p.url} alt={`Capture ${i + 1}`} className="w-full h-full object-cover" />
                    <button
                      onClick={() => removeEnrollPhoto(i)}
                      className="absolute top-0.5 right-0.5 bg-black/70 hover:bg-red-600 rounded-full p-0.5 text-white"
                    >
                      <X size={11} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {enrollError && <p className="text-sm text-red-400">{enrollError}</p>}

          <button
            onClick={submitEnrollment}
            disabled={enrolling}
            className="w-full bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white text-sm font-medium rounded-xl px-4 py-2.5 transition-colors"
          >
            {enrolling ? "Enrolling..." : "Enroll Worker"}
          </button>
        </div>
      </section>

      {/* ---- ROSTER ---- */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Users size={19} className="text-teal-400" />
            <div>
              <h2 className="text-lg font-display font-semibold text-white tracking-tight">
                Enrolled Workers
              </h2>
              <p className="text-sm text-zinc-500">{workers.length} on file</p>
            </div>
          </div>
          <button onClick={refresh} className="text-zinc-500 hover:text-white">
            <RefreshCw size={16} />
          </button>
        </div>

        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
          {workers.length === 0 ? (
            <div className="p-6 text-center text-sm text-zinc-500">No workers enrolled yet</div>
          ) : (
            workers.map((w) => (
              <div
                key={w.worker_id}
                className="px-5 py-3 border-b border-zinc-800 last:border-0 flex items-center justify-between"
              >
                <div className="flex items-center gap-3">
                  <img
                    src={`${API_BASE}/workers/${encodeURIComponent(w.worker_id)}/photo`}
                    alt={w.name}
                    className="w-9 h-9 rounded-full object-cover bg-zinc-800"
                    onError={(e) => { e.target.style.visibility = "hidden"; }}
                  />
                  <div>
                    <p className="text-sm text-white">
                      {w.name}{" "}
                      <span className="text-xs text-zinc-500">({w.worker_id})</span>
                    </p>
                    <p className="text-xs text-zinc-500">
                      {w.department || "No department"} · requires{" "}
                      {w.required_items.join(", ")}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => removeWorker(w.worker_id)}
                  className="text-zinc-500 hover:text-red-400"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))
          )}
        </div>
      </section>

      {/* ---- LOG ---- */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <Clock size={19} className="text-teal-400" />
          <div>
            <h2 className="text-lg font-display font-semibold text-white tracking-tight">
              Gate Log
            </h2>
            <p className="text-sm text-zinc-500">Every scan, allowed or denied</p>
          </div>
        </div>

        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden max-h-[360px] overflow-y-auto">
          {log.length === 0 ? (
            <div className="p-6 text-center text-sm text-zinc-500">No scans yet</div>
          ) : (
            log.map((entry, i) => (
              <div
                key={i}
                className="px-5 py-3 border-b border-zinc-800 last:border-0 flex items-center justify-between gap-3"
              >
                <div>
                  <p className="text-sm text-white">{entry.name || "Unrecognized"}</p>
                  <p className="text-xs text-zinc-500">
                    {new Date(entry.timestamp).toLocaleString()}
                    {entry.missing_items?.length > 0 &&
                      ` · missing ${entry.missing_items.join(", ")}`}
                  </p>
                </div>
                {decisionBadge(entry.decision)}
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
