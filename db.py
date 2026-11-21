"""db.py - the ONLY file that touches SQL (Person 2).

Contract (agreed by the team):
    init_db()                         create the table
    save_reading(percent, tables)     insert one row (time = now)
    get_history(limit=50)             latest rows, oldest first (for the chart)
    get_latest_snapshot()             newest reading + per-table details (dict)
    get_latest_tables()               same, but only the 8 occupied counts (list)
    predict_availability(date_time)   (average_percent, samples) for the planner

Tables:
    readings(id, taken_at, availability_percent)
    table_readings(reading_id, table_1, table_2, ... table_8)

availability_percent = share of seats that are FREE in the whole room (0..100).
table_readings has ONE ROW PER READING and ONE COLUMN PER TABLE. Each column
holds the number of OCCUPIED seats (people) at that table. No images, no faces.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

# data/seats.db next to this file; the folder is created automatically.
DB_PATH = Path(__file__).parent / "data" / "seats.db"

# predict_availability looks at readings within +/- this many minutes of the
# chosen time of day, on the same weekday.
WINDOW_MINUTES = 60

NUM_TABLES = 8
SEATS_PER_TABLE = 4          # every table has 4 seats
TABLE_COLUMNS = [f"table_{i}" for i in range(1, NUM_TABLES + 1)]


def _connect():
    """Open SQLite so the worker can write while the dashboard reads."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _to_text(moment):
    """datetime -> 'YYYY-MM-DD HH:MM:SS'. Strings are checked and normalised."""
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    """Create the tables if they do not exist. Safe to call many times.

    If an OLD table_readings (one row per table) is found, it is replaced by
    the new 8-column layout; its per-table details are dropped.
    """
    columns = ",\n                ".join(
        f"{name} INTEGER CHECK ({name} BETWEEN 0 AND {SEATS_PER_TABLE})"
        for name in TABLE_COLUMNS
    )
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                taken_at             TEXT NOT NULL,  -- local time, 'YYYY-MM-DD HH:MM:SS'
                availability_percent REAL NOT NULL   -- 0..100, FREE seats / total seats
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_taken_at ON readings (taken_at)"
        )

        # Old layout (table_number/people/seats/free columns) -> remove it.
        have = [r[1] for r in db.execute("PRAGMA table_info(table_readings)")]
        if have and "table_1" not in have:
            db.execute("DROP TABLE table_readings")

        # Occupied seats of each table for one reading: one column per table.
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS table_readings (
                reading_id INTEGER PRIMARY KEY
                           REFERENCES readings(id) ON DELETE CASCADE,
                {columns}
            )
            """
        )


def _occupied_from(entry):
    """Occupied seats from one table entry of process_image().

    Accepts {"occupied": n}, or {"people": n}, or {"free": n} (then
    occupied = seats - free). Clamped to 0..SEATS_PER_TABLE.
    """
    if entry.get("occupied") is not None:
        value = entry["occupied"]
    elif entry.get("people") is not None:
        value = entry["people"]
    elif entry.get("free") is not None:
        value = SEATS_PER_TABLE - int(entry["free"])
    else:
        raise KeyError("table entry needs 'occupied', 'people' or 'free'")
    return max(0, min(SEATS_PER_TABLE, int(value)))


def save_reading(availability_percent, tables=None, taken_at=None):
    """Insert one reading. The time is added here.

    availability_percent  0..100 FREE share of the whole room
    tables                optional: details["tables"] from process_image(), i.e.
                          [{"table": 1, "people": 2, "seats": 4, "free": 2}, ...]
                          Only the table number and its occupied seats are
                          stored, in the column of that table.
    taken_at              optional datetime (or ISO string); default = right now
    Returns the id of the new row.
    """
    if taken_at is None:
        taken_at = datetime.now()
    percent = float(availability_percent)
    if not 0 <= percent <= 100:
        raise ValueError(f"availability_percent must be 0..100, got {percent}")

    init_db()
    with _connect() as db:
        cursor = db.execute(
            "INSERT INTO readings (taken_at, availability_percent) VALUES (?, ?)",
            (_to_text(taken_at), percent),
        )
        reading_id = cursor.lastrowid
        if tables:
            occupied = {}
            for t in tables:
                number = int(t["table"])
                if 1 <= number <= NUM_TABLES:
                    occupied[f"table_{number}"] = _occupied_from(t)
            if occupied:
                db.execute(
                    f"INSERT INTO table_readings (reading_id, {', '.join(occupied)})"
                    f" VALUES (?, {', '.join('?' * len(occupied))})",
                    (reading_id, *occupied.values()),
                )
        return reading_id


def get_history(limit=50):
    """The latest `limit` readings, OLDEST FIRST so a chart reads left to right.

    Returns a list of dicts:
        [{"taken_at": "2026-11-20 14:00:00", "availability_percent": 62.5}, ...]
    Empty list if there is no data yet.
    """
    init_db()
    with _connect() as db:
        rows = db.execute(
            "SELECT taken_at, availability_percent FROM readings"
            " ORDER BY taken_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()

    rows.reverse()  # newest-first from SQL -> oldest-first for the chart
    return [{"taken_at": t, "availability_percent": p} for t, p in rows]


def get_latest_snapshot():
    """The newest reading that has table details, as a dict (used by app.py).

    Returns {"taken_at": ..., "availability_percent": ...,
             "tables": [{"table": 1, "occupied": 2}, {"table": 2, "occupied": 4}, ...]}
    or None if no table readings are stored yet.
    """
    init_db()
    with _connect() as db:
        row = db.execute(
            f"SELECT r.taken_at, r.availability_percent,"
            f" {', '.join('t.' + c for c in TABLE_COLUMNS)}"
            " FROM table_readings t JOIN readings r ON r.id = t.reading_id"
            " ORDER BY r.taken_at DESC, t.reading_id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None

    taken_at, percent, *occupied_seats = row
    return {
        "taken_at": taken_at,
        "availability_percent": percent,
        "tables": [{"table": number, "occupied": occupied}
                   for number, occupied in enumerate(occupied_seats, start=1)
                   if occupied is not None],
    }


def get_latest_tables():
    """Only the occupied seats of tables 1..8 of the newest reading, as a list.

    Example: [2, 1, 0, 4, 3, 2, 1, 1]. If nothing is stored yet: [0] * 8.
    (Use get_latest_snapshot() when you also need the time or need to tell
    "no data" apart from "all tables empty".)
    """
    snapshot = get_latest_snapshot()
    if snapshot is None:
        return [0] * NUM_TABLES
    return [t["occupied"] for t in snapshot["tables"]]


def predict_availability(date_time):
    """Planner: expected availability at a future date and time.

    Uses stored readings on the SAME WEEKDAY whose time of day is within
    +/- WINDOW_MINUTES of date_time, and returns their average.

    Returns (average_percent, samples), for example (58.0, 6).
    If nothing matches: (None, 0). The dashboard must handle None, and should
    always show `samples` ("based on 6 past readings").
    """
    init_db()
    if isinstance(date_time, str):
        date_time = datetime.fromisoformat(date_time)

    weekday = date_time.weekday()                        # 0 = Monday ... 6 = Sunday
    target = date_time.hour * 60 + date_time.minute      # minutes since midnight

    with _connect() as db:
        average, samples = db.execute(
            """
            SELECT AVG(availability_percent), COUNT(*)
            FROM readings
            WHERE (CAST(strftime('%w', taken_at) AS INTEGER) + 6) % 7 = ?
              AND ABS(CAST(strftime('%H', taken_at) AS INTEGER) * 60
                      + CAST(strftime('%M', taken_at) AS INTEGER) - ?) <= ?
            """,
            (weekday, target, WINDOW_MINUTES),
        ).fetchone()

    if not samples:
        return None, 0
    return round(average, 1), samples


def seed_demo():
    """Insert INVENTED readings so Person 3 can test the planner and chart.

    Clearly demo data: say so in the pitch. Run with:  python db.py --seed-demo
    """
    from datetime import timedelta
    import random

    init_db()
    rng = random.Random(42)
    today = datetime.now().replace(minute=0, second=0, microsecond=0)

    for days_back in range(1, 22):                       # 3 weeks of history
        day = today - timedelta(days=days_back)
        weekend = day.weekday() >= 5
        for hour in range(8, 21):
            busy = (1 - abs(hour - 13) / 7) * (0.5 if weekend else 1.0)
            for minute in (0, 30):
                tables = []
                for number in range(1, NUM_TABLES + 1):
                    occupied = round(SEATS_PER_TABLE * busy + rng.gauss(0, 1))
                    occupied = max(0, min(SEATS_PER_TABLE, occupied))
                    tables.append({"table": number,
                                   "people": occupied,
                                   "seats": SEATS_PER_TABLE,
                                   "free": SEATS_PER_TABLE - occupied})
                total_free = sum(t["free"] for t in tables)
                percent = total_free / (NUM_TABLES * SEATS_PER_TABLE) * 100
                save_reading(percent, tables=tables,
                             taken_at=day.replace(hour=hour, minute=minute))


if __name__ == "__main__":
    import sys

    init_db()
    if "--seed-demo" in sys.argv:
        seed_demo()
        print("Demo readings added.")
    print(f"Database ready: {DB_PATH}")
    print(f"Readings stored: {len(get_history(limit=10**9))}")