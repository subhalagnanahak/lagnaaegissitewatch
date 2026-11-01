# ============================================================
# ALERTS
# ============================================================
#
# Offline-first alerting. Every violation goes through the same
# per (camera, zone, incident) cooldown, then fans out to:
#
#   1. In-memory alert history (always) - what /alerts and the
#      dashboard's incident feed read.
#   2. A local on-disk JSONL log (always, no network) - a durable
#      audit trail even if nothing else is configured.
#   3. A local siren (always, unless LOCAL_SIREN_ENABLED=false) -
#      a synthesized WAV played through the machine's own audio
#      output. No cloud account, no internet.
#   4. A spoken-word voice alert (always, unless VOICE_ALERTS_ENABLED
#      =false) - announces what was detected and where, using the
#      offline `espeak-ng`/`espeak` CLI if installed. Played right
#      after the siren (same background thread, sequenced not
#      overlapped) so a person hears "siren, then what happened".
#   5. A violation snapshot (always, unless SNAPSHOT_ENABLED=false) -
#      the current frame (with detection boxes drawn) saved as a JPEG
#      under SNAPSHOTS_DIR the instant the alert fires, and served
#      back to the dashboard at snapshot_url. Pure local disk I/O.
#   6. Twilio SMS (OPTIONAL - only if backend/.env has real
#      credentials). This is the one channel that needs internet;
#      everything else works with none.
#
# ============================================================

import json
import math
import os
import shutil
import struct
import subprocess
import threading
import time
import wave
from datetime import datetime

import config
import gsm_sms
from ppe_logic import incident_label

try:
    from twilio.rest import Client
except ImportError:
    Client = None


# ============================================================
# TWILIO CLIENT (optional)
# ============================================================

twilio_client = None

if Client is None:

    print()
    print("=" * 60)
    print("Twilio package not installed - SMS stays off (optional).")
    print("Everything else (siren, local log, dashboard) still works.")
    print("=" * 60)

elif (
    not config.TWILIO_ACCOUNT_SID
    or not config.TWILIO_AUTH_TOKEN
    or not config.TWILIO_PHONE_NUMBER
    or not config.ALERT_PHONE_NUMBER
):

    print()
    print("=" * 60)
    print("SMS alerts: DISABLED (no Twilio credentials in backend/.env)")
    print("This is expected for a fully offline deployment.")
    print("Local siren + local alert log + dashboard still work.")
    print("=" * 60)

else:

    try:

        twilio_client = Client(
            config.TWILIO_ACCOUNT_SID,
            config.TWILIO_AUTH_TOKEN,
        )

        print()
        print("=" * 60)
        print("SMS alerts: ENABLED (needs internet)")
        print("=" * 60)

    except Exception as e:

        print()
        print("=" * 60)
        print("SMS initialization failed - continuing without it.")
        print(f"Error: {e}")
        print("=" * 60)


# ============================================================
# STATE
# ============================================================

alert_history = []
MAX_ALERT_HISTORY = 200
alert_history_lock = threading.Lock()

last_alert_times = {}
alert_lock = threading.Lock()


# ============================================================
# LOCAL SIREN - synthesized WAV, no bundled audio asset needed
# ============================================================

def _ensure_siren_wav():
    """Generate a short two-tone siren WAV the first time it's
    needed, using only the standard library (wave/math/struct).
    Nothing to download, nothing to fail without internet."""

    path = config.SIREN_WAV_PATH

    if os.path.exists(path):
        return path

    os.makedirs(os.path.dirname(path), exist_ok=True)

    sample_rate = 44100
    duration_s = 1.2
    n_samples = int(sample_rate * duration_s)

    with wave.open(path, "w") as wav_file:

        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        frames = bytearray()

        for i in range(n_samples):

            t = i / sample_rate

            # Two-tone wail: frequency sweeps between 700-1200Hz.
            freq = 950 + 250 * math.sin(2 * math.pi * 1.5 * t)

            amplitude = 0.6 * math.sin(2 * math.pi * freq * t)

            # Fade in/out 30ms to avoid a click at start/end.
            fade = min(1.0, i / (sample_rate * 0.03),
                       (n_samples - i) / (sample_rate * 0.03))

            sample = int(amplitude * fade * 32767)

            frames += struct.pack("<h", sample)

        wav_file.writeframes(bytes(frames))

    return path


_SIREN_PLAYERS = ["paplay", "aplay", "afplay"]


def _play_siren():

    if not config.LOCAL_SIREN_ENABLED:
        return

    try:

        wav_path = _ensure_siren_wav()

        player = next((p for p in _SIREN_PLAYERS if shutil.which(p)), None)

        if player is None:

            print(
                "[siren] no audio player found (tried "
                f"{', '.join(_SIREN_PLAYERS)}) - install alsa-utils "
                "or pulseaudio-utils for an audible local alarm."
            )
            return

        subprocess.run(
            [player, wav_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

    except Exception as e:

        print(f"[siren] playback failed (non-fatal): {e}")


# ============================================================
# VOICE ALERT - offline TTS via espeak/espeak-ng, no cloud API
# ============================================================
#
# Deliberately NOT a cloud TTS service (Google/Amazon/ElevenLabs/etc)
# - that would be the one part of the alert path that stops working
# without internet, which conflicts with this project's offline-first
# design (see the module docstring above and README.md). espeak-ng is
# a small, widely-packaged offline CLI synthesizer; if it isn't
# installed the voice alert is silently skipped (the siren still
# fires either way) rather than failing the whole alert.
# ============================================================

_VOICE_PLAYERS = ["espeak-ng", "espeak"]


def _speak_alert(text):

    if not config.VOICE_ALERTS_ENABLED or not text:
        return

    try:

        player = next((p for p in _VOICE_PLAYERS if shutil.which(p)), None)

        if player is None:

            print(
                "[voice alert] no offline TTS engine found (tried "
                f"{', '.join(_VOICE_PLAYERS)}) - install espeak-ng for "
                "spoken alerts (e.g. `apt install espeak-ng`)."
            )
            return

        subprocess.run(
            [player, "-s", "150", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )

    except Exception as e:

        print(f"[voice alert] playback failed (non-fatal): {e}")


def _voice_phrase(incident_type, camera_info):

    return (
        f"Warning. {incident_type} detected at "
        f"{camera_info['location']}, {camera_info['zone']}."
    )


def _play_siren_then_voice(voice_text):
    """Runs the siren and the spoken announcement back-to-back on the
    same background thread, so they're sequenced (never overlapping)
    and neither blocks the camera worker's frame loop."""

    _play_siren()
    _speak_alert(voice_text)


def trigger_local_alarm(voice_text=None):

    threading.Thread(
        target=_play_siren_then_voice, args=(voice_text,), daemon=True
    ).start()


# ============================================================
# VIOLATION SNAPSHOTS - "surveillance of the live feed", stored
# locally as JPEGs, no cloud storage / no video upload anywhere
# ============================================================

def _prune_old_snapshots():

    if config.MAX_SNAPSHOTS <= 0:
        return

    try:

        files = [
            os.path.join(config.SNAPSHOTS_DIR, name)
            for name in os.listdir(config.SNAPSHOTS_DIR)
            if name.lower().endswith(".jpg")
        ]

        if len(files) <= config.MAX_SNAPSHOTS:
            return

        files.sort(key=os.path.getmtime)

        for path in files[: len(files) - config.MAX_SNAPSHOTS]:

            try:
                os.remove(path)
            except OSError:
                pass

    except Exception as e:

        print(f"[snapshot] prune failed (non-fatal): {e}")


def _save_snapshot(camera_id, incident_type, frame_jpeg):
    """Writes the already-JPEG-encoded frame (with detection boxes
    already drawn on it by CameraWorker._draw) to disk and returns
    just the filename, or None if snapshots are disabled/unavailable.
    Never raises - a failed snapshot must never block the rest of the
    alert (siren/log/SMS all still need to fire)."""

    if not config.SNAPSHOT_ENABLED or not frame_jpeg:
        return None

    try:

        os.makedirs(config.SNAPSHOTS_DIR, exist_ok=True)

        slug = "".join(
            c if c.isalnum() else "_" for c in incident_type.lower()
        ).strip("_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{camera_id}_{slug}_{timestamp}.jpg"

        with open(os.path.join(config.SNAPSHOTS_DIR, filename), "wb") as f:
            f.write(frame_jpeg)

        _prune_old_snapshots()

        return filename

    except Exception as e:

        print(f"[snapshot] save failed (non-fatal): {e}")
        return None


def trigger_local_siren():

    threading.Thread(target=_play_siren, daemon=True).start()


# ============================================================
# LOCAL ALERT LOG - durable, offline, no external service
# ============================================================

def append_local_log(alert):

    try:

        os.makedirs(os.path.dirname(config.LOCAL_ALERT_LOG_PATH), exist_ok=True)

        with open(config.LOCAL_ALERT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert) + "\n")

    except Exception as e:

        print(f"[local alert log] write failed (non-fatal): {e}")


# ============================================================
# ALERT RECORD
# ============================================================

def create_alert_record(camera_id, camera_info, detection, snapshot_filename=None):

    incident_type = incident_label(detection.get("class_name", ""))
    confidence = float(detection.get("confidence", 0))

    return {
        "camera_id": camera_id,
        "location": camera_info["location"],
        "zone": camera_info["zone"],
        "incident_type": incident_type,
        "class_name": detection.get("class_name", ""),
        "severity": detection.get("severity", "HIGH"),
        "confidence": round(confidence, 3),
        "confidence_percent": round(confidence * 100),
        "source": detection.get("source", "model"),
        "timestamp": datetime.now().isoformat(),
        "status": "ALERT",
        # None when snapshots are disabled/unavailable - the dashboard
        # only renders a thumbnail when this is present.
        "snapshot_url": f"/snapshots/{snapshot_filename}" if snapshot_filename else None,
        # "vision+sensor" / "vision-only" / "sensor-only" context from
        # fire_sensor.py's fusion - None for anything that isn't a
        # fire/smoke detection (fire_sensor.py is never involved in a
        # PPE violation).
        "sensor_note": detection.get("sensor_note"),
        # Which enrolled worker (if any) this violation was attributed
        # to via face_id - None when unmatched/unavailable/not a
        # per-worker PPE violation (e.g. a fire/smoke event).
        "identified_worker": detection.get("identified_worker"),
    }


def store_alert(alert):

    global alert_history

    with alert_history_lock:

        alert_history.insert(0, alert)

        if len(alert_history) > MAX_ALERT_HISTORY:
            alert_history = alert_history[:MAX_ALERT_HISTORY]


def get_alert_history():

    with alert_history_lock:
        return list(alert_history)


def clear_alert_history():

    global alert_history

    with alert_history_lock:
        alert_history = []


# ============================================================
# SMS (optional)
# ============================================================

def _send_sms(camera_id, camera_info, incident_type, severity, confidence_percent):

    if twilio_client is None:
        return

    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    header = "CRITICAL SAFETY ALERT" if severity == "CRITICAL" else "CONSTRUCTION SAFETY ALERT"

    message_body = (
        f"\U0001F6A8 {header}\n"
        f"Incident: {incident_type}\n"
        f"Camera: {camera_id}\n"
        f"Location: {camera_info['location']}\n"
        f"Zone: {camera_info['zone']}\n"
        f"Severity: {severity}\n"
        f"Confidence: {confidence_percent}%\n"
        f"Time: {timestamp}"
    )

    def send():

        try:

            message = twilio_client.messages.create(
                body=message_body,
                from_=config.TWILIO_PHONE_NUMBER,
                to=config.ALERT_PHONE_NUMBER,
            )

            print()
            print("=" * 60)
            print("SMS ALERT SENT")
            print(f"Incident: {incident_type}")
            print(f"Camera: {camera_id}")
            print(f"Message SID: {message.sid}")
            print("=" * 60)

        except Exception as e:

            # No internet, Twilio down, bad credentials, etc. - the
            # local siren/log/dashboard already fired regardless,
            # so a safety alert was still raised even if this fails.
            print()
            print("=" * 60)
            print("SMS ALERT FAILED (local siren/log still fired)")
            print(f"Incident: {incident_type}")
            print(f"Camera: {camera_id}")
            print(f"Error: {e}")
            print("=" * 60)

    threading.Thread(target=send, daemon=True).start()


# ============================================================
# OFFLINE GSM SMS (optional - see gsm_sms.py)
# ============================================================
#
# A SECOND, independent SMS channel from Twilio above - needs a
# wired-up GSM modem instead of internet. Both are best-effort and
# run side by side; see gsm_sms.py's module docstring.
# ============================================================

def _send_gsm_sms(camera_id, camera_info, incident_type, severity, confidence_percent):

    if not gsm_sms.gsm_available:
        return

    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    header = "CRITICAL SAFETY ALERT" if severity == "CRITICAL" else "CONSTRUCTION SAFETY ALERT"

    message_body = (
        f"{header}\n"
        f"Incident: {incident_type}\n"
        f"Camera: {camera_id}\n"
        f"Location: {camera_info['location']}\n"
        f"Zone: {camera_info['zone']}\n"
        f"Severity: {severity}\n"
        f"Confidence: {confidence_percent}%\n"
        f"Time: {timestamp}"
    )

    gsm_sms.send_sms(message_body)


# ============================================================
# RAISE ALERT (fan-out to every channel)
# ============================================================

def raise_alert(camera_id, camera_info, detection, frame_jpeg=None):

    incident_type = incident_label(detection.get("class_name", ""))
    severity = detection.get("severity", "HIGH")
    confidence = float(detection.get("confidence", 0))
    confidence_percent = round(confidence * 100)

    alert_key = (camera_id, camera_info["zone"], incident_type)
    current_time = time.time()

    with alert_lock:

        last_time = last_alert_times.get(alert_key, 0)

        if current_time - last_time < config.ALERT_COOLDOWN:
            return

        # Reserve the slot immediately so multiple detections in the
        # same frame (or the next few frames) don't double-fire.
        last_alert_times[alert_key] = current_time

    # Only actually write a snapshot to disk once we know this alert
    # is really firing (past the cooldown gate above) - not on every
    # single frame a violation happens to still be visible in.
    snapshot_filename = _save_snapshot(camera_id, incident_type, frame_jpeg)

    alert = create_alert_record(camera_id, camera_info, detection, snapshot_filename)

    # Offline channels - always run, never need a network.
    store_alert(alert)
    append_local_log(alert)
    trigger_local_alarm(_voice_phrase(incident_type, camera_info))

    # Optional channels - online (Twilio) and offline (GSM modem),
    # independent of each other, both best-effort.
    _send_sms(camera_id, camera_info, incident_type, severity, confidence_percent)
    _send_gsm_sms(camera_id, camera_info, incident_type, severity, confidence_percent)


def process_alerts(camera_id, camera_info, detections, frame_jpeg=None):
    """Raise an alert for every detection flagged violation=True."""

    for detection in detections:

        if detection.get("violation", False):
            raise_alert(camera_id, camera_info, detection, frame_jpeg=frame_jpeg)
