import cv2
from ultralytics import YOLO
from pathlib import Path

model = YOLO("yolo11l.pt")
# looping through the images folder
for image_path in sorted(Path("img").glob("*.jpg")):
    img = cv2.imread(str(image_path))
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
    for table_id in range(1, 9):
        count = sum((table_id, s) in occupied for s in range(1, 5))

        print(f"Table {table_id}: {count}/4 occupied")

    h, w = img.shape[:2]         
    scale = min(1.0, 720 / h)

    view = cv2.resize(img, None, fx=scale, fy=scale)
    #show images at once
    cv2.imshow(f"Result - {image_path.name}", view)
    # cv2.imshow(f"People Counter at tabel 1{count}", img)
        # print(f"Total people detected on table{number}:", count)
        # number += 1

    #for i in range(1, 9):
       # print(f"Total people detected on table {i}: {table_count[i]}")
        
cv2.waitKey(0)
cv2.destroyAllWindows()
