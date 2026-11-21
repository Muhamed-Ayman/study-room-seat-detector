import subprocess
import sys
import time
from pathlib import Path

PROCESS_SCRIPT = "main_program.py"   # your YOLO / OpenCV file
DASHBOARD = "app.py"         # the Streamlit file
OUTPUT_DIR = Path("output")          # your output folder
IMAGES_DIR = Path("D:\\images\\images")          # your images folder
DELAY_SECONDS = 1                    # pause between two images
EXTENSIONS = {".jpg", ".jpeg", ".png"}

idx = 0

dashboard = subprocess.Popen([sys.executable, "-m", "streamlit", "run", DASHBOARD])

try:
    lap = 0
    while True:
        images = sorted(p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in EXTENSIONS)
        if not images:
            print("No images in Images/ yet, waiting...")
            time.sleep(3)
            continue

        lap += 1
        print(f"--- lap {lap} ({len(images)} images) ---")
        for img_path in images:
            print(f"Processing {img_path.name} ...")
            idx += 1
            subprocess.run([sys.executable, PROCESS_SCRIPT, str(img_path)])
            time.sleep(DELAY_SECONDS)
except KeyboardInterrupt:
    pass
finally:
    dashboard.terminate()