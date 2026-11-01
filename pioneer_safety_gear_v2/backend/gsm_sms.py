# ============================================================
# GSM_SMS - offline SMS to the supervisor over a serial GSM modem
# ============================================================
#
# alerts.py's existing SMS channel (Twilio) needs internet. This is
# the offline alternative: a cheap GSM/GPRS modem module (SIM800L,
# SIM900, A9/A9G, etc.) wired to this machine over USB or UART,
# talked to with plain AT commands - the standard "Hayes" command
# set every GSM modem understands, so this isn't tied to one
# specific board/vendor SDK.
#
# Both SMS channels are independent and optional - a deployment can
# run Twilio only (needs internet, no hardware), this only (needs
# hardware, no internet), both, or neither. alerts.py calls both from
# raise_alert(); each fails silently (non-fatal) on its own if its
# prerequisite (credentials / hardware) isn't there.
#
# Requires pyserial (`pip install pyserial`). If it isn't installed,
# or GSM_SMS_ENABLED=false (the default), or the configured serial
# port can't be opened, this module disables itself and prints why -
# the same graceful-degradation pattern as every other optional
# channel in this project (siren/voice/Twilio above).
# ============================================================

import threading
import time

import config

try:
    import serial
except ImportError:
    serial = None


gsm_available = False
_unavailable_reason = None

if serial is None:

    _unavailable_reason = "pyserial not installed (pip install pyserial)"

elif not config.GSM_SMS_ENABLED:

    _unavailable_reason = "disabled in config (GSM_SMS_ENABLED=false)"

elif not config.GSM_SUPERVISOR_NUMBER:

    _unavailable_reason = (
        "no destination number configured "
        "(set GSM_SUPERVISOR_NUMBER or ALERT_PHONE_NUMBER in .env)"
    )

else:

    # Don't actually open the serial port at import time - a modem
    # that's briefly unplugged/still booting shouldn't prevent the
    # whole backend from starting. Each send attempt opens, uses, and
    # closes its own connection (see _send below) - simple, and
    # tolerant of the modem being connected/disconnected between
    # alerts rather than assuming one long-lived connection stays
    # healthy for a multi-hour demo.
    gsm_available = True

    print()
    print("=" * 60)
    print(f"Offline GSM SMS: ENABLED (port={config.GSM_SERIAL_PORT}, "
          f"baud={config.GSM_BAUD_RATE})")
    print("Actual send will be attempted on the first alert - if the")
    print("modem isn't actually plugged in/registered, that send will")
    print("fail (non-fatal, logged) without affecting anything else.")
    print("=" * 60)

if not gsm_available and config.GSM_SMS_ENABLED:

    print()
    print("=" * 60)
    print(f"Offline GSM SMS: DISABLED - {_unavailable_reason}")
    print("This is expected if no GSM modem is wired up yet.")
    print("=" * 60)


def _read_until(ser, timeout, expect=("OK", "ERROR")):
    """Reads from the modem until one of `expect` shows up in the
    accumulated response or `timeout` seconds pass. Cheap polling
    loop rather than relying on serial's own read timeout semantics,
    since AT-command responses arrive in unpredictable chunks."""

    deadline = time.time() + timeout
    buf = b""

    while time.time() < deadline:

        chunk = ser.read(ser.in_waiting or 1)

        if chunk:
            buf += chunk
            text = buf.decode(errors="ignore")
            if any(token in text for token in expect):
                return text

        else:
            time.sleep(0.05)

    return buf.decode(errors="ignore")


def _send_at(ser, command, timeout, expect=("OK", "ERROR")):

    ser.write((command + "\r").encode())
    return _read_until(ser, timeout, expect)


def _send_blocking(message, number=None):
    """Does the actual AT-command SMS send. Runs on its own thread
    (see send_sms below) so a slow/unresponsive modem never blocks
    the camera worker thread that triggered the alert. Returns
    (ok: bool, detail: str) - never raises."""

    if not gsm_available:
        return False, _unavailable_reason

    destination = number or config.GSM_SUPERVISOR_NUMBER
    timeout = config.GSM_SEND_TIMEOUT

    try:

        ser = serial.Serial(
            config.GSM_SERIAL_PORT, config.GSM_BAUD_RATE, timeout=1,
        )

    except Exception as e:
        return False, f"could not open {config.GSM_SERIAL_PORT}: {e}"

    try:

        # Wake the modem / confirm it's responsive.
        reply = _send_at(ser, "AT", timeout)
        if "OK" not in reply:
            return False, f"modem not responding to AT: {reply!r}"

        # Text mode (vs PDU mode) - simplest for a plain message body.
        reply = _send_at(ser, "AT+CMGF=1", timeout)
        if "OK" not in reply:
            return False, f"AT+CMGF=1 failed: {reply!r}"

        # Start a message to `destination` - modem replies with a
        # '>' prompt waiting for the body, then Ctrl+Z (0x1A) sends.
        ser.write(f'AT+CMGS="{destination}"\r'.encode())
        time.sleep(0.3)

        ser.write(message.encode() + bytes([26]))

        reply = _read_until(ser, timeout, expect=("+CMGS", "ERROR"))

        if "+CMGS" in reply:
            return True, "sent"

        return False, f"send failed: {reply!r}"

    except Exception as e:

        return False, f"serial error: {e}"

    finally:

        try:
            ser.close()
        except Exception:
            pass


def send_sms(message, number=None):
    """Fire-and-forget, non-blocking - matches alerts.py's
    _send_sms (Twilio) calling convention exactly, so raise_alert()
    can call both the same way."""

    if not gsm_available:
        return

    def run():

        ok, detail = _send_blocking(message, number)

        print()
        print("=" * 60)
        if ok:
            print("OFFLINE GSM SMS SENT")
        else:
            print("OFFLINE GSM SMS FAILED (other alert channels still fired)")
            print(f"Detail: {detail}")
        print("=" * 60)

    threading.Thread(target=run, daemon=True).start()
