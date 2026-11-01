# ============================================================
# CONFIG
# ============================================================
#
# Central place for:
#   - .env loading (no external dependency required)
#   - Twilio / alert settings
#   - PPE class maps (what the model was actually trained on)
#   - Camera list (loaded from cameras.json, one physical camera
#     or a recorded-video file per entry - see cameras.json)
#
# ============================================================

import os
import json


# ============================================================
# RTSP / FFMPEG TOLERANCE OPTIONS - must be set before ANY
# cv2.VideoCapture() call anywhere in the app, so this lives here at
# the very top of the first module everything else imports.
#
# rtsp_transport;tcp - OpenCV's ffmpeg backend defaults to UDP for
# RTSP, which drops packets under any real-world network jitter -
# H.265/HEVC streams tolerate that badly, since losing one packet can
# cost a whole reference frame. TCP transport is far more reliable
# (in-order, lossless at the transport layer); it costs a little
# latency, which doesn't matter here since nothing in this project
# needs frame-perfect real-time CCTV, only "a recent enough frame".
#
# probesize / analyzeduration - raised well above ffmpeg's defaults
# because some DVR/NVR firmware (several budget/OEM-rebadged
# Hikvision-compatible boxes, confirmed on at least one real unit
# this project was tested against) doesn't reliably put the H.265
# parameter sets (VPS/SPS/PPS) in the RTSP session description the
# way it's supposed to, so ffmpeg has to pick them up from the stream
# itself - and with the default (small/fast) probe budget it can give
# up before it's seen a full set, which is what produces errors like:
#   [hevc @ 0x...] VPS 14 does not exist
#   [hevc @ 0x...] SPS 0 does not exist.
#   [hevc @ 0x...] PPS id out of range: 0
# even with TCP transport already in place and nothing wrong with the
# network. Giving it a much bigger budget to look before failing fixes
# this for the DVRs that exhibit it; harmless for ones that don't
# (connect is a little slower, that's all - still well under a
# second in practice).
# ============================================================

os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|probesize;10000000|analyzeduration;10000000",
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)


# ============================================================
# .env LOADER (no python-dotenv dependency required)
# ============================================================

def load_dotenv(path=None):

    path = path or os.path.join(BASE_DIR, ".env")

    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:

        for line in f:

            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")

            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value


load_dotenv()


# ============================================================
# ALERTING
# ============================================================
#
# The system is offline-first: the REQUIRED alert path is a local
# siren (synthesized WAV, played through the machine's own audio
# output) + a local on-disk alert log + the dashboard itself
# (websocket, same LAN/localhost, no internet involved). None of
# that needs a network connection beyond the LAN link between the
# backend and the dashboard - and if backend and dashboard run on
# the same machine, not even that.
#
# Twilio SMS is an OPTIONAL bonus channel layered on top. It is the
# only thing in this project that needs real internet access, and
# it stays fully disabled unless backend/.env has real credentials
# in it. Leave that file blank/absent for a fully offline deployment.
# ============================================================

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
ALERT_PHONE_NUMBER = os.getenv("ALERT_PHONE_NUMBER", "")

# Same camera + zone + incident won't fire another alert (local
# siren/log AND SMS, if enabled) for this many seconds.
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", os.getenv("SMS_ALERT_COOLDOWN", "30")))

# Local siren - no internet, no cloud account, just the machine's
# own speaker. Disable with LOCAL_SIREN_ENABLED=false in .env if
# running headless with no audio device.
LOCAL_SIREN_ENABLED = os.getenv("LOCAL_SIREN_ENABLED", "true").lower() not in (
    "false", "0", "no",
)

SIREN_WAV_PATH = os.getenv(
    "SIREN_WAV_PATH",
    os.path.join(BASE_DIR, "assets", "siren.wav"),
)

# Every alert is appended as one JSON line here, regardless of
# whether SMS/siren are enabled - a fully offline audit trail.
LOCAL_ALERT_LOG_PATH = os.getenv(
    "LOCAL_ALERT_LOG_PATH",
    os.path.join(BASE_DIR, "alert_log.jsonl"),
)

# Spoken-word alert (offline, via the espeak/espeak-ng CLI - see
# alerts.py's module docstring) that announces WHAT was detected and
# WHERE, played right after the siren so the two never overlap. Off
# entirely with VOICE_ALERTS_ENABLED=false; silently skipped (siren
# still plays) if no offline TTS engine is installed on the machine.
VOICE_ALERTS_ENABLED = os.getenv("VOICE_ALERTS_ENABLED", "true").lower() not in (
    "false", "0", "no",
)

# Violation snapshot storage ("surveillance of the live feed" per the
# brief) - the instant a violation actually fires (i.e. passes the
# cooldown below), the current frame - WITH the same bounding-box
# overlay the live feed shows - is saved as a JPEG next to the alert,
# entirely on this machine. Off entirely with SNAPSHOT_ENABLED=false.
SNAPSHOT_ENABLED = os.getenv("SNAPSHOT_ENABLED", "true").lower() not in (
    "false", "0", "no",
)

SNAPSHOTS_DIR = os.getenv(
    "SNAPSHOTS_DIR",
    os.path.join(BASE_DIR, "snapshots"),
)

# Snapshot files kept on disk before the oldest are pruned, so a demo
# / judging run left going for hours doesn't slowly fill the disk.
# 0 disables pruning (keep every snapshot ever taken).
MAX_SNAPSHOTS = int(os.getenv("MAX_SNAPSHOTS", "500"))

os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


# ============================================================
# CONTINUOUS FOOTAGE RECORDING
# ============================================================
#
# Distinct from the violation SNAPSHOTS above (one still JPEG the
# instant an alert actually fires): this is continuous, CCTV-style
# recording of every enabled camera's own live (annotated) feed, cut
# into fixed-length clips, entirely on local disk - no cloud storage,
# same offline-first philosophy as everything else here. See
# recording.py for the writer/rotation/retention logic and main.py's
# GET /footage, GET /footage/stats, DELETE /footage/{id}.
# ============================================================

RECORDING_ENABLED = os.getenv("RECORDING_ENABLED", "true").lower() not in (
    "false", "0", "no",
)

FOOTAGE_DIR = os.getenv(
    "FOOTAGE_DIR",
    os.path.join(BASE_DIR, "footage"),
)

# Every closed clip gets one JSON line appended here - same pattern
# as LOCAL_ALERT_LOG_PATH above, so footage metadata survives a
# backend restart without needing a real database.
FOOTAGE_INDEX_PATH = os.getenv(
    "FOOTAGE_INDEX_PATH",
    os.path.join(BASE_DIR, "footage_index.jsonl"),
)

os.makedirs(FOOTAGE_DIR, exist_ok=True)

# How long each recorded clip is before it's closed out and a new one
# starts - keeps any single file to a sane, browsable/seekable size
# instead of one ever-growing recording per camera.
RECORDING_SEGMENT_SECONDS = int(os.getenv("RECORDING_SEGMENT_SECONDS", "300"))

# Frames are written to disk at most this often, independent of the
# camera's actual processing rate (which can vary with load/hardware) -
# so playback speed stays roughly real-time instead of running fast on
# a machine that happens to process frames quickly.
RECORDING_FPS = int(os.getenv("RECORDING_FPS", "10"))

# Retention - two independent caps, oldest clips deleted first,
# whichever limit is actually configured (0 disables that particular
# cap; both at 0 means keep everything forever, e.g. for a single
# judging-day demo where the disk won't fill).
RECORDING_RETENTION_DAYS = int(os.getenv("RECORDING_RETENTION_DAYS", "7"))
RECORDING_MAX_STORAGE_GB = float(os.getenv("RECORDING_MAX_STORAGE_GB", "20"))
RECORDING_CLEANUP_INTERVAL = int(os.getenv("RECORDING_CLEANUP_INTERVAL", "300"))


# ============================================================
# ADMIN LOGIN
# ============================================================
#
# A single shared admin account gates the dashboard itself (the
# frontend shows nothing but the login screen until this succeeds)
# AND the camera-management / settings / delete endpoints below
# (main.py's require_admin dependency) - so someone who never logs
# in can't drive those from the API directly either, even though the
# read-only endpoints (health, camera list, live feed, alerts,
# footage, hotspots) stay open so the gated dashboard itself can load
# them without also having to smuggle a token into an <img>/<video>
# tag's src. CHANGE THESE before any real deployment - the defaults
# below only exist so login works out of the box for a demo.
# ============================================================

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "aegis123")

# How long a login stays valid before the dashboard has to sign in
# again - default 8 hours (a full shift/demo day).
ADMIN_SESSION_TIMEOUT_SECONDS = int(
    os.getenv("ADMIN_SESSION_TIMEOUT_SECONDS", str(8 * 60 * 60))
)


# ============================================================
# MODELS
# ============================================================
#
# Using yolo11n_ppe2/weights/ppe_best.pt (100-epoch run, best
# mAP50 among the PPE runs) and yolo11n_fire_smoke4/weights/best.pt
# (33-epoch run - meaningfully better than the yolo11n_fire_smoke2
# checkpoint that was deployed before: mAP50 ~0.60 vs ~0.52).
# ============================================================

PPE_MODEL_PATH = os.getenv(
    "PPE_MODEL_PATH",
    os.path.join(
        PROJECT_DIR,
        "runs", "detect", "yolo11n_ppe2", "weights", "ppe_best.pt"
    )
)

FIRE_MODEL_PATH = os.getenv(
    "FIRE_MODEL_PATH",
    os.path.join(
        PROJECT_DIR,
        "fire_smoke_detection", "runs", "detect",
        "yolo11n_fire_smoke4", "weights", "best.pt"
    )
)

# "Other industry" gear - mask/respirator, ear protection, face
# shield. The construction-PPE model above was never trained on
# these classes, so this is a THIRD, separately-trained model
# rather than a config tweak to the first one (see
# extra_gear_detection/README.md for where it came from and its
# license). Entirely optional: if the weights file below isn't
# present, main.py just skips loading it and every camera behaves
# exactly as before.
EXTRA_GEAR_MODEL_PATH = os.getenv(
    "EXTRA_GEAR_MODEL_PATH",
    os.path.join(
        PROJECT_DIR, "extra_gear_detection", "weights", "sh17_yolov8n.pt"
    )
)


# ============================================================
# DETECTION SETTINGS
# ============================================================

# NOTE: both PPE_train.py and fire_smoke_detection/fire_train.py trained
# at imgsz=512. Running inference at a different size than training hurts
# accuracy - most visibly on small objects (a lighter flame, an early wisp
# of smoke) since they shrink to just a few pixels once resized down. Keep
# these matched to the training imgsz unless you deliberately retrain at a
# different size.
#
# The two models are also given independent confidence thresholds: the
# fire/smoke model is the weaker of the two (33 epochs, mAP50~0.60) and
# fire/smoke signals are naturally smaller and rarer in-frame than a PPE
# item on a person, so it needs a lower bar to avoid missing real events.
# If that ends up flagging things like warm lighting or steam, raise
# FIRE_CONFIDENCE back up in .env.
CONFIDENCE = float(os.getenv("CONFIDENCE", "0.18"))  # legacy fallback for both models
INFERENCE_SIZE = int(os.getenv("INFERENCE_SIZE", "512"))  # legacy fallback for both models

PPE_CONFIDENCE = float(os.getenv("PPE_CONFIDENCE", os.getenv("CONFIDENCE", "0.18")))
FIRE_CONFIDENCE = float(os.getenv("FIRE_CONFIDENCE", "0.12"))

PPE_INFERENCE_SIZE = int(os.getenv("PPE_INFERENCE_SIZE", os.getenv("INFERENCE_SIZE", "512")))
FIRE_INFERENCE_SIZE = int(os.getenv("FIRE_INFERENCE_SIZE", os.getenv("INFERENCE_SIZE", "512")))

PPE_FRAME_SKIP = int(os.getenv("PPE_FRAME_SKIP", "2"))
FIRE_FRAME_SKIP = int(os.getenv("FIRE_FRAME_SKIP", "5"))

# ============================================================
# FIRE / SMOKE ALERT CONFIRMATION
# ============================================================
#
# A single-frame YOLO hit isn't enough to justify a siren/SMS/log
# entry on its own - a lighter being flicked, a lit cigarette, a
# stove flame glimpsed through a doorway, or a one-frame false
# positive (warm lighting, a reflection) would all fire a real "fire
# detected" alert otherwise. A genuine fire or smoke event PERSISTS -
# it's still there a second later. A lighter flick usually isn't.
#
# So a hazard class only gets promoted from "detected this frame" to
# an actual alert once it's been seen in FIRE_CONFIRM_FRAMES
# consecutive PROCESSED frames in a row (every FIRE_FRAME_SKIP-th raw
# frame - see cameras.py). Any single miss resets that streak to
# zero, so an intermittent/flickering false positive never
# accumulates toward an alert either. Detections that haven't hit the
# streak yet still show up on the live feed in a distinct "checking"
# style (see CameraWorker._draw) so the dashboard visibly shows the
# system noticing something and watching it, rather than either
# ignoring it or crying wolf.
#
# Set to 1 - the earliest possible: a fire/smoke box on a SINGLE
# processed frame is enough to alert/siren immediately, same as
# before this confirmation gate existed. There's then no "streak" to
# build up and no delay at all between detection and alert. The
# tradeoff is what this whole gate was built to avoid - a lighter
# flick, a lit cigarette, or a one-frame false positive (warm
# lighting, a reflection) will also trigger the siren, since nothing
# distinguishes a one-frame blip from a real sustained fire anymore.
# Raise this back to 2+ if that becomes a problem - each extra frame
# adds real-world delay before a genuine fire alerts too, roughly
# (FIRE_FRAME_SKIP / achieved-frame-rate) seconds per step, so watch
# the "AI FPS" the dashboard reports for your actual hardware.
FIRE_CONFIRM_FRAMES = int(os.getenv("FIRE_CONFIRM_FRAMES", "1"))

# A weak, OFF-BY-DEFAULT secondary filter: drops fire/smoke boxes
# smaller than this fraction of the frame area before confirmation
# counting even starts. Deliberately off (0) by default - a genuine
# small fire, far from the camera or just starting, is also small
# in-frame, so this is a blunt instrument that trades away real
# early-detection range. Only turn it on (e.g. 0.001-0.005) if
# FIRE_CONFIRM_FRAMES alone isn't enough for your specific camera
# framing, and after confirming it doesn't cost you real small-fire
# detection at the distances your cameras actually cover.
FIRE_MIN_BOX_AREA_RATIO = float(os.getenv("FIRE_MIN_BOX_AREA_RATIO", "0"))

# The extra-gear model only ever runs for a camera that's actually
# watching one of WATCHABLE_EXTRA_GEAR_ITEMS below (see cameras.py),
# so it can afford a slightly lower confidence bar and a wider skip
# than the core PPE model without costing CPU on cameras that never
# use it.
EXTRA_GEAR_CONFIDENCE = float(os.getenv("EXTRA_GEAR_CONFIDENCE", "0.25"))
EXTRA_GEAR_INFERENCE_SIZE = int(os.getenv("EXTRA_GEAR_INFERENCE_SIZE", "640"))
EXTRA_GEAR_FRAME_SKIP = int(os.getenv("EXTRA_GEAR_FRAME_SKIP", "3"))

JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "65"))

CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "640"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "480"))


# ============================================================
# VIOLATION HOTSPOT HEATMAP
# ============================================================
#
# A second, complementary way to see WHERE violations concentrate, on
# top of the raw incident feed:
#   - Per-camera: a low-res grid tracks where in the FRAME violations
#     have been landing, decayed every processed frame so it reflects
#     recent activity rather than accumulating forever. Opt-in per
#     camera (cameras.json's "show_heatmap" / PUT /cameras/{id}/
#     heatmap) and blended straight into that camera's own live MJPEG
#     feed when on - see CameraWorker._blend_heatmap.
#   - Site-wide: GET /analytics/hotspots ranks ZONES by how many
#     alerts (the same cooldown-gated ones the Alert Log shows) have
#     actually fired there this session - built entirely from the
#     existing in-memory alert history, no extra storage.
# Both are pure logic on top of what's already detected/logged - no
# retrain, no new model, no meaningful extra inference cost.
# ============================================================

# Resolution of the per-camera accumulation grid - deliberately coarse
# (not per-pixel): a hotspot is "this general area of the frame", not
# an exact point, and a coarse grid keeps the per-frame update trivial
# (a handful of array increments, not a full-frame operation).
HEATMAP_GRID_ROWS = int(os.getenv("HEATMAP_GRID_ROWS", "30"))
HEATMAP_GRID_COLS = int(os.getenv("HEATMAP_GRID_COLS", "40"))

# Multiplies the whole grid down every processed frame BEFORE adding
# this frame's violations, so the heatmap reflects recent activity -
# an area that had violations a while ago and none since fades out
# instead of staying "hot" forever. Close to 1.0 = slow fade (good for
# a single demo/judging run); lower = only very recent violations
# show at all.
HEATMAP_DECAY = float(os.getenv("HEATMAP_DECAY", "0.997"))

# How strongly the colorized heatmap is blended over the live frame,
# where it actually has signal (0 = invisible, 1 = fully replaces the
# frame there). Detection boxes/labels are drawn AFTER the blend (see
# CameraWorker._draw), so they always stay crisp on top of it.
HEATMAP_BLEND_ALPHA = float(os.getenv("HEATMAP_BLEND_ALPHA", "0.45"))


# ============================================================
# PPE CLASS MAPS
# ============================================================
#
# The core 5 classes (helmet/gloves/vest/boots/goggles, plus their
# no_* negatives) match data.yaml exactly - that's the construction-
# PPE model. mask/ear_protection/face_shield are added on top, from
# the separate extra-gear model (EXTRA_GEAR_MODEL_PATH above) - see
# EXTRA_GEAR_CLASS_MAP further down for how its raw class names map
# to these.
#
# IMPORTANT: the construction dataset has no "no_vest" class - the
# model only ever learned "vest present". ppe_logic.py compensates
# for that by inferring a vest violation when a detected person has
# no vest box associated with them (see ppe_logic.py for the
# inference + honesty notes on that) - and the extra-gear model has
# the exact same gap for all three of its classes, handled the same
# way via PRESENCE_ONLY_ITEMS below.
# ============================================================

PPE_COMPLIANT_CLASSES = {
    "helmet", "gloves", "vest", "boots", "goggles",
    "mask", "ear_protection", "face_shield",
}

PPE_EXPLICIT_VIOLATION_CLASSES = {
    "no_helmet", "no_gloves", "no_boots", "no_goggle", "no_goggles",
}

PPE_NEUTRAL_CLASSES = {"person", "none"}

# Required by the PS06 brief: helmet, high-vis vest, safety
# footwear (boots), gloves. Goggles is detected but not required.
REQUIRED_PPE_ITEMS = {"helmet", "vest", "gloves", "boots"}

# Every PPE item the model can be asked to check for, in a fixed
# display order - this is the full menu a camera's "watched items"
# can be picked from (see cameras.json's per-camera "watched_items"
# and PUT /cameras/{id}/watched-items). Goggles is included even
# though it's not one of the PS06-required items, since the model
# can detect it and an operator may still want it watched on a
# specific camera.
WATCHABLE_PPE_ITEMS = ["helmet", "vest", "gloves", "boots", "goggles"]

# "Other industry" gear - detected by the separate extra-gear model
# above (EXTRA_GEAR_MODEL_PATH), not the construction-PPE one. Same
# opt-in-per-camera mechanism as WATCHABLE_PPE_ITEMS, just listed
# separately so the dashboard can group them under their own heading
# instead of implying they're part of the PS06-required set. Never
# added to REQUIRED_PPE_ITEMS - a camera only checks for one of these
# if an operator explicitly turns it on.
WATCHABLE_EXTRA_GEAR_ITEMS = ["mask", "ear_protection", "face_shield"]

# Combined menu used wherever a watched-item name just needs
# validating (e.g. PUT /cameras/{id}/watched-items), regardless of
# which of the two models actually produces it.
ALL_WATCHABLE_PPE_ITEMS = WATCHABLE_PPE_ITEMS + WATCHABLE_EXTRA_GEAR_ITEMS

# Items with no explicit "not wearing X" class in ANY model - vest
# (see ppe_logic.py's module docstring on the "no_vest" gap) and all
# three extra-gear items, since the SH17 model that detects them was
# only ever trained to recognize their PRESENCE, the same gap. Either
# way, ppe_logic.py infers a violation when a detected person has no
# matching box, exactly like it already did for vest alone.
PRESENCE_ONLY_ITEMS = {"vest", "mask", "ear_protection", "face_shield"}

# Same idea, for the fire/smoke model: which hazard classes a camera
# reports on (cameras.json's "watched_hazards" / PUT /cameras/{id}/
# watched-hazards). Both are on by default - a camera only narrows
# this if a customization explicitly sets it (e.g. a camera aimed at
# a welding bay that should only ever raise "fire", never "smoke").
WATCHABLE_HAZARD_ITEMS = ["fire", "smoke"]

INCIDENT_LABELS = {
    "no_helmet": "No Helmet",
    "no_vest": "No Safety Vest",
    "no_gloves": "No Gloves",
    "no_boots": "No Safety Boots",
    "no_goggle": "No Safety Goggles",
    "no_goggles": "No Safety Goggles",
    "no_mask": "No Mask / Respirator",
    "no_ear_protection": "No Ear Protection",
    "no_face_shield": "No Face Shield",
    "fire": "Fire Detected",
    "smoke": "Smoke Detected",
}

# ============================================================
# EXTRA-GEAR CLASS MAP
# ============================================================
#
# The extra-gear model (SH17 dataset, 17 classes) detects far more
# than we use here - tools, glasses, regular shoes, medical/chemical
# suits, etc. This maps ONLY the raw class names we care about to the
# watchable item name the rest of the pipeline expects; every other
# class the model outputs is silently ignored (see
# ppe_logic.relabel_extra_gear_boxes).
# ============================================================

EXTRA_GEAR_CLASS_MAP = {
    "face-mask": "mask",
    "ear-mufs": "ear_protection",
    "face-guard": "face_shield",
}


# ============================================================
# UPLOADED VIDEOS
# ============================================================
#
# A recorded-video camera source has always worked as a typed-in
# path (cv2.VideoCapture treats a file path exactly like a webcam
# index or an RTSP URL). This is for the OTHER way to add one: a
# real upload from the dashboard's Add Camera form (POST
# /cameras/upload-video in main.py) - saved here, then used as an
# ordinary path from that point on. Nothing about camera handling
# itself changes; this only affects where a video file comes from.
# ============================================================

UPLOADED_VIDEOS_DIR = os.getenv(
    "UPLOADED_VIDEOS_DIR",
    os.path.join(BASE_DIR, "uploaded_videos"),
)

os.makedirs(UPLOADED_VIDEOS_DIR, exist_ok=True)

# Rejects an upload once it exceeds this many bytes, so a browser
# upload can't quietly fill the disk on a demo laptop. 500MB is
# generous for a short demo clip and small enough to catch an
# accidental wrong-file upload early with a clear error instead of a
# slow failure.
MAX_UPLOAD_VIDEO_BYTES = int(
    os.getenv("MAX_UPLOAD_VIDEO_BYTES", str(500 * 1024 * 1024))
)

# Extensions cv2's bundled ffmpeg can typically decode. Not
# exhaustive - anything else is rejected up front with a clear error
# rather than accepted and only failing later, deep inside a camera
# worker thread. main.py's upload endpoint also actually tries to
# open the file with cv2 before accepting it, so a file with the
# right extension but an unsupported codec still gets caught.
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


# ============================================================
# CAMERAS
# ============================================================
#
# Loaded from cameras.json so cameras (webcams, RTSP/CCTV URLs,
# or recorded video files - the brief explicitly allows either)
# can be added/changed without touching code. Falls back to a
# single webcam entry if the file is missing.
# ============================================================

CAMERAS_CONFIG_PATH = os.getenv(
    "CAMERAS_CONFIG_PATH",
    os.path.join(BASE_DIR, "cameras.json")
)


def load_cameras():

    if os.path.exists(CAMERAS_CONFIG_PATH):

        with open(CAMERAS_CONFIG_PATH, "r", encoding="utf-8") as f:
            cameras = json.load(f)

    else:

        cameras = [
            {
                "id": "CAM-01",
                "source": 0,
                "location": "Main Construction Area",
                "zone": "Zone A",
                "enabled": True,
            }
        ]

    # Normalize: numeric-looking string sources become int device indexes.
    for cam in cameras:

        source = cam.get("source", 0)

        if isinstance(source, str) and source.isdigit():
            cam["source"] = int(source)

        # Which PPE items THIS camera should check for. A MISSING key
        # means "everything the brief requires" (the original,
        # pre-customization behaviour) - an omitted field never
        # silently means "detect nothing". A key that's PRESENT but
        # set to an empty list is different and deliberate - "this
        # camera watches no PPE item" - and is kept as-is rather than
        # upgraded back to the default, so a customization saved via
        # PUT /cameras/{id}/watched-items survives a restart exactly
        # as the operator left it. Unknown item names are dropped
        # rather than rejected, so a hand-edited config with a typo
        # degrades gracefully instead of crashing startup.
        if "watched_items" in cam and cam["watched_items"] is not None:
            cam["watched_items"] = [
                item for item in cam["watched_items"] if item in ALL_WATCHABLE_PPE_ITEMS
            ]
        else:
            cam["watched_items"] = list(REQUIRED_PPE_ITEMS)

        # Same normalization for the fire/smoke hazard classes a
        # camera reports on - see WATCHABLE_HAZARD_ITEMS above.
        if "watched_hazards" in cam and cam["watched_hazards"] is not None:
            cam["watched_hazards"] = [
                item for item in cam["watched_hazards"] if item in WATCHABLE_HAZARD_ITEMS
            ]
        else:
            cam["watched_hazards"] = list(WATCHABLE_HAZARD_ITEMS)

        # Whether THIS camera blends its own violation-hotspot heatmap
        # into its live feed (PUT /cameras/{id}/heatmap) - opt-in, off
        # by default, same pattern as watched_items/watched_hazards.
        cam["show_heatmap"] = bool(cam.get("show_heatmap", False))

    return cameras


CAMERAS = load_cameras()


def save_cameras():
    """Persist the current in-memory CAMERAS list (including any
    runtime watched_items change from PUT /cameras/{id}/watched-items)
    back to cameras.json, so a per-camera customization survives a
    backend restart instead of silently reverting. Written to a temp
    file and swapped in with os.replace so a crash mid-write can't
    leave a truncated/corrupt config behind."""

    tmp_path = CAMERAS_CONFIG_PATH + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(CAMERAS, f, indent=2)

    os.replace(tmp_path, CAMERAS_CONFIG_PATH)


# ============================================================
# WORKER FACE IDENTITY (entry gate enrollment + live attribution)
# ============================================================
#
# Offline, on-device face recognition built entirely on OpenCV's own
# DNN module - NOT a cloud face API, NOT dlib/face_recognition (which
# is a painful build on Jetson's aarch64). Two small ONNX models from
# the official OpenCV Zoo, loaded through cv2.FaceDetectorYN (face
# detection) and cv2.FaceRecognizerSF (128-d embedding + built-in
# cosine/L2 matching) - both ship inside opencv-contrib-python, no
# extra inference runtime needed. See backend/models/README.md for
# the one-time download (small files, done once at setup, not at
# runtime - consistent with the rest of this project's offline-first
# design: nothing here needs internet once it's running).
#
# If the model files aren't present, or this OpenCV build lacks
# FaceDetectorYN/FaceRecognizerSF, face_id.py disables itself and
# prints why - exactly like VOICE_ALERTS_ENABLED/LOCAL_SIREN_ENABLED
# degrade when espeak-ng/an audio player isn't installed. Nothing
# else in the system depends on this: cameras, PPE/fire detection,
# alerts, footage all work identically whether or not any worker has
# ever been enrolled.
# ============================================================

FACE_ID_ENABLED = os.getenv("FACE_ID_ENABLED", "true").lower() not in (
    "false", "0", "no",
)

FACE_MODELS_DIR = os.getenv(
    "FACE_MODELS_DIR", os.path.join(BASE_DIR, "models")
)

FACE_DETECTOR_MODEL_PATH = os.getenv(
    "FACE_DETECTOR_MODEL_PATH",
    os.path.join(FACE_MODELS_DIR, "face_detection_yunet_2023mar.onnx"),
)

FACE_RECOGNIZER_MODEL_PATH = os.getenv(
    "FACE_RECOGNIZER_MODEL_PATH",
    os.path.join(FACE_MODELS_DIR, "face_recognition_sface_2021dec.onnx"),
)

# SFace's own recommended cosine-similarity threshold (OpenCV Zoo
# docs): two embeddings scoring at or above this are considered the
# same person. Raise it (closer to 1.0) to demand a more confident
# match and cut false positives; lower it if real workers are being
# missed because of camera angle/lighting at the gate.
FACE_MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.363"))

# Faces smaller than this (as a fraction of frame width) are skipped
# rather than matched - a distant, blurry, few-pixel face produces an
# unreliable embedding and is more likely to misidentify someone than
# to correctly identify them.
FACE_MIN_SIZE_RATIO = float(os.getenv("FACE_MIN_SIZE_RATIO", "0.06"))

# Where enrolled workers (name, department, required PPE, and their
# averaged face embedding) are stored - a flat JSON file, same
# no-database pattern as cameras.json. Enrollment photos themselves
# are kept too (for the dashboard's enrollment list), under
# WORKER_PHOTOS_DIR.
WORKERS_DB_PATH = os.getenv(
    "WORKERS_DB_PATH", os.path.join(BASE_DIR, "workers.json")
)

WORKER_PHOTOS_DIR = os.getenv(
    "WORKER_PHOTOS_DIR", os.path.join(BASE_DIR, "worker_photos")
)

os.makedirs(WORKER_PHOTOS_DIR, exist_ok=True)

# Running face detection+matching on every worker box, every
# processed frame, on top of the PPE and fire/smoke models is more
# than a Jetson needs to spend - identity only actually matters at
# the moment a violation is about to be logged (attributing WHO it
# was), not on every compliant frame. So live-feed worker ID only
# runs on workers who already have at least one PPE violation this
# frame. Set to false to also skip it there (face ID then only runs
# at the entry gate, never during ordinary live monitoring).
FACE_ID_ON_VIOLATIONS_ONLY = os.getenv(
    "FACE_ID_ON_VIOLATIONS_ONLY", "true"
).lower() not in ("false", "0", "no")


# ============================================================
# ENTRY GATE (biometric face check + PPE check before entry)
# ============================================================
#
# A worker steps up to a gate camera -> the backend detects + matches
# their face against WORKERS_DB_PATH -> if matched, the SAME frame is
# run through the existing PPE model -> entry is ALLOWED only if
# every item in GATE_REQUIRED_PPE_ITEMS is compliant on that frame,
# otherwise DENIED with the specific missing items. Every scan
# (matched or not, allowed or denied) is appended to GATE_LOG_PATH -
# same durable flat-file audit trail pattern as alert_log.jsonl.
# ============================================================

GATE_LOG_PATH = os.getenv(
    "GATE_LOG_PATH", os.path.join(BASE_DIR, "gate_log.jsonl")
)

# Defaults to the same four items the rest of the site requires
# (helmet/vest/gloves/boots) - override if the gate itself should
# check a different/smaller set (e.g. just helmet + vest).
GATE_REQUIRED_PPE_ITEMS_ENV = os.getenv("GATE_REQUIRED_PPE_ITEMS", "")
GATE_REQUIRED_PPE_ITEMS = (
    {item.strip() for item in GATE_REQUIRED_PPE_ITEMS_ENV.split(",") if item.strip()}
    if GATE_REQUIRED_PPE_ITEMS_ENV
    else set(REQUIRED_PPE_ITEMS)
)

# Same worker scanning again within this many seconds reuses the
# previous result instead of re-running detection - stops someone
# lingering in front of the gate camera from spamming the log/alarm.
GATE_SCAN_COOLDOWN = int(os.getenv("GATE_SCAN_COOLDOWN", "10"))

# ------------------------------------------------------------
# Gate camera source - an RTSP/CCTV feed (e.g. one channel of a
# DVR/NVR), read server-side with OpenCV, same mechanism cameras.json
# already uses for PPE/fire. This is DELIBERATELY SEPARATE from
# cameras.json: the gate camera isn't a general watched camera (no
# PPE-hazard grid, no heatmap, no /video_feed entry) - it only ever
# feeds gate.process_gate_scan(), either on a timer (GATE_CAMERA_
# AUTO_SCAN) or on demand (POST /gate/camera/capture, the dashboard's
# "Capture & Scan" button).
#
# A DVR/NVR channel is NOT a local webcam - a browser's getUserMedia()
# can never see it, since the video only exists as this RTSP network
# stream, not a USB/driver-level device on whatever machine is running
# the dashboard. That's why this is read here in the backend and
# served to the dashboard as JPEG snapshots/MJPEG, exactly like every
# other camera in this project, rather than asked for client-side.
#
# Typical Hikvision DVR/NVR RTSP URL (hybrid DVRs - analog channels
# wired straight into the DVR, not separate IP cameras - use this
# same URL shape, just via the DVR's own IP instead of each camera's):
#   rtsp://<user>:<password>@<dvr-ip>:554/Streaming/Channels/<CH><SS>
# where <CH> is the channel number (1-4 on a 4-channel DVR) and <SS>
# is "01" for the main/high-res stream or "02" for the lighter
# substream (e.g. channel 3's main stream = 301, its substream = 302).
# Leave blank (the default) to disable entirely - POST /gate/scan
# (manual photo upload) keeps working either way, same graceful-
# degradation pattern as every other optional/hardware feature here.
GATE_CAMERA_SOURCE = os.getenv("GATE_CAMERA_SOURCE", "")

# Seconds between frame reads from the gate camera - also the
# snapshot/MJPEG feed's effective refresh rate.
GATE_CAMERA_POLL_INTERVAL = float(os.getenv("GATE_CAMERA_POLL_INTERVAL", "1.0"))

# If true, the gate camera watches continuously and runs a scan on its
# own (throttled to roughly once per GATE_SCAN_COOLDOWN, and only when
# a face is actually in frame) - no one has to click anything for an
# entry to be logged. If false, the gate camera still streams a live
# preview to the dashboard, but a scan only happens when someone
# presses "Capture & Scan" (POST /gate/camera/capture).
GATE_CAMERA_AUTO_SCAN = os.getenv("GATE_CAMERA_AUTO_SCAN", "true").lower() not in (
    "false", "0", "no",
)


# ============================================================
# OFFLINE SMS TO SUPERVISOR (GSM/GPRS serial modem - no internet)
# ============================================================
#
# A SECOND, independent SMS channel alongside the optional Twilio one
# above - this one needs NO internet at all, just a GSM modem module
# (e.g. SIM800L/SIM900/A9) wired to this machine over USB/UART and
# talked to with plain AT commands (the same modem command set every
# cheap GSM module understands, so this isn't tied to one specific
# board). Both channels are independently optional and run side by
# side: Twilio needs internet but no extra hardware, this needs
# hardware but no internet, and a deployment can enable either,
# neither, or both.
# ============================================================

GSM_SMS_ENABLED = os.getenv("GSM_SMS_ENABLED", "false").lower() not in (
    "false", "0", "no",
)

# e.g. /dev/ttyUSB0 (Linux/Jetson) or COM3 (Windows dev machine).
GSM_SERIAL_PORT = os.getenv("GSM_SERIAL_PORT", "/dev/ttyUSB0")
GSM_BAUD_RATE = int(os.getenv("GSM_BAUD_RATE", "9600"))

# How long to wait for the modem to answer each AT command before
# giving up on that send - a cheap modem can be slow to register on
# the network, but this must never block the camera worker thread
# that triggered it (see gsm_sms.py - always run on its own thread).
GSM_SEND_TIMEOUT = float(os.getenv("GSM_SEND_TIMEOUT", "8"))

# Phone number the offline SMS goes to. Falls back to the same
# ALERT_PHONE_NUMBER Twilio uses, so a deployment only has to set one
# number for both channels unless they actually need to differ.
GSM_SUPERVISOR_NUMBER = os.getenv("GSM_SUPERVISOR_NUMBER", ALERT_PHONE_NUMBER)


# ============================================================
# FIRE / SMOKE SENSOR FUSION (hardware sensor + vision agreement)
# ============================================================
#
# Optional: an MCU (e.g. an Arduino reading an MQ2 gas/smoke sensor
# and a flame sensor) connected over serial, sending one line per
# reading in a simple "FLAME:<0|1>,SMOKE:<raw-analog-value>" text
# format. This machine just reads lines off that port - no GPIO
# library, no board-specific SDK, so the same code works whether the
# sensor MCU is plugged into the Jetson directly or into another
# machine on the same network-free serial link. Entirely optional:
# with FIRE_SENSOR_ENABLED=false (the default) or no sensor plugged
# in, fire/smoke detection behaves exactly as it does today - vision-
# only, nothing here gates or blocks it.
# ============================================================

FIRE_SENSOR_ENABLED = os.getenv("FIRE_SENSOR_ENABLED", "false").lower() not in (
    "false", "0", "no",
)

FIRE_SENSOR_SERIAL_PORT = os.getenv("FIRE_SENSOR_SERIAL_PORT", "/dev/ttyACM0")
FIRE_SENSOR_BAUD_RATE = int(os.getenv("FIRE_SENSOR_BAUD_RATE", "9600"))

# A reading older than this is treated as "unknown" rather than
# trusted - a frozen/disconnected sensor must never silently keep
# agreeing (or disagreeing) with vision forever.
FIRE_SENSOR_STALE_SECONDS = float(os.getenv("FIRE_SENSOR_STALE_SECONDS", "5"))

# Raw analog value above which the MQ2-style smoke reading counts as
# "smoke present" - depends on the specific sensor/wiring, so this is
# deliberately tunable rather than hardcoded; calibrate against clean
# air vs. actual smoke on the real hardware once it's connected.
FIRE_SENSOR_SMOKE_THRESHOLD = int(os.getenv("FIRE_SENSOR_SMOKE_THRESHOLD", "600"))

# If true, the sensor can raise a (lower-severity) alert entirely on
# its own even when the camera model sees nothing - useful since a
# gas/flame sensor often reacts faster or sees around an occluded
# camera angle. If false, the sensor only ever ADJUSTS the severity
# of a vision-triggered alert (agree -> more confident, disagree ->
# downgraded), never triggers one by itself.
FIRE_SENSOR_CAN_ALERT_ALONE = os.getenv(
    "FIRE_SENSOR_CAN_ALERT_ALONE", "false"
).lower() not in ("false", "0", "no")
