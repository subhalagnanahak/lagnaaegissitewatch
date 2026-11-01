"""One-time setup helper - downloads the two small ONNX models
face_id.py needs (see models/README.md). Run this once, on a machine
with internet access:

    python3 models/download_face_models.py

Safe to re-run - skips a file that's already present (unless
--force). Nothing in this project needs internet access again after
this finishes; everything else here runs fully offline."""

import argparse
import os
import urllib.request

MODELS_DIR = os.path.dirname(os.path.abspath(__file__))

FILES = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx"
    ),
}


def main():

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="Re-download even if already present")
    args = ap.parse_args()

    for filename, url in FILES.items():

        dest = os.path.join(MODELS_DIR, filename)

        if os.path.exists(dest) and not args.force:
            print(f"Already have {filename} - skipping (use --force to re-download).")
            continue

        print(f"Downloading {filename} ...")

        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as e:
            print(f"FAILED: {filename} - {e}")
            print("You can also grab it manually - see models/README.md.")
            continue

        size_kb = os.path.getsize(dest) // 1024
        print(f"  done ({size_kb} KB) -> {dest}")

    print()
    print("Verify with: python3 -c \"import face_id; print(face_id.status())\"")


if __name__ == "__main__":
    main()
