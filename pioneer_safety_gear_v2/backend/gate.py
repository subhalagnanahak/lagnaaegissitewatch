# ============================================================
# GATE - biometric (face) entry gate with a PPE check
# ============================================================
#
# A worker steps up to a gate camera (any webcam/phone snapshot
# posted to POST /gate/scan works - it doesn't have to be one of the
# monitoring cameras in cameras.json):
#
#   1. face_id detects + matches their face against enrolled workers
#      (face_id.enroll_worker() is how they got enrolled in the first
#      place - see the dashboard's Entry Gate page).
#   2. If matched, the SAME frame is run through the existing PPE
#      model (reusing ppe_logic.py exactly like cameras.py does -
#      this is not a separate/different PPE check).
#   3. Entry is ALLOWED only if every item in
#      config.GATE_REQUIRED_PPE_ITEMS is compliant on that worker in
#      that frame; otherwise DENIED, listing which items are missing.
#   4. Every scan - matched or not, allowed or denied - is appended
#      to GATE_LOG_PATH (same flat JSONL audit-trail pattern as
#      alert_log.jsonl), and a DENIED result fans out to the same
#      local alarm channels alerts.py already has (siren + spoken
#      announcement) so a gate denial is heard, not just logged.
# ============================================================

import json
import os
import threading
import time
from datetime import datetime

import face_id
import config
from ppe_logic import run_model, build_worker_compliance
from alerts import trigger_local_alarm


_last_scan_by_worker = {}
_cooldown_lock = threading.Lock()

_log_lock = threading.Lock()
_recent_lock = threading.Lock()
_recent_scans = []
MAX_RECENT_SCANS = 100


def _append_log(record):

    with _log_lock:

        try:
            os.makedirs(os.path.dirname(config.GATE_LOG_PATH), exist_ok=True)
            with open(config.GATE_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            print(f"[gate log] write failed (non-fatal): {e}")


def _remember(record):

    global _recent_scans

    with _recent_lock:
        _recent_scans.insert(0, record)
        _recent_scans = _recent_scans[:MAX_RECENT_SCANS]


def get_recent_scans():

    with _recent_lock:
        return list(_recent_scans)


def get_log_tail(limit=200):
    """Reads the last `limit` entries from GATE_LOG_PATH - the
    durable, restart-surviving history (get_recent_scans() above is
    just the in-memory cache of the current process's run, same
    split as alerts.alert_history vs LOCAL_ALERT_LOG_PATH)."""

    if not os.path.exists(config.GATE_LOG_PATH):
        return []

    try:
        with open(config.GATE_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    out = []

    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    out.reverse()
    return out


def process_gate_scan(frame_bgr, ppe_model, camera_id="GATE"):
    """The whole scan pipeline for one frame. Returns a result dict
    that's both logged and handed back to the API caller:

      {
        "matched": bool,
        "worker_id" / "name" / "department": str or None,
        "match_score": float or None,
        "decision": "ALLOWED" | "DENIED" | "UNKNOWN_FACE" | "NO_FACE",
        "missing_items": [str, ...],
        "required_items": [str, ...],
        "timestamp": iso string,
      }

    Never raises - a camera glitch or an unenrolled face is a normal,
    expected outcome here (UNKNOWN_FACE/NO_FACE), not an error."""

    timestamp = datetime.now().isoformat()

    if not face_id.is_available():

        result = {
            "matched": False, "worker_id": None, "name": None,
            "department": None, "match_score": None,
            "decision": "FACE_ID_UNAVAILABLE", "missing_items": [],
            "required_items": sorted(config.GATE_REQUIRED_PPE_ITEMS),
            "timestamp": timestamp, "camera_id": camera_id,
        }
        _remember(result)
        return result

    faces = face_id.detect_faces(frame_bgr)

    if not faces:

        result = {
            "matched": False, "worker_id": None, "name": None,
            "department": None, "match_score": None,
            "decision": "NO_FACE", "missing_items": [],
            "required_items": sorted(config.GATE_REQUIRED_PPE_ITEMS),
            "timestamp": timestamp, "camera_id": camera_id,
        }
        _remember(result)
        return result

    best_face = max(faces, key=lambda f: f["confidence"])
    embedding = face_id.embed_face(frame_bgr, best_face["raw"])
    identity = face_id.identify(embedding)

    if identity is None:

        result = {
            "matched": False, "worker_id": None, "name": None,
            "department": None, "match_score": None,
            "decision": "UNKNOWN_FACE", "missing_items": [],
            "required_items": sorted(config.GATE_REQUIRED_PPE_ITEMS),
            "timestamp": timestamp, "camera_id": camera_id,
        }
        _append_log(result)
        _remember(result)
        trigger_local_alarm("Warning. Unrecognized worker at entry gate.")
        return result

    worker_id = identity["worker_id"]

    # Cooldown: the same worker scanning repeatedly while standing at
    # the gate reuses nothing from a cache (PPE compliance can change
    # second to second as they put gear on) but DOES skip re-logging/
    # re-alarming within the window, so a denial doesn't re-trigger
    # the siren every single frame while they're still standing there.
    with _cooldown_lock:

        last = _last_scan_by_worker.get(worker_id, 0)
        now = time.time()
        within_cooldown = (now - last) < config.GATE_SCAN_COOLDOWN
        _last_scan_by_worker[worker_id] = now

    required_items = (
        identity["required_items"]
        if identity.get("required_items")
        else sorted(config.GATE_REQUIRED_PPE_ITEMS)
    )

    height, width = frame_bgr.shape[:2]
    ppe_boxes = run_model(
        ppe_model, frame_bgr, config.PPE_CONFIDENCE, config.PPE_INFERENCE_SIZE,
    )
    workers = build_worker_compliance(ppe_boxes, width, height, required_items)

    # The gate camera is expected to frame one worker at a time - if
    # more than one person box turns up, use whichever is largest
    # (closest to the camera, i.e. actually at the gate).
    missing_items = required_items

    if workers:

        def box_area(w):
            x1, y1, x2, y2 = w["bbox"]
            return max(0, x2 - x1) * max(0, y2 - y1)

        gate_worker = max(workers, key=box_area)
        missing_items = [v["item"] for v in gate_worker["violations"]]

    decision = "ALLOWED" if not missing_items else "DENIED"

    result = {
        "matched": True,
        "worker_id": worker_id,
        "name": identity["name"],
        "department": identity["department"],
        "match_score": identity["score"],
        "decision": decision,
        "missing_items": missing_items,
        "required_items": required_items,
        "timestamp": timestamp,
        "camera_id": camera_id,
    }

    _append_log(result)
    _remember(result)

    if decision == "DENIED" and not within_cooldown:

        missing_label = ", ".join(
            item.replace("_", " ") for item in missing_items
        )
        trigger_local_alarm(
            f"Entry denied. {identity['name']} is missing {missing_label}."
        )

    return result
