# ============================================================
# FIRE_SENSOR - hardware sensor fusion for fire/smoke detection
# ============================================================
#
# Optional second signal alongside the camera-based YOLO fire/smoke
# model: an MCU (e.g. an Arduino reading an MQ2-style gas/smoke
# sensor and a flame sensor) connected over serial, sending one line
# per reading in a simple text protocol:
#
#     FLAME:<0|1>,SMOKE:<raw-analog-value>\n
#
# This machine just reads lines off that port on a background
# thread - no GPIO library, no board-specific SDK - so the exact
# same code works whether the sensor MCU talks directly to the
# Jetson's USB/UART or to any other machine with a serial port. If
# FIRE_SENSOR_ENABLED=false (the default) or no sensor is plugged
# in, this module disables itself and vision-only fire/smoke
# detection behaves exactly as it does today - nothing here ever
# gates or blocks a vision alert, it only adjusts confidence/
# severity when a sensor reading happens to be available (see
# fuse() below).
#
# Requires pyserial, same as gsm_sms.py.
# ============================================================

import threading
import time

import config
import alerts

try:
    import serial
except ImportError:
    serial = None


_lock = threading.Lock()
_latest = {"flame": None, "smoke_raw": None, "timestamp": 0.0}
_connected = False
_stop_flag = threading.Event()
_thread = None

_unavailable_reason = None

if serial is None:
    _unavailable_reason = "pyserial not installed (pip install pyserial)"
elif not config.FIRE_SENSOR_ENABLED:
    _unavailable_reason = "disabled in config (FIRE_SENSOR_ENABLED=false)"


def _reader_loop():

    global _connected

    while not _stop_flag.is_set():

        try:

            ser = serial.Serial(
                config.FIRE_SENSOR_SERIAL_PORT,
                config.FIRE_SENSOR_BAUD_RATE,
                timeout=2,
            )

        except Exception as e:

            _connected = False
            print(f"[fire sensor] could not open "
                  f"{config.FIRE_SENSOR_SERIAL_PORT} ({e}) - retrying in 10s")
            _stop_flag.wait(10)
            continue

        print(f"[fire sensor] connected on {config.FIRE_SENSOR_SERIAL_PORT}")
        _connected = True

        try:

            while not _stop_flag.is_set():

                line = ser.readline().decode(errors="ignore").strip()

                if not line:
                    continue

                reading = _parse_line(line)

                if reading is None:
                    continue

                with _lock:
                    _latest["flame"] = reading["flame"]
                    _latest["smoke_raw"] = reading["smoke_raw"]
                    _latest["timestamp"] = time.time()

                _maybe_alert_alone(reading)

        except Exception as e:

            print(f"[fire sensor] read error (reconnecting): {e}")

        finally:

            _connected = False
            try:
                ser.close()
            except Exception:
                pass

        _stop_flag.wait(3)


_SENSOR_CAMERA_INFO = {"location": "Fire/Smoke Sensor", "zone": "Sensor Zone"}


def _maybe_alert_alone(reading):
    """Lets the hardware sensor raise an alert entirely on its own
    (no vision agreement needed) when config.FIRE_SENSOR_CAN_ALERT_ALONE
    is true - a gas/flame sensor often reacts faster, or sees around
    an occluded camera angle, than the vision model does. Reuses
    alerts.raise_alert() directly, so this gets the exact same
    cooldown/log/siren/voice/SMS fan-out as a vision-triggered alert,
    just with camera_id="FIRE-SENSOR" identifying the source. Calling
    this on every parsed serial line is safe - raise_alert() no-ops
    anything inside its own per-(camera,zone,incident) cooldown
    window, so it's cheap even at several readings/second."""

    if not config.FIRE_SENSOR_CAN_ALERT_ALONE:
        return

    if reading["flame"]:
        class_name, confidence = "fire", 0.9
    elif reading["smoke_raw"] >= config.FIRE_SENSOR_SMOKE_THRESHOLD:
        class_name, confidence = "smoke", 0.7
    else:
        return

    detection = {
        "class_name": class_name,
        "confidence": confidence,
        "severity": "CRITICAL" if class_name == "fire" else "HIGH",
        "source": "sensor",
        "sensor_note": "sensor-only (no vision agreement required)",
    }

    try:
        alerts.raise_alert("FIRE-SENSOR", _SENSOR_CAMERA_INFO, detection, frame_jpeg=None)
    except Exception as e:
        print(f"[fire sensor] sensor-only alert failed (non-fatal): {e}")


def _parse_line(line):
    """Parses "FLAME:1,SMOKE:742" -> {"flame": True, "smoke_raw": 742}.
    Returns None for anything that doesn't match - a partial line from
    mid-boot chatter on the MCU's serial port is common and should be
    silently skipped, not crash the reader thread."""

    parts = {}

    for field in line.split(","):

        if ":" not in field:
            continue

        key, _, value = field.partition(":")
        parts[key.strip().upper()] = value.strip()

    if "FLAME" not in parts or "SMOKE" not in parts:
        return None

    try:
        flame = parts["FLAME"] in ("1", "true", "True")
        smoke_raw = int(float(parts["SMOKE"]))
    except ValueError:
        return None

    return {"flame": flame, "smoke_raw": smoke_raw}


def start():
    """Called once at backend startup (main.py). No-op if disabled/
    pyserial missing - safe to always call."""

    global _thread

    if serial is None or not config.FIRE_SENSOR_ENABLED:

        print()
        print("=" * 60)
        print(f"Fire/smoke sensor fusion: DISABLED - {_unavailable_reason}")
        print("Fire/smoke detection stays vision-only (unaffected).")
        print("=" * 60)
        return

    print()
    print("=" * 60)
    print(f"Fire/smoke sensor fusion: ENABLED "
          f"(port={config.FIRE_SENSOR_SERIAL_PORT}, "
          f"baud={config.FIRE_SENSOR_BAUD_RATE})")
    print("Connecting in the background - vision detection is unaffected")
    print("either way, this only adds a second signal once connected.")
    print("=" * 60)

    _stop_flag.clear()
    _thread = threading.Thread(target=_reader_loop, daemon=True)
    _thread.start()


def stop():

    _stop_flag.set()


def is_connected():

    return _connected


def get_reading():
    """Returns {"flame": bool, "smoke": bool, "smoke_raw": int,
    "age_seconds": float, "stale": bool} or None if never connected /
    no reading received yet. `stale` (age past
    config.FIRE_SENSOR_STALE_SECONDS) means treat this as unknown,
    not as "no fire" - a frozen/disconnected sensor must never be
    read as a confident negative."""

    with _lock:
        latest = dict(_latest)

    if latest["timestamp"] == 0.0:
        return None

    age = time.time() - latest["timestamp"]

    return {
        "flame": bool(latest["flame"]),
        "smoke": latest["smoke_raw"] is not None
        and latest["smoke_raw"] >= config.FIRE_SENSOR_SMOKE_THRESHOLD,
        "smoke_raw": latest["smoke_raw"],
        "age_seconds": round(age, 1),
        "stale": age > config.FIRE_SENSOR_STALE_SECONDS,
    }


def status():
    """For GET /health / GET /sensors/fire."""

    reading = get_reading()

    return {
        "enabled": config.FIRE_SENSOR_ENABLED,
        "connected": is_connected(),
        "reason": None if (serial is not None and config.FIRE_SENSOR_ENABLED) else _unavailable_reason,
        "reading": reading,
    }


# ============================================================
# FUSION - called from cameras.py alongside vision fire/smoke boxes
# ============================================================

def fuse(class_name, vision_confidence):
    """class_name: "fire" or "smoke" - whichever hazard the vision
    model just confirmed (post FIRE_CONFIRM_FRAMES). Returns
    (severity_boost: bool, note: str) describing how the sensor
    reading (if any, if fresh) agrees/disagrees - this is advisory
    only (used to tag the alert record, e.g. "vision+sensor" vs
    "vision-only"), it never suppresses a vision-triggered alert.
    config.FIRE_SENSOR_CAN_ALERT_ALONE (used separately, by the
    camera worker loop) controls whether the sensor can ALSO trigger
    an alert with no vision detection at all."""

    reading = get_reading()

    if reading is None or reading["stale"]:
        return False, "vision-only (no fresh sensor reading)"

    sensor_agrees = (
        (class_name == "fire" and reading["flame"])
        or (class_name == "smoke" and reading["smoke"])
    )

    if sensor_agrees:
        return True, "vision+sensor (sensor confirms)"

    return False, "vision-only (sensor disagrees - possible false positive)"


def sensor_only_hazard():
    """For the camera loop to optionally raise an alert from the
    sensor alone, with zero vision detection - only when
    config.FIRE_SENSOR_CAN_ALERT_ALONE is true. Returns "fire",
    "smoke", or None."""

    if not config.FIRE_SENSOR_CAN_ALERT_ALONE:
        return None

    reading = get_reading()

    if reading is None or reading["stale"]:
        return None

    if reading["flame"]:
        return "fire"

    if reading["smoke"]:
        return "smoke"

    return None
