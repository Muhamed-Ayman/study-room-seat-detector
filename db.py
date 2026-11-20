
"""db.py - the ONLY file that touches SQL (Person 2).

Contract (agreed by the team):
    init_db()                         create the tables
    save_reading(percent)             insert one row (time = now)
    get_history(limit=50)             latest rows, oldest first (for the chart)
    get_latest_tables()               newest reading + per-table people counts
    predict_availability(date_time)   (average_percent, samples) for the planner

Tables:
    readings(id, taken_at, availability_percent)

    table_readings(
        reading_id,
        table_1, table_2, table_3, table_4,
        table_5, table_6, table_7, table_8
    )

Each table column stores ONLY the number of people detected.
No free-seat calculations or additional occupancy data.
No images, no faces.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

# data/seats.db next to this file; the folder is created automatically.
DB_PATH = Path(__file__).parent / "data" / "seats.db"

NUM_TABLES = 8
SEATS_PER_TABLE = 4
TABLE_NUMBERS = range(1, NUM_TABLES + 1)

TABLE_COLS = [f"table_{n}" for n in TABLE_NUMBERS]

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
    """Create the database tables. Safe to call multiple times."""

    with _connect() as db:

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                taken_at             TEXT NOT NULL,
                availability_percent REAL NOT NULL
            )
            """
        )

        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_taken_at "
            "ON readings (taken_at)"
        )

        required_columns = ["reading_id"] + TABLE_COLS

        # Check whether table_readings already exists.
        existing_columns = [
            row[1]
            for row in db.execute(
                "PRAGMA table_info(table_readings)"
            )
        ]

        # Migrate the previous per-table structure if necessary.
        if existing_columns and existing_columns != required_columns:

            db.execute(
                "ALTER TABLE table_readings "
                "RENAME TO table_readings_old"
            )

            db.execute(
                """
                CREATE TABLE table_readings (
                    reading_id INTEGER PRIMARY KEY
                        REFERENCES readings(id) ON DELETE CASCADE,

                    table_1 INTEGER NOT NULL DEFAULT 0,
                    table_2 INTEGER NOT NULL DEFAULT 0,
                    table_3 INTEGER NOT NULL DEFAULT 0,
                    table_4 INTEGER NOT NULL DEFAULT 0,
                    table_5 INTEGER NOT NULL DEFAULT 0,
                    table_6 INTEGER NOT NULL DEFAULT 0,
                    table_7 INTEGER NOT NULL DEFAULT 0,
                    table_8 INTEGER NOT NULL DEFAULT 0
                )
                """
            )

            # Preserve existing people counts from the old structure.
            if "table_number" in existing_columns and "people" in existing_columns:

                db.execute(
                    """
                    INSERT INTO table_readings (
                        reading_id,
                        table_1, table_2, table_3, table_4,
                        table_5, table_6, table_7, table_8
                    )
                    SELECT
                        reading_id,
                        COALESCE(MAX(CASE WHEN table_number = 1 THEN people END), 0),
                        COALESCE(MAX(CASE WHEN table_number = 2 THEN people END), 0),
                        COALESCE(MAX(CASE WHEN table_number = 3 THEN people END), 0),
                        COALESCE(MAX(CASE WHEN table_number = 4 THEN people END), 0),
                        COALESCE(MAX(CASE WHEN table_number = 5 THEN people END), 0),
                        COALESCE(MAX(CASE WHEN table_number = 6 THEN people END), 0),
                        COALESCE(MAX(CASE WHEN table_number = 7 THEN people END), 0),
                        COALESCE(MAX(CASE WHEN table_number = 8 THEN people END), 0)
                    FROM table_readings_old
                    GROUP BY reading_id
                    """
                )

            db.execute("DROP TABLE table_readings_old")

        # Create the new structure if it does not exist.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS table_readings (
                reading_id INTEGER PRIMARY KEY
                    REFERENCES readings(id) ON DELETE CASCADE,

                table_1 INTEGER NOT NULL DEFAULT 0,
                table_2 INTEGER NOT NULL DEFAULT 0,
                table_3 INTEGER NOT NULL DEFAULT 0,
                table_4 INTEGER NOT NULL DEFAULT 0,
                table_5 INTEGER NOT NULL DEFAULT 0,
                table_6 INTEGER NOT NULL DEFAULT 0,
                table_7 INTEGER NOT NULL DEFAULT 0,
                table_8 INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def save_reading(availability_percent, tables=None, taken_at=None):
    """Insert one reading. The time is added here.

    availability_percent  0..100 for the whole room

    tables                optional: details["tables"] from process_image(), i.e.
                          [{"table": 1, "people": 2}, ...]

    taken_at              optional datetime (or ISO string); default = right now

    Returns the id of the new row.
    """

    if taken_at is None:
        taken_at = datetime.now()

    percent = float(availability_percent)

    if not 0 <= percent <= 100:
        raise ValueError(
            f"availability_percent must be 0..100, got {percent}"
        )

    # Initialize all 8 tables with zero people.
    people = {n: 0 for n in TABLE_NUMBERS}

    if tables:
        for table in tables:

            number = int(table["table"])

            if number in people:
                people[number] = max(
                    0,
                    min(SEATS_PER_TABLE, int(table["people"]))
                )

    # Arrange people counts in table order.
    values = [people[n] for n in TABLE_NUMBERS]

    init_db()

    with _connect() as db:

        cursor = db.execute(
            """
            INSERT INTO readings
                (taken_at, availability_percent)
            VALUES (?, ?)
            """,
            (_to_text(taken_at), percent),
        )

        reading_id = cursor.lastrowid

        # Store all 8 people counts in ONE row.
        columns = ["reading_id"] + TABLE_COLS

        placeholders = ", ".join("?" for _ in columns)

        db.execute(
            f"""
            INSERT INTO table_readings
                ({', '.join(columns)})
            VALUES ({placeholders})
            """,
            [reading_id] + values,
        )

        return reading_id


def get_history(limit=50):
    """The latest `limit` readings, OLDEST FIRST so a chart reads left to right.

    Returns a list of dicts:
        [{"taken_at": "2026-11-20 14:00:00",
          "availability_percent": 62.5}, ...]

    Empty list if there is no data yet.
    Easy to use: pd.DataFrame(db.get_history())
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
    """The newest reading with its per-table people counts.

    Returns:
        {
            "taken_at": "...",
            "availability_percent": 62.5,
            "tables": [
                {"table": 1, "people": 2},
                {"table": 2, "people": 1},
                ...
            ]
        }

    Returns None if there are no readings.
    """

    init_db()

    with _connect() as db:

        row = db.execute(
            f"""
            SELECT
                r.id,
                r.taken_at,
                r.availability_percent,
                {', '.join('t.' + col for col in TABLE_COLS)}
            FROM readings r
            JOIN table_readings t
                ON t.reading_id = r.id
            ORDER BY r.taken_at DESC, r.id DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None

    reading_id, taken_at, percent = row[:3]

    # The remaining 8 values represent people at each table.
    people_values = row[3:11]

    tables = []

    for index, number in enumerate(TABLE_NUMBERS):

        tables.append({
            "table": number,
            "people": people_values[index]
        })

    return {
        "taken_at": taken_at,
        "availability_percent": percent,
        "tables": tables
    }


def predict_availability(date_time):
    """Planner: expected availability at a future date and time.

    Uses stored readings on the SAME WEEKDAY whose time of day is within
    +/- WINDOW_MINUTES of date_time, and returns their average.

    Returns (average_percent, samples), for example (58.0, 6).
    If nothing matches: (None, 0).
    """

    init_db()

    if isinstance(date_time, str):
        date_time = datetime.fromisoformat(date_time)

    weekday = date_time.weekday()
    target = date_time.hour * 60 + date_time.minute

    with _connect() as db:

        average, samples = db.execute(
            """
            SELECT AVG(availability_percent), COUNT(*)
            FROM readings
            WHERE (CAST(strftime('%w', taken_at) AS INTEGER) + 6) % 7 = ?
              AND ABS(
                    CAST(strftime('%H', taken_at) AS INTEGER) * 60
                    + CAST(strftime('%M', taken_at) AS INTEGER) - ?
                  ) <= ?
            """,
            (weekday, target, WINDOW_MINUTES),
        ).fetchone()

    if not samples:
        return None, 0

    return round(average, 1), samples


def seed_demo():
    """Insert INVENTED readings so Person 3 can test the planner and chart.

    Clearly demo data: say so in the pitch.
    Run with: python db.py --seed-demo
    """

    from datetime import timedelta
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

                free = 100 - busy * 85 + rng.gauss(0, 8)

                save_reading(
                    max(0, min(100, free)),
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
