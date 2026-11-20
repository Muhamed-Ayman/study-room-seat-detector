
"""smartseat_db.py - the ONLY file that touches SQL (Person 2).

Uses the original team function names and interfaces,
but keeps the new database structure.

Functions:
    init_db()
    save_reading(availability_percent, tables=None, taken_at=None)
    get_history(limit=50)
    get_latest_tables()
    predict_availability(date_time)
    seed_demo()

Tables:
    readings:
        id, taken_at, source, availability_percent

    table_readings:
        ONE ROW PER SNAPSHOT
        reading_id,
        t1_people, t1_free,
        t2_people, t2_free,
        ...
        t8_people, t8_free

Every table has 4 seats.
No images, no faces - only counts.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "seats.db"

NUM_TABLES = 8
SEATS_PER_TABLE = 4
TABLE_NUMBERS = range(1, NUM_TABLES + 1)

WINDOW_MINUTES = 60

PEOPLE_COLS = [f"t{n}_people" for n in TABLE_NUMBERS]
FREE_COLS = [f"t{n}_free" for n in TABLE_NUMBERS]


def _connect():
    """Open SQLite so the worker can write while the dashboard reads."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _to_text(moment):
    """datetime or ISO string -> YYYY-MM-DD HH:MM:SS."""
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    """Create the database tables. Safe to call multiple times."""

    with _connect() as db:

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                taken_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'replay',
                availability_percent REAL NOT NULL
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_readings_taken_at
            ON readings (taken_at)
            """
        )

        # Upgrade an older readings table if necessary.
        columns = [
            row[1]
            for row in db.execute("PRAGMA table_info(readings)")
        ]

        if "source" not in columns:
            db.execute(
                """
                ALTER TABLE readings
                ADD COLUMN source TEXT NOT NULL DEFAULT 'replay'
                """
            )

        # Remove the old table structure if it exists.
        old_columns = [
            row[1]
            for row in db.execute(
                "PRAGMA table_info(table_readings)"
            )
        ]

        if "table_number" in old_columns:
            db.execute("DROP TABLE table_readings")

        # Create two columns for each of the eight tables.
        table_columns = ",\n".join(
            f"""
            t{n}_people INTEGER NOT NULL DEFAULT 0,
            t{n}_free INTEGER NOT NULL DEFAULT 4
            """
            for n in TABLE_NUMBERS
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS table_readings (
                reading_id INTEGER PRIMARY KEY
                    REFERENCES readings(id) ON DELETE CASCADE,

                {table_columns}
            )
            """
        )


def save_reading(availability_percent, tables=None, taken_at=None):
    """
    Save one reading.

    availability_percent:
        Room availability percentage (0-100).

    tables:
        Optional list from process_image():

        [
            {"table": 1, "people": 2, "seats": 4, "free": 2},
            {"table": 2, "people": 1, "seats": 4, "free": 3},
            ...
        ]

    taken_at:
        Optional datetime or ISO string.

    Returns:
        ID of the new reading.
    """

    if taken_at is None:
        taken_at = datetime.now()

    percent = float(availability_percent)

    if not 0 <= percent <= 100:
        raise ValueError(
            f"availability_percent must be between 0 and 100, got {percent}"
        )

    # Default: all tables are empty.
    people = {n: 0 for n in TABLE_NUMBERS}

    if tables:
        for table in tables:
            number = int(table["table"])

            if number in people:
                people[number] = max(
                    0,
                    min(SEATS_PER_TABLE, int(table["people"]))
                )

    # Calculate free seats automatically.
    free = {
        n: SEATS_PER_TABLE - people[n]
        for n in TABLE_NUMBERS
    }

    init_db()

    with _connect() as db:

        reading_id = db.execute(
            """
            INSERT INTO readings
            (taken_at, source, availability_percent)
            VALUES (?, ?, ?)
            """,
            (_to_text(taken_at), "replay", percent),
        ).lastrowid

        columns = ["reading_id"]
        values = [reading_id]

        for n in TABLE_NUMBERS:
            columns.extend([
                f"t{n}_people",
                f"t{n}_free"
            ])

            values.extend([
                people[n],
                free[n]
            ])

        db.execute(
            f"""
            INSERT INTO table_readings
            ({', '.join(columns)})
            VALUES ({', '.join('?' for _ in columns)})
            """,
            values,
        )

        return reading_id


def get_history(limit=50):
    """
    Get the latest readings, OLDEST FIRST.

    Returns:
        [
            {
                "taken_at": "...",
                "availability_percent": 62.5
            },
            ...
        ]
    """

    init_db()

    with _connect() as db:

        rows = db.execute(
            """
            SELECT taken_at, availability_percent
            FROM readings
            ORDER BY taken_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    rows.reverse()

    return [
        {
            "taken_at": taken_at,
            "availability_percent": percent
        }
        for taken_at, percent in rows
    ]


def get_latest_tables():
    """
    Get the newest reading and details of all eight tables.

    Returns:
        {
            "taken_at": "...",
            "availability_percent": 62.5,
            "tables": [
                {
                    "table": 1,
                    "people": 2,
                    "seats": 4,
                    "free": 2
                },
                ...
            ]
        }

    Returns None if no readings exist.
    """

    init_db()

    with _connect() as db:

        db.row_factory = sqlite3.Row

        row = db.execute(
            """
            SELECT r.id, r.taken_at, r.availability_percent, t.*
            FROM readings r
            JOIN table_readings t
                ON t.reading_id = r.id
            ORDER BY r.taken_at DESC, r.id DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None

    tables = []

    for n in TABLE_NUMBERS:
        tables.append({
            "table": n,
            "people": row[f"t{n}_people"],
            "seats": SEATS_PER_TABLE,
            "free": row[f"t{n}_free"]
        })

    return {
        "taken_at": row["taken_at"],
        "availability_percent": row["availability_percent"],
        "tables": tables
    }


def predict_availability(date_time):
    """
    Predict room availability for a selected date and time.

    Uses readings from the same weekday within
    +/- WINDOW_MINUTES of the selected time.

    Returns:
        (average_percent, samples)

    Example:
        (58.0, 6)

    If no matching readings:
        (None, 0)
    """

    init_db()

    if isinstance(date_time, str):
        date_time = datetime.fromisoformat(date_time)

    weekday = date_time.weekday()

    target = (
        date_time.hour * 60
        + date_time.minute
    )

    with _connect() as db:

        average, samples = db.execute(
            """
            SELECT
                AVG(availability_percent),
                COUNT(*)
            FROM readings
            WHERE
                (CAST(strftime('%w', taken_at) AS INTEGER) + 6) % 7 = ?

                AND ABS(
                    CAST(strftime('%H', taken_at) AS INTEGER) * 60
                    + CAST(strftime('%M', taken_at) AS INTEGER)
                    - ?
                ) <= ?
            """,
            (weekday, target, WINDOW_MINUTES),
        ).fetchone()

    if not samples:
        return None, 0

    return round(average, 1), samples


def seed_demo():
    """
    Insert invented readings for testing the chart and planner.

    Run:
        python smartseat_db.py --seed-demo
    """

    import random

    init_db()

    rng = random.Random(42)

    today = datetime.now().replace(
        minute=0,
        second=0,
        microsecond=0
    )

    for days_back in range(1, 22):

        day = today - timedelta(days=days_back)

        weekend = day.weekday() >= 5

        for hour in range(8, 21):

            busy = (
                (1 - abs(hour - 13) / 7)
                * (0.5 if weekend else 1.0)
            )

            for minute in (0, 30):

                tables = []

                total_free = 0

                for n in TABLE_NUMBERS:

                    people = max(
                        0,
                        min(
                            SEATS_PER_TABLE,
                            round(busy * 3 + rng.gauss(0, 1))
                        )
                    )

                    free = SEATS_PER_TABLE - people

                    total_free += free

                    tables.append({
                        "table": n,
                        "people": people,
                        "seats": SEATS_PER_TABLE,
                        "free": free
                    })

                percent = (
                    total_free
                    / (NUM_TABLES * SEATS_PER_TABLE)
                    * 100
                )

                save_reading(
                    percent,
                    tables=tables,
                    taken_at=day.replace(
                        hour=hour,
                        minute=minute
                    )
                )


if __name__ == "__main__":
    import sys

    init_db()

    if "--seed-demo" in sys.argv:
        seed_demo()
        print("Demo readings added.")

    print(f"Database ready: {DB_PATH}")
    print(
        f"Readings stored: {len(get_history(limit=10**9))}"
    )
