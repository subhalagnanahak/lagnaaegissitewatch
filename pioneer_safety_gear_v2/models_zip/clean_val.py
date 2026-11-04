import re, pathlib
base = pathlib.Path(r"C:\Users\subha\OneDrive\Desktop\AEGIS SITEWATCH\pioneer_safety_gear_v2\pioneer_safety_gear_v2\fire_dataset")
key = lambda p: re.sub(r"\.rf\.[0-9a-f]+", "", p.stem)
train_keys = {key(p) for p in (base/"train"/"images").glob("*")}
clean = [p for p in (base/"valid"/"images").glob("*") if key(p) not in train_keys]
(base/"valid_clean.txt").write_text("\n".join(str(p) for p in clean))
(base/"data_clean.yaml").write_text(
    f"train: {base/'train'/'images'}\nval: {base/'valid_clean.txt'}\nnames:\n  0: fire\n  1: smoke\n")
print("clean valid images:", len(clean))

from ultralytics import YOLO
YOLO("fire-smoke-best.pt").val(data=str(base/"data_clean.yaml"), imgsz=640, workers=0)
