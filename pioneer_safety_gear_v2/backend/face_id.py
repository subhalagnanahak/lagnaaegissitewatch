# ============================================================
# FACE_ID - offline worker face identity
# ============================================================
#
# Shared by TWO features:
#   1. Entry gate (gate.py): enroll a worker's face once, then
#      match it at the gate before checking their PPE.
#   2. Live monitoring (cameras.py): when a worker already flagged
#      with a PPE violation can be matched against an enrolled face,
#      attribute that violation to a NAME instead of an anonymous
#      "Worker #3" (config.FACE_ID_ON_VIOLATIONS_ONLY controls
#      whether this runs on every worker or only violators).
#
# Built entirely on OpenCV's own DNN module - cv2.FaceDetectorYN
# (face detection, "YuNet") and cv2.FaceRecognizerSF ("SFace": a
# 128-d embedding plus a built-in cosine/L2 matcher) - both ship in
# opencv-contrib-python with no separate inference runtime. This is
# a deliberate choice over dlib/face_recognition: dlib is a slow,
# often broken build on Jetson's aarch64, while YuNet+SFace are a
# few hundred KB / ~37MB of ONNX run through OpenCV's existing DNN
# backend, the same one this project already depends on via cv2.
#
# OFFLINE: once the two model files are downloaded (one-time setup,
# see backend/models/README.md), nothing here ever touches a
# network. Enrollment and matching are both pure local compute.
#
# GRACEFUL DEGRADATION: if the model files are missing, or this
# OpenCV build doesn't have FaceDetectorYN/FaceRecognizerSF (older
# opencv-python without contrib), face ID disables itself at import
# time and every public function below becomes a safe no-op -
# exactly like VOICE_ALERTS_ENABLED/LOCAL_SIREN_ENABLED degrade when
# espeak-ng/an audio player isn't installed. Nothing else in the
# system depends on this ever being available.
# ============================================================

import json
import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np

import config


# ============================================================
# MODEL LOADING (lazy, once)
# ============================================================

_detector = None
_recognizer = None
_load_lock = threading.Lock()
_load_attempted = False
_disabled_reason = None


def _try_load_models():

    global _detector, _recognizer, _load_attempted, _disabled_reason

    with _load_lock:

        if _load_attempted:
            return

        _load_attempted = True

        if not config.FACE_ID_ENABLED:
            _disabled_reason = "disabled in config (FACE_ID_ENABLED=false)"
            return

        if not hasattr(cv2, "FaceDetectorYN_create") or not hasattr(
            cv2, "FaceRecognizerSF_create"
        ):
            _disabled_reason = (
                "this OpenCV build has no FaceDetectorYN/FaceRecognizerSF "
                "(needs opencv-contrib-python >= 4.5.4, not plain opencv-python)"
            )
            return

        if not os.path.exists(config.FACE_DETECTOR_MODEL_PATH):
            _disabled_reason = (
                f"face detector model not found: {config.FACE_DETECTOR_MODEL_PATH} "
                "(see backend/models/README.md)"
            )
            return

        if not os.path.exists(config.FACE_RECOGNIZER_MODEL_PATH):
            _disabled_reason = (
                f"face recognizer model not found: {config.FACE_RECOGNIZER_MODEL_PATH} "
                "(see backend/models/README.md)"
            )
            return

        try:

            # input_size is set per-frame via setInputSize() below -
            # this constructor value is just a placeholder.
            _detector = cv2.FaceDetectorYN_create(
                config.FACE_DETECTOR_MODEL_PATH, "", (320, 320),
                score_threshold=0.7,
            )
            _recognizer = cv2.FaceRecognizerSF_create(
                config.FACE_RECOGNIZER_MODEL_PATH, "",
            )

        except Exception as e:

            _detector = None
            _recognizer = None
            _disabled_reason = f"failed to load face models: {e}"


def is_available():

    _try_load_models()
    return _detector is not None and _recognizer is not None


def status():
    """Used by GET /health and GET /gate/status so the dashboard can
    show whether face ID is actually usable, and why not if it isn't."""

    _try_load_models()

    return {
        "available": is_available(),
        "reason": None if is_available() else _disabled_reason,
    }


# ============================================================
# DETECTION + EMBEDDING
# ============================================================

def detect_faces(frame_bgr):
    """Returns a list of {"bbox": [x, y, w, h], "landmarks": face,
    "confidence": float} for every face YuNet finds in this frame,
    or [] if face ID isn't available/no faces found. `face` (the raw
    15-value YuNet row) is kept as-is since FaceRecognizerSF.align
    needs that exact format, not just the bbox."""

    if not is_available():
        return []

    height, width = frame_bgr.shape[:2]
    _detector.setInputSize((width, height))

    _, results = _detector.detect(frame_bgr)

    if results is None:
        return []

    out = []

    for face in results:

        x, y, w, h = face[:4].astype(int)
        confidence = float(face[-1])

        if w <= 0 or h <= 0:
            continue

        if (w / float(width)) < config.FACE_MIN_SIZE_RATIO:
            continue

        out.append({
            "bbox": [int(x), int(y), int(w), int(h)],
            "raw": face,
            "confidence": round(confidence, 3),
        })

    return out


def embed_face(frame_bgr, raw_face):
    """raw_face is one row from detect_faces()'s "raw" field (YuNet's
    own output format - NOT a plain [x,y,w,h] bbox). Returns a 1x128
    float32 embedding, or None if face ID isn't available."""

    if not is_available():
        return None

    aligned = _recognizer.alignCrop(frame_bgr, raw_face)
    return _recognizer.feature(aligned)


def best_face_in_box(frame_bgr, bbox_xyxy):
    """Convenience for cameras.py: given a PERSON box (x1,y1,x2,y2)
    from the PPE model, crop to it (faces are near the top of a
    person box) and find the single best face inside, if any. Returns
    (embedding, confidence) or (None, None)."""

    if not is_available():
        return None, None

    x1, y1, x2, y2 = [int(v) for v in bbox_xyxy]
    height, width = frame_bgr.shape[:2]

    # Faces live in the upper portion of a person box - cropping to
    # roughly the top half keeps the detector from wasting time on a
    # torso/legs region that can never contain one, and reduces the
    # chance of matching a DIFFERENT person's face that happens to
    # overlap this box's lower half in a crowded frame.
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(width, x2)
    y2 = min(height, y1 + int((y2 - y1) * 0.55))

    if x2 <= x1 or y2 <= y1:
        return None, None

    crop = frame_bgr[y1:y2, x1:x2]

    if crop.size == 0:
        return None, None

    faces = detect_faces(crop)

    if not faces:
        return None, None

    best = max(faces, key=lambda f: f["confidence"])
    embedding = embed_face(crop, best["raw"])

    return embedding, best["confidence"]


def match_score(embedding_a, embedding_b):

    return float(_recognizer.match(
        embedding_a, embedding_b, cv2.FaceRecognizerSF_FR_COSINE,
    ))


# ============================================================
# WORKER REGISTRY (enrolled workers - name, required PPE, embedding)
# ============================================================
#
# Flat JSON file, same no-database pattern as cameras.json. Each
# worker's embedding is stored as a plain list of floats (JSON has no
# binary type) - cheap to reload (128 floats/worker) and keeps this
# consistent with every other piece of state in this project living
# in a plain file, not a database.
# ============================================================

_registry_lock = threading.Lock()


def _load_registry():

    if not os.path.exists(config.WORKERS_DB_PATH):
        return []

    try:
        with open(config.WORKERS_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_registry(workers):

    tmp_path = config.WORKERS_DB_PATH + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(workers, f, indent=2)

    os.replace(tmp_path, config.WORKERS_DB_PATH)


def list_workers():
    """Public listing - embeddings stripped out (128 floats each,
    meaningless to the dashboard and not something the API should
    hand back anyway)."""

    with _registry_lock:
        workers = _load_registry()

    return [
        {k: v for k, v in w.items() if k != "embedding"}
        for w in workers
    ]


def enroll_worker(name, department, required_items, photo_frames, worker_id=None):
    """photo_frames: a list of already-decoded BGR frames (one or
    more enrollment photos - averaging their embeddings makes the
    match more robust to a single bad angle/lighting). Returns the
    new worker record (without the embedding), or raises ValueError
    if no usable face was found in any photo, or RuntimeError if face
    ID isn't available at all."""

    if not is_available():
        raise RuntimeError(f"Face ID unavailable: {_disabled_reason}")

    embeddings = []

    for frame in photo_frames:

        faces = detect_faces(frame)

        if not faces:
            continue

        best = max(faces, key=lambda f: f["confidence"])
        embeddings.append(embed_face(frame, best["raw"]))

    if not embeddings:
        raise ValueError("No face detected in any of the enrollment photos.")

    averaged = np.mean(np.stack(embeddings), axis=0, keepdims=True).astype(np.float32)

    with _registry_lock:

        workers = _load_registry()

        if worker_id is None:
            existing_ids = {w["worker_id"] for w in workers}
            n = 1
            while f"W-{n:03d}" in existing_ids:
                n += 1
            worker_id = f"W-{n:03d}"

        record = {
            "worker_id": worker_id,
            "name": name,
            "department": department or "",
            "required_items": sorted(set(required_items)) or sorted(config.GATE_REQUIRED_PPE_ITEMS),
            "embedding": averaged.flatten().tolist(),
            "enrolled_at": datetime.now().isoformat(),
            "photo_count": len(embeddings),
        }

        # Re-enrolling an existing worker_id replaces their record
        # (new photos, possibly a new name/department) rather than
        # creating a duplicate.
        workers = [w for w in workers if w["worker_id"] != worker_id]
        workers.append(record)

        _save_registry(workers)

    return {k: v for k, v in record.items() if k != "embedding"}


def delete_worker(worker_id):

    with _registry_lock:

        workers = _load_registry()
        remaining = [w for w in workers if w["worker_id"] != worker_id]

        if len(remaining) == len(workers):
            return False

        _save_registry(remaining)
        return True


def identify(embedding):
    """Matches one face embedding against every enrolled worker.
    Returns {"worker_id", "name", "department", "required_items",
    "score"} for the best match at/above FACE_MATCH_THRESHOLD, or
    None if nobody matches closely enough (including when nobody is
    enrolled yet, or face ID is unavailable)."""

    if not is_available() or embedding is None:
        return None

    with _registry_lock:
        workers = _load_registry()

    best = None
    best_score = -1.0

    for w in workers:

        stored = np.array([w["embedding"]], dtype=np.float32)
        score = match_score(embedding, stored)

        if score > best_score:
            best_score = score
            best = w

    if best is None or best_score < config.FACE_MATCH_THRESHOLD:
        return None

    return {
        "worker_id": best["worker_id"],
        "name": best["name"],
        "department": best["department"],
        "required_items": best["required_items"],
        "score": round(best_score, 3),
    }


# ============================================================
# LIVE-MONITORING ATTRIBUTION (cameras.py hook)
# ============================================================

def identify_workers_in_frame(frame_bgr, workers, violations_only=None):
    """Mutates nothing - returns a NEW list of worker dicts (same
    shape build_worker_compliance() produces, each with an added
    "identity" key: the identify() result, or None if unmatched/
    skipped). Never raises - a face-ID failure must never take down
    the PPE detection pipeline it's riding alongside.

    violations_only=None uses config.FACE_ID_ON_VIOLATIONS_ONLY."""

    if violations_only is None:
        violations_only = config.FACE_ID_ON_VIOLATIONS_ONLY

    if not is_available():
        return [{**w, "identity": None} for w in workers]

    out = []

    for w in workers:

        if violations_only and w["compliant"]:
            out.append({**w, "identity": None})
            continue

        try:
            embedding, _ = best_face_in_box(frame_bgr, w["bbox"])
            identity = identify(embedding) if embedding is not None else None
        except Exception:
            identity = None

        out.append({**w, "identity": identity})

    return out
