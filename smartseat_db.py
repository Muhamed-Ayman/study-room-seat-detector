"""SmartSeat database and availability forecast.

Stores one row per detection run (a "snapshot") plus the number of people
counted at each table. Only counts are stored: no images, no faces.

app.py calls these three functions:
    latest_snapshot()      -> newest reading, for the dashboard
    history_totals()       -> one total per snapshot, for the trend chart
    predict_availability() -> the forecast on the "Find Available Seats" page

main_program.py calls save_snapshot() after every detection run.
"""

import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "smartseat.db"

# Keep in sync with TABLE_CAPACITY in app.py (4 seats per table).
TABLE_CAPACITY = 4

# The forecast refuses to give a number below these amounts of history.
MIN_READINGS = 5   # snapshots inside the time window
MIN_DAYS = 2       # different calendar days those snapshots come from


def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    """Create the tables if they do not exist yet. Safe to call repeatedly."""
    with _connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL,   -- local time, ISO format
                source        TEXT    NOT NULL,   -- live / replay / demo
                weekday       INTEGER NOT NULL,   -- 0 = Monday ... 6 = Sunday
                minute_of_day INTEGER NOT NULL    -- 0 ... 1439
            );
            CREATE TABLE IF NOT EXISTS table_counts (
                snapshot_id INTEGER NOT NULL
                            REFERENCES snapshots(id) ON DELETE CASCADE,
                table_name  TEXT    NOT NULL,     -- e.g. "Table 3"
                people      INTEGER NOT NULL,
                PRIMARY KEY (snapshot_id, table_name)
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_slot
                ON snapshots (weekday, minute_of_day);
        """)


def save_snapshot(counts, source="replay", timestamp=None):
    """Store one detection run.

    counts     {"Table 1": 2, "Table 2": 0, ...} (or {1: 2, 2: 0, ...})
    source     "live" (camera), "replay" (recorded photos) or "demo"
    timestamp  datetime; defaults to now
    Returns the id of the new snapshot.
    """
    if source not in ("live", "replay", "demo"):
        raise ValueError(f"Unknown source: {source!r}")

    init_db()
    moment = timestamp or datetime.now()

    with _connect() as db:
        cursor = db.execute(
            "INSERT INTO snapshots (timestamp, source, weekday, minute_of_day)"
            " VALUES (?, ?, ?, ?)",
            (
                moment.isoformat(timespec="seconds"),
                source,
                moment.weekday(),
                moment.hour * 60 + moment.minute,
            ),
        )
        snapshot_id = cursor.lastrowid

        rows = []
        for name, people in counts.items():
            label = name if isinstance(name, str) else f"Table {name}"
            rows.append((snapshot_id, label, max(0, int(people))))

        db.executemany(
            "INSERT INTO table_counts (snapshot_id, table_name, people)"
            " VALUES (?, ?, ?)",
            rows,
        )

    return snapshot_id


def latest_snapshot():
    """Newest real reading (live or replay), or None if the DB is empty.

    Returns {"counts": {...}, "source": ..., "timestamp": ISO text}.
    Demo rows are skipped: they exist only to feed the forecast.
    """
    init_db()

    with _connect() as db:
        row = db.execute(
            "SELECT id, timestamp, source FROM snapshots"
            " WHERE source IN ('live', 'replay')"
            " ORDER BY timestamp DESC, id DESC LIMIT 1"
        ).fetchone()

        if row is None:
            return None

        snapshot_id, timestamp, source = row
        counts = dict(db.execute(
            "SELECT table_name, people FROM table_counts"
            " WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall())

    return {"counts": counts, "source": source, "timestamp": timestamp}


def history_totals():
    """Total people detected per snapshot, oldest first."""
    init_db()

    with _connect() as db:
        rows = db.execute(
            "SELECT s.timestamp, s.source, SUM(c.people)"
            " FROM snapshots s JOIN table_counts c ON c.snapshot_id = s.id"
            " GROUP BY s.id ORDER BY s.timestamp, s.id"
        ).fetchall()

    return [
        {"time": timestamp, "source": source, "people_detected": int(total)}
        for timestamp, source, total in rows
    ]


def predict_availability(weekday, visit_time, seats_needed,
                         window_minutes=15):
    """Chance that at least one table has `seats_needed` free seats.

    Looks at every stored snapshot on the same weekday within
    +/- window_minutes of visit_time. The probability is the share of those
    snapshots where some table had (capacity - people) >= seats_needed.

    Returns {"probability": 0..1 or None, "readings_used": n,
             "days_covered": n, "source": "live"/"replay"/"demo"}.
    probability is None when there is too little history.
    """
    init_db()

    target = visit_time.hour * 60 + visit_time.minute
    low, high = target - window_minutes, target + window_minutes

    with _connect() as db:
        rows = db.execute(
            "SELECT s.id, s.timestamp, s.source, c.people"
            " FROM snapshots s JOIN table_counts c ON c.snapshot_id = s.id"
            " WHERE s.weekday = ? AND s.minute_of_day BETWEEN ? AND ?",
            (weekday, low, high),
        ).fetchall()

    snapshots = {}
    for snapshot_id, timestamp, source, people in rows:
        entry = snapshots.setdefault(
            snapshot_id, {"date": timestamp[:10], "source": source,
                          "free": []}
        )
        entry["free"].append(max(0, TABLE_CAPACITY - people))

    readings = len(snapshots)
    days = len({entry["date"] for entry in snapshots.values()})

    # Report the least trustworthy source present: demo < replay < live.
    present = {entry["source"] for entry in snapshots.values()}
    source = next(
        (name for name in ("demo", "replay", "live") if name in present),
        None,
    )

    result = {"probability": None, "readings_used": readings,
              "days_covered": days, "source": source}

    if readings < MIN_READINGS or days < MIN_DAYS:
        return result

    enough = sum(
        1 for entry in snapshots.values()
        if max(entry["free"]) >= seats_needed
    )
    result["probability"] = enough / readings
    return result


def seed_demo(weeks=3, per_hour=4):
    """Fill the DB with invented history (source='demo') to test the forecast.

    Busier around midday, quieter on weekends. The dashboard labels any
    forecast built from these rows as DEMO DATA. Never present it as real.
    """
    init_db()
    rng = random.Random(42)
    today = datetime.now().replace(minute=0, second=0, microsecond=0)

    for day_back in range(1, weeks * 7 + 1):
        day = today - timedelta(days=day_back)
        weekend = day.weekday() >= 5

        for hour in range(8, 21):
            busy = 1 - abs(hour - 13) / 7          # peaks at 13:00
            busy *= 0.5 if weekend else 1.0

            for k in range(per_hour):
                moment = day.replace(hour=hour, minute=k * (60 // per_hour))
                counts = {
                    f"Table {i}": min(
                        TABLE_CAPACITY,
                        max(0, round(rng.gauss(busy * 3.5, 1.2))),
                    )
                    for i in range(1, 9)
                }
                save_snapshot(counts, source="demo", timestamp=moment)


if __name__ == "__main__":
    import sys

    init_db()
    if "--seed-demo" in sys.argv:
        seed_demo()
        print("Demo history added (source = demo).")
    else:
        print(f"Database ready: {DB_PATH}")
        print(f"Snapshots stored: {len(history_totals())}")
