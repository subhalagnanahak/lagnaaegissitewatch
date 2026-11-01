# Wiring up a Hikvision (or any RTSP) DVR/NVR

This project reads every camera - PPE, fire/smoke, and the entry gate
- the same way: with OpenCV's `cv2.VideoCapture`, which understands a
plain RTSP URL exactly as well as a local device index. A DVR/NVR
channel is **not** a USB webcam and never shows up in a browser's
camera list (`getUserMedia()`/`enumerateDevices()`) - the video only
exists as this network stream, so it's always read here in the
backend, never requested from the browser.

With a 4-channel hybrid DVR and 4 CCTV cameras, a natural split is:

| DVR channel | Used for                | Configured in |
|---|---|---|
| 1 | PPE detection            | `backend/cameras.json` |
| 2 | Fire/smoke detection      | `backend/cameras.json` |
| 3 | Entry gate (face + PPE)   | `backend/.env` → `GATE_CAMERA_SOURCE` |
| 4 | Spare / general monitoring | add a third `cameras.json` entry whenever you want it |

Adjust the channel numbers below to match however your cameras are
actually wired into the DVR - the channel number is set on the DVR
itself (or in its web UI / Hikvision's iVMS software), not by this
project.

## 1. Find your DVR's RTSP URL format

Hikvision (and most hybrid DVRs/NVRs that speak RTSP) use this shape:

```
rtsp://<username>:<password>@<dvr-ip>:554/Streaming/Channels/<CH><SS>
```

- `<CH>` - the channel number, 1-4 on a 4-channel DVR.
- `<SS>` - `01` for the main/high-res stream, `02` for the lighter
  substream. The substream is usually enough for detection models and
  puts less load on the DVR and network - start with `02` and switch
  to `01` if you need more resolution.
- Default RTSP port is `554` unless you changed it in the DVR's
  network settings.
- Default credentials are whatever you set on first boot of the DVR
  (Hikvision no longer ships a fixed default password) - use the same
  username/password you log into the DVR's own web UI or iVMS-4200
  with.

Example - channel 3, substream:

```
rtsp://admin:MyRealPassword@192.168.1.64:554/Streaming/Channels/302
```

**If your password contains `@`, `:`, `/`, `#`, `?`, `%`, or a space,
you MUST percent-encode it**, or the URL parses wrong (the `@` in
`user:password@host` is the separator between credentials and the
host - a second, literal `@` inside the password breaks that). A
broken-but-not-obviously-broken URL like this is exactly what causes
`PPS id out of range` / HEVC decode spam even with RTSP-over-TCP
already fixed - ffmpeg ends up connecting to the wrong thing entirely
and tries to decode whatever garbage comes back as video.

The one that bites people most often is `@` itself - encode it as
`%40`. E.g. a DVR password of `Robo@7788` becomes:

```
rtsp://admin:Robo%407788@192.168.1.64:554/Streaming/Channels/302
```

Other characters, if you ever hit them: space → `%20`, `:` → `%3A`,
`/` → `%2F`, `#` → `%23`, `?` → `%3F`, `%` itself → `%25`.

If you're not sure of the DVR's IP, check your router's connected-
devices list, or open the DVR's own menu (Network settings usually
shows it directly on the small front-panel/HDMI UI).

## 2. Point PPE and fire/smoke detection at the DVR

Edit `backend/cameras.json` and replace each entry's `"source"` with
your real RTSP URL (the file ships with obvious placeholder
credentials/IP - `admin:yourpassword@192.168.1.64` - that you must
replace, or those two cameras just won't connect):

```json
[
  {
    "id": "0",
    "source": "rtsp://admin:MyRealPassword@192.168.1.64:554/Streaming/Channels/101",
    "watched_hazards": ["fire"],
    ...
  },
  {
    "id": "2",
    "source": "rtsp://admin:MyRealPassword@192.168.1.64:554/Streaming/Channels/201",
    "watched_items": ["gloves", "helmet", "vest", "goggles"],
    ...
  }
]
```

(Everything else in each entry - `location`, `zone`, `enabled`,
`show_heatmap` - works exactly the same as with a local webcam; see
the main README's camera-config section for the full field list.)

## 3. Point the entry gate at the DVR

Edit `backend/.env` (copy from `.env.example` if you don't have one
yet) and set:

```
GATE_CAMERA_SOURCE=rtsp://admin:MyRealPassword@192.168.1.64:554/Streaming/Channels/302
GATE_CAMERA_AUTO_SCAN=true
```

Restart the backend. The Entry Gate page's camera status line will
show "Gate camera connected" once `gate_camera.py` successfully opens
the stream - if it instead shows an error, double check the IP,
credentials, channel number, and that this machine can actually reach
the DVR on the network (same LAN, or a route to it - `ping
192.168.1.64` from the backend machine is a quick sanity check).

With `GATE_CAMERA_AUTO_SCAN=true` the gate watches on its own: roughly
once every `GATE_SCAN_COOLDOWN` seconds (10s by default), if a face is
in frame, it's matched and PPE-checked automatically, with no one
touching the dashboard. Set it to `false` to keep the live preview but
require an explicit "Capture & Scan" click on the Entry Gate page for
every entry.

## Troubleshooting: `[hevc @ 0x...] PPS id out of range: 0` spam

This is ffmpeg's H.265/HEVC decoder failing on corrupted frames -
almost always dropped RTSP packets (the default transport is UDP,
which has no retransmission; H.265 streams, which most DVRs send by
default on their main channel, tolerate that badly). Two fixes are
already in the code for this:

1. `config.py` forces RTSP over TCP for every camera in this project
   (`OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`).
2. `cameras.py` / `gate_camera.py` explicitly force OpenCV's **FFmpeg**
   backend (`cv2.VideoCapture(url, cv2.CAP_FFMPEG)`) when opening an
   RTSP URL, instead of letting OpenCV auto-pick one. This matters
   because #1 only has any effect through the FFmpeg backend - on
   some builds (Jetson/L4T in particular, where OpenCV is often
   compiled with GStreamer support too) auto-selection can silently
   land on GStreamer instead, in which case the TCP-transport env var
   does nothing at all and you'd see this exact error even with it
   set. Forcing FFmpeg explicitly guarantees the TCP fix actually
   applies (it falls back to an unforced open if this particular
   OpenCV build has no FFmpeg support at all).

Make sure you're running the updated code (not an older copy) and
have **restarted the backend process** - these are read once at
startup, not hot-reloaded.

To check which backend your OpenCV build actually has, from the
backend's Python environment:

```bash
python3 -c "import cv2; bi = cv2.getBuildInformation(); print('\n'.join(l for l in bi.splitlines() if 'FFMPEG' in l or 'GStreamer' in l))"
```

If `FFMPEG` says `NO`, the fixes above can't take effect at all on this
install - see the GStreamer note below.

If the error still happens after both fixes and a restart:

- Point the URL at the **substream** instead of the main stream (the
  `02` suffix instead of `01`, e.g. `.../Channels/302` instead of
  `.../Channels/301`) - Hikvision substreams are lower-resolution and
  often H.264, which is both lighter on the Jetson and far more
  tolerant of corruption than H.265. This is the single most reliable
  fix if the DVR's H.265 encoder itself is simply flaky with
  third-party RTSP clients (a known issue on some budget/OEM-rebadged
  hybrid DVRs, independent of transport or backend).
- Or, in the DVR's own web UI / iVMS-4200 (Configuration → Image →
  Video), switch that channel's **Video Encoding** from H.265 to
  H.264 directly - H.264 is universally better-supported by
  ffmpeg/OpenCV and avoids this whole class of error.
- A flaky WiFi hop between the DVR and the Jetson makes packet loss
  worse even over TCP (TCP just retries, it doesn't eliminate jitter)
  - a wired Ethernet connection for both, through the same switch, is
    the most reliable setup for continuous RTSP pulls like this.
- If your OpenCV build has no FFmpeg support (`FFMPEG: NO` above) and
  only GStreamer, the TCP fix needs a different form - a custom
  GStreamer pipeline string with `protocols=tcp` on `rtspsrc` - ask
  for help wiring that up if you hit this, since the exact pipeline
  depends on which GStreamer plugins are installed.

## If the DVR's HEVC stream is fundamentally broken (confirmed, not just a hunch)

Some budget/OEM-rebadged hybrid DVRs ship with a genuinely non-compliant
H.265 encoder - not a transport or config problem, a firmware bug. You
can confirm this independent of this project entirely:

```bash
sudo apt install ffmpeg -y
ffplay -rtsp_transport tcp "rtsp://<user>:<pass>@<dvr-ip>:<port>/Streaming/Channels/<CH><SS>"
```

If you see the same video garbage/errors in `ffplay` as in the
backend's logs - especially a line like `vps_reserved_ffff_16bits is
not 0xffff` - that's the proof: that field is required by the H.265
spec to always be `0xFFFF`, so the DVR is emitting a stream that
violates the spec itself. No RTSP transport setting, OpenCV backend
choice, or stream (main/sub) selection can fix that - every standards-
compliant client (ffmpeg, VLC, this project) will reject it the same
way. Worth a 2-minute check first: does the DVR have a firmware update
available (its own Maintenance/Upgrade menu, or the manufacturer's
support site by model number)? Some of these HEVC bugs do get fixed in
later firmware. If not, or you're on a deadline, there are two ways
around it - pick whichever fits your situation:

### Option A: ISAPI snapshot polling (no DVR/cable changes at all)

Works entirely over the network, from the Jetson, with zero physical
access to the DVR or cameras needed. Most Hikvision-based DVRs (even
OEM/rebadged ones) expose a JPEG-snapshot endpoint (**ISAPI**) that's
a completely separate code path from the broken H.265 video encoder -
it just returns one already-encoded JPEG image per request, so the
corruption doesn't apply to it at all. This project polls that
endpoint on an interval instead of decoding continuous video -
`isapi_snapshot.py` handles this automatically whenever a camera's
`source` is an `http://` or `https://` URL instead of an `rtsp://`
URL or a device number.

1. **Find the DVR's real reachable port(s).** Some DVRs don't expose
   their default ports (`554` for RTSP, `80` for HTTP) directly, even
   to devices on the same LAN - check Configuration → Network → NAT on
   the DVR's own screen. If "Enable" is on and "Mapping Type" is
   "Auto", it lists an **External Port** per service (HTTP Port, RTSP
   Port, ...) that's actually reachable, separate from the internal
   port shown next to it. Test with:
   ```bash
   timeout 3 bash -c "echo > /dev/tcp/<dvr-ip>/<port>" && echo REACHABLE || echo BLOCKED
   ```
   **Set "Mapping Type" to "Manual" with a fixed port once you've found
   one that works** - "Auto" reassigns a new external port every time
   NAT is toggled or the DVR reboots, which will silently break the
   URLs below later if left on Auto.
2. **Confirm the snapshot endpoint itself works**, independent of this
   project, with `curl` (swap in your real IP/port/credentials/
   channel):
   ```bash
   curl -v --digest -u 'admin:yourpassword' \
     "http://<dvr-ip>:<http-port>/ISAPI/Streaming/channels/101/picture" \
     -o test.jpg
   file test.jpg   # should say "JPEG image data", not HTML/XML
   ```
   Note the `--digest` flag - ISAPI requires HTTP Digest auth, not
   Basic auth, which is why a plain `user:pass@host` URL alone (as a
   browser or `cv2.VideoCapture` would send) doesn't work here; this
   project's `isapi_snapshot.py` sends proper digest auth internally.
   `101`/`201`/`301` = channel 1/2/3's main stream - try the channel
   numbers in turn if you're not sure which physical camera is which.
3. **Use that URL as the source**, with credentials embedded exactly
   like an `rtsp://` URL already does elsewhere in this project
   (percent-encode special characters in the password the same way -
   see the encoding table earlier in this file):
   - `backend/cameras.json`, for PPE/fire cameras:
     ```json
     "source": "http://admin:yourpassword@192.168.1.64:31903/ISAPI/Streaming/channels/101/picture",
     ```
   - `backend/.env`, for the gate camera:
     ```
     GATE_CAMERA_SOURCE=http://admin:yourpassword@192.168.1.64:31903/ISAPI/Streaming/channels/301/picture
     ```
4. Restart the backend. Each of these now polls a plain JPEG snapshot
   every ~0.5s instead of decoding video - no RTSP, no H.265, none of
   the corruption applies. Resolution will typically be lower than a
   clean mainstream video feed (often CIF/D1, e.g. 704x576) but is
   plenty for the PPE/fire/face models, which already only process
   every few frames rather than every single one.

### Option B: USB capture card (if you're able to tap the coax cables)

**Capture each camera's raw analog signal directly, bypassing the
DVR's RTSP/H.265 encoder entirely.** Needs brief physical access to
where the BNC cables plug into the DVR (not the cameras themselves) -
skip this if that's not possible for you and use Option A instead.

1. Buy one small USB video capture dongle per camera you need (PPE,
   fire/smoke, gate - 3 total, or however many of the 3 you're
   actually using). Look for ones described as:
   - "USB 2.0 video capture card, composite/CVBS/AV input"
   - **UVC-compliant** (works as a plain Linux webcam, no special
     driver needed - look for this explicitly in the listing to avoid
     a dongle that only has Windows drivers)
   - Supports **PAL** (India's analog TV standard - most budget
     CCTV cameras here output PAL; a dongle that's NTSC-only won't
     show a clean picture)
   These are inexpensive and widely available (search "USB composite
   video capture card UVC" on Amazon/Flipkart/local electronics
   market) - should be same-day in most Indian cities if bought
   locally rather than shipped.
2. Buy a couple of cheap **BNC T-splitters** (sometimes sold as "BNC
   T-connector" or "BNC 2-way splitter") - one per camera you're
   tapping. This lets you tap each camera's existing coax cable
   *without* disconnecting it from the DVR, so the DVR keeps
   recording normally and this project gets the same signal in
   parallel.
3. At the DVR's camera-in ports (or at a junction along the coax run),
   insert the T-splitter so the camera's signal goes to both the DVR's
   port AND a short coax-to-RCA/composite cable into the capture
   dongle.
4. Plug each dongle into the Jetson's USB ports. Find its device index:
   ```bash
   ls /dev/video*
   v4l2-ctl --list-devices   # if installed - shows friendly names per index
   ```
5. Use that plain number (not a URL) as the source:
   - In `backend/cameras.json`, for the PPE/fire cameras - note this
     must be a bare JSON number, not a quoted string:
     ```json
     "source": 2,
     ```
   - In `backend/.env`, for the gate camera:
     ```
     GATE_CAMERA_SOURCE=2
     ```
     (`gate_camera.py` auto-detects a plain digit value here and opens
     it as a local device instead of a URL.)
6. Restart the backend. These now behave exactly like local webcams -
   completely independent of the DVR's RTSP/H.265 output, so none of
   the corruption applies.

If your cameras connect to the DVR over Ethernet/RJ45 instead of
BNC/coax (true IP cameras, not analog), this particular workaround
doesn't apply the same way - ask for help figuring out the right
approach for that setup instead.

## 4. No DVR yet?

Everything keeps working without one. Leave `cameras.json`'s sources
as local device indices (`0`, `1`, ...) for PPE/fire, and leave
`GATE_CAMERA_SOURCE` blank - the Entry Gate page falls back to a plain
"upload a photo" button (`POST /gate/scan`) instead of a live feed.
Nothing else in the project changes either way.
