from ultralytics import YOLO

# Load pretrained model
model = YOLO("/home/lonewolf8/Desktop/hackathon 2026/construction-ppe/yolo11n.pt")

# Train the model on Construction-PPE dataset 
model.train(data="/home/lonewolf8/Desktop/hackathon 2026/pioneer_safety_gear_v2/ppe_dataset/data.yaml", epochs=100, imgsz=512, workers=0, batch=1,device='cuda:0',name='yolo11n_ppe',save=True)
