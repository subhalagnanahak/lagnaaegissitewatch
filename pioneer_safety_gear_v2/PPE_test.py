import torch
import numpy as np
from ultralytics import YOLO

model = YOLO("/home/lonewolf8/Desktop/hackathon 2026/construction-ppe/runs/detect/yolo11n_ppe2/weights/best.pt")
pred=model.predict(source="/home/lonewolf8/Desktop/hackathon 2026/construction-ppe/images/val/image176.jpg", save=True, device='cpu', conf=0.5, imgsz=512)
print(pred)
