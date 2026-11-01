import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Users,
  ShieldCheck,
  AlertTriangle,
  Camera,
  Flame,
  Cloud,
  MapPin,
  Bell,
  BarChart3,
  ScanFace,
  Activity,
  Wifi,
  WifiOff,
  Clock,
  Trash2,
  Volume2,
  Smartphone,
  FileText,
  Mic,
  Camera as CameraIcon,
  Download,
  LayoutDashboard,
  Video,
} from "lucide-react";

import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import StatCard from "../components/StatCard";
import CameraCard from "../components/CameraCard";
import DetectionSettingsSidebar from "../components/DetectionSettingsSidebar";
import ViolationHotspots from "../components/ViolationHotspots";
import FootagePanel from "../components/FootagePanel";
import EntryGatePanel from "../components/EntryGatePanel";
import {
  playAlertTone,
  notifyIncident,
  requestNotificationPermission,
} from "../lib/localAlert";
import { authFetch } from "../lib/auth";

// Configurable via ppe-safety-dashboard/.env (VITE_API_BASE / VITE_WS_URL)
// so the dashboard can point at a backend on another machine on the
// network - falls back to localhost for local single-machine demos.
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const WS_URL =
  import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/detections";

const ITEM_LABELS = {
  helmet: "Helmet",
  vest: "Safety Vest",
  gloves: "Gloves",
  boots: "Safety Boots",
  goggles: "Goggles",
  mask: "Mask / Respirator",
  ear_protection: "Ear Protection",
  face_shield: "Face Shield",
};

// Fallback menus if the backend response hasn't arrived yet - kept in
// sync with backend/config.py's WATCHABLE_PPE_ITEMS/
// WATCHABLE_EXTRA_GEAR_ITEMS/WATCHABLE_HAZARD_ITEMS, but the live
// values from GET /cameras always win once they load.
const DEFAULT_WATCHABLE_ITEMS = ["helmet", "vest", "gloves", "boots", "goggles"];
const DEFAULT_WATCHABLE_EXTRA_ITEMS = ["mask", "ear_protection", "face_shield"];
const DEFAULT_WATCHABLE_HAZARDS = ["fire", "smoke"];

export default function Dashboard({ onLogout }) {
  // Which sidebar page is showing - lives here (not in Sidebar
  // itself) so this component's render can decide what each page
  // actually contains. See Sidebar.jsx's controlled-nav docstring.
  const [activePage, setActivePage] = useState("Dashboard");

  // Static camera roster (id/location/enabled) from /cameras.
  const [cameraRoster, setCameraRoster] = useState([]);

  // Live per-camera snapshots from the websocket.
  const [cameraSnapshots, setCameraSnapshots] = useState({});

  const [backendOnline, setBackendOnline] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [smsEnabled, setSmsEnabled] = useState(false);
  const [sirenEnabled, setSirenEnabled] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [snapshotsEnabled, setSnapshotsEnabled] = useState(false);
  const [gsmSmsEnabled, setGsmSmsEnabled] = useState(false);
  const [faceIdAvailable, setFaceIdAvailable] = useState(false);
  const [fireSensorConnected, setFireSensorConnected] = useState(false);

  // The backend's real, persisted alert log (GET /alerts) - every
  // entry here actually fired (passed the cooldown, was written to
  // alert_log.jsonl, triggered the siren, and sent SMS if configured).
  // Distinct from `incidents` below, which is just what's visible in
  // the CURRENT frame and resets as soon as the camera stops seeing it.
  const [alertLog, setAlertLog] = useState([]);

  // The full menu of PPE items a camera's watched-items can be picked
  // from (GET /cameras' watchable_ppe_items), and which camera's
  // settings drawer (if any) is currently open.
  const [watchableItems, setWatchableItems] = useState(DEFAULT_WATCHABLE_ITEMS);
  const [watchableExtraItems, setWatchableExtraItems] = useState(
    DEFAULT_WATCHABLE_EXTRA_ITEMS
  );
  const [watchableHazards, setWatchableHazards] = useState(DEFAULT_WATCHABLE_HAZARDS);
  const [savingWatchedItems, setSavingWatchedItems] = useState(false);

  /*
   * =========================================================
   * CAMERA ROSTER + BACKEND HEALTH
   * =========================================================
   */

  useEffect(() => {
    let mounted = true;

    const checkHealth = async () => {
      try {
        const [healthRes, camerasRes] = await Promise.all([
          fetch(`${API_BASE}/health`),
          fetch(`${API_BASE}/cameras`),
        ]);

        if (!healthRes.ok || !camerasRes.ok) {
          throw new Error("Backend unavailable");
        }

        const [healthData, camerasData] = await Promise.all([
          healthRes.json(),
          camerasRes.json(),
        ]);

        if (!mounted) return;

        setBackendOnline(true);
        setCameraRoster(camerasData.cameras ?? []);
        setSmsEnabled(Boolean(healthData.sms_alerts));
        setSirenEnabled(Boolean(healthData.local_siren));
        setVoiceEnabled(Boolean(healthData.voice_alerts));
        setSnapshotsEnabled(Boolean(healthData.snapshots_enabled));
        setGsmSmsEnabled(Boolean(healthData.gsm_sms_alerts));
        setFaceIdAvailable(Boolean(healthData.face_id?.available));
        setFireSensorConnected(Boolean(healthData.fire_sensor?.connected));

        if (Array.isArray(camerasData.watchable_ppe_items)) {
          setWatchableItems(camerasData.watchable_ppe_items);
        }

        if (Array.isArray(camerasData.watchable_extra_gear_items)) {
          setWatchableExtraItems(camerasData.watchable_extra_gear_items);
        }

        if (Array.isArray(camerasData.watchable_hazard_items)) {
          setWatchableHazards(camerasData.watchable_hazard_items);
        }
      } catch {
        if (!mounted) return;

        setBackendOnline(false);
      }
    };

    checkHealth();

    const interval = setInterval(checkHealth, 5000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  /*
   * =========================================================
   * ALERT LOG - the backend's real, persisted alert history
   * (GET /alerts). Every entry here already passed the cooldown
   * and fired every configured channel (siren, local log, and
   * SMS if enabled) - this is what actually happened, timestamped.
   * =========================================================
   */

  useEffect(() => {
    let mounted = true;

    const fetchAlerts = async () => {
      try {
        const res = await fetch(`${API_BASE}/alerts`);
        if (!res.ok) throw new Error("alerts unavailable");

        const data = await res.json();
        if (!mounted) return;

        setAlertLog(data.alerts ?? []);
      } catch {
        // Leave the last-known alert log in place rather than
        // clearing it on a transient network hiccup.
      }
    };

    fetchAlerts();

    const interval = setInterval(fetchAlerts, 4000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const clearAlertLog = async () => {
    try {
      await authFetch(`${API_BASE}/alerts`, { method: "DELETE" });
      setAlertLog([]);
    } catch {
      // Best-effort - if the backend is unreachable there's nothing
      // to clear anyway.
    }
  };

  /*
   * =========================================================
   * VIOLATION HOTSPOTS - zones ranked by alerts fired this session
   * (GET /analytics/hotspots), the site-wide counterpart to each
   * camera's own live pixel-space heatmap overlay below. Polled
   * alongside the alert log, since it's derived from the same
   * underlying alert history and only changes when that does.
   * =========================================================
   */

  const [hotspotZones, setHotspotZones] = useState([]);

  useEffect(() => {
    let mounted = true;

    const fetchHotspots = async () => {
      try {
        const res = await fetch(`${API_BASE}/analytics/hotspots`);
        if (!res.ok) throw new Error("hotspots unavailable");

        const data = await res.json();
        if (!mounted) return;

        setHotspotZones(data.zones ?? []);
      } catch {
        // Leave the last-known ranking in place on a transient hiccup.
      }
    };

    fetchHotspots();

    const interval = setInterval(fetchHotspots, 4000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  /*
   * =========================================================
   * FOOTAGE STATS (for the Reports page's stat cards)
   * =========================================================
   */

  const [footageStats, setFootageStats] = useState(null);

  useEffect(() => {
    let mounted = true;

    const fetchFootageStats = async () => {
      try {
        const res = await fetch(`${API_BASE}/footage/stats`);
        if (!res.ok) throw new Error("footage stats unavailable");

        const data = await res.json();
        if (!mounted) return;

        setFootageStats(data);
      } catch {
        // Leave the last-known stats in place on a transient hiccup.
      }
    };

    fetchFootageStats();

    const interval = setInterval(fetchFootageStats, 10000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  /*
   * =========================================================
   * WEBSOCKET - LIVE MULTI-CAMERA DETECTIONS
   *
   * Backend sends: { cameras: [ {camera_id, location, zone,
   * online, fps, workers, compliance_percent, detections,
   * violation_count, ...}, ... ] }
   * =========================================================
   */

  useEffect(() => {
    let socket = null;
    let reconnectTimer = null;
    let stopped = false;

    const connect = () => {
      if (stopped) return;

      socket = new WebSocket(WS_URL);

      socket.onopen = () => setWsConnected(true);

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (Array.isArray(data.cameras)) {
            const byId = {};

            data.cameras.forEach((snap) => {
              byId[snap.camera_id] = snap;
            });

            setCameraSnapshots(byId);
          }
        } catch (error) {
          console.error("WebSocket JSON error:", error);
        }
      };

      socket.onclose = () => {
        setWsConnected(false);

        if (!stopped) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      };

      socket.onerror = () => {
        setWsConnected(false);
      };
    };

    connect();

    return () => {
      stopped = true;

      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket) socket.close();
    };
  }, []);

  /*
   * =========================================================
   * MERGE ROSTER + LIVE SNAPSHOTS
   *
   * Cameras that are disabled in cameras.json (or the backend
   * hasn't sent a snapshot for yet) still show up, as offline.
   * =========================================================
   */

  const cameras = useMemo(() => {
    return cameraRoster.map((cam) => {
      const live = cameraSnapshots[cam.camera_id];

      return {
        camera_id: cam.camera_id,
        location: cam.location,
        zone: cam.zone,
        enabled: cam.enabled,
        online: live?.online ?? false,
        lastError: live?.last_error ?? cam.last_error ?? null,
        watchedItems: live?.watched_items ?? cam.watched_items ?? [],
        watchedHazards: live?.watched_hazards ?? cam.watched_hazards ?? [],
        showHeatmap: live?.show_heatmap ?? cam.show_heatmap ?? false,
        fps: live?.fps ?? 0,
        workers: live?.workers ?? [],
        worker_count: live?.worker_count ?? 0,
        compliance_percent: live?.compliance_percent ?? null,
        detections: live?.detections ?? [],
        violation_count: live?.violation_count ?? 0,
        ppe_violation_count: live?.ppe_violation_count ?? 0,
        fire_smoke_violation_count: live?.fire_smoke_violation_count ?? 0,
        status: live?.status ?? "UNKNOWN",
      };
    });
  }, [cameraRoster, cameraSnapshots]);

  /*
   * =========================================================
   * PER-CAMERA DETECTION SETTINGS (PPE items + fire/smoke hazards)
   *
   * PUT /cameras/{id}/watched-items and /watched-hazards take effect
   * on the backend's very next frame - no restart. Optimistically
   * updates the local roster too, so the checkbox/pill state doesn't
   * wait on the next 5s /cameras poll or websocket tick to catch up.
   * =========================================================
   */

  const updateWatchedItems = async (cameraId, items) => {
    setCameraRoster((prev) =>
      prev.map((cam) =>
        cam.camera_id === cameraId ? { ...cam, watched_items: items } : cam
      )
    );

    setSavingWatchedItems(true);

    try {
      const res = await authFetch(
        `${API_BASE}/cameras/${encodeURIComponent(cameraId)}/watched-items`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items }),
        }
      );

      const data = await res.json();

      if (data.error) {
        console.error("Failed to update watched items:", data.error);
      }
    } catch {
      // Best-effort - if this fails the next /cameras poll reconciles
      // the roster back to whatever the backend actually has.
    } finally {
      setSavingWatchedItems(false);
    }
  };

  const handleToggleWatchedItem = useCallback(
    (cameraId, item, checked) => {
      const camera = cameras.find((c) => c.camera_id === cameraId);
      const current = camera?.watchedItems ?? [];

      const next = checked
        ? [...current, item]
        : current.filter((i) => i !== item);

      updateWatchedItems(cameraId, next);
    },
    [cameras]
  );

  const updateWatchedHazards = async (cameraId, items) => {
    setCameraRoster((prev) =>
      prev.map((cam) =>
        cam.camera_id === cameraId ? { ...cam, watched_hazards: items } : cam
      )
    );

    setSavingWatchedItems(true);

    try {
      const res = await authFetch(
        `${API_BASE}/cameras/${encodeURIComponent(cameraId)}/watched-hazards`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items }),
        }
      );

      const data = await res.json();

      if (data.error) {
        console.error("Failed to update watched hazards:", data.error);
      }
    } catch {
      // Best-effort - the next /cameras poll reconciles the roster.
    } finally {
      setSavingWatchedItems(false);
    }
  };

  const handleToggleWatchedHazard = useCallback(
    (cameraId, hazard, checked) => {
      const camera = cameras.find((c) => c.camera_id === cameraId);
      const current = camera?.watchedHazards ?? [];

      const next = checked
        ? [...current, hazard]
        : current.filter((h) => h !== hazard);

      updateWatchedHazards(cameraId, next);
    },
    [cameras]
  );

  /*
   * =========================================================
   * PER-CAMERA HEATMAP OVERLAY TOGGLE (PUT /cameras/{id}/heatmap)
   *
   * Turns that camera's own live violation-hotspot overlay on/off -
   * the accumulated grid keeps building server-side either way, this
   * only controls whether it's blended into the MJPEG feed. Takes
   * effect on the very next frame, no restart needed.
   * =========================================================
   */

  const toggleHeatmap = async (cameraId, enabled) => {
    setCameraRoster((prev) =>
      prev.map((cam) =>
        cam.camera_id === cameraId ? { ...cam, show_heatmap: enabled } : cam
      )
    );

    try {
      const res = await authFetch(
        `${API_BASE}/cameras/${encodeURIComponent(cameraId)}/heatmap`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        }
      );

      const data = await res.json();

      if (data.error) {
        console.error("Failed to toggle heatmap:", data.error);
      }
    } catch {
      // Best-effort - the next /cameras poll reconciles the roster.
    }
  };

  /*
   * =========================================================
   * CAMERA ROSTER MANAGEMENT - add / rename zone-location / enable
   * or disable / delete. Every camera physically deployed on site
   * gets its own zone name here, editable at any time (a camera
   * that gets moved to a different area doesn't need a restart or
   * a hand-edited cameras.json to reflect that).
   * =========================================================
   */

  // Probes local webcam indexes (GET /cameras/discover) so the Add
  // Camera form can offer a pick list instead of making the user find
  // the index on the terminal first (only webcam indexes can be
  // auto-detected this way - RTSP URLs are still typed, and video
  // files can either be typed or uploaded via uploadVideo below).
  const discoverCameras = async () => {
    try {
      const res = await fetch(`${API_BASE}/cameras/discover`);

      if (!res.ok) throw new Error("discover failed");

      const data = await res.json();
      return { cameras: data.cameras ?? [] };
    } catch {
      return { error: "Could not scan for cameras - is the backend running?" };
    }
  };

  // Uploads a recorded video from the browser (POST /cameras/upload-
  // video) - the OTHER way to get a video-file camera source, next to
  // typing in a path to a file already on the backend's machine. On
  // success returns { status: "ok", path, filename } - the Add
  // Camera form then uses `path` exactly like a typed-in one.
  const uploadVideo = async (file) => {
    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await authFetch(`${API_BASE}/cameras/upload-video`, {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      if (data.error) {
        return { error: data.error };
      }

      return data;
    } catch {
      return { error: "Upload failed - is the backend running?" };
    }
  };

  const addCamera = async (payload) => {
    try {
      const res = await authFetch(`${API_BASE}/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();

      if (data.error) {
        return { error: data.error };
      }

      if (Array.isArray(data.cameras)) {
        setCameraRoster(data.cameras);
      }

      return {};
    } catch {
      return { error: "Could not reach the backend - is it running?" };
    }
  };

  const updateCameraMeta = async (cameraId, { location, zone }) => {
    setCameraRoster((prev) =>
      prev.map((cam) =>
        cam.camera_id === cameraId
          ? {
              ...cam,
              ...(location !== undefined ? { location } : {}),
              ...(zone !== undefined ? { zone } : {}),
            }
          : cam
      )
    );

    try {
      const res = await authFetch(
        `${API_BASE}/cameras/${encodeURIComponent(cameraId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ location, zone }),
        }
      );

      const data = await res.json();

      if (data.error) {
        console.error("Failed to rename camera:", data.error);
      }
    } catch {
      // Best-effort - the next /cameras poll reconciles the roster.
    }
  };

  const toggleCameraEnabled = async (cameraId, enabled) => {
    setCameraRoster((prev) =>
      prev.map((cam) =>
        cam.camera_id === cameraId ? { ...cam, enabled } : cam
      )
    );

    try {
      const res = await authFetch(
        `${API_BASE}/cameras/${encodeURIComponent(cameraId)}/enabled`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        }
      );

      const data = await res.json();

      if (data.error) {
        console.error("Failed to toggle camera:", data.error);
      }
    } catch {
      // Best-effort - the next /cameras poll reconciles the roster.
    }
  };

  const deleteCamera = async (cameraId) => {
    if (
      !window.confirm(
        `Remove ${cameraId} permanently? This stops its stream and deletes it from cameras.json.`
      )
    ) {
      return;
    }

    setCameraRoster((prev) => prev.filter((cam) => cam.camera_id !== cameraId));

    try {
      const res = await authFetch(
        `${API_BASE}/cameras/${encodeURIComponent(cameraId)}`,
        { method: "DELETE" }
      );

      const data = await res.json();

      if (data.error) {
        console.error("Failed to delete camera:", data.error);
      }
    } catch {
      // Best-effort - the next /cameras poll reconciles the roster.
    }
  };

  /*
   * =========================================================
   * SITE-WIDE STATS (aggregated across all online cameras)
   * =========================================================
   */

  const stats = useMemo(() => {
    const onlineCameras = cameras.filter((c) => c.online);

    const totalWorkers = onlineCameras.reduce(
      (sum, c) => sum + c.worker_count,
      0
    );

    const compliantWorkers = onlineCameras.reduce(
      (sum, c) =>
        sum + c.workers.filter((w) => w.compliant).length,
      0
    );

    const ppeViolations = onlineCameras.reduce(
      (sum, c) => sum + c.ppe_violation_count,
      0
    );

    const fireCount = onlineCameras.reduce(
      (sum, c) =>
        sum +
        c.detections.filter(
          (d) => d.violation && d.class_name === "fire"
        ).length,
      0
    );

    const smokeCount = onlineCameras.reduce(
      (sum, c) =>
        sum +
        c.detections.filter(
          (d) => d.violation && d.class_name === "smoke"
        ).length,
      0
    );

    const compliancePercent =
      totalWorkers > 0
        ? Math.round((compliantWorkers / totalWorkers) * 100)
        : 0;

    const avgFps =
      onlineCameras.length > 0
        ? onlineCameras.reduce((sum, c) => sum + c.fps, 0) /
          onlineCameras.length
        : 0;

    return {
      totalWorkers,
      compliantWorkers,
      compliancePercent,
      ppeViolations,
      fireCount,
      smokeCount,
      onlineCameraCount: onlineCameras.length,
      avgFps,
    };
  }, [cameras]);

  /*
   * =========================================================
   * INCIDENT LIST (across all cameras)
   * =========================================================
   */

  const incidents = useMemo(() => {
    const result = [];

    cameras.forEach((cam) => {
      if (!cam.online) return;

      cam.detections
        .filter((d) => d.violation)
        .forEach((d, index) => {
          const isFire = d.class_name === "fire";
          const isSmoke = d.class_name === "smoke";

          result.push({
            id: `${cam.camera_id}-${d.class_name}-${index}`,
            // Stable across frames (no index) - used to detect a
            // genuinely NEW incident for the local tone/notification,
            // matching the backend's own (camera, zone, incident)
            // alert-cooldown key.
            signature: `${cam.camera_id}|${cam.zone}|${d.class_name}`,
            type: isFire ? "FIRE" : isSmoke ? "SMOKE" : "PPE VIOLATION",
            icon: isFire ? (
              <Flame size={18} />
            ) : isSmoke ? (
              <Cloud size={18} />
            ) : (
              <AlertTriangle size={18} />
            ),
            camera: cam.camera_id,
            location: cam.location,
            severity: d.severity,
            confidence: `${Math.round(d.confidence * 100)}%`,
            violation: isFire || isSmoke ? null : incidentLabel(d.class_name),
            inferred: d.source === "inferred",
          });
        });
    });

    // Fire/smoke first, most urgent.
    return result.sort((a, b) => {
      const rank = (t) => (t === "FIRE" ? 0 : t === "SMOKE" ? 1 : 2);
      return rank(a.type) - rank(b.type);
    });
  }, [cameras]);

  function incidentLabel(className) {
    const labels = {
      no_helmet: "Missing Helmet",
      no_vest: "Missing Safety Vest",
      no_gloves: "Missing Gloves",
      no_boots: "Missing Safety Boots",
      no_goggle: "Missing Goggles",
      no_goggles: "Missing Goggles",
    };

    return labels[className] ?? className.replace(/_/g, " ");
  }

  /*
   * =========================================================
   * LOCAL, OFFLINE ALERTING (browser tone + OS notification)
   *
   * Entirely client-side - no server round trip, no internet.
   * Works whether or not the backend's optional Twilio SMS is
   * configured, and fires the moment a NEW incident (one whose
   * signature we haven't already alerted on) appears in the
   * websocket stream.
   * =========================================================
   */

  const seenSignatures = useRef(new Set());

  useEffect(() => {
    requestNotificationPermission();
  }, []);

  useEffect(() => {
    const currentSignatures = new Set(incidents.map((i) => i.signature));

    const newIncidents = incidents.filter(
      (i) => !seenSignatures.current.has(i.signature)
    );

    if (newIncidents.length > 0) {
      playAlertTone();
      newIncidents.forEach(notifyIncident);
    }

    seenSignatures.current = currentSignatures;
  }, [incidents]);

  /*
   * =========================================================
   * RENDER
   *
   * Sidebar is a controlled nav (activePage/onNavigate) - each menu
   * item maps to one of the page blocks below. All the data-fetching
   * above runs regardless of which page is showing (so switching
   * pages never shows stale data), and several page blocks reuse the
   * exact same section JSX the single-page layout used to render
   * unconditionally (statusBar, statCardsBlock, incidentsSection,
   * workerComplianceSection, alertLogSection, hotspotsSection) - just
   * split apart so each page shows only what it's actually about.
   * =========================================================
   */

  const statusBar = (
    <div className="flex flex-wrap items-center gap-3 mb-6">
      <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2">
        <span
          className={`w-2 h-2 rounded-full ${
            backendOnline ? "bg-green-500" : "bg-red-500"
          }`}
        />
        <span className="text-xs text-zinc-300">
          Backend {backendOnline ? "Online" : "Offline"}
        </span>
      </div>

      <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2">
        {stats.onlineCameraCount > 0 ? (
          <Wifi size={13} className="text-green-400" />
        ) : (
          <WifiOff size={13} className="text-red-400" />
        )}
        <span className="text-xs text-zinc-300">
          {stats.onlineCameraCount} camera
          {stats.onlineCameraCount === 1 ? "" : "s"} online
        </span>
      </div>

      <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2">
        <span
          className={`w-2 h-2 rounded-full ${
            wsConnected ? "bg-green-500 animate-pulse" : "bg-yellow-500"
          }`}
        />
        <span className="text-xs text-zinc-300">
          AI Stream {wsConnected ? "Connected" : "Connecting"}
        </span>
      </div>

      <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2">
        <Activity size={13} className="text-teal-400" />
        <span className="text-xs text-zinc-300">
          {stats.avgFps > 0 ? `${stats.avgFps.toFixed(2)} FPS` : "-- FPS"}
        </span>
      </div>
    </div>
  );

  const statCardsBlock = (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-5 mb-8">
      <StatCard
        title="Workers Detected"
        value={stats.totalWorkers}
        subtitle="Across all cameras"
        icon={<Users />}
      />

      <StatCard
        title="PPE Compliant"
        value={`${stats.compliancePercent}%`}
        subtitle={
          stats.totalWorkers > 0
            ? `${stats.compliantWorkers}/${stats.totalWorkers} workers`
            : "No workers detected"
        }
        icon={<ShieldCheck />}
      />

      <StatCard
        title="PPE Violations"
        value={stats.ppeViolations}
        subtitle={
          stats.ppeViolations > 0 ? "Requires attention" : "No violations"
        }
        icon={<AlertTriangle />}
      />

      <StatCard
        title="Fire Events"
        value={stats.fireCount}
        subtitle={
          stats.fireCount > 0 ? "Critical incident" : "No fire detected"
        }
        icon={<Flame />}
      />

      <StatCard
        title="Smoke Events"
        value={stats.smokeCount}
        subtitle={
          stats.smokeCount > 0 ? "Under monitoring" : "No smoke detected"
        }
        icon={<Cloud />}
      />

      <StatCard
        title="Active Cameras"
        value={stats.onlineCameraCount}
        subtitle={`${cameras.length} configured`}
        icon={<Camera />}
      />
    </div>
  );

  const cameraGrid = (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
      {cameras.map((cam) => (
        <CameraCard key={cam.camera_id} snapshot={cam} />
      ))}
    </div>
  );

  const liveMonitoringHeader = (
    <div className="flex items-center justify-between mb-5">
      <div>
        <h2 className="text-lg font-display font-semibold text-white tracking-tight">
          Live Monitoring
        </h2>
        <p className="text-sm text-zinc-500">
          Real-time PPE, smoke and fire detection
        </p>
      </div>

      <div
        className={`flex items-center gap-2 text-xs ${
          wsConnected ? "text-green-400" : "text-yellow-400"
        }`}
      >
        <span
          className={`w-2 h-2 rounded-full ${
            wsConnected ? "bg-green-500 animate-pulse" : "bg-yellow-500"
          }`}
        />
        {wsConnected ? "AI MONITORING ACTIVE" : "CONNECTING TO AI"}
      </div>
    </div>
  );

  const incidentsSection = (
    <section>
      <div className="flex items-center gap-2 mb-4">
        <Bell size={19} className="text-red-400" />
        <div>
          <h2 className="text-lg font-display font-semibold text-white tracking-tight">
            Active Incidents
          </h2>
          <p className="text-sm text-zinc-500">Live safety events</p>
        </div>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
        {incidents.length === 0 ? (
          <div className="p-6 text-center">
            <ShieldCheck className="mx-auto text-green-400 mb-2" size={28} />
            <p className="text-sm text-green-400">No active incidents</p>
            <p className="text-xs text-zinc-500 mt-1">
              Site currently appears safe
            </p>
          </div>
        ) : (
          incidents.map((incident, index) => (
            <div
              key={incident.id}
              className={`p-4 ${
                index !== incidents.length - 1 ? "border-b border-zinc-800" : ""
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex gap-3">
                  <div className="mt-0.5 text-red-400">{incident.icon}</div>

                  <div>
                    <p className="font-semibold text-white">{incident.type}</p>

                    {incident.violation && (
                      <p className="text-xs text-yellow-400 mt-1">
                        {incident.violation}
                        {incident.inferred && (
                          <span className="text-zinc-500">
                            {" "}
                            (inferred - no vest visible)
                          </span>
                        )}
                      </p>
                    )}

                    <p className="text-sm text-zinc-400 mt-1">
                      {incident.camera} · {incident.location}
                    </p>
                  </div>
                </div>

                <div className="text-right">
                  <p className="text-xs font-semibold text-red-400">
                    {incident.severity}
                  </p>
                  <p className="text-xs text-zinc-500 mt-1">
                    {incident.confidence}
                  </p>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );

  const workerComplianceSection = (
    <section>
      <div className="flex items-center gap-2 mb-4">
        <MapPin size={19} className="text-teal-400" />
        <div>
          <h2 className="text-lg font-display font-semibold text-white tracking-tight">
            Worker Compliance
          </h2>
          <p className="text-sm text-zinc-500">Per-worker PPE status, this frame</p>
        </div>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden max-h-[285px] overflow-y-auto">
        {stats.totalWorkers === 0 ? (
          <div className="p-6 text-center">
            <Users className="mx-auto text-zinc-600 mb-2" size={28} />
            <p className="text-sm text-zinc-400">No workers currently detected</p>
          </div>
        ) : (
          cameras
            .filter((c) => c.online && c.workers.length > 0)
            .flatMap((cam) =>
              cam.workers.map((w) => (
                <div
                  key={`${cam.camera_id}-${w.worker_id}`}
                  className="px-5 py-3 border-b border-zinc-800 flex items-center justify-between"
                >
                  <div>
                    <p className="text-sm text-white">
                      {w.identity ? w.identity.name : `Worker #${w.worker_id + 1}`}{" "}
                      <span className="text-xs text-zinc-500">
                        ({cam.camera_id})
                      </span>
                    </p>
                    <p className="text-xs text-zinc-500 mt-1">
                      {Object.entries(w.items)
                        .map(([item, state]) => `${ITEM_LABELS[item] ?? item}: ${state}`)
                        .join(" · ")}
                    </p>
                  </div>

                  <span
                    className={`text-xs font-semibold px-2 py-1 rounded-lg ${
                      w.compliant
                        ? "bg-green-950/40 text-green-400"
                        : "bg-red-950/40 text-red-400"
                    }`}
                  >
                    {w.compliant ? "COMPLIANT" : "VIOLATION"}
                  </span>
                </div>
              ))
            )
        )}
      </div>
    </section>
  );

  const hotspotsSection = <ViolationHotspots zones={hotspotZones} />;

  const alertLogSection = (
    <section>
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <FileText size={19} className="text-purple-400" />
          <div>
            <h2 className="text-lg font-display font-semibold text-white tracking-tight">
              Alert Log
            </h2>
            <p className="text-sm text-zinc-500">
              Every alert that actually fired (cooldown-gated, timestamped,
              written to the local audit log)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
              sirenEnabled
                ? "bg-green-950/30 border-green-900 text-green-400"
                : "bg-zinc-800/50 border-zinc-700 text-zinc-500"
            }`}
          >
            <Volume2 size={12} />
            Siren {sirenEnabled ? "On" : "Off"}
          </span>

          <span
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
              voiceEnabled
                ? "bg-green-950/30 border-green-900 text-green-400"
                : "bg-zinc-800/50 border-zinc-700 text-zinc-500"
            }`}
          >
            <Mic size={12} />
            Voice Alert {voiceEnabled ? "On" : "Off"}
          </span>

          <span
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
              snapshotsEnabled
                ? "bg-green-950/30 border-green-900 text-green-400"
                : "bg-zinc-800/50 border-zinc-700 text-zinc-500"
            }`}
          >
            <CameraIcon size={12} />
            Snapshots {snapshotsEnabled ? "On" : "Off"}
          </span>

          <span
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
              smsEnabled
                ? "bg-green-950/30 border-green-900 text-green-400"
                : "bg-zinc-800/50 border-zinc-700 text-zinc-500"
            }`}
          >
            <Smartphone size={12} />
            SMS {smsEnabled ? "On" : "Off (offline mode)"}
          </span>

          <span
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
              gsmSmsEnabled
                ? "bg-green-950/30 border-green-900 text-green-400"
                : "bg-zinc-800/50 border-zinc-700 text-zinc-500"
            }`}
          >
            <Smartphone size={12} />
            Offline SMS (GSM) {gsmSmsEnabled ? "On" : "Off"}
          </span>

          <span
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
              faceIdAvailable
                ? "bg-green-950/30 border-green-900 text-green-400"
                : "bg-zinc-800/50 border-zinc-700 text-zinc-500"
            }`}
          >
            <CameraIcon size={12} />
            Face ID {faceIdAvailable ? "On" : "Off"}
          </span>

          <span
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
              fireSensorConnected
                ? "bg-green-950/30 border-green-900 text-green-400"
                : "bg-zinc-800/50 border-zinc-700 text-zinc-500"
            }`}
          >
            <Flame size={12} />
            Fire Sensor {fireSensorConnected ? "Connected" : "Off"}
          </span>

          {alertLog.length > 0 && (
            <button
              onClick={clearAlertLog}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-zinc-700 text-zinc-400 hover:text-red-400 hover:border-red-900 transition-colors"
            >
              <Trash2 size={12} />
              Clear
            </button>
          )}
        </div>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden max-h-[420px] overflow-y-auto">
        {alertLog.length === 0 ? (
          <div className="p-6 text-center">
            <ShieldCheck className="mx-auto text-green-400 mb-2" size={28} />
            <p className="text-sm text-green-400">No alerts have fired yet</p>
            <p className="text-xs text-zinc-500 mt-1">
              This log fills in the moment a violation or fire/smoke event
              passes the cooldown
            </p>
          </div>
        ) : (
          alertLog.map((alert, index) => {
            const isFire = alert.class_name === "fire";
            const isSmoke = alert.class_name === "smoke";

            const time = new Date(alert.timestamp);
            const timeLabel = Number.isNaN(time.getTime())
              ? alert.timestamp
              : time.toLocaleString(undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                  day: "2-digit",
                  month: "short",
                });

            return (
              <div
                key={`${alert.camera_id}-${alert.timestamp}-${index}`}
                className={`p-4 ${
                  index !== alertLog.length - 1 ? "border-b border-zinc-800" : ""
                }`}
              >
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div className="flex gap-3">
                    {alert.snapshot_url ? (
                      <a
                        href={`${API_BASE}${alert.snapshot_url}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="Open full-size violation snapshot"
                        className="shrink-0"
                      >
                        <img
                          src={`${API_BASE}${alert.snapshot_url}`}
                          alt={`Snapshot of ${alert.incident_type} on ${alert.camera_id}`}
                          className="w-20 h-14 object-cover rounded-lg border border-zinc-800 hover:border-teal-600 transition-colors"
                          loading="lazy"
                        />
                      </a>
                    ) : (
                      <div
                        className={`mt-0.5 ${
                          isFire
                            ? "text-red-400"
                            : isSmoke
                              ? "text-orange-400"
                              : "text-yellow-400"
                        }`}
                      >
                        {isFire ? (
                          <Flame size={18} />
                        ) : isSmoke ? (
                          <Cloud size={18} />
                        ) : (
                          <AlertTriangle size={18} />
                        )}
                      </div>
                    )}

                    <div>
                      <p className="font-semibold text-white flex items-center gap-2">
                        {alert.incident_type}
                        {alert.source === "inferred" && (
                          <span className="text-zinc-500 font-normal text-xs">
                            (inferred - no vest visible)
                          </span>
                        )}
                      </p>

                      <p className="text-sm text-zinc-400 mt-1 flex items-center gap-1.5 flex-wrap">
                        <MapPin size={12} />
                        {alert.camera_id} · {alert.location} · Zone {alert.zone}
                      </p>

                      {alert.identified_worker && (
                        <p className="text-xs text-teal-400 mt-1">
                          {alert.identified_worker.name}
                          {alert.identified_worker.department &&
                            ` · ${alert.identified_worker.department}`}
                        </p>
                      )}

                      {alert.sensor_note && (
                        <p className="text-xs text-zinc-500 mt-1">{alert.sensor_note}</p>
                      )}

                      <p className="text-xs text-zinc-500 mt-1 flex items-center gap-1.5">
                        <Clock size={12} />
                        {timeLabel}
                      </p>
                    </div>
                  </div>

                  <div className="text-right">
                    <p
                      className={`text-xs font-semibold ${
                        alert.severity === "CRITICAL"
                          ? "text-red-400"
                          : "text-yellow-400"
                      }`}
                    >
                      {alert.severity}
                    </p>
                    <p className="text-xs text-zinc-500 mt-1">
                      {alert.confidence_percent}% confidence
                    </p>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );

  // REPORTS - a downloadable combined log (backend's GET
  // /reports/export.csv), covering every PPE/fire/smoke incident AND
  // every recorded footage clip. Camera/zone-based, not per-worker -
  // face_id.py can attribute a live violation or a gate scan to a
  // NAMED worker (see the Alert Log above and the Entry Gate page),
  // but that attribution isn't folded into this CSV export yet.
  const reportsSection = (
    <section>
      <div className="flex items-center gap-2 mb-5">
        <Download size={19} className="text-teal-400" />
        <div>
          <h2 className="text-lg font-display font-semibold text-white tracking-tight">
            Reports
          </h2>
          <p className="text-sm text-zinc-500">
            Download a combined log of PPE violations, fire/smoke events and
            recorded footage
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-6">
        <StatCard
          title="PPE Violations"
          value={stats.ppeViolations}
          subtitle="This session"
          icon={<AlertTriangle />}
        />
        <StatCard
          title="Fire / Smoke Events"
          value={stats.fireCount + stats.smokeCount}
          subtitle="This session"
          icon={<Flame />}
        />
        <StatCard
          title="Footage Clips"
          value={footageStats?.total_segments ?? "--"}
          subtitle={
            footageStats
              ? `${(footageStats.total_bytes / (1024 ** 3)).toFixed(2)} GB stored`
              : "Loading..."
          }
          icon={<Video />}
        />
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6">
        <h3 className="text-sm font-semibold text-white mb-1">
          Full site log (CSV)
        </h3>
        <p className="text-xs text-zinc-500 mb-4 max-w-xl">
          One spreadsheet-ready file with every alert that fired (PPE
          violations, fire, smoke - camera, zone, severity, confidence,
          timestamp) and every recorded footage clip (camera, zone, time
          range, duration, which flags it carries, file size). Opens
          directly in Excel/Sheets/LibreOffice - no login needed to
          download it since it's read-only, same as the rest of this
          dashboard's data.
        </p>

        <a
          href={`${API_BASE}/reports/export.csv`}
          className="inline-flex items-center gap-2 bg-teal-600 hover:bg-teal-500 text-white text-sm font-medium rounded-xl px-4 py-2.5 transition-colors"
        >
          <Download size={15} />
          Download Full Log (CSV)
        </a>
      </div>
    </section>
  );

  const PAGE_META = {
    Dashboard: {
      title: "Dashboard",
      subtitle: "Site-wide overview - status, live coverage and open incidents",
      icon: LayoutDashboard,
    },
    "Live Monitoring": {
      title: "Live Monitoring",
      subtitle: "Watch every enabled camera's real-time AI feed",
      icon: Camera,
    },
    Violations: {
      title: "Violations",
      subtitle: "Active safety incidents and per-worker PPE compliance",
      icon: AlertTriangle,
    },
    Alerts: {
      title: "Alerts",
      subtitle: "Every alert that actually fired, with channel status",
      icon: Bell,
    },
    Footage: {
      title: "Footage",
      subtitle: "Continuous recorded coverage - browse, search, play back",
      icon: Video,
    },
    "Entry Gate": {
      title: "Entry Gate",
      subtitle: "Biometric (face) check + PPE compliance before entry",
      icon: ScanFace,
    },
    "Reports & Analytics": {
      title: "Reports & Analytics",
      subtitle: "Site-wide stats, zones ranked by violations, and a downloadable log",
      icon: BarChart3,
    },
    Settings: {
      title: "Settings",
      subtitle: "Manage cameras, watched items and hazard detection",
      icon: FileText,
    },
  };

  const meta = PAGE_META[activePage] ?? PAGE_META.Dashboard;
  const PageIcon = meta.icon;

  return (
    <div className="min-h-screen bg-zinc-950 flex">
      <Sidebar activePage={activePage} onNavigate={setActivePage} />

      <div className="flex-1">
        <Topbar onLogout={onLogout} />

        <main className="p-8">
          <div className="flex items-center gap-2 mb-6">
            <PageIcon size={20} className="text-teal-400" />
            <div>
              <h1 className="text-xl font-display font-semibold text-white tracking-tight">
                {meta.title}
              </h1>
              <p className="text-sm text-zinc-500">{meta.subtitle}</p>
            </div>
          </div>

          {activePage === "Dashboard" && (
            <>
              {statusBar}
              {statCardsBlock}
              {liveMonitoringHeader}
              {cameraGrid}
              <div className="mt-8">{incidentsSection}</div>
            </>
          )}

          {activePage === "Live Monitoring" && (
            <>
              {statusBar}
              {liveMonitoringHeader}
              <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-6 items-start">
                {cameraGrid}

                <DetectionSettingsSidebar
                  cameras={cameras}
                  watchableItems={watchableItems}
                  watchableExtraItems={watchableExtraItems}
                  watchableHazards={watchableHazards}
                  onToggleItem={handleToggleWatchedItem}
                  onToggleHazard={handleToggleWatchedHazard}
                  onToggleHeatmap={toggleHeatmap}
                  onUpdateCameraMeta={updateCameraMeta}
                  onToggleCameraEnabled={toggleCameraEnabled}
                  onDeleteCamera={deleteCamera}
                  onDiscoverCameras={discoverCameras}
                  onUploadVideo={uploadVideo}
                  onAddCamera={addCamera}
                  saving={savingWatchedItems}
                />
              </div>
            </>
          )}

          {activePage === "Violations" && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              {incidentsSection}
              {workerComplianceSection}
            </div>
          )}

          {activePage === "Alerts" && alertLogSection}

          {activePage === "Footage" && <FootagePanel cameras={cameras} />}

          {activePage === "Entry Gate" && <EntryGatePanel />}

          {activePage === "Reports & Analytics" && (
            <div className="space-y-8">
              {hotspotsSection}
              {reportsSection}
            </div>
          )}

          {activePage === "Settings" && (
            <div className="max-w-md">
              <DetectionSettingsSidebar
                cameras={cameras}
                watchableItems={watchableItems}
                watchableExtraItems={watchableExtraItems}
                watchableHazards={watchableHazards}
                onToggleItem={handleToggleWatchedItem}
                onToggleHazard={handleToggleWatchedHazard}
                onToggleHeatmap={toggleHeatmap}
                onUpdateCameraMeta={updateCameraMeta}
                onToggleCameraEnabled={toggleCameraEnabled}
                onDeleteCamera={deleteCamera}
                onDiscoverCameras={discoverCameras}
                onUploadVideo={uploadVideo}
                onAddCamera={addCamera}
                saving={savingWatchedItems}
              />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
