# ============================================================
# CONTINUOUS FOOTAGE RECORDING
# ============================================================
#
# Real CCTV-style recording, on top of the violation-snapshot system
# already in alerts.py: every enabled camera's own live (annotated)
# feed is continuously written to disk, cut into fixed-length clips
# (config.RECORDING_SEGMENT_SECONDS), independent of whether anything
# is actually happening in frame - a snapshot only exists because an
# alert fired; footage exists regardless, so you can see what
# happened right BEFORE an incident too, not just the instant after.
#
# No database here - metadata for each closed clip is appended as one
# JSON line to config.FOOTAGE_INDEX_PATH, the same flat-file pattern
# alerts.py already uses for its own audit log (LOCAL_ALERT_LOG_PATH).
# Good enough at hackathon/demo scale, and keeps this project's
# existing "no external database" shape rather than introducing one
# just for this feature.
#
# A footage clip itself stays camera/zone-based, not per-worker: just
# which camera, which zone, what time range, and whether a PPE
# violation / fire / smoke alert fired anywhere in that window
# (derived from alerts.py's in-memory alert_history, the same source
# GET /alerts and the violation hotspots already read - see
# _flags_for_window below). Worker identity (face_id.py) attributes a
# live violation or an entry-gate scan to a NAMED worker, but that
# attribution isn't folded into a footage SEGMENT's own record.
# ============================================================

import glob
import json
import os
import threading
import time
from datetime import datetime, timedelta

import cv2

import config
import alerts


# ============================================================
# CODEC FALLBACK
# ============================================================
#
# Not every machine has a working hardware/software H.264 encoder
# available to OpenCV's bundled ffmpeg (a headless Jetson or a bare
# Linux box commonly doesn't) - cv2.VideoWriter silently fails to
# open in that case rather than raising, so the candidates below are
# tried in order, most-compatible-container-first, until one actually
# opens. Once some candidate proves itself on this machine, later
# rotations skip straight to it via _known_working (module-level - it
# doesn't change camera to camera, only machine to machine) instead
# of re-attempting doomed codecs (and their ffmpeg console noise)
# forever; still self-healing if the cached one ever stops working
# (e.g. a USB capture card or codec library disappears).
# ============================================================

_CANDIDATES = [
    ("mp4", "avc1"),
    ("mp4", "H264"),
    ("mp4", "mp4v"),
    ("avi", "MJPG"),
]

_known_working = None
_codec_logged = False


def _segment_day_dir(camera_id, start_ts):

    day = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d")
    path = os.path.join(config.FOOTAGE_DIR, camera_id, day)
    os.makedirs(path, exist_ok=True)

    return path


def _try_open(day_dir, camera_id, fname_base, ext, fourcc_str, fps, size):

    path = os.path.join(day_dir, f"{fname_base}.{ext}")
    fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
    writer = cv2.VideoWriter(path, fourcc, fps, size)

    if writer is not None and writer.isOpened():
        return writer, path

    if writer is not None:
        writer.release()

    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

    return None, None


def _open_writer(day_dir, camera_id, fname_base, fps, size):

    global _known_working, _codec_logged

    if _known_working is not None:

        ext, fourcc_str = _known_working
        writer, path = _try_open(day_dir, camera_id, fname_base, ext, fourcc_str, fps, size)

        if writer is not None:
            return writer, path, os.path.relpath(path, config.FOOTAGE_DIR)

        print(f"[recording] previously-working codec {fourcc_str}/{ext} just "
              f"failed for {camera_id} - re-scanning candidates.")
        _known_working = None

    for ext, fourcc_str in _CANDIDATES:

        writer, path = _try_open(day_dir, camera_id, fname_base, ext, fourcc_str, fps, size)

        if writer is not None:

            _known_working = (ext, fourcc_str)

            if not _codec_logged:
                print(f"[recording] using {fourcc_str}/.{ext} for footage recording.")
                _codec_logged = True

            return writer, path, os.path.relpath(path, config.FOOTAGE_DIR)

    print(f"[recording] could not open ANY video writer for {camera_id} - "
          f"footage recording disabled for this segment.")

    return None, None, None


# ============================================================
# FOOTAGE INDEX (flat JSONL file - see module docstring)
# ============================================================

_index_lock = threading.Lock()


def _append_index(record):

    with _index_lock:

        os.makedirs(os.path.dirname(config.FOOTAGE_INDEX_PATH) or ".", exist_ok=True)

        with open(config.FOOTAGE_INDEX_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def _read_index():

    if not os.path.exists(config.FOOTAGE_INDEX_PATH):
        return []

    records = []

    with _index_lock:

        with open(config.FOOTAGE_INDEX_PATH, "r", encoding="utf-8") as f:

            for line in f:

                line = line.strip()

                if not line:
                    continue

                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue  # skip a corrupt/truncated line rather than crash

    return records


def _write_index(records):
    """Atomic full rewrite - only used by deletion/cleanup, which
    need to actually remove a line. Ordinary new-clip writes use the
    much cheaper _append_index above. Same temp-file + os.replace
    pattern as config.save_cameras, so a crash mid-write can't leave
    a truncated/corrupt index behind."""

    tmp_path = config.FOOTAGE_INDEX_PATH + ".tmp"

    with _index_lock:

        with open(tmp_path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")

        os.replace(tmp_path, config.FOOTAGE_INDEX_PATH)


def get_footage(
    camera_id=None, zone=None, date=None,
    has_violation=None, has_fire=None, has_smoke=None,
    order="desc", limit=100, offset=0,
):
    """date is a 'YYYY-MM-DD' string, matched against the clip's
    local start-of-day - same format the frontend's <input
    type="date"> gives. Includes each camera's in-progress segment
    (see _live_records) alongside already-closed clips, so a segment
    shows up here the moment it starts recording rather than only
    once it rotates out."""

    records = _read_index() + _live_records()

    if camera_id:
        records = [r for r in records if r["camera_id"] == camera_id]

    if zone:
        records = [r for r in records if r.get("zone") == zone]

    if date:
        records = [
            r for r in records
            if datetime.fromtimestamp(r["start_time"]).strftime("%Y-%m-%d") == date
        ]

    if has_violation is not None:
        records = [r for r in records if r.get("has_violation") == has_violation]

    if has_fire is not None:
        records = [r for r in records if r.get("has_fire") == has_fire]

    if has_smoke is not None:
        records = [r for r in records if r.get("has_smoke") == has_smoke]

    records.sort(key=lambda r: r["start_time"], reverse=(order != "asc"))

    total = len(records)
    page = records[offset:offset + limit]

    return page, total


def get_footage_stats():
    """Same closed-plus-in-progress merge as get_footage, so the
    Reports page's "Footage Clips" / storage-used stat cards count
    what's recording right now too, not just already-closed clips."""

    records = _read_index() + _live_records()

    total_bytes = sum(r.get("file_size_bytes", 0) for r in records)

    per_camera = {}

    for r in records:
        per_camera[r["camera_id"]] = per_camera.get(r["camera_id"], 0) + 1

    return {
        "total_segments": len(records),
        "total_bytes": total_bytes,
        "oldest_start_time": min((r["start_time"] for r in records), default=None),
        "newest_start_time": max((r["start_time"] for r in records), default=None),
        "by_camera": per_camera,
    }


def delete_segment(segment_id):

    records = _read_index()

    target = next((r for r in records if r["id"] == segment_id), None)

    if target is None:
        return False

    for key in ("file_path", "thumbnail_path"):

        rel = target.get(key)

        if not rel:
            continue

        abs_path = os.path.join(config.FOOTAGE_DIR, rel)

        try:
            os.remove(abs_path)
        except OSError:
            pass

    remaining = [r for r in records if r["id"] != segment_id]
    _write_index(remaining)

    return True


# ============================================================
# FOOTAGE RECORDER (one per camera)
# ============================================================
#
# _active_recorders lets get_footage()/get_footage_stats() below show
# the segment a camera is recording RIGHT NOW, not just clips that
# have already rotated out and been written to the index - otherwise
# a freshly-added camera shows nothing in the Footage page for up to
# RECORDING_SEGMENT_SECONDS even though its .mp4 already exists on
# disk and is visibly growing, which looks like recording is broken
# when it isn't.
# ============================================================

_active_recorders = {}
_active_lock = threading.Lock()


def _live_records():
    """record-shaped dicts for every camera's in-progress segment
    (skipping any camera that hasn't written a frame yet), merged
    into get_footage()/get_footage_stats() alongside the persisted
    index."""

    with _active_lock:
        recorders = list(_active_recorders.values())

    records = []

    for rec in recorders:

        snap = rec.snapshot()

        if snap is not None:
            records.append(snap)

    return records


class FootageRecorder:

    def __init__(self, camera_id, location, zone):

        self.camera_id = camera_id
        self.location = location
        self.zone = zone

        self._lock = threading.Lock()

        self._writer = None
        self._path = None          # absolute
        self._rel_path = None      # relative to config.FOOTAGE_DIR
        self._start_time = None
        self._frame_size = None
        self._frame_count = 0
        self._thumbnail_rel = None
        self._next_write_at = 0.0

        with _active_lock:
            _active_recorders[self.camera_id] = self

    def snapshot(self):
        """A record-shaped dict for the segment currently being
        written, if any (None if nothing has actually been written
        to it yet) - same shape _close_locked appends to the index,
        plus in_progress=True and an end_time/duration that advance
        on every call rather than being fixed at close time."""

        with self._lock:

            if self._writer is None or self._path is None or self._frame_count == 0:
                return None

            camera_id = self.camera_id
            location = self.location
            zone = self.zone
            start_time = self._start_time
            rel_path = self._rel_path
            thumbnail_rel = self._thumbnail_rel
            frame_count = self._frame_count
            path = self._path

        now = time.time()

        try:
            file_size = os.path.getsize(path)
        except OSError:
            file_size = 0

        flags = _flags_for_window(camera_id, start_time, now)

        return {
            "id": f"{camera_id}_{int(start_time * 1000)}",
            "camera_id": camera_id,
            "location": location,
            "zone": zone,
            "start_time": start_time,
            "end_time": now,
            "duration_seconds": round(now - start_time, 1),
            "file_path": rel_path,
            "thumbnail_path": thumbnail_rel,
            "file_size_bytes": file_size,
            "frame_count": frame_count,
            "created_at": datetime.now().isoformat(),
            "in_progress": True,
            **flags,
        }

    def update_location_zone(self, location=None, zone=None):
        """Mirrors CameraWorker.set_camera_info's live rename (no
        restart needed) - a plain attribute reassignment is safe
        without the lock (single string swap, not a read-modify-
        write), and only affects the NEXT segment's metadata; the
        currently-open segment keeps recording under whatever name
        was current when it started."""

        if location is not None:
            self.location = location

        if zone is not None:
            self.zone = zone

    def write_frame(self, frame):

        if not config.RECORDING_ENABLED:
            return

        height, width = frame.shape[:2]
        size = (width, height)
        now = time.time()

        with self._lock:

            needs_new_segment = (
                self._writer is None
                or size != self._frame_size
                or (now - self._start_time) >= config.RECORDING_SEGMENT_SECONDS
            )

            if needs_new_segment:
                self._rotate_locked(now, size)

            if self._writer is None:
                return  # no working codec on this machine - see _open_writer

            if now < self._next_write_at:
                return

            self._next_write_at = now + (1.0 / config.RECORDING_FPS)

            self._writer.write(frame)
            self._frame_count += 1

            if self._frame_count == 1:
                self._save_thumbnail_locked(frame)

    def _rotate_locked(self, now, size):

        self._close_locked(end_ts=now)

        fname_base = f"{self.camera_id}_{int(now * 1000)}"
        day_dir = _segment_day_dir(self.camera_id, now)

        writer, path, rel_path = _open_writer(
            day_dir, self.camera_id, fname_base, config.RECORDING_FPS, size,
        )

        self._writer = writer
        self._path = path
        self._rel_path = rel_path
        self._start_time = now
        self._frame_size = size
        self._frame_count = 0
        self._thumbnail_rel = None

    def _save_thumbnail_locked(self, frame):

        if self._path is None:
            return

        thumb_path = os.path.splitext(self._path)[0] + "_thumb.jpg"

        ok, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY],
        )

        if not ok:
            return

        try:
            with open(thumb_path, "wb") as f:
                f.write(buffer.tobytes())
        except OSError:
            return

        self._thumbnail_rel = os.path.relpath(thumb_path, config.FOOTAGE_DIR)

    def _close_locked(self, end_ts=None):

        if self._writer is None:
            return

        self._writer.release()
        self._writer = None

        end_ts = end_ts if end_ts is not None else time.time()

        if self._frame_count == 0:

            # Nothing was ever actually written (e.g. the very first
            # write_frame call opened the writer but then hit the
            # rate gate before it could write anything, and then the
            # camera stopped) - no clip worth keeping a database row
            # for, so clean up the empty file rather than leave orphan
            # 0-byte clips on disk.
            for path in (self._path, ):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

            self._path = None
            self._rel_path = None
            return

        try:
            file_size = os.path.getsize(self._path) if self._path else 0
        except OSError:
            file_size = 0

        flags = _flags_for_window(self.camera_id, self._start_time, end_ts)

        record = {
            "id": f"{self.camera_id}_{int(self._start_time * 1000)}",
            "camera_id": self.camera_id,
            "location": self.location,
            "zone": self.zone,
            "start_time": self._start_time,
            "end_time": end_ts,
            "duration_seconds": round(end_ts - self._start_time, 1),
            "file_path": self._rel_path,
            "thumbnail_path": self._thumbnail_rel,
            "file_size_bytes": file_size,
            "frame_count": self._frame_count,
            "created_at": datetime.now().isoformat(),
            **flags,
        }

        try:
            _append_index(record)
        except OSError as e:
            print(f"[recording] failed to write footage index entry: {e}")

    def close(self):

        with self._lock:
            self._close_locked()

        with _active_lock:
            _active_recorders.pop(self.camera_id, None)


def _flags_for_window(camera_id, start_ts, end_ts):
    """Scans the existing in-memory alert history (alerts.py) for
    this camera in [start_ts, end_ts) - the same cooldown-gated
    entries GET /alerts and the violation hotspots already read, so
    this costs nothing extra to compute. Best-effort, like the rest
    of this project's in-memory alert history: it's capped
    (alerts.MAX_ALERT_HISTORY) and clearable (DELETE /alerts), so a
    very old segment's flags can go stale if the log has since
    rolled over or been cleared - acceptable at demo/hackathon scale,
    same tradeoff the violation hotspots already accept."""

    has_violation = False
    has_fire = False
    has_smoke = False

    for alert in alerts.get_alert_history():

        if alert.get("camera_id") != camera_id:
            continue

        try:
            ts = datetime.fromisoformat(alert["timestamp"]).timestamp()
        except (KeyError, ValueError):
            continue

        if not (start_ts <= ts < end_ts):
            continue

        class_name = alert.get("class_name")

        if class_name == "fire":
            has_fire = True
        elif class_name == "smoke":
            has_smoke = True
        else:
            has_violation = True

    return {
        "has_violation": has_violation,
        "has_fire": has_fire,
        "has_smoke": has_smoke,
    }


# ============================================================
# RETENTION CLEANUP (background thread, same daemon-thread pattern
# CameraWorker already uses)
# ============================================================

_cleanup_thread = None
_cleanup_running = False


def run_cleanup_pass():
    """One pass: delete anything past RECORDING_RETENTION_DAYS, then
    if still over RECORDING_MAX_STORAGE_GB, delete oldest-first until
    under the cap. Either limit can be 0 to disable it independently."""

    records = _read_index()
    changed = False

    if config.RECORDING_RETENTION_DAYS > 0:

        cutoff = (datetime.now() - timedelta(days=config.RECORDING_RETENTION_DAYS)).timestamp()
        expired = [r for r in records if r["start_time"] < cutoff]

        for r in expired:
            delete_segment(r["id"])
            changed = True

        if expired:
            records = _read_index()

    if config.RECORDING_MAX_STORAGE_GB > 0:

        cap_bytes = config.RECORDING_MAX_STORAGE_GB * (1024 ** 3)
        total = sum(r.get("file_size_bytes", 0) for r in records)

        if total > cap_bytes:

            for r in sorted(records, key=lambda r: r["start_time"]):

                if total <= cap_bytes:
                    break

                total -= r.get("file_size_bytes", 0)
                delete_segment(r["id"])
                changed = True

    return changed


def _cleanup_loop():

    global _cleanup_running

    elapsed = 0

    while _cleanup_running:

        time.sleep(1)
        elapsed += 1

        if elapsed >= config.RECORDING_CLEANUP_INTERVAL:

            elapsed = 0

            try:
                run_cleanup_pass()
            except Exception as e:
                print(f"[recording] cleanup pass failed: {e}")


def start_retention_cleanup():

    global _cleanup_thread, _cleanup_running

    if config.RECORDING_RETENTION_DAYS <= 0 and config.RECORDING_MAX_STORAGE_GB <= 0:
        return  # both caps disabled - nothing to ever clean up

    if _cleanup_running:
        return

    _cleanup_running = True
    _cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
    _cleanup_thread.start()


def stop_retention_cleanup():

    global _cleanup_running

    _cleanup_running = False
