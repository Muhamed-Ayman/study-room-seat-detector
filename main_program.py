import cv2
from ultralytics import YOLO

import smartseat_db

# Where these photos come from: "replay" = recorded photos,
# "live" = a real camera stream. The dashboard labels data by this.
DATA_SOURCE = "replay"

model = YOLO("yolo11l.pt")
img = cv2.imread("./images/1.jpg")
if img is None:
    raise FileNotFoundError("Image not found!")

# number = 1
# masks=[]
# masks.append(cv2.imread("D:/images/all images/mask1.png", cv2.IMREAD_GRAYSCALE))
# masks.append(cv2.imread("D:/images/all images/mask2.png", cv2.IMREAD_GRAYSCALE))
# masks.append(cv2.imread("D:/images/all images/mask3.png", cv2.IMREAD_GRAYSCALE))
# masks.append(cv2.imread("D:/images/all images/mask4.png", cv2.IMREAD_GRAYSCALE))
# masks.append(cv2.imread("D:/images/all images/masK5.png", cv2.IMREAD_GRAYSCALE))
# masks.append(cv2.imread("D:/images/all images/mask6.png", cv2.IMREAD_GRAYSCALE))
# masks.append(cv2.imread("D:/images/all images/mask7.png", cv2.IMREAD_GRAYSCALE))
# masks.append(cv2.imread("D:/images/all images/mask8.png", cv2.IMREAD_GRAYSCALE))
# for mask in masks:
#     if mask is None:
#         raise FileNotFoundError("Mask not found!")

# imgregion = cv2.bitwise_and(img, img, mask=mask)
results = model.predict(
    img,
    imgsz=1280,      # try 1280 or 1920
    conf=0.35,       # lower threshold for hard cases
    iou=0.3,         # NMS overlap; higher keeps more neighbouring people
    classes=[0],     # person only
    max_det=300,
    augment=True,    # test-time augmentation
)
tables_dimensions = [
    (66, 612, 258, 271),
    (429, 612, 258, 271),
    (792, 612, 258, 271),
    (76, 323, 237, 254),
    (429, 312, 300, 271),
    (792, 330, 246, 268),
    (76, 41, 258, 271),
    (508, 44, 297, 254),
]

table_count = {i: 0 for i in range(1, 9)}   
# drawing_rectangles
for table in tables_dimensions:
    X, Y, W, H = table
    cv2.rectangle(img, (X, Y), (X + W, Y + H), (0, 0, 255), 3)
            
        
        
    
        
    # tables_count.append(0)

for box in results[0].boxes:
    
# count = 0
    
    x1, y1, x2, y2 = map(int, box.xyxy[0])
    
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    cv2.circle(img, (cx, cy), 5, (0, 255, 0), -1)
    
    for i in range(1, 9):
        X, Y, W, H = tables_dimensions[i - 1]
        if X < cx < X + W and Y < cy < Y + H:
            table_count[i] += 1
# cv2.putText(img, f"People: {count}", (30, 120),
            # cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
# cv2.rectangle(img, (X, Y), (X + W, Y + H), (0, 0, 255), 3)


h, w = img.shape[:2]         
scale = min(1.0, 720 / h)

view = cv2.resize(img, None, fx=scale, fy=scale)
cv2.imshow("Result", view)
# cv2.imshow(f"People Counter at tabel 1{count}", img)
    # print(f"Total people detected on table{number}:", count)
    # number += 1

for i in range(1, 9):
    print(f"Total people detected on table {i}: {table_count[i]}")

# Save this reading so the dashboard and the forecast can use it.
smartseat_db.save_snapshot(
    {f"Table {i}": table_count[i] for i in range(1, 9)},
    source=DATA_SOURCE,
)
print("Saved to database.")
    
cv2.waitKey(0)
cv2.destroyAllWindows()
