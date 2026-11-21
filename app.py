"""app.py - SmartSeat dashboard (clean version)

Data flow, nothing else:
  db.get_latest_tables()  ->  table_data  ->  everything on the page
  output/Latest_image.jpg ->  the picture
There is NO smartseat_db, NO snapshot and NO session-state copy of the data,
so nothing can freeze. The whole script re-runs every 2 seconds.
"""
import time
from datetime import datetime, timedelta, time as dtime
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import db

# ---------- page setup (must be the first Streamlit calls) ----------
st.set_page_config(
    page_title="SmartSeat | University of Debrecen",
    page_icon="🪑",
    layout="wide",
)
st_autorefresh(interval=2000, key="refresh")   # re-run the page every 2 s

BASE = Path(__file__).parent
IMAGE_PATH = BASE / "output" / "Latest_image.jpg"
CAPACITY = 4          # seats per table
N_TABLES = 8

for css in (BASE / "style.css", BASE / "assets" / "style.css"):
    if css.exists():
        st.markdown(f"<style>{css.read_text(encoding='utf-8')}</style>",
                    unsafe_allow_html=True)
        break

DEEP_GREEN, GREEN = "#103725", "#174b35"
SOFT_GREEN, AMBER, SOFT_AMBER = "#e8f1e9", "#8a5a00", "#fdf1d3"
RED, SOFT_RED, LINE = "#b42318", "#fee4e2", "#e2ded4"

# ---------- read the data from the DB on EVERY run ----------
try:
    counts = list(db.get_latest_tables() or [])
except Exception as error:
    counts = []
    st.warning(f"Could not read the database: {error}")

counts = (counts + [0] * N_TABLES)[:N_TABLES]
table_data = {f"Table {i + 1:02d}": int(c or 0) for i, c in enumerate(counts)}

total_people = sum(table_data.values())
total_capacity = CAPACITY * N_TABLES
occupancy = total_people / total_capacity * 100
free_seats = max(total_capacity - total_people, 0)
occupied_tables = sum(1 for c in table_data.values() if c > 0)


# ---------- small helpers ----------
def state(count):
    if count <= 0:
        return SOFT_GREEN, GREEN, "AVAILABLE"
    if count >= CAPACITY:
        return SOFT_RED, RED, "FULL"
    return SOFT_AMBER, AMBER, "PARTLY OCCUPIED"


def kpi(label, value):
    st.html(f"""
    <div style="background:#fff;border:1px solid {LINE};border-radius:12px;
                padding:17px;min-height:95px;">
        <div style="color:#55655b;font-size:12px;font-weight:600;
                    margin-bottom:8px;">{escape(label)}</div>
        <div style="color:{DEEP_GREEN};font-size:28px;font-weight:800;">
            {escape(str(value))}</div>
    </div>""")


def table_map():
    layout = [(3, 5), (37, 5), (3, 36), (37, 36), (71, 36),
              (3, 67), (37, 67), (71, 67)]
    boxes = ""
    for (name, count), (left, top) in zip(table_data.items(), layout):
        bg, colour, label = state(count)
        dots = "".join(
            f'<span style="width:12px;height:12px;border-radius:50%;'
            f'border:1.5px solid {colour};box-sizing:border-box;'
            f'background:{colour if i < count else "#fff"};"></span>'
            for i in range(CAPACITY))
        boxes += f"""
        <div style="position:absolute;left:{left}%;top:{top}%;width:26%;
                    height:25%;box-sizing:border-box;background:{bg};
                    border:1.5px solid {colour};border-radius:11px;
                    display:flex;flex-direction:column;align-items:center;
                    justify-content:center;gap:4px;">
            <div style="font-size:13px;font-weight:700;color:{DEEP_GREEN};">{name}</div>
            <div style="font-size:18px;font-weight:800;color:{DEEP_GREEN};">{count} / {CAPACITY}</div>
            <div style="display:flex;gap:5px;">{dots}</div>
            <div style="font-size:11px;font-weight:700;color:{colour};">{label}</div>
        </div>"""
    st.html(f"""<div style="position:relative;height:460px;background:#faf8f4;
                border:1px solid {LINE};border-radius:12px;overflow:hidden;">
                {boxes}</div>""")


def show_image():
    """Read the file as bytes each time, so the newest picture is always shown."""
    for attempt in range(2):
        try:
            st.image(IMAGE_PATH.read_bytes(), use_container_width=True)
            return
        except Exception as error:
            if attempt == 0:
                time.sleep(1)       # wait 1 second and try once more
            else:
                st.info(f"Waiting for the first processed image... ({error})")


# ---------- sidebar ----------
with st.sidebar:
    st.markdown("## SmartSeat")
    page = st.radio("NAVIGATION", ["Overview", "Find Available Seats"])
    st.caption("Data: replay of recorded images")
    st.caption(f"Last refresh: {time.strftime('%H:%M:%S')}")

st.markdown("# SmartSeat")
st.caption("University of Debrecen · Intelligent study-space monitoring")

# ---------- page 1: overview ----------
if page == "Overview":
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi("PEOPLE DETECTED", total_people)
    with c2:
        kpi("FREE SEATS", f"{free_seats} / {total_capacity}")
    with c3:
        kpi("OCCUPIED TABLES", f"{occupied_tables} / {N_TABLES}")
    with c4:
        kpi("OCCUPANCY", f"{occupancy:.0f}%")

    st.markdown("---")
    left, right = st.columns(2)
    with left:
        st.markdown("### Room (processed image)")
        show_image()
    with right:
        st.markdown("### Occupancy Monitor")
        table_map()

    st.markdown("---")
    chart_col, history_col = st.columns(2)
    with chart_col:
        st.markdown("### People per table")
        st.bar_chart(
            pd.DataFrame({"Table": list(table_data), "People": list(table_data.values())}),
            x="Table", y="People", color=GREEN)
    with history_col:
        st.markdown("### Availability history")
        try:
            rows = db.get_history(50)
            rows = rows if rows and isinstance(rows[0], dict) else [tuple(r) for r in rows]
            frame = pd.DataFrame(rows).iloc[:, :2]
            frame.columns = ["time", "availability %"]
            frame = frame.iloc[::-1] if len(frame) > 1 and str(frame["time"].iloc[0]) > str(frame["time"].iloc[-1]) else frame
            st.line_chart(frame, x="time", y="availability %", color=GREEN)
        except Exception as error:
            st.info(f"No history to show yet ({error})")

# ---------- page 2: planner ----------
else:
    st.markdown("## Find Available Seats")
    st.caption("A forecast from past readings, not a reservation.")
    d_col, t_col = st.columns(2)
    visit_date = d_col.date_input("Visit date", value=datetime.now().date() + timedelta(days=1))
    visit_time = t_col.time_input("Arrival time", value=dtime(10, 0), step=900)
    when = datetime.combine(visit_date, visit_time)
    try:
        result = db.predict_availability(when)
        percent, samples = result
        if not samples:
            st.info("Not enough past readings for that day and time yet.")
        else:
            st.success(f"Expected about {percent:.0f}% free "
                       f"(based on {samples} past readings)")
            st.caption("The history is demo data from the processed images.")
    except Exception as error:
        st.info(f"The forecast is not available yet ({error})")
