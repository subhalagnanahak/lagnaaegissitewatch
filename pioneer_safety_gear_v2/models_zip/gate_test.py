import cv2
from collections import deque
from ultralytics import YOLO

ppe, fire = YOLO("ppe-v4-best.pt"), YOLO("fire-smoke-best.pt")
N = 15
h = {k: deque(maxlen=N) for k in ("helmet", "vest", "fire", "smoke")}
cap = cv2.VideoCapture(0)
while True:
    ok, frame = cap.read()
    if not ok:
        break
    rp = ppe.predict(frame, imgsz=640, conf=0.25, verbose=False)[0]
    rf = fire.predict(frame, imgsz=640, conf=0.5, verbose=False)[0]
    sp = {rp.names[int(c)] for c in rp.boxes.cls}
    sf = {rf.names[int(c)] for c in rf.boxes.cls}
    for k in ("helmet", "vest"):
        h[k].append(k in sp)
    for k in ("fire", "smoke"):
        h[k].append(k in sf)
    ppe_ok = all(sum(h[k]) >= 0.6 * N for k in ("helmet", "vest"))
    alarm = sum(h["fire"]) >= 8 or sum(h["smoke"]) >= 8
    out = rp.plot()
    cv2.putText(out, "PPE: PASS" if ppe_ok else "PPE: FAIL", (10, 30), 0, 1, (0, 255, 0) if ppe_ok else (0, 0, 255), 2)
    cv2.putText(out, "FIRE/SMOKE ALARM" if alarm else "no fire", (10, 65), 0, 1, (0, 0, 255) if alarm else (0, 255, 0), 2)
    cv2.imshow("Aegis test", out)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
cap.release()
cv2.destroyAllWindows()
