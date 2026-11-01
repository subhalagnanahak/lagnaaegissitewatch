# ============================================================
# CAMERAS
# ============================================================
#
# One CameraWorker per configured camera (see cameras.json).
# A "camera" can be a webcam index, a recorded video file path,
# or an RTSP/HTTP CCTV stream URL - cv2.VideoCapture handles all
# three the same way, so no special-casing is needed beyond
# picking the right backend for a plain integer webcam index.
# ============================================================

import re
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np

import config
import isapi_snapshot
from ppe_logic import (
    run_model,
    build_worker_compliance,
    flatten_ppe_detections,
    classify_fire_boxes,
    filter_boxes_by_watched_items,
    filter_fire_boxes_by_watched_hazards,
    filter_boxes_by_min_area,
    update_fire_confirmation,
    relabel_extra_gear_boxes,
    pad_box,
    point_in_box,
    box_center,
)
from alerts import process_alerts
import recording
import face_id
import fire_sensor


def _worker_identity_for_bbox(bbox, workers, frame_w, frame_h):
    """Best-effort: which (already face-identified) worker a flat PPE
    detection box belongs to, so the alert log/SMS can say WHO a
    violation was attributed to, not just WHAT/WHERE. Same
    padded-box-containment test build_worker_compliance() already
    uses to associate a PPE item box with a person - reapplied here
    against the detection's own box center. Returns the identity dict
    (name/worker_id/...) or None (no face match, or this detection's
    center doesn't fall inside any worker's padded box - e.g. a
    second, unidentified person in frame)."""

    center = box_center(bbox)

    for w in workers:

        if not w.get("identity"):
            continue

        if point_in_box(center, pad_box(w["bbox"], frame_w, frame_h)):
            return w["identity"]

    return None


# ============================================================
# CAMERA DISCOVERY - "how do I find the camera index" made automatic
# ============================================================
#
# Only webcam INDEXES can be auto-discovered this way (probing local
# /dev/videoN-style device slots) - a recorded video file path or an
# RTSP/HTTP CCTV stream URL has no "index" to scan for, so those are
# still typed in by hand in the Add Camera form.
# ============================================================

def _v4l2_device_names():
    """Best-effort friendly names for webcam indexes via `v4l2-ctl
    --list-devices` (Linux, and only if v4l-utils is installed).
    Purely cosmetic - discovery still works without it, just with
    generic 'Webcam N' names instead of e.g. 'HD Webcam C270'."""

    if not shutil.which("v4l2-ctl"):
        return {}

    try:
        output = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return {}

    names = {}
    current_name = None

    for line in output.splitlines():

        if line and not line.startswith(("\t", " ")):
            current_name = line.split(" (")[0].strip()
            continue

        match = re.match(r"/dev/video(\d+)", line.strip())

        if match and current_name:
            names[int(match.group(1))] = current_name

    return names


def _index_opens(index):
    """Mirrors CameraWorker.open()'s own fallback: try the explicit
    V4L2 backend first, then the default backend, since some webcams
    don't like being forced onto V4L2."""

    capture = None

    try:

        capture = cv2.VideoCapture(index, cv2.CAP_V4L2)

        if not capture.isOpened():

            capture.release()
            capture = cv2.VideoCapture(index)

        return bool(capture and capture.isOpened())

    except Exception:

        return False

    finally:

        if capture is not None:
            capture.release()


def discover_cameras(max_index=10):
    """Probes local webcam indexes 0..max_index-1 and reports which
    ones actually open - so 'Add Camera' can offer a pick list instead
    of the user having to find the index on the terminal themselves.
    An index already claimed by a configured camera is reported as
    in-use rather than re-probed, so this never touches (or steals
    frames from) an already-running camera's stream."""

    max_index = max(1, min(int(max_index), 32))

    used_by = {
        cam["source"]: cam["id"]
        for cam in config.CAMERAS
        if isinstance(cam.get("source"), int)
    }

    names = _v4l2_device_names()
    results = []

    for index in range(max_index):

        if index in used_by:

            results.append({
                "index": index,
                "name": names.get(index, f"Webcam {index}"),
                "available": False,
                "in_use_by": used_by[index],
            })
            continue

        if _index_opens(index):

            results.append({
                "index": index,
                "name": names.get(index, f"Webcam {index}"),
                "available": True,
                "in_use_by": None,
            })

    return results


class CameraWorker:

    def __init__(
        self, camera_id, source, location, zone, ppe_model, fire_model,
        watched_items=None, watched_hazards=None, extra_gear_model=None,
        show_heatmap=False,
    ):

        self.camera_id = camera_id
        self.source = source
        self.camera_info = {"location": location, "zone": zone}

        self.ppe_model = ppe_model
        self.fire_model = fire_model
        # "Other industry" gear (mask/ear protection/face shield) -
        # optional, may be None if extra_gear_detection/weights/... was
        # never downloaded. Only ever run for a camera that's actually
        # watching one of config.WATCHABLE_EXTRA_GEAR_ITEMS - see
        # _loop() below.
        self.extra_gear_model = extra_gear_model

        # Which PPE items THIS camera reports on - None means "not
        # configured, use the default required set"; an empty set is
        # a deliberate, distinct "watch nothing" state. Reassigned
        # wholesale (never mutated in place) by set_watched_items(),
        # so a read from _loop() mid-update is always a consistent
        # snapshot without needing a lock.
        self.watched_items = (
            set(config.REQUIRED_PPE_ITEMS) if watched_items is None else set(watched_items)
        )

        # Same idea for fire/smoke - which hazard classes THIS camera
        # reports on. None -> both fire and smoke (the default).
        self.watched_hazards = (
            set(config.WATCHABLE_HAZARD_ITEMS) if watched_hazards is None
            else set(watched_hazards)
        )

        self.capture = None
        self.running = False
        self.thread = None

        self.lock = threading.Lock()

        self.latest_jpeg = None
        self.latest_workers = []
        self.latest_detections = []
        self.fps = 0.0
        self.total_frames = 0
        self.online = False
        self.last_error = None
        self._last_open_attempt = 0.0

        self._cached_ppe_boxes = []
        self._cached_fire_boxes = []
        self._cached_extra_boxes = []

        # Per-hazard-class consecutive-detection streak, for the
        # confirmation gate below (config.FIRE_CONFIRM_FRAMES) - e.g.
        # {"fire": 2, "smoke": 0}. Starts empty; update_fire_confirmation
        # fills in whichever classes it actually sees.
        self._fire_confirm_counts = {}
        # Detected-this-cycle-but-not-yet-confirmed boxes, shown on the
        # live feed in a distinct "checking" style (_draw below) so the
        # dashboard visibly shows the system noticing something without
        # crying wolf on it.
        self._cached_fire_monitoring_boxes = []

        # Whether THIS camera blends its own violation-hotspot heatmap
        # into its live feed - opt-in, see config.py's "VIOLATION
        # HOTSPOT HEATMAP" section and set_heatmap_enabled() below.
        self.show_heatmap = bool(show_heatmap)

        # Low-res grid of WHERE in this camera's frame violations have
        # been landing, decayed every processed frame (config.
        # HEATMAP_DECAY) so it reflects recent activity. Always
        # accumulated regardless of whether it's currently being SHOWN
        # (cheap - a handful of array increments per frame), so
        # flipping show_heatmap on mid-session immediately reveals
        # whatever already built up instead of starting blank.
        self._heatmap = np.zeros(
            (config.HEATMAP_GRID_ROWS, config.HEATMAP_GRID_COLS), dtype="float32"
        )

        # Continuous CCTV-style recording of this camera's own live
        # (annotated) feed - see recording.py. One recorder per
        # worker regardless of how it was constructed (start_all,
        # add_camera, or set_camera_enabled all go through here), so
        # it never needs repeating at each call site.
        self.recorder = recording.FootageRecorder(
            camera_id=self.camera_id, location=location, zone=zone,
        )

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    def open(self):

        self._last_open_attempt = time.time()

        # Release whatever handle we may already be holding - important
        # on a *re*open attempt (reconnect after failure/disconnect),
        # otherwise the OS can consider the device still busy from our
        # own dead handle and every retry fails for that reason alone.
        if self.capture is not None:

            try:
                self.capture.release()
            except Exception:
                pass

            self.capture = None

        try:

            is_isapi = isapi_snapshot.is_isapi_source(self.source)

            if is_isapi:
                # A DVR's ISAPI JPEG-snapshot endpoint (http:// or
                # https://), polled instead of decoding an RTSP video
                # stream - used as a fallback when a DVR's RTSP/H.265
                # output is itself broken (see DVR_SETUP.md's "If the
                # DVR's HEVC stream is fundamentally broken" section).
                # Same .isOpened()/.read()/.release()/.set() surface
                # as cv2.VideoCapture, so nothing below this branch
                # needs to know the difference.
                self.capture = isapi_snapshot.ISAPISnapshotCapture(self.source)
            elif isinstance(self.source, int):
                self.capture = cv2.VideoCapture(self.source, cv2.CAP_V4L2)
            else:
                # Force the FFmpeg backend explicitly for an RTSP URL
                # rather than letting OpenCV auto-pick one - on some
                # builds (notably Jetson/L4T, where OpenCV is often
                # compiled with GStreamer support too) auto-selection
                # can land on GStreamer instead, which SILENTLY
                # IGNORES config.py's
                # OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp -
                # that env var only does anything through the FFmpeg
                # backend. Without it forced, you can apply that fix
                # and still see the exact same HEVC decode errors,
                # because the stream never actually went through
                # FFmpeg at all. Falls back to an unforced open below
                # if this build has no FFmpeg support.
                self.capture = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)

            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            try:
                self.capture.set(cv2.CAP_PROP_FPS, 30)
            except Exception:
                pass

            if not self.capture.isOpened() and not is_isapi:

                # Retry with the default backend (some webcams /
                # video files - or a build with no FFmpeg support -
                # don't like an explicit V4L2/FFmpeg backend). Not
                # applicable to an ISAPI snapshot source - its own
                # constructor already is the one real attempt, a
                # plain cv2.VideoCapture(url) would just fail again
                # anyway since ISAPI needs HTTP digest auth that
                # cv2.VideoCapture doesn't send.
                if self.capture is not None:
                    self.capture.release()

                self.capture = cv2.VideoCapture(self.source)

            self.online = bool(self.capture and self.capture.isOpened())

            if not self.online:

                self.last_error = (
                    f"Could not open source {self.source!r}. If this is a "
                    f"USB webcam index, another process may already have "
                    f"it open, the index may not match this camera (two "
                    f"physical cameras don't always land on consecutive "
                    f"numbers), or the user running this doesn't have "
                    f"permission (Linux: needs to be in the 'video' group)."
                )
                print(f"[{self.camera_id}] {self.last_error}")

            else:

                self.last_error = None
                print(f"[{self.camera_id}] opened source: {self.source}")

        except Exception as e:

            self.online = False
            self.last_error = str(e)
            print(f"[{self.camera_id}] failed to open: {e}")

    # --------------------------------------------------------
    # START / STOP
    # --------------------------------------------------------

    def start(self):

        self.open()

        self.running = True

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):

        self.running = False

        if self.thread is not None:
            self.thread.join(timeout=2)

        if self.capture is not None:
            self.capture.release()
            self.capture = None

        # Flushes the in-progress segment (with whatever frames it
        # already has) rather than leaving it open/unindexed - e.g.
        # a camera getting disabled or removed mid-recording still
        # ends up with a complete, indexed clip instead of a dangling
        # file no GET /footage call will ever show.
        self.recorder.close()

    # --------------------------------------------------------
    # WATCHED ITEMS (live, no restart needed)
    # --------------------------------------------------------

    def set_watched_items(self, items):
        """Change which PPE items this camera reports on, live - the
        next frame processed picks it up. None resets to the default
        required set; an empty list means "watch nothing"."""

        self.watched_items = (
            set(config.REQUIRED_PPE_ITEMS) if items is None else set(items)
        )

    def set_watched_hazards(self, items):
        """Same idea, for fire/smoke. None resets to both; an empty
        list means this camera won't raise fire/smoke alerts at all."""

        self.watched_hazards = (
            set(config.WATCHABLE_HAZARD_ITEMS) if items is None else set(items)
        )

    def set_heatmap_enabled(self, enabled):
        """Turns this camera's live violation-hotspot overlay on/off -
        the next frame picks it up. The accumulation grid itself is
        untouched either way (see __init__), so re-enabling shows
        whatever already built up rather than restarting blank."""

        self.show_heatmap = bool(enabled)

    # --------------------------------------------------------
    # ZONE / LOCATION NAME (live, no restart needed)
    # --------------------------------------------------------

    def set_camera_info(self, location=None, zone=None):
        """Renames this camera's location and/or zone label, live -
        camera_info is a plain dict read fresh every frame (by _draw,
        by alerts.raise_alert, by snapshot()), never cached, so a
        rename here shows up on the very next frame with no restart
        and no gap where an alert could fire with the old zone name."""

        if location is not None:
            self.camera_info["location"] = location

        if zone is not None:
            self.camera_info["zone"] = zone

        self.recorder.update_location_zone(location=location, zone=zone)

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    RECONNECT_INTERVAL = 5.0       # seconds between reopen attempts
    MAX_CONSECUTIVE_READ_FAILURES = 20  # ~1s of failed reads before we
                                         # declare a live source offline

    def _loop(self):

        frame_counter = 0
        fps_counter = 0
        fps_start = time.time()
        consecutive_failures = 0

        while self.running:

            if self.capture is None or not self.capture.isOpened():

                # Keep retrying instead of giving up forever - a USB
                # camera that wasn't fully enumerated yet at startup,
                # or one that was briefly busy, will often succeed a
                # few seconds later without anyone restarting anything.
                if time.time() - self._last_open_attempt >= self.RECONNECT_INTERVAL:
                    self.open()

                time.sleep(0.5)
                continue

            success, frame = self.capture.read()

            if not success:

                consecutive_failures += 1

                # A recorded video file simply ends - loop it so a
                # demo/judging run doesn't just go blank.
                if not isinstance(self.source, int):

                    self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    time.sleep(0.05)
                    continue

                # A live source (webcam/RTSP) that keeps failing to
                # deliver frames is either unplugged or crashed - stop
                # reporting it as online, and force a reopen attempt
                # rather than silently reading a dead handle forever.
                if consecutive_failures >= self.MAX_CONSECUTIVE_READ_FAILURES:

                    self.online = False
                    self.last_error = "Camera stopped delivering frames (unplugged or crashed?)"
                    print(f"[{self.camera_id}] {self.last_error}")

                    if self.capture is not None:
                        self.capture.release()
                        self.capture = None

                    consecutive_failures = 0

                time.sleep(0.05)
                continue

            consecutive_failures = 0

            self.total_frames += 1
            frame_counter += 1

            height, width = frame.shape[:2]

            if width != config.CAMERA_WIDTH or height != config.CAMERA_HEIGHT:
                frame = cv2.resize(
                    frame,
                    (config.CAMERA_WIDTH, config.CAMERA_HEIGHT),
                    interpolation=cv2.INTER_AREA,
                )

            # PPE - filtered down to whatever this camera is currently
            # configured to watch (set_watched_items() can change this
            # live, between frames, with no restart needed).
            if frame_counter % config.PPE_FRAME_SKIP == 0:

                raw_ppe_boxes = run_model(
                    self.ppe_model, frame, config.PPE_CONFIDENCE, config.PPE_INFERENCE_SIZE
                )
                self._cached_ppe_boxes = filter_boxes_by_watched_items(
                    raw_ppe_boxes, self.watched_items
                )

            # Fire / smoke - same live per-camera filtering as PPE,
            # PLUS a confirmation gate (config.FIRE_CONFIRM_FRAMES):
            # a hazard class only becomes a real alert once it's been
            # seen in that many consecutive PROCESSED frames in a row
            # - see update_fire_confirmation's docstring. This is what
            # stops a lighter flick / one-frame false positive from
            # ever reaching the siren/SMS/log pipeline, without
            # needing to retrain the model.
            if (
                self.fire_model is not None
                and frame_counter % config.FIRE_FRAME_SKIP == 0
            ):

                raw_fire_boxes = run_model(
                    self.fire_model, frame, config.FIRE_CONFIDENCE, config.FIRE_INFERENCE_SIZE
                )
                watched_fire_boxes = filter_fire_boxes_by_watched_hazards(
                    raw_fire_boxes, self.watched_hazards
                )
                watched_fire_boxes = filter_boxes_by_min_area(
                    watched_fire_boxes, width, height,
                    config.FIRE_MIN_BOX_AREA_RATIO,
                )

                self._fire_confirm_counts, confirmed_fire_boxes = (
                    update_fire_confirmation(
                        self._fire_confirm_counts, watched_fire_boxes,
                        config.FIRE_CONFIRM_FRAMES,
                    )
                )

                self._cached_fire_boxes = confirmed_fire_boxes
                # Detected this cycle but hasn't hit the streak yet -
                # kept only for the live-feed "checking" overlay
                # below, never fed into violations/alerts.
                self._cached_fire_monitoring_boxes = [
                    b for b in watched_fire_boxes if b not in confirmed_fire_boxes
                ]

            # Extra gear ("other industry" PPE: mask, ear protection,
            # face shield) - a second model, only ever run when this
            # camera is actually watching at least one of those items.
            # No point spending CPU on a model whose output nothing
            # here would use, and it keeps a camera that never touches
            # this feature exactly as fast as before it existed.
            extra_watched = self.watched_items & set(config.WATCHABLE_EXTRA_GEAR_ITEMS)

            if not extra_watched:

                self._cached_extra_boxes = []

            elif (
                self.extra_gear_model is not None
                and frame_counter % config.EXTRA_GEAR_FRAME_SKIP == 0
            ):

                raw_extra_boxes = run_model(
                    self.extra_gear_model, frame,
                    config.EXTRA_GEAR_CONFIDENCE, config.EXTRA_GEAR_INFERENCE_SIZE,
                )
                self._cached_extra_boxes = filter_boxes_by_watched_items(
                    relabel_extra_gear_boxes(raw_extra_boxes), self.watched_items
                )

            # Extra-gear boxes are merged straight into the PPE boxes:
            # they're all "person" + presence-only item boxes from
            # this point on, so worker association / per-item
            # compliance / drawing treat a mask box exactly like a
            # vest box, no special-casing needed downstream.
            combined_ppe_boxes = self._cached_ppe_boxes + self._cached_extra_boxes

            workers = build_worker_compliance(
                combined_ppe_boxes, width, height, self.watched_items
            )

            # Attribute a NAME to each worker (via the entry-gate face
            # registry) instead of leaving every worker as an
            # anonymous "Worker #N" - config.FACE_ID_ON_VIOLATIONS_ONLY
            # (default true) restricts the actual face-matching work
            # to workers who already have a PPE violation this frame,
            # since identity only matters for attribution, and a
            # Jetson doesn't need to spend extra compute identifying
            # someone who's fully compliant. A no-op (identity always
            # None) until face_id.py actually has model files + at
            # least one enrolled worker - see config.FACE_ID_ENABLED.
            workers = face_id.identify_workers_in_frame(frame, workers)

            ppe_detections = flatten_ppe_detections(combined_ppe_boxes, workers)
            fire_detections = classify_fire_boxes(self._cached_fire_boxes)

            # Tag each violation with WHO (if identified) and, for
            # fire/smoke, whether the optional hardware sensor agrees
            # - both purely informational additions to the detection
            # dict, consumed by alerts.create_alert_record() for the
            # Alert Log; neither changes whether/when an alert fires.
            for d in ppe_detections:
                if d.get("violation"):
                    d["identified_worker"] = _worker_identity_for_bbox(
                        d["bbox"], workers, width, height,
                    )

            for d in fire_detections:
                _, note = fire_sensor.fuse(d["class_name"], d["confidence"])
                d["sensor_note"] = note

            detections = ppe_detections + fire_detections

            # Always feeds the hotspot heatmap grid, whether or not
            # it's currently being displayed for this camera (see
            # show_heatmap's docstring above) - cheap even every frame,
            # since it's just a decay multiply plus one increment per
            # violation, never a full-frame operation.
            self._update_heatmap(
                [d for d in detections if d.get("violation")], width, height,
            )

            self._draw(
                frame, ppe_detections, fire_detections, detections,
                self._cached_fire_monitoring_boxes, workers,
            )

            fps_counter += 1
            now = time.time()
            elapsed = now - fps_start

            if elapsed >= 1.0:
                self.fps = fps_counter / elapsed
                fps_counter = 0
                fps_start = now

            cv2.putText(
                frame, f"AI FPS: {self.fps:.1f}", (10, 150),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2,
            )

            ok, buffer = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY]
            )

            if not ok:
                continue

            frame_jpeg = buffer.tobytes()

            # Continuous recording gets the SAME fully-annotated frame
            # the live feed/snapshot show (boxes, labels, heatmap
            # blend if on) - not a bare unmarked frame.
            self.recorder.write_frame(frame)

            # Alerting happens after drawing/encoding (not before) so
            # that a violation snapshot - if this alert actually fires
            # past the cooldown - captures the same annotated frame
            # the live feed shows (boxes, labels, UNSAFE banner), not
            # a bare unmarked frame.
            process_alerts(self.camera_id, self.camera_info, detections, frame_jpeg)

            with self.lock:
                self.latest_jpeg = frame_jpeg
                self.latest_workers = workers
                self.latest_detections = detections
                self.online = True

        print(f"[{self.camera_id}] worker stopped.")

    # --------------------------------------------------------
    # VIOLATION HOTSPOT HEATMAP
    # --------------------------------------------------------

    def _update_heatmap(self, violations, width, height):
        """Decays the existing grid, then adds a hit at each
        violation's box center this frame - see config.HEATMAP_DECAY's
        docstring for why decay happens every frame regardless of
        whether there's a violation right now (so an old hotspot fades
        even through a long stretch with nothing new)."""

        self._heatmap *= config.HEATMAP_DECAY

        rows, cols = self._heatmap.shape

        for d in violations:

            x1, y1, x2, y2 = d["bbox"]
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            col = min(cols - 1, max(0, int(cx / width * cols)))
            row = min(rows - 1, max(0, int(cy / height * rows)))

            self._heatmap[row, col] += 1.0

    def _blend_heatmap(self, frame):
        """Colorizes the accumulated grid and blends it over the live
        frame, only where it actually has signal - so a camera with no
        violations yet (an all-zero grid) isn't tinted by
        COLORMAP_JET's zero-value color. Called first in _draw, before
        any boxes/labels/text, so those stay crisp on top of it."""

        peak = float(self._heatmap.max())

        if peak <= 0:
            return

        height, width = frame.shape[:2]

        heat_norm = np.clip(self._heatmap / peak * 255, 0, 255).astype("uint8")
        heat_resized = cv2.resize(
            heat_norm, (width, height), interpolation=cv2.INTER_LINEAR
        )
        heat_blurred = cv2.GaussianBlur(heat_resized, (0, 0), sigmaX=18)
        heat_color = cv2.applyColorMap(heat_blurred, cv2.COLORMAP_JET)

        # Only tint pixels with real signal, not the whole frame at a
        # faint blue (COLORMAP_JET's value for 0).
        mask = heat_blurred > 12

        blended = cv2.addWeighted(
            frame, 1 - config.HEATMAP_BLEND_ALPHA,
            heat_color, config.HEATMAP_BLEND_ALPHA, 0,
        )
        frame[mask] = blended[mask]

    # --------------------------------------------------------
    # DRAWING
    # --------------------------------------------------------

    def _draw(
        self, frame, ppe_detections, fire_detections, all_detections,
        fire_monitoring_boxes=None, workers=None,
    ):

        if self.show_heatmap:
            self._blend_heatmap(frame)

        # Identified workers (face_id matched them against an
        # enrolled worker this frame) get a name tag above their box,
        # in its own pass so it's never hidden behind a PPE item
        # box's own label drawn right after it.
        for w in (workers or []):

            identity = w.get("identity")

            if not identity:
                continue

            x1, y1, x2, y2 = w["bbox"]
            label = f"{identity['name']} ({identity['worker_id']})"

            cv2.putText(
                frame, label, (x1, max(y1 - 24, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 220, 60), 2,
            )

        for d in ppe_detections:

            x1, y1, x2, y2 = d["bbox"]
            color = (0, 0, 255) if d["violation"] else (0, 255, 0)
            label = f"{'VIOLATION: ' if d['violation'] else ''}{d['class_name']} {d['confidence']:.2f}"

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame, label, (x1, max(y1 - 8, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2,
            )

        # Detected this cycle but hasn't held for FIRE_CONFIRM_FRAMES
        # yet - drawn thin/yellow and labeled "checking", NOT as an
        # alarm, so the live feed visibly shows the system noticing a
        # lighter/momentary flame without crying wolf on it. Drawn
        # before the confirmed loop below so a box that gets confirmed
        # later in this same list still ends up looking like the real
        # alarm, not the checking state.
        for box in (fire_monitoring_boxes or []):

            x1, y1, x2, y2 = box["bbox"]
            label = f"checking {box['class_name']} {box['confidence']:.2f}"

            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 1)
            cv2.putText(
                frame, label, (x1, max(y1 - 8, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1,
            )

        for d in fire_detections:

            x1, y1, x2, y2 = d["bbox"]
            is_fire = d["class_name"] == "fire"
            color = (0, 0, 255) if is_fire else (0, 165, 255)
            label = f"{'FIRE' if is_fire else 'SMOKE'} {d['confidence']:.2f}"

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
            cv2.putText(
                frame, label, (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2,
            )

        text = f"{self.camera_id} | {self.camera_info['location']} | {self.camera_info['zone']}"
        cv2.rectangle(frame, (10, 55), (620, 90), (0, 0, 0), -1)
        cv2.putText(
            frame, text, (18, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1
        )

        violations = [d for d in all_detections if d.get("violation")]
        status_text = f"UNSAFE | VIOLATIONS: {len(violations)}" if violations else "SAFE"
        status_color = (0, 0, 255) if violations else (0, 255, 0)

        cv2.putText(
            frame, status_text, (10, 120),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2,
        )

    # --------------------------------------------------------
    # READ STATE
    # --------------------------------------------------------

    def snapshot(self):

        with self.lock:

            workers = list(self.latest_workers)
            detections = list(self.latest_detections)

        violations = [d for d in detections if d.get("violation")]
        ppe_violations = [d for d in violations if d.get("type") == "ppe"]
        fire_violations = [d for d in violations if d.get("type") in ("fire", "smoke")]

        compliant_workers = sum(1 for w in workers if w["compliant"])

        return {
            "camera_id": self.camera_id,
            "location": self.camera_info["location"],
            "zone": self.camera_info["zone"],
            "online": self.online,
            "last_error": self.last_error,
            "watched_items": sorted(self.watched_items),
            "watched_hazards": sorted(self.watched_hazards),
            "show_heatmap": self.show_heatmap,
            "fps": round(self.fps, 2),
            "frame_count": self.total_frames,
            "workers": workers,
            "worker_count": len(workers),
            "compliant_worker_count": compliant_workers,
            "compliance_percent": (
                round(compliant_workers / len(workers) * 100)
                if workers else None
            ),
            "detections": detections,
            "violation_count": len(violations),
            "ppe_violation_count": len(ppe_violations),
            "fire_smoke_violation_count": len(fire_violations),
            "status": "UNSAFE" if violations else "SAFE",
        }

    def jpeg_generator(self):

        while self.running:

            with self.lock:
                frame = self.latest_jpeg

            if frame is None:
                time.sleep(0.05)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )

            time.sleep(0.01)


# ============================================================
# CAMERA MANAGER
# ============================================================

class CameraManager:

    def __init__(self, ppe_model, fire_model, extra_gear_model=None):

        self.ppe_model = ppe_model
        self.fire_model = fire_model
        self.extra_gear_model = extra_gear_model
        self.workers = {}

        # Guards add_camera/remove_camera/set_camera_enabled, which each
        # touch config.CAMERAS (a list - unlike the single-dict-field
        # updates in set_camera_watched_items etc.) AND self.workers
        # together; a lock keeps two concurrent requests (e.g. two
        # browser tabs) from corrupting either.
        self._lock = threading.Lock()

    def start_all(self):

        for cam in config.CAMERAS:

            if not cam.get("enabled", True):
                continue

            worker = CameraWorker(
                camera_id=cam["id"],
                source=cam["source"],
                location=cam["location"],
                zone=cam["zone"],
                ppe_model=self.ppe_model,
                fire_model=self.fire_model,
                watched_items=cam.get("watched_items"),
                watched_hazards=cam.get("watched_hazards"),
                extra_gear_model=self.extra_gear_model,
                show_heatmap=cam.get("show_heatmap", False),
            )

            worker.start()
            self.workers[cam["id"]] = worker

        recording.start_retention_cleanup()

    def stop_all(self):

        recording.stop_retention_cleanup()

        for worker in self.workers.values():
            worker.stop()

    def get(self, camera_id):

        return self.workers.get(camera_id)

    def all_snapshots(self):

        return [w.snapshot() for w in self.workers.values()]

    def set_camera_watched_items(self, camera_id, items):
        """Change which PPE items a camera reports on - takes effect
        immediately (no restart) and is persisted to cameras.json so
        it survives one. Returns False if camera_id isn't configured
        at all (a typo, or a camera that was since removed)."""

        target = next((cam for cam in config.CAMERAS if cam["id"] == camera_id), None)

        if target is None:
            return False

        target["watched_items"] = list(items)
        config.save_cameras()

        worker = self.workers.get(camera_id)

        if worker is not None:
            worker.set_watched_items(items)

        return True

    def set_camera_watched_hazards(self, camera_id, items):
        """Same idea as set_camera_watched_items, for fire/smoke."""

        target = next((cam for cam in config.CAMERAS if cam["id"] == camera_id), None)

        if target is None:
            return False

        target["watched_hazards"] = list(items)
        config.save_cameras()

        worker = self.workers.get(camera_id)

        if worker is not None:
            worker.set_watched_hazards(items)

        return True

    def set_camera_heatmap_enabled(self, camera_id, enabled):
        """Turns a camera's live violation-hotspot heatmap overlay
        on/off - PUT /cameras/{id}/heatmap. Persisted to
        cameras.json; the underlying accumulation grid is untouched
        (see CameraWorker.__init__), so toggling back on shows
        whatever already built up rather than restarting blank."""

        target = next((cam for cam in config.CAMERAS if cam["id"] == camera_id), None)

        if target is None:
            return False

        target["show_heatmap"] = bool(enabled)
        config.save_cameras()

        worker = self.workers.get(camera_id)

        if worker is not None:
            worker.set_heatmap_enabled(enabled)

        return True

    def add_camera(
        self, camera_id, source, location, zone, enabled=True,
        watched_items=None, watched_hazards=None,
    ):
        """Adds a brand-new camera at runtime - no backend restart. If
        enabled, its worker starts immediately; either way it's
        persisted to cameras.json right away. Returns (True, None) on
        success, or (False, error_message) if camera_id is already
        taken (compared case-insensitively so 'cam-01' can't collide
        with 'CAM-01')."""

        with self._lock:

            if any(cam["id"].lower() == camera_id.lower() for cam in config.CAMERAS):
                return False, f"Camera ID '{camera_id}' is already in use."

            resolved_watched_items = (
                list(watched_items) if watched_items is not None
                else list(config.REQUIRED_PPE_ITEMS)
            )
            resolved_watched_hazards = (
                list(watched_hazards) if watched_hazards is not None
                else list(config.WATCHABLE_HAZARD_ITEMS)
            )

            config.CAMERAS.append({
                "id": camera_id,
                "source": source,
                "location": location,
                "zone": zone,
                "enabled": enabled,
                "watched_items": resolved_watched_items,
                "watched_hazards": resolved_watched_hazards,
                "show_heatmap": False,
            })
            config.save_cameras()

            if enabled:

                worker = CameraWorker(
                    camera_id=camera_id,
                    source=source,
                    location=location,
                    zone=zone,
                    ppe_model=self.ppe_model,
                    fire_model=self.fire_model,
                    watched_items=resolved_watched_items,
                    watched_hazards=resolved_watched_hazards,
                    extra_gear_model=self.extra_gear_model,
                )
                worker.start()
                self.workers[camera_id] = worker

        return True, None

    def remove_camera(self, camera_id):
        """Stops (if running) and permanently deletes a camera from
        cameras.json. Returns False if camera_id isn't configured."""

        with self._lock:

            target_index = next(
                (i for i, cam in enumerate(config.CAMERAS) if cam["id"] == camera_id),
                None,
            )

            if target_index is None:
                return False

            worker = self.workers.pop(camera_id, None)

            if worker is not None:
                worker.stop()

            config.CAMERAS.pop(target_index)
            config.save_cameras()

        return True

    def set_camera_enabled(self, camera_id, enabled):
        """Starts or stops a camera's worker thread without deleting
        its configuration - e.g. temporarily disabling one that's
        unplugged, or switching on one that was added disabled
        (matches cameras.json's existing 'enabled' field). Returns
        False if camera_id isn't configured."""

        with self._lock:

            target = next((cam for cam in config.CAMERAS if cam["id"] == camera_id), None)

            if target is None:
                return False

            target["enabled"] = enabled
            config.save_cameras()

            if enabled:

                if camera_id not in self.workers:

                    worker = CameraWorker(
                        camera_id=camera_id,
                        source=target["source"],
                        location=target["location"],
                        zone=target["zone"],
                        ppe_model=self.ppe_model,
                        fire_model=self.fire_model,
                        watched_items=target.get("watched_items"),
                        watched_hazards=target.get("watched_hazards"),
                        extra_gear_model=self.extra_gear_model,
                        show_heatmap=target.get("show_heatmap", False),
                    )
                    worker.start()
                    self.workers[camera_id] = worker

            else:

                worker = self.workers.pop(camera_id, None)

                if worker is not None:
                    worker.stop()

        return True

    def update_camera_meta(self, camera_id, location=None, zone=None):
        """Renames a camera's location and/or zone label - live (the
        running worker's next frame/alert picks it up immediately, no
        restart) and persisted to cameras.json. Returns False if
        camera_id isn't configured."""

        target = next((cam for cam in config.CAMERAS if cam["id"] == camera_id), None)

        if target is None:
            return False

        if location is not None:
            target["location"] = location

        if zone is not None:
            target["zone"] = zone

        config.save_cameras()

        worker = self.workers.get(camera_id)

        if worker is not None:
            worker.set_camera_info(location=location, zone=zone)

        return True

    def camera_list(self):
        """All configured cameras, including disabled/offline ones,
        so the dashboard can show them as offline instead of hiding
        them entirely."""

        result = []

        for cam in config.CAMERAS:

            worker = self.workers.get(cam["id"])

            result.append({
                "camera_id": cam["id"],
                "location": cam["location"],
                "zone": cam["zone"],
                "source": cam["source"],
                "enabled": cam.get("enabled", True),
                "online": worker.online if worker else False,
                "last_error": worker.last_error if worker else None,
                # Prefer the live worker's copy (authoritative, reflects
                # any set_watched_items() call already applied) - fall
                # back to the config entry for a disabled/not-yet-started
                # camera, which has no worker to ask.
                "watched_items": (
                    sorted(worker.watched_items) if worker is not None
                    else cam.get("watched_items", list(config.REQUIRED_PPE_ITEMS))
                ),
                "watched_hazards": (
                    sorted(worker.watched_hazards) if worker is not None
                    else cam.get("watched_hazards", list(config.WATCHABLE_HAZARD_ITEMS))
                ),
                "show_heatmap": (
                    worker.show_heatmap if worker is not None
                    else cam.get("show_heatmap", False)
                ),
            })

        return result
