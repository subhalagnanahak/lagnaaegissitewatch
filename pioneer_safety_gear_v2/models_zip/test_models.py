from ultralytics import YOLO

if __name__ == "__main__":
    base = r"C:\Users\subha\OneDrive\Desktop\AEGIS SITEWATCH\pioneer_safety_gear_v2\pioneer_safety_gear_v2"

    ppe  = YOLO("ppe-v4-best.pt")
    fire = YOLO("fire-smoke-best.pt")

    print("=== PPE ===")
    ppe.val(data=base + r"\ppe_dataset\data_aegis.yaml", imgsz=640, plots=True, workers=0)

    print("=== FIRE/SMOKE ===")
    fire.val(data=base + r"\fire_dataset\data.yaml", imgsz=640, plots=True, workers=0)
