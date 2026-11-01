# Face ID models (one-time download)

Worker face identification (`face_id.py` - used by the entry gate and
by live-monitoring worker attribution) needs two small ONNX models
from the official [OpenCV Zoo](https://github.com/opencv/opencv_zoo).
This is a **one-time download during setup**, not a runtime
dependency - once these two files are sitting in this folder, face ID
runs entirely offline forever after, exactly like the PPE/fire
weights already in this project.

If these files are missing, nothing breaks: `face_id.py` detects
that at startup, disables itself, and prints why - the entry gate and
live-monitoring worker-name feature just won't be available until you
add them. Every other feature (PPE detection, fire/smoke detection,
cameras, alerts, footage) is completely unaffected either way.

## Download

Run this once, from the `backend/` folder, on a machine with internet
access:

```bash
python3 models/download_face_models.py
```

Or manually, with curl/your browser:

```bash
curl -L -o models/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx

curl -L -o models/face_recognition_sface_2021dec.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
```

| File | Size | What it does |
|---|---|---|
| `face_detection_yunet_2023mar.onnx` | ~230 KB | Finds faces in a frame ("YuNet") |
| `face_recognition_sface_2021dec.onnx` | ~37 MB | Turns a face into a 128-d embedding + compares two faces ("SFace") |

Both are Apache-2.0 licensed, from the OpenCV Zoo project, and run
through `cv2.FaceDetectorYN` / `cv2.FaceRecognizerSF` - already part
of `opencv-contrib-python` (see `backend/requirements.txt`), no extra
inference runtime (no onnxruntime, no PyTorch) needed.

## Verify it worked

```bash
python3 -c "import face_id; print(face_id.status())"
```

should print `{'available': True, 'reason': None}`. If it instead
says the files aren't found, double-check they landed in this exact
folder (`backend/models/`) - `FACE_MODELS_DIR` in `backend/.env` can
override that location if you'd rather keep them elsewhere.
