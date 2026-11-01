import { useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  Flame,
  Power,
  Trash2,
  Plus,
  X,
  RefreshCw,
  Upload,
  Thermometer,
} from "lucide-react";

const ITEM_LABELS = {
  helmet: "Helmet",
  vest: "Safety Vest",
  gloves: "Gloves",
  boots: "Safety Boots",
  goggles: "Goggles",
};

// "Other industry" gear - a second, separately-trained model (see
// backend/config.py's EXTRA_GEAR_MODEL_PATH), grouped under its own
// heading below rather than mixed into the PS06-required PPE pills.
const EXTRA_ITEM_LABELS = {
  mask: "Mask / Respirator",
  ear_protection: "Ear Protection",
  face_shield: "Face Shield",
};

const HAZARD_LABELS = {
  fire: "Fire",
  smoke: "Smoke",
};

/*
 * Click-to-edit text - shows plain text; clicking it turns it into an
 * input; Enter/blur commits (calling onSave with the trimmed value,
 * only if it actually changed), Escape cancels. Used for a camera's
 * location and zone name, both renameable in place.
 */
function EditableField({ value, onSave, className, placeholder }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={(e) => e.target.select()}
        onBlur={() => {
          const trimmed = draft.trim();
          setEditing(false);

          if (trimmed && trimmed !== value) {
            onSave(trimmed);
          } else {
            setDraft(value);
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.currentTarget.blur();
          } else if (e.key === "Escape") {
            setDraft(value);
            setEditing(false);
          }
        }}
        className="w-full bg-zinc-800 border border-teal-700 rounded px-1.5 py-0.5 text-white text-xs outline-none"
      />
    );
  }

  return (
    <button
      type="button"
      onClick={() => {
        setDraft(value);
        setEditing(true);
      }}
      title="Click to rename"
      className={`text-left hover:underline decoration-dotted underline-offset-2 truncate ${className ?? ""}`}
    >
      {value}
    </button>
  );
}

const SOURCE_MODES = [
  { key: "webcam", label: "Webcam" },
  { key: "file", label: "Video File" },
  { key: "rtsp", label: "RTSP Stream" },
];

/*
 * Collapsed by default: a dashed "+ Add Camera" button. Expands into
 * a small form (ID, source, location, zone, enable-immediately) that
 * POSTs /cameras via onAdd - so new physical cameras can be added as
 * they're deployed, no cameras.json hand-editing or backend restart.
 *
 * The source field has 3 modes. "Webcam" is the automatic one: it
 * calls onDiscoverCameras (GET /cameras/discover) to probe local
 * webcam indexes and offers a dropdown - no need to find the index
 * on the terminal first. "Video File" offers an actual upload button
 * (onUploadVideo - POST /cameras/upload-video) alongside a plain text
 * field for a path to a file already on this machine; "RTSP Stream"
 * is just the plain text field, since there's no "index"/file to
 * pick for a stream URL.
 */
function AddCameraForm({ onAdd, onDiscoverCameras, onUploadVideo, saving }) {
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [sourceMode, setSourceMode] = useState("webcam");
  const [source, setSource] = useState("");
  const [location, setLocation] = useState("");
  const [zone, setZone] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [discovered, setDiscovered] = useState([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState("");

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadedFilename, setUploadedFilename] = useState("");

  const reset = () => {
    setId("");
    setSourceMode("webcam");
    setSource("");
    setLocation("");
    setZone("");
    setEnabled(true);
    setError("");
    setDiscovered([]);
    setDiscoverError("");
    setUploading(false);
    setUploadError("");
    setUploadedFilename("");
  };

  const scan = async () => {
    setDiscovering(true);
    setDiscoverError("");

    const result = await onDiscoverCameras();

    setDiscovering(false);

    if (result?.error) {
      setDiscoverError(result.error);
      return;
    }

    const found = result?.cameras ?? [];
    setDiscovered(found);

    setSource((prev) => {
      if (prev) return prev;
      const firstAvailable = found.find((cam) => cam.available);
      return firstAvailable ? String(firstAvailable.index) : prev;
    });
  };

  const openForm = () => {
    setOpen(true);
    scan();
  };

  const handleFileUpload = async (file) => {
    if (!file) return;

    setUploading(true);
    setUploadError("");

    const result = await onUploadVideo(file);

    setUploading(false);

    if (result?.error) {
      setUploadError(result.error);
      return;
    }

    setSource(result.path);
    setUploadedFilename(result.filename);
  };

  const submit = async (e) => {
    e.preventDefault();

    if (!id.trim() || !source.trim() || !location.trim() || !zone.trim()) {
      setError(
        sourceMode === "webcam" && !source.trim()
          ? "Scan for cameras and pick one, or switch to Video File / RTSP Stream."
          : "All fields are required."
      );
      return;
    }

    setError("");
    setSubmitting(true);

    const result = await onAdd({
      id: id.trim(),
      source: source.trim(),
      location: location.trim(),
      zone: zone.trim(),
      enabled,
    });

    setSubmitting(false);

    if (result?.error) {
      setError(result.error);
      return;
    }

    reset();
    setOpen(false);
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={openForm}
        className="w-full flex items-center justify-center gap-1.5 text-xs font-medium text-teal-400 border border-dashed border-zinc-700 hover:border-teal-700 rounded-xl py-2.5 transition-colors"
      >
        <Plus size={13} />
        Add Camera
      </button>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="border border-zinc-800 rounded-xl p-3.5 space-y-2.5"
    >
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-white">New Camera</p>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            reset();
          }}
          className="text-zinc-500 hover:text-white"
        >
          <X size={14} />
        </button>
      </div>

      <input
        value={id}
        onChange={(e) => setId(e.target.value)}
        placeholder="Camera ID (e.g. CAM-05)"
        className="w-full text-xs bg-zinc-800/60 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-white placeholder:text-zinc-600 outline-none focus:border-teal-700"
      />

      <div className="flex gap-1.5">
        {SOURCE_MODES.map((mode) => (
          <button
            key={mode.key}
            type="button"
            onClick={() => {
              setSourceMode(mode.key);
              setSource("");
              setUploadError("");
              setUploadedFilename("");
            }}
            className={`flex-1 text-[11px] font-medium px-2 py-1.5 rounded-lg border transition-colors ${
              sourceMode === mode.key
                ? "bg-teal-950/40 border-teal-800 text-teal-300"
                : "bg-zinc-800/40 border-zinc-700 text-zinc-400 hover:border-zinc-600"
            }`}
          >
            {mode.label}
          </button>
        ))}
      </div>

      {sourceMode === "webcam" ? (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <select
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="flex-1 min-w-0 text-xs bg-zinc-800/60 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-white outline-none focus:border-teal-700"
            >
              <option value="" disabled>
                {discovering
                  ? "Scanning…"
                  : discovered.length === 0
                    ? "No webcams found yet"
                    : "Select a detected webcam"}
              </option>

              {discovered.map((cam) => (
                <option
                  key={cam.index}
                  value={cam.index}
                  disabled={!cam.available}
                >
                  {cam.index} — {cam.name}
                  {!cam.available ? ` (in use by ${cam.in_use_by})` : ""}
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={scan}
              disabled={discovering}
              title="Scan for connected webcams"
              className="shrink-0 flex items-center gap-1 text-[11px] font-medium text-teal-400 hover:text-teal-300 disabled:opacity-50 border border-zinc-700 hover:border-teal-700 rounded-lg px-2 py-1.5 transition-colors"
            >
              <RefreshCw size={11} className={discovering ? "animate-spin" : ""} />
              Scan
            </button>
          </div>

          {discoverError && (
            <p className="text-[11px] text-red-400">{discoverError}</p>
          )}

          {!discovering && !discoverError && discovered.length === 0 && (
            <p className="text-[11px] text-zinc-500">
              No webcams detected. Try Scan again, or switch to Video File /
              RTSP Stream above.
            </p>
          )}
        </div>
      ) : sourceMode === "file" ? (
        <div className="space-y-1.5">
          <label
            className={`flex items-center justify-center gap-1.5 text-[11px] font-medium border border-dashed rounded-lg py-2 cursor-pointer transition-colors ${
              uploading
                ? "opacity-50 cursor-not-allowed border-zinc-700 text-zinc-500"
                : uploadedFilename
                  ? "border-teal-800 text-teal-300 bg-teal-950/30"
                  : "border-zinc-700 text-teal-400 hover:border-teal-700"
            }`}
          >
            <Upload size={12} className={uploading ? "animate-pulse" : ""} />
            {uploading
              ? "Uploading…"
              : uploadedFilename
                ? `Uploaded: ${uploadedFilename}`
                : "Upload a video file"}
            <input
              type="file"
              accept="video/*"
              disabled={uploading}
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = ""; // allow re-selecting the same file
                handleFileUpload(file);
              }}
            />
          </label>

          {uploadError && (
            <p className="flex items-start gap-1.5 text-[11px] text-red-400">
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              {uploadError}
            </p>
          )}

          <p className="text-[11px] text-zinc-500">
            or type a path to a video already on this machine:
          </p>

          <input
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setUploadedFilename("");
            }}
            placeholder="Video file path (e.g. path/to/video.mp4)"
            className="w-full text-xs bg-zinc-800/60 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-white placeholder:text-zinc-600 outline-none focus:border-teal-700"
          />
        </div>
      ) : (
        <input
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder="RTSP/HTTP stream URL (e.g. rtsp://user:pass@host:554/stream1)"
          className="w-full text-xs bg-zinc-800/60 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-white placeholder:text-zinc-600 outline-none focus:border-teal-700"
        />
      )}

      <input
        value={location}
        onChange={(e) => setLocation(e.target.value)}
        placeholder="Location (e.g. North Entrance)"
        className="w-full text-xs bg-zinc-800/60 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-white placeholder:text-zinc-600 outline-none focus:border-teal-700"
      />

      <input
        value={zone}
        onChange={(e) => setZone(e.target.value)}
        placeholder="Zone name (e.g. Zone E)"
        className="w-full text-xs bg-zinc-800/60 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-white placeholder:text-zinc-600 outline-none focus:border-teal-700"
      />

      <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="w-3.5 h-3.5 accent-teal-600 cursor-pointer"
        />
        Enable immediately
      </label>

      {error && (
        <p className="flex items-start gap-1.5 text-[11px] text-red-400">
          <AlertTriangle size={12} className="shrink-0 mt-0.5" />
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting || saving}
        className="w-full text-xs font-semibold bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white rounded-lg py-2 transition-colors"
      >
        {submitting ? "Adding…" : "Add Camera"}
      </button>
    </form>
  );
}

/*
 * Persistent, always-visible camera + detection settings panel - NOT
 * a popup/drawer you have to open per camera. Every configured camera
 * is listed here at once:
 *   - location + zone name are click-to-edit (EditableField) - PUT
 *     /cameras/{id}, live + persisted, so a camera redeployed to a
 *     different area can be relabeled without touching cameras.json
 *   - a power button enables/disables the camera (PUT /cameras/{id}
 *     /enabled), a thermometer button toggles that camera's own live
 *     violation-hotspot heatmap overlay (PUT /cameras/{id}/heatmap -
 *     see backend/cameras.py's CameraWorker._blend_heatmap), and a
 *     trash button removes it entirely (DELETE /cameras/{id})
 *   - PPE-item and hazard pills toggle detection for that camera only
 *     (PUT /cameras/{id}/watched-items|watched-hazards)
 * "+ Add Camera" at the bottom POSTs /cameras to grow the roster as
 * new physical cameras get deployed - no backend restart needed.
 *
 * `cameras` is Dashboard's merged `cameras` useMemo; `watchableItems`
 * /`watchableExtraItems`/`watchableHazards` are the full menus every
 * camera's pills are drawn from (GET /cameras' watchable_ppe_items/
 * watchable_extra_gear_items/watchable_hazard_items). watchableExtraItems
 * ("other industry" gear - mask, ear protection, face shield, from a
 * second model) renders as its own pill group per camera, same
 * onToggleItem handler - the backend treats them as ordinary
 * watched_items entries either way.
 */
export default function DetectionSettingsSidebar({
  cameras,
  watchableItems,
  watchableExtraItems,
  watchableHazards,
  onToggleItem,
  onToggleHazard,
  onToggleHeatmap,
  onUpdateCameraMeta,
  onToggleCameraEnabled,
  onDeleteCamera,
  onAddCamera,
  onDiscoverCameras,
  onUploadVideo,
  saving,
}) {
  return (
    <aside className="xl:sticky xl:top-8">
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 max-h-[calc(100vh-4rem)] overflow-y-auto">
        <div className="flex items-center gap-2">
          <ShieldCheck size={18} className="text-teal-400" />
          <h2 className="text-base font-display font-semibold text-white tracking-tight">
            Camera &amp; Detection Settings
          </h2>
        </div>

        <p className="text-xs text-zinc-500 mt-1 mb-5 leading-relaxed">
          Click a location or zone name to rename it. Click a PPE/hazard
          item to turn detection on/off for that camera. Everything below
          applies immediately - no restart needed.
        </p>

        {cameras.length === 0 ? (
          <p className="text-sm text-zinc-500 mb-5">
            No cameras configured yet.
          </p>
        ) : (
          <div className="space-y-5 mb-5">
            {cameras.map((camera) => {
              const watched = camera.watchedItems ?? [];
              const watchedHazards = camera.watchedHazards ?? [];
              const enabled = camera.enabled !== false;
              const showHeatmap = Boolean(camera.showHeatmap);

              return (
                <div
                  key={camera.camera_id}
                  className="pb-5 border-b border-zinc-800 last:border-b-0 last:pb-0"
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-sm font-semibold text-white truncate">
                          {camera.camera_id}
                        </p>
                        <span
                          className={`shrink-0 text-[9px] font-semibold px-1.5 py-0.5 rounded ${
                            enabled
                              ? "bg-green-950/50 text-green-400"
                              : "bg-zinc-800 text-zinc-500"
                          }`}
                        >
                          {enabled ? "ON" : "OFF"}
                        </span>
                      </div>

                      <EditableField
                        value={camera.location}
                        placeholder="Location"
                        onSave={(v) =>
                          onUpdateCameraMeta(camera.camera_id, { location: v })
                        }
                        className="block text-xs text-zinc-400 mt-0.5"
                      />

                      <EditableField
                        value={camera.zone}
                        placeholder="Zone name"
                        onSave={(v) =>
                          onUpdateCameraMeta(camera.camera_id, { zone: v })
                        }
                        className="block text-xs text-zinc-500"
                      />
                    </div>

                    <div className="flex items-center gap-0.5 shrink-0">
                      <button
                        type="button"
                        title={enabled ? "Disable camera" : "Enable camera"}
                        onClick={() =>
                          onToggleCameraEnabled(camera.camera_id, !enabled)
                        }
                        className={`p-1.5 rounded-lg transition-colors ${
                          enabled
                            ? "text-green-400 hover:bg-green-950/40"
                            : "text-zinc-500 hover:bg-zinc-800"
                        }`}
                      >
                        <Power size={13} />
                      </button>

                      <button
                        type="button"
                        title={
                          showHeatmap
                            ? "Hide violation hotspot overlay on this feed"
                            : "Show violation hotspot overlay on this feed"
                        }
                        onClick={() =>
                          onToggleHeatmap(camera.camera_id, !showHeatmap)
                        }
                        className={`p-1.5 rounded-lg transition-colors ${
                          showHeatmap
                            ? "text-amber-400 bg-amber-950/30 hover:bg-amber-950/50"
                            : "text-zinc-500 hover:bg-zinc-800"
                        }`}
                      >
                        <Thermometer size={13} />
                      </button>

                      <button
                        type="button"
                        title="Delete camera"
                        onClick={() => onDeleteCamera(camera.camera_id)}
                        className="p-1.5 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-red-950/30 transition-colors"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>

                  <p className="text-[10px] uppercase tracking-wide text-zinc-500 font-semibold mb-2">
                    PPE Items
                  </p>

                  <div className="flex flex-wrap gap-1.5 mb-1">
                    {watchableItems.map((item) => {
                      const checked = watched.includes(item);

                      return (
                        <button
                          key={item}
                          type="button"
                          disabled={saving}
                          onClick={() =>
                            onToggleItem(camera.camera_id, item, !checked)
                          }
                          className={`text-xs font-medium px-2.5 py-1.5 rounded-full border transition-colors disabled:opacity-50 ${
                            checked
                              ? "bg-teal-950/40 border-teal-800 text-teal-300"
                              : "bg-zinc-800/40 border-zinc-700 text-zinc-400 hover:border-zinc-600"
                          }`}
                        >
                          {ITEM_LABELS[item] ?? item}
                        </button>
                      );
                    })}
                  </div>

                  {watched.length === 0 && (
                    <p className="flex items-start gap-1.5 text-[11px] text-amber-400 mt-1.5 mb-3">
                      <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                      No PPE items selected - counts workers only, flags no
                      violations.
                    </p>
                  )}

                  {watchableExtraItems?.length > 0 && (
                    <>
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500 font-semibold mb-2 mt-3">
                        Other Industry Gear
                      </p>

                      <div className="flex flex-wrap gap-1.5">
                        {watchableExtraItems.map((item) => {
                          const checked = watched.includes(item);

                          return (
                            <button
                              key={item}
                              type="button"
                              disabled={saving}
                              onClick={() =>
                                onToggleItem(camera.camera_id, item, !checked)
                              }
                              className={`text-xs font-medium px-2.5 py-1.5 rounded-full border transition-colors disabled:opacity-50 ${
                                checked
                                  ? "bg-amber-950/40 border-amber-800 text-amber-300"
                                  : "bg-zinc-800/40 border-zinc-700 text-zinc-400 hover:border-zinc-600"
                              }`}
                            >
                              {EXTRA_ITEM_LABELS[item] ?? item}
                            </button>
                          );
                        })}
                      </div>
                    </>
                  )}

                  <p className="text-[10px] uppercase tracking-wide text-zinc-500 font-semibold mb-2 mt-3">
                    Hazard Detection
                  </p>

                  <div className="flex flex-wrap gap-1.5">
                    {watchableHazards.map((hazard) => {
                      const checked = watchedHazards.includes(hazard);

                      return (
                        <button
                          key={hazard}
                          type="button"
                          disabled={saving}
                          onClick={() =>
                            onToggleHazard(camera.camera_id, hazard, !checked)
                          }
                          className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-full border transition-colors disabled:opacity-50 ${
                            checked
                              ? "bg-red-950/40 border-red-800 text-red-300"
                              : "bg-zinc-800/40 border-zinc-700 text-zinc-400 hover:border-zinc-600"
                          }`}
                        >
                          <Flame size={11} />
                          {HAZARD_LABELS[hazard] ?? hazard}
                        </button>
                      );
                    })}
                  </div>

                  {watchedHazards.length === 0 && (
                    <p className="flex items-start gap-1.5 text-[11px] text-amber-400 mt-1.5">
                      <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                      Neither fire nor smoke selected for this camera.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <AddCameraForm
          onAdd={onAddCamera}
          onDiscoverCameras={onDiscoverCameras}
          onUploadVideo={onUploadVideo}
          saving={saving}
        />

        {saving && <p className="text-xs text-teal-400 mt-4">Saving…</p>}
      </div>
    </aside>
  );
}
