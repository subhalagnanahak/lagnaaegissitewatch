# ============================================================
# GATE CAMERA - pulls the entry gate's live feed directly from a
# CCTV/DVR channel (RTSP) server-side, instead of asking a browser
# for local webcam access.
#
# Why this exists: a DVR/NVR camera channel (e.g. a Hikvision hybrid
# DVR's 4 analog/IP channels) is NOT a local webcam. A browser's
# navigator.mediaDevices.getUserMedia() can only ever see a camera the
# OS itself exposes as a local capture device (USB/UVC, a laptop's
# built-in cam) - it has no way to reach a camera that only exists as
# a network stream coming out of a DVR somewhere on the LAN. So the
# gate camera is read here, server-side, with OpenCV (which
# understands RTSP URLs natively, exactly like cameras.json's entries
# already do for PPE/fire), and served to the dashboard as plain JPEG
# snapshots / an MJPEG feed - the same pattern CameraWorker uses in
# cameras.py, just for one special-purpose, gate-only camera that
# isn't part of the general watched-camera grid.
#
# Two independent things happen here:
#   1. A background thread keeps one frame "latest" at all times, so
#      GET /gate/camera/feed (MJPEG) and the "Capture & Scan" button
#      always have something fresh to show/use - no per-request RTSP
#      connect latency.
#   2. If config.GATE_CAMERA_AUTO_SCAN is on, that same thread also
#      runs gate.process_gate_scan() on its own whenever a face shows
#      up in frame, throttled to roughly once every
#      config.GATE_SCAN_COOLDOWN seconds (a cheap face-presence check
#      runs every tick; the full match+PPE pipeline only runs when
#      something's actually there AND the throttle has cleared) - an
#      unattended gate then logs entries without anyone touching the
#      dashboard at all. Set it to false to keep the live preview but
#      require an explicit "Capture & Scan" click for every entry.
#
# Disables itself cleanly - is_available() stays False, the dashboard
# shows a clear reason - if GATE_CAMERA_SOURCE is blank or the stream
# can't be opened. POST /gate/scan (manual photo upload, unaffected by
# any of this) keeps working either way, same graceful-degradation
# pattern as every other optional/hardware feature in this project.
# ============================================================

import threading
import time

import cv2

import config
import face_id
import gate
import isapi_snapshot


_lock = threading.Lock()
_latest_frame = None
_latest_jpeg = None
_connected = False
_last_error = ""
_last_auto_scan_attempt = 0.0

_thread = None
_stop_event = threading.Event()
_ppe_model = None  # set by start() - the already-loaded YOLO PPE model


def is_available():

    with _lock:
        return _connected


def status():

    with _lock:
        return {
            "configured": bool(config.GATE_CAMERA_SOURCE),
            "connected": _connected,
            "auto_scan": config.GATE_CAMERA_AUTO_SCAN,
            "error": _last_error or None,
        }


def get_latest_frame():

    with _lock:
        return None if _latest_frame is None else _latest_frame.copy()


def get_latest_jpeg():

    with _lock:
        return _latest_jpeg


def jpeg_generator():
    """Same MJPEG multipart-stream shape as CameraWorker.jpeg_generator()
    in cameras.py - GET /gate/camera/feed just wraps this in a
    StreamingResponse, identical to /video_feed/{camera_id}."""

    while not _stop_event.is_set():

        frame = get_latest_jpeg()

        if frame is None:
            time.sleep(0.2)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )

        time.sleep(0.05)


def capture_and_scan():
    """Manual trigger (the dashboard's "Capture & Scan" button, and
    enrollment's "Add Photo") - grabs whatever frame is currently
    live and runs it through gate.process_gate_scan(), bypassing the
    auto-scan throttle since this is an explicit, deliberate action.
    Returns the same result shape as POST /gate/scan."""

    frame = get_latest_frame()

    if frame is None:
        return {
            "error": "No live frame from the gate camera yet - "
                     "check GATE_CAMERA_SOURCE and that the DVR is reachable."
        }

    return gate.process_gate_scan(frame, _ppe_model, camera_id="GATE-DVR")


def _reader_loop():

    global _latest_frame, _latest_jpeg, _connected, _last_error
    global _last_auto_scan_attempt

    cap = None

    while not _stop_event.is_set():

        if not config.GATE_CAMERA_SOURCE:

            with _lock:
                _connected = False
                _last_error = "GATE_CAMERA_SOURCE not set"

            time.sleep(5)
            continue

        if cap is None or not cap.isOpened():

            # GATE_CAMERA_SOURCE comes from .env as a plain string, so
            # a local-device index (e.g. "2", from a USB capture
            # card) needs converting to an actual int before
            # cv2.VideoCapture will treat it as a device rather than
            # trying (and failing) to open it as a URL/filename - the
            # same int-vs-string handling cameras.py's
            # CameraWorker.open() already does for cameras.json
            # entries. This matters for the USB BNC/composite
            # capture-card fallback (see DVR_SETUP.md's "DVR HEVC
            # stream is broken" section) - plug a capture card in for
            # a channel whose DVR RTSP/HEVC output can't be fixed, and
            # GATE_CAMERA_SOURCE just becomes a plain device number
            # again, exactly like a local webcam.
            source = config.GATE_CAMERA_SOURCE.strip()
            is_isapi = isapi_snapshot.is_isapi_source(source)
            is_device_index = source.isdigit()

            if is_isapi:
                # A DVR's ISAPI JPEG-snapshot endpoint (http:// or
                # https://), polled instead of decoding an RTSP video
                # stream - the fallback for a DVR whose RTSP/H.265
                # output is itself broken (see DVR_SETUP.md's "If the
                # DVR's HEVC stream is fundamentally broken" section).
                cap = isapi_snapshot.ISAPISnapshotCapture(source)
            elif is_device_index:
                cap = cv2.VideoCapture(int(source), cv2.CAP_V4L2)
            else:
                # Force the FFmpeg backend explicitly rather than letting
                # OpenCV auto-pick one - on some builds (Jetson/L4T in
                # particular, where GStreamer support is often compiled
                # in alongside FFmpeg) auto-selection can land on
                # GStreamer instead, which silently ignores config.py's
                # OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp (that
                # only does anything through the FFmpeg backend) - without
                # this forced, the TCP fix can be in place and you'd still
                # see the same HEVC decode errors, because the stream
                # never went through FFmpeg at all.
                cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)

            if not cap.isOpened() and not is_isapi:
                # Fall back to an unforced open - a build with no
                # FFmpeg/V4L2 support as forced above would otherwise
                # never connect at all. Not applicable to an ISAPI
                # snapshot source - see the matching comment in
                # cameras.py's CameraWorker.open().
                cap.release()
                cap = cv2.VideoCapture(int(source) if is_device_index else source)

            if not cap.isOpened():

                with _lock:
                    _connected = False
                    _last_error = (
                        "Could not open GATE_CAMERA_SOURCE - check the DVR "
                        "IP/port, RTSP credentials, and that this machine "
                        "can reach it on the network."
                    )

                time.sleep(5)
                continue

            print(f"[gate_camera] connected to {config.GATE_CAMERA_SOURCE!r}")

        ok, frame = cap.read()

        if not ok or frame is None:

            with _lock:
                _connected = False
                _last_error = "Lost connection to the gate camera - retrying"

            try:
                cap.release()
            except Exception:
                pass

            cap = None
            time.sleep(3)
            continue

        jpeg_ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY]
        )

        with _lock:
            _latest_frame = frame
            _latest_jpeg = buf.tobytes() if jpeg_ok else _latest_jpeg
            _connected = True
            _last_error = ""

        if config.GATE_CAMERA_AUTO_SCAN and face_id.is_available() and _ppe_model is not None:

            now = time.time()

            if (now - _last_auto_scan_attempt) >= config.GATE_SCAN_COOLDOWN:

                # Cheap presence check before paying for the full
                # match + PPE pass - most ticks nobody's at the gate.
                try:
                    has_face = bool(face_id.detect_faces(frame))
                except Exception:
                    has_face = False

                if has_face:

                    _last_auto_scan_attempt = now

                    try:
                        gate.process_gate_scan(frame, _ppe_model, camera_id="GATE-DVR")
                    except Exception as e:
                        print(f"[gate_camera] auto-scan failed (non-fatal): {e}")

        time.sleep(config.GATE_CAMERA_POLL_INTERVAL)

    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass


def start(ppe_model):
    """ppe_model: the already-loaded YOLO PPE model (main.py's global
    `ppe_model`) - passed in rather than imported, same as every other
    call site of gate.process_gate_scan(), so this module never has to
    know how/when models get loaded."""

    global _thread, _ppe_model

    _ppe_model = ppe_model

    if _thread is not None and _thread.is_alive():
        return

    if not config.GATE_CAMERA_SOURCE:
        print("[gate_camera] GATE_CAMERA_SOURCE not set - gate camera disabled "
              "(POST /gate/scan with an uploaded photo still works).")
        return

    _stop_event.clear()
    _thread = threading.Thread(target=_reader_loop, daemon=True)
    _thread.start()
    print(f"[gate_camera] reader thread started "
          f"(auto_scan={config.GATE_CAMERA_AUTO_SCAN})")


def stop():

    _stop_event.set()

    if _thread is not None:
        _thread.join(timeout=3)
