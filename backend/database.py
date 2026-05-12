import sqlite3

DB_NAME = "medical_assistant.db"


def init_db():
    conn   = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recordings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path     TEXT    NOT NULL,
            transcript    TEXT    NOT NULL,
            intent        TEXT,
            confidence    REAL,
            entities      TEXT,
            created_at    TEXT    NOT NULL,
            patient_name  TEXT,
            action_type   TEXT,
            user_id       TEXT,
            user_role     TEXT,
            result_status TEXT
        )
    """)

    # Add any missing columns without destroying existing data (FR-15/FR-17)
    existing_columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info(recordings)").fetchall()
    }
    for col, col_type in [
        ("intent",        "TEXT"),
        ("confidence",    "REAL"),
        ("entities",      "TEXT"),
        ("patient_name",  "TEXT"),
        ("action_type",   "TEXT"),
        ("user_id",       "TEXT"),
        ("user_role",     "TEXT"),
        ("result_status", "TEXT"),
    ]:
        if col not in existing_columns:
            cursor.execute(f"ALTER TABLE recordings ADD COLUMN {col} {col_type}")

    conn.commit()
    conn.close()


def insert_recording(file_path, transcript, intent, confidence, entities, timestamp,
                     patient_name=None, action_type=None, user_id="unknown",
                     user_role="user", result_status="unknown"):
    conn   = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO recordings
            (file_path, transcript, intent, confidence, entities, created_at,
             patient_name, action_type, user_id, user_role, result_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (file_path, transcript, intent, confidence, entities, timestamp,
          patient_name, action_type, user_id, user_role, result_status))
    conn.commit()
    conn.close()


def get_all_recordings():
    conn   = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM recordings ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_recordings_by_user(user_id: str):
    conn   = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM recordings WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_recordings_by_date(date_prefix: str):
    """date_prefix is YYYYMMDD — matches against created_at which uses %Y%m%d_%H%M%S"""
    conn   = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM recordings WHERE created_at LIKE ? ORDER BY id DESC",
        (f"{date_prefix}%",),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows
