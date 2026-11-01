# Aegis SiteWatch — Construction Site Safety Monitoring

BPUT Hackathon 2026 · PS06 (STPI & EmTek) — real-time PPE compliance +
fire/smoke detection with location-specific alerts.

## Runs fully offline

Nothing in this system needs internet access at runtime:

- Detection (YOLO11n, both models) runs entirely on-device from local
  weight files - no model download, no cloud inference API.
- `main.py` explicitly disables Ultralytics' auto-install and HUB
  telemetry (`YOLO_AUTOINSTALL=False`, `settings.sync = False`), so it
  never attempts an outbound call even in the background.
- Alerting is offline-first: a **local siren** (a short WAV synthesized
  at first run with nothing but Python's standard library - no asset to
  download) plays through the machine's own speaker, and every alert is
  appended to a local audit log (`backend/alert_log.jsonl`). Both work
  with the network cable unplugged.
- The dashboard adds its own **fully client-side** tone + OS notification
  (Web Audio API + Notification API, `src/lib/localAlert.js`) the instant
  a new incident appears over the websocket - no server round-trip.
- Twilio SMS is the **one optional exception** - it needs internet, stays
  off unless you put real credentials in `backend/.env`, and every other
  channel above fires regardless of whether it's configured or whether
  the SMS send actually succeeds.
- A camera source can be an RTSP/HTTP CCTV stream on your local network -
  that's your LAN, not the internet, and needs no external connectivity
  either.

The only things that need internet at all are one-time setup steps
(`pip install`, `npm install` to fetch packages the first time) and the
optional Twilio SMS - never core detection, alerting, or the dashboard.

## What it does

- Ingests one or more camera feeds — a webcam, a recorded video file, or an
  RTSP/HTTP CCTV stream on the local network — configured in
  `backend/cameras.json`. A recorded video can either be a server-side
  path typed into that config, or **uploaded straight from the dashboard**
  (Add Camera → "recorded video" → drag/pick a file) - see "Recorded
  video upload" below.
- Runs two YOLO11n models per frame: a custom PPE model (helmet, vest,
  gloves, boots, goggles) and a custom fire/smoke model.
- Optionally runs a **third model** for "other industry" gear - mask/
  respirator, ear protection, face shield - on any camera an operator
  opts into it. Off by default, zero cost on a camera that doesn't use
  it. See "Other industry gear" below.
- Associates each detected PPE item with the nearest detected worker, so
  compliance is computed **per worker**, not just as a frame-wide count.
- The moment a violation or fire/smoke event is detected: sounds a local
  siren, logs it locally, pushes it to the dashboard (tone + OS
  notification + incident feed) - and optionally sends an SMS via Twilio
  if configured. All capped by a per-(camera, zone, incident) cooldown so
  nothing spams.
- A fire/smoke hit reaches that alert pipeline the moment it's seen on
  a single processed frame - fastest possible response, at the cost of
  no filtering for one-frame false positives (a lighter flick, a
  reflection). See "Fire/smoke alert confirmation" below to trade
  speed for a filter if you need one.
- Streams live annotated video (MJPEG) and detections (WebSocket) to a
  React dashboard showing site-wide stats, per-camera feeds, an incident
  feed, and a per-worker compliance breakdown.
- Tracks **where** violations concentrate, not just that they happened -
  a per-camera live heatmap overlay and a site-wide "Violation Hotspots"
  ranking by zone. See "Violation hotspots" below.

## New: finals change-points (Pioneers PS06)

Added on top of everything above, in response to the team's post-
qualifier change-point list:

| # | Ask | Status |
|---|---|---|
| 1 | Contextual voice alert | Already built (`alerts.py` - offline `espeak-ng`, announces what + where) |
| 2 | Siren | Already built (`alerts.py` - synthesized WAV, no audio asset needed) |
| 3 | SMS to supervisor hardware device (offline) | **New** - `gsm_sms.py`, a second SMS channel over a serial GSM modem (AT commands), independent of the existing internet-only Twilio channel |
| 4 | Entry gate - biometric (PPE) | **New** - `gate.py` + `face_id.py`, offline face match (OpenCV YuNet+SFace) + PPE check before entry, see "Entry gate" below |
| 5 | Recognize the worker during live detection, per entry-gate data | **New** - the same enrolled-worker registry attributes a live PPE violation to a name, not just "Worker #3" (`cameras.py`) |
| 6 | Increase the epoch for fire/smoke training | Already configured (`epochs=100` in both train scripts) - the real blocker was `batch=1` on-device; see `Aegis_Cloud_Training.ipynb` for training on a real GPU instead |
| 7 | Merge 'Reports' and 'Analytics' in the sidebar | **Done** - one "Reports & Analytics" page |

Hardware-part items (fire/smoke decision sensor, Jetson, full-body
CCTV camera, other gear) are procurement, not code - except the
sensor, which now has a software side too:

### Entry gate (face ID)

Offline, on-device face recognition - **not** a cloud API, **not**
dlib (a painful aarch64/Jetson build) - built on OpenCV's own
`FaceDetectorYN`/`FaceRecognizerSF` (`face_id.py`). One-time model
download (see `backend/models/README.md`), fully offline after that.

- Enroll a worker from the dashboard's new **Entry Gate** page (name,
  department, required PPE, one or more photos).
- The gate camera is a **DVR/CCTV channel, not a browser webcam** -
  `gate_camera.py` pulls it server-side over RTSP (`GATE_CAMERA_SOURCE`
  in `backend/.env`), exactly like `cameras.json` already does for
  PPE/fire. See `backend/DVR_SETUP.md` for the Hikvision RTSP URL
  format and wiring a hybrid DVR's channels to PPE/fire/gate. With
  `GATE_CAMERA_AUTO_SCAN=true` (the default once a source is set) the
  gate watches on its own and logs/decides entries with nobody
  touching the dashboard; the **Entry Gate** page also has a manual
  "Capture & Scan" button (`POST /gate/camera/capture`) and, with no
  DVR configured, a plain photo-upload fallback (`POST /gate/scan`)
  that keeps working either way.
- `gate.process_gate_scan()` (shared by all three paths above) matches
  the face, runs the same PPE model used everywhere else, and
  returns ALLOWED / DENIED / UNKNOWN_FACE / NO_FACE. Every scan is
  logged to `backend/gate_log.jsonl` and a DENIED fires the same
  local siren + voice channel as a regular violation.
- Live monitoring: a worker who already has a PPE violation this
  frame gets a real face-match attempt (`config.FACE_ID_ON_VIOLATIONS_ONLY`,
  default on) - matched, their name appears on the live feed and in
  the Alert Log instead of an anonymous worker number.

### Offline SMS (GSM modem)

`gsm_sms.py` talks plain AT commands to a serial GSM/GPRS modem
(SIM800L, SIM900, A9, ...) - `GSM_SMS_ENABLED=true` plus
`GSM_SERIAL_PORT` in `.env`. Runs alongside the existing Twilio
channel, not instead of it; needs hardware, not internet.

### Fire/smoke sensor fusion

`fire_sensor.py` reads a simple `FLAME:<0|1>,SMOKE:<raw>` line off a
serial port (an MCU/Arduino reading a flame + MQ2-style gas sensor).
Tags each vision-confirmed fire/smoke alert with whether the sensor
agrees (`FIRE_SENSOR_ENABLED=true`), and can optionally raise an
alert from the sensor alone (`FIRE_SENSOR_CAN_ALERT_ALONE=true`) for
when it reacts faster than the camera. Vision-only detection is
completely unaffected either way - this only adds a second signal.

## Project layout

```
backend/
  main.py          FastAPI app - routes, model loading, startup/shutdown
  config.py        .env loading, model paths, class maps, camera config
  ppe_logic.py     YOLO box parsing + per-worker PPE association
  cameras.py       Per-camera capture/inference worker thread
  alerts.py        Local siren + local alert log + Twilio SMS + GSM SMS
  face_id.py       Offline face detection/matching (entry gate + live ID)
  gate.py          Entry-gate scan pipeline (face match -> PPE check)
  gsm_sms.py       Offline SMS over a serial GSM modem (AT commands)
  fire_sensor.py   Hardware fire/smoke sensor fusion over serial
  models/          Face ID ONNX models (one-time download - see its README)
  cameras.json     Camera list (webcam / video file / RTSP per entry)
  uploaded_videos/ Videos uploaded from the dashboard (generated at
                    runtime, gitignored)
  alert_log.jsonl  Offline audit trail (generated at runtime, gitignored)
  gate_log.jsonl   Entry-gate scan history (generated at runtime, gitignored)
  workers.json     Enrolled workers + face embeddings (generated at runtime)
  assets/siren.wav Synthesized on first run (stdlib only, gitignored)
  .env             Optional Twilio/GSM/sensor config (from .env.example, gitignored)
  requirements.txt

ppe-safety-dashboard/
  React + Vite + Tailwind dashboard (Aegis SiteWatch)
  src/lib/localAlert.js         Client-side offline tone + OS notification
  src/components/EntryGatePanel.jsx   Enroll workers + run gate scans

runs/, fire_smoke_detection/  Trained weights this build uses, plus the
                              training scripts and data.yaml that produced
                              them (PPE_train.py, fire_train.py).

extra_gear_detection/
  README.md        Where the "other industry" model came from, its
                    license, and the known safety-harness gap
  weights/sh17_yolov8n.pt   Optional third model - mask, ear
                    protection, face shield (see below)
```

## Running it

Backend:

```
cd backend
pip install -r requirements.txt
# Optional - only if you want SMS too. Leave unset/blank for a fully
# offline deployment; the local siren, local log, and dashboard alerts
# all work either way.
cp .env.example .env
python main.py
```

Requires an audio player for the local siren to actually be audible -
`pulseaudio-utils` (`paplay`) or `alsa-utils` (`aplay`) on Linux, which
most desktop distros already have. If neither is present it logs a note
and simply skips playback (never crashes) - the local log and dashboard
alerts still fire.

Frontend:

```
cd ppe-safety-dashboard
npm install
cp .env.example .env   # only needed if the backend isn't on localhost:8000
npm run dev
```

Backend model paths and camera list are read from `backend/config.py` /
`backend/cameras.json`. If you move the project folder, either keep the
same relative layout (`runs/...`, `fire_smoke_detection/...`,
`extra_gear_detection/...` next to `backend/`) or set `PPE_MODEL_PATH` /
`FIRE_MODEL_PATH` / `EXTRA_GEAR_MODEL_PATH` in `backend/.env`.

## Other industry gear (optional, off by default)

Construction PPE (helmet/vest/gloves/boots/goggles) is the PS06-required
set. On top of that, any camera can also opt into detecting gear from
**other industries** - mask/respirator, ear protection, face shield -
powered by a second, separately-trained model (the construction-PPE
model was never trained on these classes, so this couldn't be a config
tweak to it). Toggle it per camera in the dashboard's "Other Industry
Gear" pills, or via `PUT /cameras/{id}/watched-items`.

- **Model:** `extra_gear_detection/weights/sh17_yolov8n.pt`, from the
  open [SH17 dataset](https://github.com/ahmadmughees/SH17dataset)
  (CC BY-NC-SA 4.0 - non-commercial; fine for this submission, see
  `extra_gear_detection/README.md` for the full license note).
- **Same "presence-only" limitation as vest** (see below): the model
  can say "I see a mask," never "this person isn't wearing one," so a
  missing item is inferred the same honest way vest already is
  (`source: "inferred"` in the API).
- **Off by default, zero cost when unused:** none of the three are in
  `REQUIRED_PPE_ITEMS`, and a camera that never opts in never runs
  this model at all (`cameras.py`'s per-frame loop skips it entirely).
- **Known gap: safety harness.** We looked for a small, freely
  downloadable, off-the-shelf model that also covers a harness/
  working-at-height class and didn't find one meeting the same bar as
  SH17 - see `extra_gear_detection/README.md` for what we checked and
  what training one yourself would take. Not shipped rather than
  shipped inaccurately.

## Recorded video upload

A camera source doesn't have to be live hardware. From the dashboard's
Add Camera form, pick "recorded video" and either upload a file straight
from your browser or type a path to a video already on the machine
running the backend - both end up as an ordinary camera source, processed
by the same PPE + fire/smoke pipeline, siren, logging, and dashboard feed
as a live webcam. This is the easiest way to demo the system with a
known-good clip (e.g. a real fire/smoke video, or footage with a worker
missing PPE) instead of relying on whatever a live camera happens to see.

- `POST /cameras/upload-video` accepts a multipart file upload, streams
  it to `backend/uploaded_videos/` (gitignored - not committed), and
  hands back a server-side path you then add as a camera's `source`
  exactly like a typed-in path.
- Uploaded filenames are sanitized (no path traversal, no unsafe
  characters) and made unique, and are capped at `MAX_UPLOAD_VIDEO_BYTES`
  (default 500MB, configurable in `backend/.env`).
- The upload is independently re-opened with OpenCV and a test frame is
  read before it's accepted - a file with a video-looking extension but
  corrupt/non-video contents is rejected up front with a clear error,
  rather than becoming a camera that silently never comes online.
- A recorded video source loops back to its start when it reaches the
  end (`CameraWorker`'s existing playback logic), so a short demo clip
  keeps the feed - and any violations/fire in it - running continuously.

## Fire/smoke alert confirmation

Fire/smoke alerting is set to the **earliest** it can be: a hazard
class (`fire` or `smoke`) reaches the siren/log/SMS pipeline the
moment it's detected on a single processed frame - no waiting, no
streak to build up. This is controlled by `FIRE_CONFIRM_FRAMES`
(`ppe_logic.update_fire_confirmation`), currently set to its minimum,
**1**.

- The tradeoff of running at 1: nothing distinguishes a genuine,
  sustained fire from a one-frame false positive (a lighter flick, a
  lit cigarette, warm lighting, a reflection) - both alert instantly.
  That's intentional for demo purposes - the priority is a siren that
  visibly, reliably fires the instant real fire/smoke is in frame.
- If false positives from brief/incidental flame sources become a
  problem, raise `FIRE_CONFIRM_FRAMES` in `backend/.env` (2 already
  filters a pure one-frame blip while still confirming a real fire in
  well under a second on most hardware). Each extra frame adds real
  delay before a genuine fire alerts too, so watch the "AI FPS" the
  dashboard reports for your actual hardware before raising it far.
- Detections that haven't yet reached the confirm-frame count (only
  relevant if you raise it above 1) show up on the live MJPEG feed in
  a thin yellow **"checking"** box rather than a full alert - so
  raising this value never means the dashboard just goes silent on
  something it noticed.
- A second, weaker knob - `FIRE_MIN_BOX_AREA_RATIO` - drops fire/smoke
  boxes below a given fraction of the frame area before they're even
  considered. **Off by default**, because a genuine small fire, far
  from the camera or just starting, is also small in-frame; only turn
  it on if you need to filter tiny/distant spurious boxes and raising
  `FIRE_CONFIRM_FRAMES` isn't the fix you want.
- This is a detection-*logic* setting, not a retrain: the underlying
  model (`yolo11n_fire_smoke4`, 33/100 epochs, mAP50≈0.60 - see "Model
  accuracy" below) is unchanged and still the thing that ultimately
  limits how early/reliably a real fire is seen. This setting controls
  how *fast vs. how filtered* the alert pipeline reacts to whatever
  the model already detects; it can't make
  the model detect something it was never trained to recognize well.

## Violation hotspots

Knowing a violation happened is one thing; knowing WHERE they keep
happening is what actually tells a safety officer where to intervene -
a doorway everyone forgets to helmet up at, a bay where the same
worker keeps showing up without gloves. Two views of the same idea,
built entirely from data the system already collects (no new model, no
extra storage):

- **Per-camera live overlay.** Each `CameraWorker` keeps a low-res grid
  tracking where in ITS OWN frame violations have been landing,
  decayed a little every processed frame (`config.HEATMAP_DECAY`) so it
  reflects recent activity rather than accumulating forever. Toggle it
  per camera with the thermometer icon next to each camera's power/
  delete buttons in "Camera & Detection Settings" (`PUT /cameras/{id}/
  heatmap`) - once on, it blends a colorized heatmap straight into that
  camera's live MJPEG feed, with detection boxes/labels still drawn
  crisply on top. The grid keeps accumulating in the background even
  while the overlay is off, so turning it on mid-demo immediately shows
  whatever's already built up instead of starting blank.
- **Site-wide "Violation Hotspots" panel.** Below the incident/
  compliance section, the dashboard ranks every ZONE by how many alerts
  have actually fired there this session (`GET /analytics/hotspots`) -
  the same cooldown-gated alerts the Alert Log lists chronologically,
  just grouped by zone with a fire/smoke-vs-PPE breakdown. Answers
  "which zone needs attention" where the per-camera overlay answers
  "where in this camera's view".
- Both reset to empty at backend startup (the grid and the alert
  history are in-memory, by design - see "Runs fully offline" above);
  a longer-running deployment builds up a genuinely useful picture over
  a shift rather than staying interesting only for a few minutes.

## Design notes / known limitations (read before a judge asks)

**The "no_vest" class was never trained.** `data.yaml` has a `vest` class
(worn) but no negative counterpart — unlike helmet/gloves/boots, which all
have an explicit `no_*` class. Rather than silently ignore missing-vest
violations, `ppe_logic.py` infers one: if a detected worker has no `vest`
box associated with them, that's reported as a vest violation with
`source: "inferred"` (visible in the API and flagged in the dashboard),
distinct from the model's own direct `no_helmet`/`no_gloves`/`no_boots`
detections (`source: "model"`). The honest fix is retraining with real
`no_vest` examples; this is the interim, transparent workaround.

**Model accuracy.** The deployed PPE model (`yolo11n_ppe2`, 100 epochs)
scores mAP50 ≈ 0.57. The fire/smoke model in use (`yolo11n_fire_smoke4`,
33 of a planned 100 epochs) scores mAP50 ≈ 0.60 — the best of four training
attempts, all of which stopped early. Training was run with `batch=1,
workers=0` in `PPE_train.py` / `fire_train.py`, which is extremely slow
(the longest run took ~30 hours for 33 epochs); raising batch size and
worker count would let training actually finish and likely improve both
models further.

**Multi-camera support is config-driven but only one camera is real.**
`cameras.json` ships with `CAM-01` enabled (the physical webcam) and three
disabled placeholder entries showing how to point at a second webcam, a
recorded video file, or an RTSP stream — flip `"enabled": true` and fill in
`"source"` to add a real one.

**Per-worker association is a simple box-containment heuristic**, not
pose/keypoint-based: a PPE item is assigned to whichever person's
(padded) bounding box contains its center point. This is a common,
reasonably robust choice for a prototype, but can misattribute PPE in
frames with heavily overlapping workers.
