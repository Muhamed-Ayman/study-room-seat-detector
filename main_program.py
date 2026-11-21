
import cv2
import numpy as np
from ultralytics import YOLO
import os
import sys
from db import get_latest_tables, save_reading




# ============================================================
# 1. LOAD MODEL AND IMAGE
# ============================================================

model = YOLO("models/yolo11m.pt")


img = cv2.imread(sys.argv[1])
# img = cv2.imread("D:\\images\\images\\1.jpg")



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
    (76, 41, 258, 271),
    (508, 44, 297, 254),
    (76, 323, 237, 254),
    (429, 312, 300, 271),
    (792, 330, 246, 268),
    (66, 612, 258, 271),
    (429, 612, 258, 271),
    (792, 612, 258, 271),
]

table_count = {i: 0 for i in range(1, 9)}   
# drawing_rectangles
idx=0
print("table_count:", table_count)
for table in tables_dimensions:
    idx += 1
    X, Y, W, H = table             
    if table_count[idx] == 0:
        the_color = (0, 255, 0)
    elif table_count[idx] == 4:
        the_color = (0, 0, 255)
    else:
        the_color = (0, 255, 255)
    cv2.rectangle(img, (X, Y), (X + W, Y + H), the_color, 3)
            
        
        
    
        
    # tables_count.append(0)

for box in results[0].boxes:
    
# count = 0
    
    x1, y1, x2, y2 = map(int, box.xyxy[0])
    
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    #cv2.circle(img, (cx, cy), 5, (0, 255, 0), -1)
    
    for i in range(1, 9):
        X, Y, W, H = tables_dimensions[i - 1]
        if X < cx < X + W and Y < cy < Y + H:
            table_count[i] += 1
# cv2.putText(img, f"People: {count}", (30, 120),
            # cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
# cv2.rectangle(img, (X, Y), (X + W, Y + H), (0, 0, 255), 3)


# 1. Calculate the four chair positions for every table
chairs = []

for table_id, (X, Y, W, H) in enumerate(tables_dimensions, 1):
    for seat_id, (fx, fy) in enumerate([
        (0.28, 0.16), (0.72, 0.16),
        (0.28, 0.84), (0.72, 0.84)
    ], 1):
        chairs.append((
            table_id, seat_id,
            int(X + W * fx), int(Y + H * fy)
        ))

# 2. Find which chairs have people
occupied = set()

for box in results[0].boxes:
    x1, y1, x2, y2 = map(int, box.xyxy[0])
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

    nearest = min(
        chairs,
        key=lambda c: (cx - c[2])**2 + (cy - c[3])**2
    )

    distance = (cx - nearest[2])**2 + (cy - nearest[3])**2

    if distance < 80**2:
        occupied.add((nearest[0], nearest[1]))

# 3. Draw red and green circles on the chairs
for table_id, seat_id, sx, sy in chairs:
    taken = (table_id, seat_id) in occupied

    color = (0, 0, 255) if taken else (0, 255, 0)

    cv2.circle(img, (sx, sy), 12, color, -1)

# 4. Calculate occupancy percentage for each table
people_count = sum(table_count.values())
all_table_count = []  # Total seats across all tables
# print(all_table_count)
for table_id in range(1, 9):
    count = sum((table_id, s) in occupied for s in range(1, 5))
    count=int(count)
    all_table_count.append(count)
# print(all_table_count)

tables = [
    {"table": i + 1, "occupied": all_table_count[i]}
    for i in range(8)
]
#     occupancy = count / 4 * 100 if count > 0 else 0
#     print(f"Table {table_id}: {count}/4 occupied ({occupancy:.1f}%)")
# print(f"available seats: {32 - people_count}/32")

    
    


h, w = img.shape[:2]         
scale = min(1.0, 720 / h)
view = cv2.resize(img, None, fx=scale, fy=scale)
#show images at once
cv2.imwrite("./output/Latest_image.jpg", view)
# os.replace("output/tmp.jpg", "output/Latest_image.jpg")
x=save_reading((32-people_count)/32,tables)

print(get_latest_tables())
# cv2.imshow("Result", view)
# cv2.imshow(f"People Counter at tabel 1{count}", img)
    # print(f"Total people detected on table{number}:", count)
    # number += 1

cv2.waitKey(0)
cv2.destroyAllWindows()
