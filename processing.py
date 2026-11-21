import sys

import cv2
from ultralytics import YOLO

from db import get_latest_tables, save_reading


# ---------- load model and image ----------
model = YOLO("models/yolo11m.pt")
img = cv2.imread(sys.argv[1])

results = model.predict(
    img,
    imgsz=1280,
    conf=0.35,       # low threshold so hard cases still get picked up
    iou=0.3,         # NMS overlap, higher keeps more neighbouring people
    classes=[0],     # person only
    max_det=300,
    augment=True,    # test-time augmentation
)

# table rectangles (x, y, w, h) for the fixed camera angle
tables_dimensions = [
    (76, 41, 258, 252),
    (508, 41, 297, 252),
    (76, 324, 237, 252),
    (429, 324, 292, 252),
    (792, 323, 241, 254),
    (66, 614, 258, 271),
    (429, 610, 258, 271),
    (792, 610, 258, 271),
]


# ---------- count people per table ----------
# a person belongs to a table if the center of their box is inside it
table_count = {i: 0 for i in range(1, 9)}

for box in results[0].boxes:
    x1, y1, x2, y2 = map(int, box.xyxy[0])
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

    for i in range(1, 9):
        X, Y, W, H = tables_dimensions[i - 1]
        if X < cx < X + W and Y < cy < Y + H:
            table_count[i] += 1


# ---------- draw table rectangles ----------
# green = empty, red = full (4 people), orange = partly used
for idx, (X, Y, W, H) in enumerate(tables_dimensions, 1):
    if table_count[idx] == 0:
        the_color = (20, 255, 57)
    elif table_count[idx] == 4:
        the_color = (49, 49, 255)
    else:
        the_color = (70, 140, 255)
    cv2.rectangle(img, (X, Y), (X + W, Y + H), the_color, 3)


# ---------- chair positions ----------
# 4 chairs per table, placed at fixed fractions of the table rectangle
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


# ---------- find occupied chairs ----------
# each person takes the nearest chair, only if it's within 80px
occupied = set()

for box in results[0].boxes:
    x1, y1, x2, y2 = map(int, box.xyxy[0])
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

    nearest = min(
        chairs,
        key=lambda c: (cx - c[2]) ** 2 + (cy - c[3]) ** 2
    )
    distance = (cx - nearest[2]) ** 2 + (cy - nearest[3]) ** 2

    if distance < 80 ** 2:
        occupied.add((nearest[0], nearest[1]))


# ---------- draw chairs (red = taken, green = free) ----------
for table_id, seat_id, sx, sy in chairs:
    taken = (table_id, seat_id) in occupied
    color = (0, 0, 255) if taken else (0, 255, 0)
    cv2.circle(img, (sx, sy), 12, color, -1)


# ---------- results ----------
people_count = sum(table_count.values())

tables = [
    {
        "table": table_id,
        "occupied": sum((table_id, s) in occupied for s in range(1, 5)),
    }
    for table_id in range(1, 9)
]


# ---------- save output image and reading ----------
h, w = img.shape[:2]
scale = min(1.0, 720 / h)
view = cv2.resize(img, None, fx=scale, fy=scale)
cv2.imwrite("./output/Latest_image.jpg", view)

# availability = free seats out of 32
save_reading((32 - people_count) / 32, tables)

print(get_latest_tables())
