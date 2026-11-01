# ============================================================
# ISAPI SNAPSHOT CAPTURE - a cv2.VideoCapture-compatible wrapper that
# polls a Hikvision-style DVR's JPEG snapshot endpoint (ISAPI) over
# plain HTTP, instead of decoding its RTSP/H.265 video stream.
#
# Why this exists: on at least some OEM/rebadged hybrid DVRs, the
# RTSP stream's H.265 encoder output is genuinely non-compliant with
# the spec (confirmed independently via ffmpeg/ffplay, VLC, and
# OpenCV all rejecting the same stream the same way - a firmware bug,
# not a transport/config/client problem - see DVR_SETUP.md). The
# DVR's ISAPI snapshot endpoint
# (/ISAPI/Streaming/channels/<CH><SS>/picture) returns a single,
# already-encoded JPEG image per request, on a completely separate
# code path from the broken video encoder - so it isn't affected by
# that bug at all. Polling it on an interval (instead of decoding a
# continuous video stream) is what "live video" becomes here.
#
# This is a DROP-IN replacement for cv2.VideoCapture wherever a
# camera's source is an http(s):// ISAPI URL instead of an rtsp://
# URL or a local device index - cameras.py's CameraWorker and
# gate_camera.py's reader loop both just call .isOpened()/.read()/
# .release() on whatever open() gives them, so neither needs to know
# or care which kind of source it's actually talking to.
# ============================================================

import time
from urllib.parse import urlsplit, urlunsplit, unquote

import cv2
import numpy as np
import requests
from requests.auth import HTTPDigestAuth


def is_isapi_source(source):
    """True for any http(s):// source string - the one thing that
    distinguishes an ISAPI snapshot URL from an rtsp:// URL or a
    local device index (an int) at every call site that needs to
    pick which kind of capture object to construct."""

    return isinstance(source, str) and source.lower().startswith(("http://", "https://"))


class ISAPISnapshotCapture:
    """Same shape as cv2.VideoCapture - isOpened()/read()/release()/
    set() - so it can be used anywhere a CameraWorker currently does
    self.capture = cv2.VideoCapture(...) without touching the rest of
    that class's logic (the main per-frame loop, drawing, recording,
    alerts, etc. are all unaffected by which kind of source this is).

    `source` is the full URL exactly as a user would type it,
    credentials included - e.g.:
      http://admin:Robo%407788@192.168.29.161:31903/ISAPI/Streaming/channels/101/picture
    (the same shape as an rtsp:// source already used elsewhere in
    this project) - username/password are parsed out and used for
    HTTP DIGEST auth (what Hikvision's ISAPI requires; a plain
    user:pass@host URL alone, as cv2.VideoCapture or a browser would
    send via Basic auth, does NOT work against ISAPI - this is why a
    real HTTP client with explicit digest support is needed here
    instead of just handing the URL to cv2.VideoCapture directly)."""

    def __init__(self, source, poll_interval=0.5, timeout=5):

        parts = urlsplit(source)

        # Request URL WITHOUT embedded credentials - requests.get()
        # takes those separately via the auth= parameter.
        self._url = urlunsplit((
            parts.scheme, parts.hostname if not parts.port
            else f"{parts.hostname}:{parts.port}",
            parts.path, parts.query, parts.fragment,
        ))

        # urlsplit()'s .username/.password do NOT percent-decode on
        # every Python build - confirmed by testing, not assumed - so
        # this un-escapes them explicitly (matching how the rtsp://
        # URLs elsewhere in this project need %40 etc. for an '@' in
        # the password - without this, a password containing %40
        # would be sent to the DVR as the literal six characters
        # "%40" instead of "@", and auth would fail silently).
        username = unquote(parts.username) if parts.username else None
        password = unquote(parts.password) if parts.password else None

        self._auth = HTTPDigestAuth(username, password) if username else None

        self._poll_interval = poll_interval
        self._timeout = timeout
        self._session = requests.Session()

        self._last_error = None

        # Try one fetch right away so isOpened() reflects real status
        # immediately, the same as cv2.VideoCapture does (it attempts
        # the connection in its constructor too) - a caller's existing
        # "if not capture.isOpened(): ..." fallback logic (see
        # cameras.py / gate_camera.py) works unmodified either way.
        self._opened = self._fetch_frame() is not None

    def _fetch_frame(self):

        try:
            resp = self._session.get(
                self._url, auth=self._auth, timeout=self._timeout,
            )
        except Exception as e:
            self._last_error = str(e)
            return None

        if resp.status_code != 200:
            self._last_error = (
                f"ISAPI snapshot returned HTTP {resp.status_code} - "
                f"check the URL/channel number and credentials."
            )
            return None

        try:
            array = np.frombuffer(resp.content, dtype=np.uint8)
            frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
        except Exception as e:
            frame = None
            self._last_error = f"Could not decode snapshot as an image: {e}"

        if frame is None:
            self._last_error = (
                "ISAPI snapshot response wasn't a decodable JPEG "
                "(got an HTML/XML error page instead of an image?)."
            )

        return frame

    # ---- cv2.VideoCapture-compatible surface ----

    def isOpened(self):
        return self._opened

    def read(self):

        frame = self._fetch_frame()

        if frame is None:
            self._opened = False
            return False, None

        self._opened = True

        # Throttles how often a NEW snapshot is actually fetched - the
        # per-frame loop that calls read() runs much faster than this
        # DVR needs to be polled at, and a plain JPEG-snapshot "video"
        # doesn't gain anything from being fetched faster than this
        # anyway, so no reason to hammer the DVR with requests.
        time.sleep(self._poll_interval)

        return True, frame

    def release(self):

        try:
            self._session.close()
        except Exception:
            pass

        self._opened = False

    def set(self, *args, **kwargs):
        # Resolution/buffer-size/FPS properties don't apply to
        # snapshot polling - a no-op so call sites that unconditionally
        # call self.capture.set(...) (matching cv2.VideoCapture's own
        # API) don't need an isinstance check before every call.
        return False
