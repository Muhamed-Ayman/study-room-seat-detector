"""db.py - the ONLY file that touches SQL (Person 2).

Contract (agreed by the team):
    init_db()                         create the table
    save_reading(percent)             insert one row (time = now)
    get_history(limit=50)             latest rows, oldest first (for the chart)
    get_latest_tables()               newest reading + per-table details (cards)
    predict_availability(date_time)   (average_percent, samples) for the planner

Table:  readings(id, taken_at, availability_percent)
Stores the room percent plus per-table counts. No images, no faces.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

# data/seats.db next to this file; the folder is created automatically.
DB_PATH = Path(__file__).parent / "data" / "seats.db"

# predict_availability looks at readings within +/- this many minutes of the
# chosen time of day, on the same weekday.
WINDOW_MINUTES = 60


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
    """Create the readings table if it does not exist. Safe to call many times."""
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                taken_at             TEXT NOT NULL,  -- local time, 'YYYY-MM-DD HH:MM:SS'
                availability_percent REAL NOT NULL   -- 0..100, free seats / total seats
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_taken_at ON readings (taken_at)"
        )
        # Per-table details of each reading (so no JSON file is needed).
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS table_readings (
                reading_id   INTEGER NOT NULL
                             REFERENCES readings(id) ON DELETE CASCADE,
                table_number INTEGER NOT NULL,
                people       INTEGER NOT NULL,
                seats        INTEGER NOT NULL,
                free         INTEGER NOT NULL,
                PRIMARY KEY (reading_id, table_number)
            )
            """
        )


def save_reading(availability_percent, tables=None, taken_at=None):
    """Insert one reading. The time is added here.

    availability_percent  0..100 for the whole room
    tables                optional: details["tables"] from process_image(), i.e.
                          [{"table": 1, "people": 2, "seats": 4, "free": 2}, ...]
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
            db.executemany(
                "INSERT INTO table_readings"
                " (reading_id, table_number, people, seats, free)"
                " VALUES (?, ?, ?, ?, ?)",
                [(reading_id, int(t["table"]), int(t["people"]),
                  int(t["seats"]), int(t["free"])) for t in tables],
            )
        return reading_id


def get_history(limit=50):
    """The latest `limit` readings, OLDEST FIRST so a chart reads left to right.

    Returns a list of dicts:
        [{"taken_at": "2026-11-20 14:00:00", "availability_percent": 62.5}, ...]
    Empty list if there is no data yet.
    Easy to use:  pd.DataFrame(db.get_history())
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


def get_latest_tables():
    """The newest reading with its per-table details, for the dashboard cards.

    Returns {"taken_at": ..., "availability_percent": ...,
             "tables": [{"table": 1, "people": 2, "seats": 4, "free": 2}, ...]}
    or None if there are no readings yet. "tables" is an empty list if the
    newest reading was saved without table details.
    """
    init_db()
    with _connect() as db:
        row = db.execute(
            "SELECT id, taken_at, availability_percent FROM readings"
            " ORDER BY taken_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        reading_id, taken_at, percent = row
        tables = db.execute(
            "SELECT table_number, people, seats, free FROM table_readings"
            " WHERE reading_id = ? ORDER BY table_number",
            (reading_id,),
        ).fetchall()

    return {
        "taken_at": taken_at,
        "availability_percent": percent,
        "tables": [{"table": n, "people": p, "seats": se, "free": f}
                   for n, p, se, f in tables],
    }


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
                free = 100 - busy * 85 + rng.gauss(0, 8)
                save_reading(max(0, min(100, free)),
                             taken_at=day.replace(hour=hour, minute=minute))


if __name__ == "__main__":
    import sys

    init_db()
    if "--seed-demo" in sys.argv:
        seed_demo()
        print("Demo readings added.")
    print(f"Database ready: {DB_PATH}")
    print(f"Readings stored: {len(get_history(limit=10**9))}")