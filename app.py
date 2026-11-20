

import base64
import io
import re
import time
from collections import Counter
from datetime import datetime, timedelta, time as dtime
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import smartseat_db
except ImportError:
    smartseat_db = None

# ==========================================
# PAGE SETUP
# ==========================================

st.set_page_config(
    page_title="SmartSeat | University of Debrecen",
    page_icon="🪑",
    layout="wide",
    initial_sidebar_state="expanded"
)

css_path = Path(__file__).parent / "assets" / "style.css"

if css_path.exists():
    with open(css_path, "r", encoding="utf-8") as css_file:
        st.markdown(
            f"<style>{css_file.read()}</style>",
            unsafe_allow_html=True
        )

# ==========================================
# SYSTEM CONFIGURATION
# ==========================================

# Defaults, used only while the database returns no snapshot.
DATA_SOURCE = "demo"            # "demo", "replay" or "live"
CAMERA_CONNECTED = False
DETECTION_STATUS = "Awaiting integration"
LAST_DETECTION_UPDATE = None    # a datetime once real data is loaded

# Live data older than this many seconds is shown as STALE.
STALE_AFTER_SECONDS = 120

# Find Available Seats looks this many minutes either side of the time.
AVAILABILITY_WINDOW_MINUTES = 15

OPENING_TIME = None
CLOSING_TIME = None


DEFAULT_TABLE_DATA = {
    "Table 01": 1,
    "Table 02": 1,
    "Table 03": 0,
    "Table 04": 2,
    "Table 05": 1,
    "Table 06": 2,
    "Table 07": 1,
    "Table 08": 1
}


TABLE_CAPACITY = {name: 4 for name in DEFAULT_TABLE_DATA}

TABLE_LAYOUT = {
    "Table 01": (3, 5),      # top row, left column
    "Table 02": (37, 5),     # top row, middle column
    "Table 03": (3, 36),     # middle row
    "Table 04": (37, 36),
    "Table 05": (71, 36),
    "Table 06": (3, 67),     # bottom row
    "Table 07": (37, 67),
    "Table 08": (71, 67)
}

TABLE_BOX_WIDTH = 26         # width of each table box, % of the room box


MAP_HEIGHT = 460

ROOM_IMAGE_NAMES = (
    "room.jpg", "room.jpeg", "room.png", "room.jpg.jpeg"
)

# Colours (University of Debrecen palette plus status colours)
DEEP_GREEN = "#103725"
GREEN = "#174b35"
SOFT_GREEN = "#e8f1e9"
AMBER = "#8a5a00"
SOFT_AMBER = "#fdf1d3"
RED = "#b42318"
SOFT_RED = "#fee4e2"
GOLD = "#c6a45b"
MUTED = "#55655b"
LINE = "#e2ded4"

# Table states: (background, strong colour, label)
STATE_STYLE = {
    "available": (SOFT_GREEN, GREEN, "AVAILABLE"),
    "partial": (SOFT_AMBER, AMBER, "PARTLY OCCUPIED"),
    "full": (SOFT_RED, RED, "FULL"),
}

DATA_SOURCE_TEXT = {
    "demo": "DEMO DATA",
    "replay": "REPLAY DATA",
    "live": "LIVE"
}

DATA_MODE_TEXT = {
    "demo": "Demonstration data",
    "replay": "Replay of recorded images",
    "live": "Live detections"
}

# ==========================================
# ADDITIONAL DASHBOARD STYLES
# ==========================================

st.markdown("""
<style>
.smartseat-kpi {
    background: #ffffff;
    border: 1px solid #e2ded4;
    border-radius: 12px;
    padding: 17px;
    min-height: 105px;
}
.smartseat-kpi-label {
    color: #55655b;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 8px;
}
.smartseat-kpi-value {
    color: #103725;
    font-size: 28px;
    font-weight: 800;
}
.smartseat-section-note {
    color: #55655b;
    font-size: 12px;
}
.smartseat-panel {
    background: #ffffff;
    border: 1px solid #e2ded4;
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 12px;
}
.smartseat-panel-title {
    color: #103725;
    font-weight: 700;
    margin-bottom: 7px;
}
.smartseat-panel-text {
    color: #55655b;
    font-size: 13px;
    line-height: 1.5;
}
.smartseat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 14px;
    padding: 10px 0;
    border-bottom: 1px solid #e2ded4;
    color: #1b2a22;
}
</style>
""", unsafe_allow_html=True)


# ==========================================
# SESSION STATE (sample data only)
# ==========================================

if "table_data" not in st.session_state:
    st.session_state.table_data = dict(DEFAULT_TABLE_DATA)
else:
    # Keep any saved sample data aligned with the configured tables.
    st.session_state.table_data = {
        name: st.session_state.table_data.get(name, default)
        for name, default in DEFAULT_TABLE_DATA.items()
    }

# Messages about the data, shown at the top of the page.
data_problems = []



def to_local_naive(value):

    if value is None:
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)

    return value


def canonical_table_name(name):
    """Make table names match: 'Table 1', 'table 01' and 'T1' all become
    'Table 01', the name used by this dashboard."""
    match = re.search(r"(\d+)", str(name))

    if match is None:
        return str(name)

    return f"Table {int(match.group(1)):02d}"


def load_latest_snapshot():

    if smartseat_db is None:
        return None

    reader = getattr(smartseat_db, "latest_snapshot", None)

    if reader is None:
        data_problems.append(
            "smartseat_db has no latest_snapshot() function, "
            "so sample data is shown."
        )
        return None

    try:
        return reader()
    except Exception as error:
        data_problems.append(f"Could not read the latest snapshot: {error}")
        return None


def load_history():

    if smartseat_db is None:
        return [], None

    reader = getattr(smartseat_db, "history_totals", None)

    if reader is None:
        return [], "smartseat_db has no history_totals() function."

    try:
        return list(reader()), None
    except Exception as error:
        return [], f"Could not read the history: {error}"


def load_availability(weekday, visit_time, seats_needed):
    
    function = (
        getattr(smartseat_db, "predict_availability", None)
        if smartseat_db is not None else None
    )

    if function is None:
        return {"status": "not_connected"}

    try:
        raw = function(
            weekday, visit_time, seats_needed, AVAILABILITY_WINDOW_MINUTES
        )
    except Exception as error:
        return {"status": "error", "message": str(error)}

    if isinstance(raw, dict):
        probability = raw.get("probability")
        details = {
            "readings_used": raw.get("readings_used"),
            "days_covered": raw.get("days_covered"),
            "source": raw.get("source"),
        }
    else:
        probability = raw
        details = {
            "readings_used": None, "days_covered": None, "source": None
        }

    if probability is None:
        return {"status": "not_enough_data", **details}

    try:
        probability = float(probability)
    except (TypeError, ValueError):
        return {
            "status": "error",
            "message": "The forecast function did not return a number."
        }

    if not 0 <= probability <= 1:
        return {
            "status": "error",
            "message": "The probability must be between 0 and 1."
        }

    return {"status": "ok", "probability": probability, **details}


# ==========================================
# DATA CALCULATIONS
# ==========================================

snapshot = load_latest_snapshot()
use_snapshot = False

if snapshot:
    configured = list(DEFAULT_TABLE_DATA)
    incoming = {}

    for raw_name, raw_value in (snapshot.get("counts") or {}).items():
        name = canonical_table_name(raw_name)

        if name in incoming:
            data_problems.append(
                f"Two readings both mean {name}; the last one is used."
            )

        incoming[name] = raw_value

    if not any(name in incoming for name in configured):
        data_problems.append(
            "The stored snapshot uses table names that do not match this "
            "dashboard (expected names with a number, like 'Table 1' to "
            "'Table 8'), so sample data is shown instead."
        )
    else:
        use_snapshot = True

        unknown = [name for name in incoming if name not in configured]
        missing = [name for name in configured if name not in incoming]

        if unknown:
            data_problems.append(
                "Ignored unknown table names in the snapshot: "
                + ", ".join(sorted(map(str, unknown)))
            )
        if missing:
            data_problems.append(
                "No reading for: " + ", ".join(missing)
                + ". They are shown as 0."
            )

        table_data = {}
        for name in configured:
            try:
                table_data[name] = max(0, int(incoming.get(name, 0) or 0))
            except (TypeError, ValueError):
                table_data[name] = 0
                data_problems.append(f"Invalid count for {name}, shown as 0.")

        DATA_SOURCE = snapshot.get("source", "replay")
        if DATA_SOURCE not in DATA_SOURCE_TEXT or DATA_SOURCE == "demo":
            DATA_SOURCE = "replay"

        LAST_DETECTION_UPDATE = to_local_naive(snapshot.get("timestamp"))
        DETECTION_STATUS = (
            "Running" if DATA_SOURCE == "live"
            else "Replay of recorded images"
        )

if not use_snapshot:
    table_data = dict(st.session_state.table_data)

# Is live data out of date?
data_is_stale = False

if DATA_SOURCE == "live":
    if LAST_DETECTION_UPDATE is None:
        data_problems.append(
            "The live snapshot has no usable timestamp, so its age "
            "cannot be checked."
        )
    else:
        seconds_old = (datetime.now() - LAST_DETECTION_UPDATE).total_seconds()
        data_is_stale = seconds_old > STALE_AFTER_SECONDS

if data_is_stale:
    DETECTION_STATUS = "No recent update"

CAMERA_CONNECTED = DATA_SOURCE == "live" and not data_is_stale

total_tables = len(table_data)
people_detected = sum(table_data.values())
occupied_tables = sum(1 for count in table_data.values() if count > 0)
unoccupied_tables = total_tables - occupied_tables

tables_with_people_rate = (
    occupied_tables / total_tables * 100 if total_tables else 0
)

total_capacity = sum(TABLE_CAPACITY[name] for name in table_data)
occupancy_rate = (
    people_detected / total_capacity * 100 if total_capacity else 0
)
occupancy_rate_bar = int(min(occupancy_rate, 100))


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def table_state(count, capacity):
    """available (nobody), partial (some seats taken) or full."""
    if count <= 0:
        return "available"
    if count >= capacity:
        return "full"
    return "partial"


def status_badge(text, kind="demo"):
    """Small coloured label for source, connection and table states."""

    colours = {
        "demo": ("#f6ebcf", "#5c4410"),
        "off": ("#eceeea", "#4a554e"),
        "ok": (SOFT_GREEN, GREEN),
        "available": (SOFT_GREEN, GREEN),
        "partial": (SOFT_AMBER, AMBER),
        "full": (SOFT_RED, RED),
    }

    background, text_colour = colours[kind]

    return (
        f'<span style="background:{background};color:{text_colour};'
        f'font-size:11px;font-weight:700;padding:4px 10px;'
        f'border-radius:999px;letter-spacing:0.05em;'
        f'white-space:nowrap;">{escape(text)}</span>'
    )


def state_badge(count, capacity):
    state = table_state(count, capacity)
    return status_badge(STATE_STYLE[state][2], state)


def time_ago(moment):
    seconds = max(0, int((datetime.now() - moment).total_seconds()))

    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def source_badge():
    if data_is_stale:
        return status_badge(
            f"STALE · {time_ago(LAST_DETECTION_UPDATE)}", "demo"
        )

    kind = "ok" if DATA_SOURCE == "live" else "demo"
    return status_badge(DATA_SOURCE_TEXT[DATA_SOURCE], kind)


def camera_badge():
    if CAMERA_CONNECTED:
        return status_badge("CONNECTED", "ok")
    return status_badge("NOT CONNECTED", "off")


def last_update_text():
    if LAST_DETECTION_UPDATE is None:
        return "No live update"
    return (
        LAST_DETECTION_UPDATE.strftime("%d %b %Y, %H:%M:%S")
        + f" ({time_ago(LAST_DETECTION_UPDATE)})"
    )


def display_kpi(label, value, description=""):

    st.html(f"""
    <div class="smartseat-kpi">
        <div class="smartseat-kpi-label">{escape(str(label))}</div>
        <div class="smartseat-kpi-value">{escape(str(value))}</div>
        <div class="smartseat-section-note">{escape(str(description))}</div>
    </div>
    """)


def display_panel(title, text_html):
    """Panel with a title and body. text_html is trusted HTML:
    escape() any dynamic text before passing it in."""

    st.html(f"""
    <div class="smartseat-panel">
        <div class="smartseat-panel-title">{escape(title)}</div>
        <div class="smartseat-panel-text">{text_html}</div>
    </div>
    """)


def display_status_rows(title, rows):
    """rows = list of (label, html_on_the_right)."""

    body = "".join(
        f'<div class="smartseat-row"><span>{escape(label)}</span>'
        f'<span>{right_html}</span></div>'
        for label, right_html in rows
    )

    st.html(f"""
    <div class="smartseat-panel">
        <div class="smartseat-panel-title">{escape(title)}</div>
        {body}
    </div>
    """)


def display_problems():
    for problem in data_problems:
        st.warning(problem)


def display_demo_notice():
    if data_is_stale:
        st.warning(
            "Live data is out of date. The last detection was "
            f"{time_ago(LAST_DETECTION_UPDATE)}. Check that the "
            "detection process is still running."
        )
    elif DATA_SOURCE == "demo":
        st.info(
            "Demonstration mode: The occupancy values currently "
            "shown are sample data, not live camera detections."
        )
    elif DATA_SOURCE == "replay":
        st.info(
            "Replay mode: Values come from recorded images "
            "processed by the detection system, not a live camera."
        )


# ==========================================
# STUDY-AREA MAP (schematic, table level)
# ==========================================

def display_table_map(selected_table=None):
    """Draw the room: each table shows how many people were detected out
    of its capacity, as a number and a row of dots.

    The dots are a COUNT METER. The detector gives people per table, not
    which chair is taken, so the dots never point at a specific chair.
    """

    boxes = ""

    for name, count in table_data.items():

        if name not in TABLE_LAYOUT:
            continue

        left, top = TABLE_LAYOUT[name]
        capacity = TABLE_CAPACITY[name]
        state = table_state(count, capacity)
        background, colour, label = STATE_STYLE[state]

        border = (
            f"3px solid {GOLD}" if name == selected_table
            else f"1.5px solid {colour}"
        )

        dots = "".join(
            f'<span style="width:12px;height:12px;border-radius:50%;'
            f'box-sizing:border-box;border:1.5px solid {colour};'
            f'background:{colour if i < count else "#ffffff"};"></span>'
            for i in range(capacity)
        )

        boxes += f"""
        <div title="{count} of {capacity} people detected"
             style="position:absolute;left:{left}%;top:{top}%;
                    width:{TABLE_BOX_WIDTH}%;height:25%;
                    box-sizing:border-box;background:{background};
                    border:{border};border-radius:11px;
                    display:flex;flex-direction:column;
                    align-items:center;justify-content:center;gap:4px;">
            <div style="font-size:13px;font-weight:700;color:{DEEP_GREEN};">
                {escape(str(name))}
            </div>
            <div style="font-size:18px;font-weight:800;color:{DEEP_GREEN};">
                {count} / {capacity}
            </div>
            <div style="display:flex;gap:5px;">{dots}</div>
            <div style="font-size:11px;font-weight:700;color:{colour};
                        text-align:center;padding:0 4px;">
                {label}
            </div>
        </div>
        """

    st.html(f"""
    <div style="position:relative;height:{MAP_HEIGHT}px;background:#faf8f4;
                border:1px solid {LINE};border-radius:12px;
                overflow:hidden;">
        {boxes}
    </div>
    """)


# ==========================================
# ROOM PHOTO (Overview page only)
# ==========================================

def find_room_image():
    """First room photo found next to app.py or in assets/, else None."""
    base = Path(__file__).parent

    for folder in (base, base / "assets"):
        for filename in ROOM_IMAGE_NAMES:
            candidate = folder / filename
            if candidate.exists():
                return candidate

    return None


@st.cache_data(show_spinner="Running YOLO on the room photo...")
def run_yolo_on_photo(path_text, modified_time):
    """OPTIONAL: draw the team's YOLO detections on the room photo.

    Uses the team's own module: processing.process_image(image) must return
    (annotated_image_in_BGR, details). The imports are inside the function,
    so the dashboard starts normally when OpenCV or the module is missing.
    modified_time only makes the cache refresh when the photo changes.
    """
    import cv2
    import processing

    image = cv2.imread(path_text)

    if image is None:
        raise ValueError("The room photo could not be read.")

    annotated, details = processing.process_image(image)

    return cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB), details


def to_data_uri(picture):
    """Shrink a PIL picture and return it as text a web page can show."""
    picture = picture.convert("RGB")
    picture.thumbnail((1100, 1100))

    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=85)

    encoded = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/jpeg;base64," + encoded


@st.cache_data(show_spinner=False)
def plain_photo_uri(path_text, modified_time):
    from PIL import Image
    return to_data_uri(Image.open(path_text))


@st.cache_data(show_spinner=False)
def yolo_photo_uri(path_text, modified_time):
    from PIL import Image
    annotated, _details = run_yolo_on_photo(path_text, modified_time)
    return to_data_uri(Image.fromarray(annotated))


def display_room_overview():
    """The room photo, MAP_HEIGHT pixels tall so it lines up with the map.

    Shows the YOLO detections if the team's module is available, and the
    plain photo otherwise.
    """
    photo = find_room_image()

    if photo is None:
        st.warning(
            "Room photo not found. Put room.jpg next to app.py "
            "(or in the assets folder)."
        )
        return

    stamp = photo.stat().st_mtime
    caption = "Study room (photo)"
    problem = None
    uri = None

    try:
        uri = yolo_photo_uri(str(photo), stamp)
        caption = "Study room with YOLO detections"
    except ImportError:
        pass    # no YOLO module here: show the plain photo
    except Exception as error:
        problem = (
            "YOLO detection could not run, so the plain photo is "
            f"shown: {error}"
        )

    if uri is None:
        uri = plain_photo_uri(str(photo), stamp)

    st.html(
        f'<img src="{uri}" alt="Study room" '
        f'style="display:block;width:100%;height:{MAP_HEIGHT}px;'
        f'object-fit:cover;border-radius:12px;border:1px solid {LINE};">'
    )
    st.caption(caption)

    if problem:
        st.warning(problem)


# ==========================================
# FIND AVAILABLE SEATS (forecast card)
# ==========================================

def forecast_message_card(badge_text, title, message, kind="off"):
    """A card with no number: used for every non-forecast state."""

    st.html(f"""
    <div style="border:1px dashed #b9b5a9;border-radius:12px;
                padding:24px 22px;background:#ffffff;">
        <div>{status_badge(badge_text, kind)}</div>
        <div style="font-size:17px;font-weight:700;color:{DEEP_GREEN};
                    margin:12px 0 6px 0;">{escape(title)}</div>
        <div style="font-size:14px;color:{MUTED};line-height:1.6;">
            {escape(message)}
        </div>
    </div>
    """)


def display_visit_forecast(visit_date, visit_time, seats_needed):
    """Check the request, ask the database, and draw the right card."""

    visit_moment = datetime.combine(visit_date, visit_time)
    weekday_name = visit_date.strftime("%A")

    # 1. Check the request itself.
    if visit_moment <= datetime.now():
        forecast_message_card(
            "CHECK INPUT", "Choose a future date and time",
            "That date and time have already passed."
        )
        return

    if (
        OPENING_TIME and CLOSING_TIME
        and not OPENING_TIME <= visit_time <= CLOSING_TIME
    ):
        forecast_message_card(
            "CHECK INPUT", "Time not available",
            f"The study room is closed at {visit_time:%H:%M}. Opening "
            f"hours are {OPENING_TIME:%H:%M} to {CLOSING_TIME:%H:%M}."
        )
        return

    # 2. Ask the database.
    forecast = load_availability(
        visit_date.weekday(), visit_time, seats_needed
    )
    status = forecast["status"]

    if status == "not_connected":
        forecast_message_card(
            "NOT CONNECTED", "Availability data is not connected yet",
            "The database forecast function is not connected, so no "
            "number is shown."
        )
        return

    if status == "error":
        forecast_message_card(
            "FORECAST ERROR", "The forecast could not be calculated",
            "No number is shown. Details: "
            + str(forecast.get("message", "unknown error"))
        )
        return

    if status == "not_enough_data":
        found = forecast.get("readings_used")
        extra = (
            f" Only {found} past readings were found."
            if found is not None else ""
        )
        forecast_message_card(
            "NO FORECAST", "Not enough history yet",
            f"There are too few past readings for {weekday_name}s around "
            f"{visit_time:%H:%M} to estimate {seats_needed} seat(s), so "
            f"no number is shown.{extra}"
        )
        return

    # 3. A real number.
    window = AVAILABILITY_WINDOW_MINUTES
    start = (visit_moment - timedelta(minutes=window)).strftime("%H:%M")
    end = (visit_moment + timedelta(minutes=window)).strftime("%H:%M")
    percent = round(forecast["probability"] * 100)

    sources = {
        "live": ("LIVE DATA", "ok"),
        "replay": ("REPLAY DATA", "demo"),
        "demo": ("DEMO DATA", "demo"),
    }
    source_text, source_kind = sources.get(
        forecast.get("source"), ("SOURCE NOT STATED", "off")
    )

    based_on = ""
    if forecast.get("readings_used") is not None:
        based_on = f"{forecast['readings_used']} past readings"
        if forecast.get("days_covered") is not None:
            based_on += f" on {forecast['days_covered']} days"

    based_on_row = (
        f'<div class="smartseat-row"><span>Based on</span>'
        f'<span>{escape(based_on)}</span></div>' if based_on else ""
    )

    st.html(f"""
    <div class="smartseat-panel" style="padding:22px;">
        <div style="display:flex;justify-content:space-between;
                    align-items:center;">
            <div class="smartseat-panel-title">Estimated availability</div>
            <div>{status_badge(source_text, source_kind)}</div>
        </div>
        <div class="smartseat-panel-text">
            Estimated chance that at least one table has
            {seats_needed} free seat(s)
        </div>
        <div style="font-size:64px;font-weight:800;color:{DEEP_GREEN};
                    line-height:1.1;margin:10px 0;">{percent}%</div>
        <div style="height:8px;border-radius:4px;background:{SOFT_GREEN};
                    margin-bottom:14px;">
            <div style="height:8px;border-radius:4px;background:{GREEN};
                        width:{percent}%;"></div>
        </div>
        <div class="smartseat-row"><span>Requested visit</span>
            <span>{weekday_name}, {visit_date:%d %b %Y}, {visit_time:%H:%M}</span></div>
        <div class="smartseat-row"><span>Time window</span>
            <span>{weekday_name}s, {start} to {end}</span></div>
        {based_on_row}
        <div class="smartseat-panel-text"
             style="background:#faf8f4;border-radius:8px;padding:12px;
                    margin-top:14px;">
            This is an estimate from past patterns, not a reservation or
            a guarantee. Free seats are counted as rated capacity minus
            people detected at each table. It does not know about exams,
            holidays or events.
        </div>
    </div>
    """)


# ==========================================
# SIDEBAR
# ==========================================

with st.sidebar:

    st.html(
        """
        <div class="sidebar-brand">
            <div class="brand-name">SmartSeat</div>
            <div class="brand-subtitle">SMART STUDY SPACE SYSTEM</div>
        </div>
        """
    )

    st.divider()

    page = st.radio(
        "NAVIGATION",
        [
            "Overview",
            "Analytics",
            "Find Available Seats",
            "About SmartSeat"
        ],
        label_visibility="visible"
    )

    st.divider()

    selected_location = st.selectbox(
        "STUDY LOCATION",
        ["Study Room A"]
    )

    st.markdown("### SYSTEM STATUS")

    st.html(
        '<div style="background:#e8f1e9;color:#174b35;padding:10px;'
        'border-radius:8px;font-size:12px;font-weight:700;">'
        '● Dashboard Online</div>'
    )

    st.caption(f"YOLO detection: {DETECTION_STATUS}")
    st.caption(
        "Camera input: "
        + ("Connected" if CAMERA_CONNECTED else "Not connected")
    )
    st.caption(f"Data mode: {DATA_MODE_TEXT[DATA_SOURCE]}")

    auto_refresh = st.toggle("Auto-refresh every 10 s", value=False)


# ==========================================
# MAIN HEADER
# ==========================================

st.html(
    """
    <div style="margin-bottom:12px;">
        <div style="color:#8a6825;font-size:11px;font-weight:800;
                    letter-spacing:2px;">UNIVERSITY OF DEBRECEN</div>
        <h1 style="color:#174b35;font-size:34px;margin-top:5px;
                   margin-bottom:3px;">SmartSeat</h1>
        <div style="color:#55655b;font-size:14px;">
            Intelligent study-space occupancy monitoring
        </div>
    </div>
    """
)

st.html(
    '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;">'
    + source_badge()
    + status_badge(
        "CAMERA CONNECTED" if CAMERA_CONNECTED else "CAMERA NOT CONNECTED",
        "ok" if CAMERA_CONNECTED else "off"
    )
    + "</div>"
)

display_problems()


# ==========================================
# PAGE 1 - OVERVIEW
# ==========================================

if page == "Overview":

    st.markdown("## Occupancy Overview")

    st.caption(
        "A summary of table occupancy and detected people "
        "in the selected study location."
    )

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        display_kpi(
            "TABLES MONITORED", total_tables, "Configured study tables"
        )
    with col2:
        display_kpi(
            "PEOPLE DETECTED", people_detected, "Across all tables"
        )
    with col3:
        display_kpi(
            "OCCUPIED TABLES", occupied_tables,
            "One or more people detected"
        )
    with col4:
        display_kpi(
            "UNOCCUPIED TABLES", unoccupied_tables, "No people detected"
        )
    with col5:
        display_kpi(
            "OCCUPANCY RATE", f"{occupancy_rate:.0f}%",
            "People detected ÷ rated capacity"
        )

    st.markdown("---")

    picker_column, _spacer = st.columns([1, 2])

    with picker_column:

        selected_table = st.selectbox(
            "Select a table to inspect",
            list(table_data.keys()),
            key="overview_selected_table"
        )

    photo_column, map_column = st.columns(2)

    with photo_column:

        st.markdown("### Room Overview")

        display_room_overview()

    with map_column:

        st.markdown("### Occupancy Monitor")

        display_table_map(selected_table)

        st.caption(
            "Schematic layout based on the room photo, not to scale. "
            "Each table shows the people detected out of its seats. "
            "Dots are a count: they do not show which chair is taken."
        )

        st.html(
            '<div style="display:flex;gap:8px;flex-wrap:wrap;'
            'margin:6px 0;">'
            + status_badge("AVAILABLE: nobody detected", "available")
            + status_badge("PARTLY OCCUPIED", "partial")
            + status_badge("FULL: all seats counted", "full")
            + status_badge("SELECTED: gold outline", "demo")
            + "</div>"
        )

    details_column, status_column = st.columns(2)

    selected_count = table_data[selected_table]
    selected_capacity = TABLE_CAPACITY[selected_table]
    free_seats = max(selected_capacity - selected_count, 0)

    with details_column:

        st.markdown("### Table Details")

        display_status_rows(selected_table, [
            ("People detected", f"<b>{selected_count}</b>"),
            ("Estimated free seats",
             f"<b>{free_seats} of {selected_capacity}</b>"),
            ("State", state_badge(selected_count, selected_capacity)),
            ("Data source", source_badge()),
        ])

        st.caption(
            "Free seats = rated capacity minus people detected. "
            "It is an estimate, not chair-by-chair detection."
        )

    with status_column:

        st.markdown("### Detection Status")

        display_status_rows("Detection Status", [
            ("YOLO detection", status_badge(DETECTION_STATUS.upper(), "off")),
            ("Camera connection", camera_badge()),
            ("Data source", source_badge()),
            ("Last detection update", escape(last_update_text())),
        ])

        st.caption(
            "Badges change to LIVE only when the detection "
            "pipeline is writing fresh counts."
        )

    st.markdown("---")

    left_column, right_column = st.columns([1.2, 1])

    with left_column:

        st.markdown("### People Detected Per Table")

        st.bar_chart(
            {
                "Table": list(table_data.keys()),
                "People Detected": list(table_data.values())
            },
            x="Table",
            y="People Detected",
            color=GREEN
        )

    with right_column:

        st.markdown("### Space Utilisation")

        st.metric("Occupancy Rate", f"{occupancy_rate:.1f}%")

        st.progress(occupancy_rate_bar)

        display_panel(
            "Current Detection Summary",
            f"{occupied_tables} of {total_tables} tables currently have "
            f"people detected.<br><br>A total of {people_detected} people "
            f"are represented in the "
            f"{escape(DATA_MODE_TEXT[DATA_SOURCE].lower())}."
        )

    display_demo_notice()


# ==========================================
# PAGE 2 - ANALYTICS
# ==========================================

elif page == "Analytics":

    st.markdown("## SmartSeat Analytics")

    st.caption(
        "Statistics derived from table-level detection data "
        "(current snapshot)."
    )

    if people_detected > 0:
        busiest_table = max(table_data, key=table_data.get)
        busiest_note = f"{table_data[busiest_table]} people detected"
    else:
        busiest_table = "None"
        busiest_note = "No people detected"

    average_per_occupied = (
        people_detected / occupied_tables if occupied_tables else 0
    )

    unoccupied_share = (
        unoccupied_tables / total_tables * 100 if total_tables else 0
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        display_kpi("MOST OCCUPIED TABLE", busiest_table, busiest_note)
    with col2:
        display_kpi(
            "AVERAGE PER OCCUPIED TABLE",
            f"{average_per_occupied:.1f}",
            f"{people_detected} people across {occupied_tables} tables"
        )
    with col3:
        display_kpi(
            "UNOCCUPIED TABLES",
            f"{unoccupied_share:.0f}%",
            f"{unoccupied_tables} of {total_tables} tables"
        )

    st.markdown("---")

    dist_column, util_column = st.columns(2)

    with dist_column:

        st.markdown("### Occupancy Distribution")
        st.caption("Number of tables, grouped by people detected.")

        counts = Counter(table_data.values())
        groups = sorted(counts)

        st.bar_chart(
            {
                "People detected": [str(n) for n in groups],
                "Tables": [counts[n] for n in groups]
            },
            x="People detected",
            y="Tables",
            color=GREEN
        )

    with util_column:

        st.markdown("### Table Utilisation")
        st.caption("People detected ÷ rated capacity, per table.")

        st.bar_chart(
            {
                "Table": list(table_data.keys()),
                "Utilisation %": [
                    round(table_data[name] / TABLE_CAPACITY[name] * 100)
                    for name in table_data
                ]
            },
            x="Table",
            y="Utilisation %",
            color=GREEN
        )

    st.markdown("---")

    st.markdown("### Historical Occupancy Trend")

    history, history_problem = load_history()

    if history_problem:
        st.warning(history_problem)

    if len(history) >= 2:

        history_frame = pd.DataFrame(history)
        history_frame["time"] = history_frame["time"].apply(to_local_naive)

        st.line_chart(
            history_frame,
            x="time",
            y="people_detected",
            color=GREEN
        )

        sources = sorted(set(history_frame["source"].astype(str)))

        st.caption(
            f"{len(history)} stored detections. "
            f"Sources: {', '.join(sources)}."
        )

        if "replay" in sources or "demo" in sources:
            st.caption(
                "Replay and demo readings are not a real day of data, so "
                "they do not show how occupancy really changed over time."
            )

    else:

        st.html(f"""
        <div style="border:1px dashed #b9b5a9;border-radius:12px;
                    padding:40px 20px;text-align:center;color:{MUTED};
                    font-size:14px;line-height:1.6;background:#ffffff;">
            {status_badge("NO HISTORY YET", "off")}
            <br><br>
            Fewer than two stored detections so far. This chart fills in
            from real records once detection results are saved over
            time. No simulated trend is shown here.
        </div>
        """)

    st.markdown("---")
    st.markdown("### Utilisation Insight")

    if tables_with_people_rate >= 80:
        table_insight = "Most monitored tables currently have people detected."
    elif tables_with_people_rate >= 40:
        table_insight = (
            "There is a moderate level of table occupancy "
            "in the monitored study area."
        )
    else:
        table_insight = (
            "Most monitored tables currently have no people detected."
        )

    display_panel("Table Occupancy Insight", escape(table_insight))

    display_demo_notice()


# ==========================================
# PAGE 3 - FIND AVAILABLE SEATS
# ==========================================

elif page == "Find Available Seats":

    st.markdown("## Find Available Seats")

    st.caption(
        "See how likely it is to find seats at the time you plan to "
        "arrive. This is a forecast from past patterns, not a reservation."
    )

    input_column, result_column = st.columns([1, 1.3])

    with input_column:

        st.markdown("### Your visit")

        with st.form("find_seats_form"):

            visit_date = st.date_input(
                "Visit date",
                value=datetime.now().date() + timedelta(days=1),
                min_value=datetime.now().date()
            )

            visit_time = st.time_input(
                "Arrival time",
                value=dtime(10, 0),
                step=900
            )

            seats_needed = st.number_input(
                "Seats needed (together at one table)",
                min_value=1,
                max_value=max(TABLE_CAPACITY.values()),
                value=1,
                step=1
            )

            submitted = st.form_submit_button("Check availability")

        st.caption(
            "SmartSeat does not reserve or hold seats. Nothing you "
            "choose here is saved."
        )

    if submitted:
        st.session_state.seat_request = (
            visit_date, visit_time, int(seats_needed)
        )

    with result_column:

        request = st.session_state.get("seat_request")

        if request is None:
            display_panel(
                "Estimated availability",
                "Choose a date, an arrival time and how many seats you "
                "need, then select Check availability."
            )
        else:
            display_visit_forecast(*request)

    st.info(
        "The estimate uses past occupancy readings stored in the "
        "database, around the same weekday and time. With too little "
        "history, no number is shown."
    )

    st.caption(
        f"Current dashboard feed: {DATA_SOURCE_TEXT[DATA_SOURCE]}. "
        f"Latest detection: {last_update_text()}"
    )


# ==========================================
# PAGE 4 - ABOUT SMARTSEAT
# ==========================================

elif page == "About SmartSeat":

    st.markdown("## About SmartSeat")

    st.markdown("""
    SmartSeat is an intelligent study-space occupancy
    monitoring concept designed to help students understand
    how busy study areas are before choosing where to sit.

    The system combines computer vision, data processing, and a
    simple dashboard to provide useful occupancy information.
    """)

    st.markdown("---")
    st.markdown("### How SmartSeat Works")

    step1, step2 = st.columns(2)

    with step1:
        display_panel(
            "01 — Image Input",
            "Photos of the study area, taken from one fixed viewpoint, "
            "are the input to the detector."
        )
        display_panel(
            "02 — Computer Vision",
            "A YOLO-based detection component identifies people and "
            "associates detections with monitored tables."
        )

    with step2:
        display_panel(
            "03 — Data Storage",
            "Table-level counts are stored with a timestamp in a SQLite "
            "database."
        )
        display_panel(
            "04 — Smart Dashboard",
            "Students and administrators can view current occupancy, "
            "utilisation statistics and an availability forecast."
        )

    st.info(
        "SmartSeat works at table level. It counts the people detected "
        "at each table. Free seats are estimated as capacity minus "
        "people detected; individual chairs are not detected."
    )

    st.markdown("---")
    st.markdown("### Technology Stack")

    tech_col1, tech_col2, tech_col3 = st.columns(3)

    with tech_col1:
        display_panel("Python", "Core application and data processing.")
    with tech_col2:
        display_panel(
            "YOLO / OpenCV", "Computer vision and people detection."
        )
    with tech_col3:
        display_panel(
            "Streamlit and SQLite",
            "Interactive dashboard and occupancy history."
        )

    st.markdown("---")
    st.markdown("### Current Development Status")

    st.write("**Dashboard:** Running")
    st.write(f"**Table occupancy data:** {DATA_MODE_TEXT[DATA_SOURCE]}")
    st.write(f"**YOLO detection:** {DETECTION_STATUS}")
    st.write(
        "**Camera stream:** "
        + ("Connected" if CAMERA_CONNECTED else "Not connected")
    )
    st.write(
        "**Availability forecast:** "
        + (
            "Connected"
            if smartseat_db is not None
            and hasattr(smartseat_db, "predict_availability")
            else "Not connected"
        )
    )

    st.info(
        "SmartSeat is a prototype. Numbers are labelled by source: "
        "DEMO, REPLAY or LIVE. Only people counts are stored. No "
        "images or faces are kept."
    )


# ==========================================
# FOOTER
# ==========================================

st.markdown("---")

st.html(
    """
    <div style="text-align:center;color:#55655b;font-size:11px;
                padding:12px 0 20px 0;">
        SMARTSEAT · INTELLIGENT STUDY SPACE MONITORING
        <br>
        University of Debrecen · Prototype Dashboard
    </div>
    """
)

# Reload every 10 seconds so new detections appear without clicking.
if auto_refresh:
    time.sleep(10)
    st.rerun()
