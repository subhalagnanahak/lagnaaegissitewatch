# ============================================================
# AEGIS SITEWATCH BACKEND
# ============================================================
#
# FastAPI + OpenCV + YOLO11 (PPE + Fire/Smoke), fully offline by
# default - see README.md's "Runs fully offline" section.
#
# Rebuilt to close the gaps found in the first version:
#   - No internet required: local siren + local alert log are the
#     primary alert channels (alerts.py); Twilio SMS is optional,
#     off unless backend/.env has real credentials, and Ultralytics'
#     auto-install/telemetry are explicitly disabled below
#   - Per-WORKER PPE compliance instead of a whole-frame estimate,
#     including an honest fix for the untrained "no_vest" class
#     (ppe_logic.py - see its module docstring)
#   - Multiple cameras (webcam / recorded video file / RTSP CCTV
#     stream, per cameras.json), each with its own MJPEG feed
#   - CORS no longer combines allow_origins="*" with credentials
#
# ============================================================

import os

os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"
os.environ["NUMEXPR_NUM_THREADS"] = "4"

# ============================================================
# FULLY OFFLINE - no auto-install of missing packages (would need
# PyPI) and no phoning home to Ultralytics HUB for analytics/update
# checks. Both are no-ops here anyway since we pass real local
# weight files, but this stops YOLO() from ever attempting a
# network call, so startup never hangs or fails on a machine with
# no internet at all.
# ============================================================

os.environ["YOLO_AUTOINSTALL"] = "False"
os.environ["YOLO_OFFLINE"] = "True"

import asyncio
import csv
import io
import secrets
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import List, Optional, Union

import cv2
import numpy as np
from fastapi import (
    Depends, FastAPI, File, Form, Header, HTTPException, UploadFile,
    WebSocket, WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ultralytics import YOLO

try:
    from ultralytics import settings as yolo_settings
    yolo_settings.update({"sync": False})
except Exception:
    pass

import config
import alerts
import recording
import face_id
import gate
import gate_camera
import fire_sensor
import gsm_sms
from cameras import CameraManager, discover_cameras


# ============================================================
# LOAD MODELS
# ============================================================

print()
print("=" * 60)
print("Loading PPE model...")
print("=" * 60)

if not os.path.exists(config.PPE_MODEL_PATH):
    raise FileNotFoundError(f"PPE model not found:\n{config.PPE_MODEL_PATH}")

ppe_model = YOLO(config.PPE_MODEL_PATH)

print("PPE model loaded. Classes:", ppe_model.names)

fire_model = None

if config.FIRE_MODEL_PATH and os.path.exists(config.FIRE_MODEL_PATH):

    print()
    print("=" * 60)
    print("Loading Fire / Smoke model...")
    print("=" * 60)

    fire_model = YOLO(config.FIRE_MODEL_PATH)

    print("Fire model loaded. Classes:", fire_model.names)

else:

    print()
    print("Fire model not configured. PPE detection will continue.")

extra_gear_model = None

if config.EXTRA_GEAR_MODEL_PATH and os.path.exists(config.EXTRA_GEAR_MODEL_PATH):

    print()
    print("=" * 60)
    print("Loading extra-gear (other-industry PPE) model...")
    print("=" * 60)

    extra_gear_model = YOLO(config.EXTRA_GEAR_MODEL_PATH)

    print("Extra-gear model loaded. Classes:", extra_gear_model.names)

else:

    print()
    print("Extra-gear model not configured (extra_gear_detection/weights/"
          "sh17_yolov8n.pt missing). Core PPE/fire detection will continue.")


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Aegis SiteWatch Backend", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # The dashboard never sends cookies/credentials, so this stays
    # False. allow_origins=["*"] + allow_credentials=True is invalid
    # per the CORS spec and browsers ignore the wildcard in that case.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

camera_manager = CameraManager(ppe_model, fire_model, extra_gear_model)

# Violation snapshots ("surveillance" storage - see alerts.py) served
# straight off local disk, no cloud storage involved. The directory is
# created in config.py at import time, so it always exists by here.
app.mount(
    "/snapshots",
    StaticFiles(directory=config.SNAPSHOTS_DIR),
    name="snapshots",
)

# Continuous footage clips (recording.py) - same local-disk-only
# pattern as /snapshots above. Unauthenticated like /snapshots and
# /video_feed: a <video src="..."> tag can't attach an Authorization
# header, and the dashboard itself is already gated behind admin
# login (see ADMIN AUTH below) before it ever renders a link here.
app.mount(
    "/footage_media",
    StaticFiles(directory=config.FOOTAGE_DIR),
    name="footage_media",
)


# ============================================================
# ADMIN AUTH
# ============================================================
#
# A single shared admin account (config.ADMIN_USERNAME/PASSWORD)
# gates the dashboard itself - see App.jsx, which shows nothing but
# the login screen until POST /admin/login succeeds - AND every
# camera-management / settings / delete endpoint below, via the
# require_admin dependency, so those can't be driven from the API
# directly by someone who never logged in either. Session tokens are
# just an in-memory dict (no external session store needed for a
# single-admin, single-process deployment like this one) - restarting
# the backend invalidates every session, which is fine; the dashboard
# just shows the login screen again.
# ============================================================

_admin_sessions = {}  # token -> expires_at (unix time)
_admin_sessions_lock = threading.Lock()


class AdminLoginPayload(BaseModel):
    username: str
    password: str


def _issue_token():

    token = secrets.token_urlsafe(32)
    expires_at = time.time() + config.ADMIN_SESSION_TIMEOUT_SECONDS

    with _admin_sessions_lock:
        _admin_sessions[token] = expires_at

    return token, expires_at


def _token_valid(token):

    with _admin_sessions_lock:

        expires_at = _admin_sessions.get(token)

        if expires_at is None:
            return False

        if time.time() >= expires_at:
            del _admin_sessions[token]
            return False

        return True


def require_admin(authorization: Optional[str] = Header(None)):
    """FastAPI dependency - add `_admin: None = Depends(require_admin)`
    to any endpoint that should require a logged-in admin session.
    Expects `Authorization: Bearer <token>` (exactly what the
    dashboard's authFetch helper sends once logged in)."""

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization[len("Bearer "):]

    if not _token_valid(token):
        raise HTTPException(status_code=401, detail="Session expired or invalid - please log in again")


@app.post("/admin/login")
def admin_login(payload: AdminLoginPayload):

    valid = (
        secrets.compare_digest(payload.username, config.ADMIN_USERNAME)
        and secrets.compare_digest(payload.password, config.ADMIN_PASSWORD)
    )

    if not valid:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token, expires_at = _issue_token()

    return {"status": "ok", "token": token, "expires_at": expires_at}


@app.get("/admin/verify")
def admin_verify(authorization: Optional[str] = Header(None)):
    """Lets the dashboard check a token it already has (e.g. from
    localStorage on page load) without re-submitting the password -
    returns 401 (caught by the frontend, which then shows the login
    screen again) if it's missing, expired, or unknown."""

    require_admin(authorization)

    return {"status": "ok"}


@app.post("/admin/logout")
def admin_logout(authorization: Optional[str] = Header(None)):

    if authorization and authorization.startswith("Bearer "):

        token = authorization[len("Bearer "):]

        with _admin_sessions_lock:
            _admin_sessions.pop(token, None)

    return {"status": "ok"}


# ============================================================
# STARTUP / SHUTDOWN
# ============================================================

@app.on_event("startup")
def startup_event():

    print()
    print("=" * 60)
    print("AEGIS SITEWATCH - CPU MODE")
    print("=" * 60)

    camera_manager.start_all()
    fire_sensor.start()
    gate_camera.start(ppe_model)

    face_status = face_id.status()

    print()
    print("Backend ready.")
    print(f"PPE  confidence/imgsz: {config.PPE_CONFIDENCE} / {config.PPE_INFERENCE_SIZE}")
    print(f"Fire confidence/imgsz: {config.FIRE_CONFIDENCE} / {config.FIRE_INFERENCE_SIZE}")
    print(f"PPE frame skip: {config.PPE_FRAME_SKIP}")
    print(f"Fire frame skip: {config.FIRE_FRAME_SKIP}")
    print(f"Extra-gear model loaded: {extra_gear_model is not None}")
    print(f"Alert cooldown: {config.ALERT_COOLDOWN}s")
    print(f"Local siren: {config.LOCAL_SIREN_ENABLED}")
    print(f"Local alert log: {config.LOCAL_ALERT_LOG_PATH}")
    print(f"SMS enabled (optional, needs internet): {alerts.twilio_client is not None}")
    print(f"Offline GSM SMS enabled (optional, needs hardware): {gsm_sms.gsm_available}")
    print(f"Face ID available: {face_status['available']}"
          + (f" ({face_status['reason']})" if not face_status["available"] else ""))
    print(f"Entry gate required PPE items: {sorted(config.GATE_REQUIRED_PPE_ITEMS)}")
    print(f"Gate camera (DVR/RTSP): {'configured' if config.GATE_CAMERA_SOURCE else 'not configured'}"
          + (f", auto-scan={config.GATE_CAMERA_AUTO_SCAN}" if config.GATE_CAMERA_SOURCE else ""))
    print(f"Fire/smoke sensor fusion enabled: {config.FIRE_SENSOR_ENABLED}")
    print(f"Cameras enabled: {list(camera_manager.workers.keys())}")
    print(f"Recording enabled: {config.RECORDING_ENABLED} (dir: {config.FOOTAGE_DIR})")
    print(f"Admin login: username='{config.ADMIN_USERNAME}' "
          f"(set ADMIN_USERNAME/ADMIN_PASSWORD in .env before real deployment "
          f"if you haven't already)")


@app.on_event("shutdown")
def shutdown_event():

    print("Stopping backend...")
    camera_manager.stop_all()
    fire_sensor.stop()
    gate_camera.stop()
    print("Backend stopped.")


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
def root():

    return {
        "service": "Aegis SiteWatch Backend",
        "status": "running",
        "mode": "CPU",
        "offline": True,
        "cameras": camera_manager.camera_list(),
        "local_siren": config.LOCAL_SIREN_ENABLED,
        "voice_alerts": config.VOICE_ALERTS_ENABLED,
        "snapshots_enabled": config.SNAPSHOT_ENABLED,
        "sms_alerts": alerts.twilio_client is not None,
        "extra_gear_model_loaded": extra_gear_model is not None,
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
        "cameras": camera_manager.camera_list(),
        "ppe_model": config.PPE_MODEL_PATH,
        "fire_model_loaded": fire_model is not None,
        "extra_gear_model_loaded": extra_gear_model is not None,
        "mode": "CPU",
        "ppe_confidence": config.PPE_CONFIDENCE,
        "ppe_inference_size": config.PPE_INFERENCE_SIZE,
        "fire_confidence": config.FIRE_CONFIDENCE,
        "fire_inference_size": config.FIRE_INFERENCE_SIZE,
        "local_siren": config.LOCAL_SIREN_ENABLED,
        "voice_alerts": config.VOICE_ALERTS_ENABLED,
        "snapshots_enabled": config.SNAPSHOT_ENABLED,
        "sms_alerts": alerts.twilio_client is not None,
        "gsm_sms_alerts": gsm_sms.gsm_available,
        "alert_cooldown": config.ALERT_COOLDOWN,
        "recording_enabled": config.RECORDING_ENABLED,
        "face_id": face_id.status(),
        "fire_sensor": fire_sensor.status(),
        "gate_camera": gate_camera.status(),
        "gate_required_items": sorted(config.GATE_REQUIRED_PPE_ITEMS),
    }


# ============================================================
# CAMERAS
# ============================================================

@app.get("/cameras")
def list_cameras():

    return {
        "cameras": camera_manager.camera_list(),
        # The full menu a camera's watched_items/watched_hazards can
        # be picked from - sent alongside the list so the dashboard's
        # per-camera settings drawer never has to hardcode either.
        "watchable_ppe_items": config.WATCHABLE_PPE_ITEMS,
        "watchable_extra_gear_items": config.WATCHABLE_EXTRA_GEAR_ITEMS,
        "watchable_hazard_items": config.WATCHABLE_HAZARD_ITEMS,
    }


@app.get("/cameras/discover")
def discover_camera_sources(max_index: int = 10):
    """Probes local webcam indexes so the dashboard's Add Camera form
    can offer a pick list instead of making the user find the index
    on the terminal themselves (only webcam indexes can be
    auto-discovered this way - an RTSP URL still has to be typed in,
    and a video file can either be typed in as a path or uploaded via
    POST /cameras/upload-video below, there's no 'index' to scan for
    either way)."""

    return {"cameras": discover_cameras(max_index=max_index)}


@app.post("/cameras/upload-video")
async def upload_video(file: UploadFile = File(...), _admin: None = Depends(require_admin)):
    """Accepts a recorded video uploaded straight from the dashboard's
    Add Camera form - the OTHER way to add a video-file camera source,
    alongside typing in a path to a file already on this machine.
    Saves it under config.UPLOADED_VIDEOS_DIR and returns the
    server-side path; the dashboard then uses that path exactly like
    any typed-in one in a normal POST /cameras call. Two separate
    steps client-side (upload, then add-camera), but from here on out
    it's just an ordinary video-file source - nothing about camera
    handling itself changes."""

    original_name = file.filename or "upload"
    ext = os.path.splitext(original_name)[1].lower()

    if ext not in config.ALLOWED_VIDEO_EXTENSIONS:
        return {
            "error": f"Unsupported video type '{ext or '(none)'}'. "
                     f"Allowed: {sorted(config.ALLOWED_VIDEO_EXTENSIONS)}",
        }

    # Strip to a safe filename - no path separators, no directory
    # traversal via a crafted "../../" original name - and make it
    # unique so two uploads with the same original name never collide
    # or silently overwrite each other.
    safe_stem = "".join(
        c for c in os.path.splitext(original_name)[0] if c.isalnum() or c in "-_"
    )[:60] or "video"

    filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}{ext}"
    dest_path = os.path.join(config.UPLOADED_VIDEOS_DIR, filename)

    written = 0
    chunk_size = 1024 * 1024  # 1MB - streamed to disk, never the
                              # whole file held in memory at once

    try:

        with open(dest_path, "wb") as out:

            while True:

                chunk = await file.read(chunk_size)

                if not chunk:
                    break

                written += len(chunk)

                if written > config.MAX_UPLOAD_VIDEO_BYTES:
                    raise ValueError("upload_too_large")

                out.write(chunk)

    except ValueError:

        _remove_quietly(dest_path)
        limit_mb = config.MAX_UPLOAD_VIDEO_BYTES // (1024 * 1024)
        return {"error": f"Video exceeds the {limit_mb}MB upload limit."}

    except Exception as e:

        _remove_quietly(dest_path)
        return {"error": f"Upload failed: {e}"}

    finally:

        await file.close()

    # Confirm cv2 can actually open what was just saved - an upload
    # that LOOKS like a video but this build of OpenCV/ffmpeg can't
    # decode would otherwise only fail later, deep inside a camera
    # worker thread with a much less clear error.
    probe = cv2.VideoCapture(dest_path)
    opens = probe.isOpened()
    read_ok = False

    if opens:
        read_ok, _ = probe.read()

    probe.release()

    if not (opens and read_ok):

        _remove_quietly(dest_path)
        return {
            "error": "The file was uploaded but couldn't be opened as a "
                     "video - likely an unsupported codec/container for "
                     "this build of OpenCV. Try re-exporting it as a "
                     "standard H.264 .mp4.",
        }

    return {
        "status": "ok",
        "path": dest_path,
        "filename": filename,
        "bytes": written,
    }


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


class NewCameraPayload(BaseModel):
    id: str = ""
    # A webcam index (0, 1, 2...), a recorded video file path, or an
    # RTSP/HTTP CCTV stream URL - cv2.VideoCapture handles all three,
    # see cameras.py's module docstring. Sent as a string from a form
    # field and normalized to int below if it's a bare number.
    source: Union[int, str] = ""
    location: str = ""
    zone: str = ""
    enabled: bool = True


@app.post("/cameras")
def add_camera(payload: NewCameraPayload, _admin: None = Depends(require_admin)):
    """Adds a brand-new camera - no backend restart needed, no
    hand-editing cameras.json required. Lets the dashboard's 'Add
    Camera' form grow the site's camera roster as new physical
    cameras get deployed."""

    camera_id = payload.id.strip()

    if not camera_id:
        return {"error": "Camera ID is required."}

    location = payload.location.strip()
    zone = payload.zone.strip()

    if not location:
        return {"error": "Location is required."}

    if not zone:
        return {"error": "Zone is required."}

    source = payload.source

    if isinstance(source, str):

        source = source.strip()

        if not source:
            return {
                "error": "Camera source is required - a webcam index "
                         "(e.g. 0), a recorded video file path, or an "
                         "RTSP/HTTP stream URL.",
            }

        if source.lstrip("-").isdigit():
            source = int(source)

    ok, error = camera_manager.add_camera(
        camera_id=camera_id,
        source=source,
        location=location,
        zone=zone,
        enabled=payload.enabled,
    )

    if not ok:
        return {"error": error}

    return {"status": "ok", "cameras": camera_manager.camera_list()}


class CameraMetaPayload(BaseModel):
    location: Optional[str] = None
    zone: Optional[str] = None


@app.put("/cameras/{camera_id}")
def update_camera_meta(camera_id: str, payload: CameraMetaPayload, _admin: None = Depends(require_admin)):
    """Renames a camera's location and/or zone label - e.g. relabeling
    'Zone A' to 'North Loading Dock'. Takes effect immediately (no
    restart) and is written back to cameras.json."""

    if payload.location is None and payload.zone is None:
        return {"error": "Nothing to update - provide location and/or zone."}

    location = payload.location.strip() if payload.location is not None else None
    zone = payload.zone.strip() if payload.zone is not None else None

    if location == "":
        return {"error": "Location can't be empty."}

    if zone == "":
        return {"error": "Zone can't be empty."}

    ok = camera_manager.update_camera_meta(camera_id, location=location, zone=zone)

    if not ok:
        return {"error": f"Unknown camera: {camera_id}"}

    return {"status": "ok", "camera_id": camera_id, "location": location, "zone": zone}


class CameraEnabledPayload(BaseModel):
    enabled: bool


@app.put("/cameras/{camera_id}/enabled")
def set_camera_enabled(camera_id: str, payload: CameraEnabledPayload, _admin: None = Depends(require_admin)):
    """Starts or stops a camera's worker without deleting it - e.g.
    disabling one that's temporarily unplugged."""

    ok = camera_manager.set_camera_enabled(camera_id, payload.enabled)

    if not ok:
        return {"error": f"Unknown camera: {camera_id}"}

    return {"status": "ok", "camera_id": camera_id, "enabled": payload.enabled}


@app.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str, _admin: None = Depends(require_admin)):
    """Permanently removes a camera from the roster (stops its worker
    first if running) and writes the change back to cameras.json."""

    ok = camera_manager.remove_camera(camera_id)

    if not ok:
        return {"error": f"Unknown camera: {camera_id}"}

    return {"status": "ok", "camera_id": camera_id}


class WatchedItemsPayload(BaseModel):
    items: List[str]


@app.put("/cameras/{camera_id}/watched-items")
def update_watched_items(camera_id: str, payload: WatchedItemsPayload, _admin: None = Depends(require_admin)):
    """Change which PPE items a camera reports violations for - e.g.
    CAM-01 = helmet only, CAM-02 = vest only. Takes effect on the
    very next frame, no restart needed, and is written back to
    cameras.json so it survives one."""

    items = [i.strip().lower() for i in payload.items if i.strip()]

    invalid = [i for i in items if i not in config.ALL_WATCHABLE_PPE_ITEMS]

    if invalid:
        return {
            "error": f"Unknown PPE item(s): {invalid}. "
                     f"Valid items: {config.ALL_WATCHABLE_PPE_ITEMS}",
        }

    ok = camera_manager.set_camera_watched_items(camera_id, items)

    if not ok:
        return {"error": f"Unknown camera: {camera_id}"}

    return {"status": "ok", "camera_id": camera_id, "watched_items": items}


@app.put("/cameras/{camera_id}/watched-hazards")
def update_watched_hazards(camera_id: str, payload: WatchedItemsPayload, _admin: None = Depends(require_admin)):
    """Same idea as watched-items, for fire/smoke - e.g. a camera
    aimed at a welding bay that should only ever raise 'fire'."""

    items = [i.strip().lower() for i in payload.items if i.strip()]

    invalid = [i for i in items if i not in config.WATCHABLE_HAZARD_ITEMS]

    if invalid:
        return {
            "error": f"Unknown hazard item(s): {invalid}. "
                     f"Valid items: {config.WATCHABLE_HAZARD_ITEMS}",
        }

    ok = camera_manager.set_camera_watched_hazards(camera_id, items)

    if not ok:
        return {"error": f"Unknown camera: {camera_id}"}

    return {"status": "ok", "camera_id": camera_id, "watched_hazards": items}


@app.put("/cameras/{camera_id}/heatmap")
def set_camera_heatmap(camera_id: str, payload: CameraEnabledPayload, _admin: None = Depends(require_admin)):
    """Turns a camera's own live violation-hotspot overlay on/off - a
    low-res grid of WHERE in that camera's frame violations have been
    landing, decayed over time and blended into its MJPEG feed when
    on (CameraWorker._blend_heatmap). Reuses CameraEnabledPayload
    (just an {"enabled": bool} body) rather than a near-duplicate
    model."""

    ok = camera_manager.set_camera_heatmap_enabled(camera_id, payload.enabled)

    if not ok:
        return {"error": f"Unknown camera: {camera_id}"}

    return {"status": "ok", "camera_id": camera_id, "show_heatmap": payload.enabled}


@app.get("/video_feed/{camera_id}")
def video_feed(camera_id: str):

    worker = camera_manager.get(camera_id)

    if worker is None:
        return {"error": f"Unknown or disabled camera: {camera_id}"}

    return StreamingResponse(
        worker.jpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ============================================================
# DETECTIONS
# ============================================================

@app.get("/detections")
def get_detections(camera_id: Optional[str] = None):

    if camera_id:

        worker = camera_manager.get(camera_id)

        if worker is None:
            return {"error": f"Unknown or disabled camera: {camera_id}"}

        return worker.snapshot()

    return {"cameras": camera_manager.all_snapshots()}


# ============================================================
# ALERTS
# ============================================================

@app.get("/alerts")
def get_alerts():

    history = alerts.get_alert_history()

    return {"count": len(history), "alerts": history}


@app.delete("/alerts")
def clear_alerts(_admin: None = Depends(require_admin)):

    alerts.clear_alert_history()

    return {"status": "ok", "message": "Alert history cleared"}


# ============================================================
# ANALYTICS
# ============================================================

@app.get("/analytics/hotspots")
def get_hotspots():
    """Ranks ZONES by how many alerts have actually fired there this
    session - the site-wide, cross-camera counterpart to each
    camera's own live pixel-space heatmap (PUT /cameras/{id}/heatmap,
    CameraWorker._blend_heatmap). Built entirely from the existing
    in-memory alert history (the same cooldown-gated entries GET
    /alerts returns), so this costs nothing extra to compute and
    needs no separate storage - it just groups what's already there
    differently."""

    history = alerts.get_alert_history()

    zone_counts = Counter()
    zone_cameras = defaultdict(set)
    zone_types = defaultdict(Counter)
    zone_hazard_counts = Counter()

    for alert in history:

        zone = alert.get("zone") or "Unknown"

        zone_counts[zone] += 1
        zone_cameras[zone].add(alert.get("camera_id", "?"))
        zone_types[zone][alert.get("incident_type", "Unknown")] += 1

        if alert.get("class_name") in ("fire", "smoke"):
            zone_hazard_counts[zone] += 1

    zones = [
        {
            "zone": zone,
            "camera_ids": sorted(zone_cameras[zone]),
            "count": count,
            "fire_smoke_count": zone_hazard_counts.get(zone, 0),
            "ppe_count": count - zone_hazard_counts.get(zone, 0),
            "by_type": dict(zone_types[zone]),
        }
        for zone, count in zone_counts.most_common()
    ]

    return {"total_incidents": len(history), "zones": zones}


# ============================================================
# FOOTAGE - continuous CCTV-style recording (recording.py)
# ============================================================

@app.get("/footage")
def list_footage(
    camera_id: Optional[str] = None,
    zone: Optional[str] = None,
    date: Optional[str] = None,
    has_violation: Optional[bool] = None,
    has_fire: Optional[bool] = None,
    has_smoke: Optional[bool] = None,
    order: str = "desc",
    limit: int = 60,
    offset: int = 0,
):

    limit = max(1, min(limit, 200))

    segments, total = recording.get_footage(
        camera_id=camera_id, zone=zone, date=date,
        has_violation=has_violation, has_fire=has_fire, has_smoke=has_smoke,
        order=order, limit=limit, offset=offset,
    )

    return {"total": total, "count": len(segments), "segments": segments}


@app.get("/footage/stats")
def footage_stats():

    return recording.get_footage_stats()


@app.delete("/footage/{segment_id}")
def delete_footage(segment_id: str, _admin: None = Depends(require_admin)):

    ok = recording.delete_segment(segment_id)

    if not ok:
        return {"error": f"Unknown footage segment: {segment_id}"}

    return {"status": "ok", "id": segment_id}


# ============================================================
# WORKER IDENTITY (face enrollment) + ENTRY GATE
# ============================================================
#
# See face_id.py and gate.py's module docstrings. Enrollment is
# admin-only (it's effectively a settings change - adding someone who
# can now pass the gate); reading the roster and running a gate scan
# are open, same convention as every other read-only/"the gated
# dashboard itself needs this to load" endpoint above.
# ============================================================

def _decode_image(raw_bytes):

    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    return frame


@app.get("/workers")
def list_workers():

    return {"workers": face_id.list_workers()}


@app.post("/workers/enroll")
async def enroll_worker(
    name: str = Form(...),
    department: str = Form(""),
    required_items: str = Form(""),
    worker_id: Optional[str] = Form(None),
    photos: List[UploadFile] = File(...),
    _admin: None = Depends(require_admin),
):
    """required_items: comma-separated (e.g. "helmet,vest,gloves,boots"),
    empty -> config.GATE_REQUIRED_PPE_ITEMS. At least one clear,
    front-facing photo is required; more (different angles/lighting)
    makes the averaged embedding more robust."""

    if not face_id.is_available():
        return {"error": f"Face ID unavailable: {face_id.status()['reason']}"}

    frames = []

    for photo in photos:

        raw = await photo.read()
        frame = _decode_image(raw)

        if frame is not None:
            frames.append(frame)

    if not frames:
        return {"error": "No readable image in the uploaded photo(s)."}

    items = [i.strip() for i in required_items.split(",") if i.strip()]

    try:

        record = face_id.enroll_worker(
            name=name, department=department, required_items=items,
            photo_frames=frames, worker_id=worker_id,
        )

    except ValueError as e:
        return {"error": str(e)}
    except RuntimeError as e:
        return {"error": str(e)}

    # Keep one reference photo on disk (the first usable one) so the
    # dashboard's worker list can show a thumbnail, same idea as
    # alert snapshots - purely for display, not used in matching.
    try:
        ok, buf = cv2.imencode(".jpg", frames[0])
        if ok:
            photo_path = os.path.join(
                config.WORKER_PHOTOS_DIR, f"{record['worker_id']}.jpg"
            )
            with open(photo_path, "wb") as f:
                f.write(buf.tobytes())
    except Exception:
        pass  # thumbnail is cosmetic only - never fail enrollment over it

    return {"status": "ok", "worker": record}


@app.get("/workers/{worker_id}/photo")
def worker_photo(worker_id: str):

    photo_path = os.path.join(config.WORKER_PHOTOS_DIR, f"{worker_id}.jpg")

    if not os.path.exists(photo_path):
        return {"error": "No photo on file for this worker."}

    with open(photo_path, "rb") as f:
        return Response(content=f.read(), media_type="image/jpeg")


@app.delete("/workers/{worker_id}")
def remove_worker(worker_id: str, _admin: None = Depends(require_admin)):

    ok = face_id.delete_worker(worker_id)

    if not ok:
        return {"error": f"Unknown worker_id: {worker_id}"}

    _remove_quietly(os.path.join(config.WORKER_PHOTOS_DIR, f"{worker_id}.jpg"))

    return {"status": "ok", "worker_id": worker_id}


@app.get("/gate/status")
def gate_status():

    return {
        "face_id": face_id.status(),
        "required_items": sorted(config.GATE_REQUIRED_PPE_ITEMS),
        "enrolled_worker_count": len(face_id.list_workers()),
        "scan_cooldown_seconds": config.GATE_SCAN_COOLDOWN,
        "camera": gate_camera.status(),
    }


@app.post("/gate/scan")
async def gate_scan(image: UploadFile = File(...)):
    """Accepts one snapshot frame (a manually uploaded photo - the
    fallback path for a site with no DVR/RTSP gate camera configured)
    and runs the full face-match + PPE-check pipeline - see
    gate.process_gate_scan(). The normal path for a DVR deployment is
    POST /gate/camera/capture below, which uses the live CCTV feed
    instead of an uploaded file."""

    raw = await image.read()
    frame = _decode_image(raw)

    if frame is None:
        return {"error": "Could not decode the uploaded image."}

    result = gate.process_gate_scan(frame, ppe_model)

    return result


@app.get("/gate/camera/feed")
def gate_camera_feed():
    """Live MJPEG preview of the gate's DVR/RTSP channel, for the
    dashboard's Entry Gate page - same multipart stream shape as
    /video_feed/{camera_id}, just for this one special-purpose camera
    that isn't in cameras.json."""

    return StreamingResponse(
        gate_camera.jpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/gate/camera/snapshot.jpg")
def gate_camera_snapshot():
    """One still JPEG frame from the gate camera - used by the
    dashboard to grab a freeze-frame for worker enrollment ("Add
    Photo" pulls from the same DVR channel as the gate scan, not a
    browser webcam)."""

    jpeg = gate_camera.get_latest_jpeg()

    if jpeg is None:
        raise HTTPException(
            status_code=503,
            detail="No live frame yet - check GATE_CAMERA_SOURCE and the DVR connection.",
        )

    return Response(content=jpeg, media_type="image/jpeg")


@app.post("/gate/camera/capture")
def gate_camera_capture():
    """Manual "Capture & Scan" trigger against the live DVR feed -
    grabs the current frame and runs it through the same pipeline as
    POST /gate/scan, bypassing the auto-scan throttle since this is an
    explicit click."""

    return gate_camera.capture_and_scan()


@app.get("/gate/log")
def gate_log(limit: int = 100):

    limit = max(1, min(limit, 500))
    return {"entries": gate.get_log_tail(limit)}


# ============================================================
# FIRE / SMOKE SENSOR (optional hardware fusion - see fire_sensor.py)
# ============================================================

@app.get("/sensors/fire")
def sensor_fire_status():

    return fire_sensor.status()


# ============================================================
# REPORTS - downloadable combined log
# ============================================================
#
# One CSV covering everything the Reports page shows on screen:
# every PPE / fire / smoke incident that actually fired (the same
# entries GET /alerts and the Alert Log panel show), AND every
# recorded footage clip (GET /footage) - so a site supervisor can
# keep an offline record or hand it to someone who wasn't watching
# the dashboard live. Both come from data that already exists
# (in-memory alert history + the footage index) - this just formats
# it as one downloadable file rather than adding new tracking.
#
# "worker_id" in the live websocket snapshot is a per-frame detection
# slot, not a stable identity on its own - face_id.py (see its module
# docstring) is what turns that into a real name, when a worker is
# enrolled and gets matched. The "identified_worker" column below is
# blank whenever that didn't happen (unenrolled worker, face ID not
# set up, or a footage row, which never carries a worker identity).
# ============================================================

@app.get("/reports/export.csv")
def export_report_csv():

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow([
        "record_type", "timestamp", "camera_id", "location", "zone",
        "category", "detail", "severity_or_duration_seconds",
        "confidence_percent_or_file_size_bytes", "identified_worker",
    ])

    for alert in alerts.get_alert_history():

        category = (
            "fire" if alert.get("class_name") == "fire"
            else "smoke" if alert.get("class_name") == "smoke"
            else "ppe_violation"
        )

        worker = alert.get("identified_worker") or {}

        writer.writerow([
            "incident",
            alert.get("timestamp", ""),
            alert.get("camera_id", ""),
            alert.get("location", ""),
            alert.get("zone", ""),
            category,
            alert.get("incident_type", ""),
            alert.get("severity", ""),
            alert.get("confidence_percent", ""),
            worker.get("name", ""),
        ])

    segments, _ = recording.get_footage(order="desc", limit=100000)

    for seg in segments:

        flags = []

        if seg.get("has_violation"):
            flags.append("ppe_violation")
        if seg.get("has_fire"):
            flags.append("fire")
        if seg.get("has_smoke"):
            flags.append("smoke")

        writer.writerow([
            "footage",
            datetime.fromtimestamp(seg["start_time"]).isoformat(),
            seg.get("camera_id", ""),
            seg.get("location", ""),
            seg.get("zone", ""),
            "|".join(flags) if flags else "clean",
            seg.get("file_path", ""),
            seg.get("duration_seconds", ""),
            seg.get("file_size_bytes", ""),
            "",
        ])

    filename = f"aegis-sitewatch-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================
# WEBSOCKET - ALL CAMERAS
# ============================================================

@app.websocket("/ws/detections")
async def websocket_detections(websocket: WebSocket):

    await websocket.accept()

    try:

        while True:

            data = {"cameras": camera_manager.all_snapshots()}

            await websocket.send_json(data)
            await asyncio.sleep(0.15)

    except WebSocketDisconnect:

        pass

    except Exception as e:

        print(f"WebSocket error: {e}")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
