import sqlite3
import json
from config import DB_PATH
from datetime import date

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize all database tables for SQLite."""
    conn = get_connection()
    cursor = conn.cursor()

    # Quizzes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Questions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            options TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            explanation TEXT,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        )
    """)

    # Quiz spaced repetition reviews
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            stage INTEGER DEFAULT 0,
            next_review_date DATE NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        )
    """)

    # Weak (wrong) questions with their own spaced repetition
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weak_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            stage INTEGER DEFAULT 0,
            next_review_date DATE NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            UNIQUE(quiz_id, question_id)
        )
    """)

    # Active quiz session
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_session (
            id INTEGER PRIMARY KEY,
            session_type TEXT NOT NULL,
            quiz_id INTEGER,
            review_id INTEGER,
            question_ids TEXT NOT NULL,
            current_index INTEGER DEFAULT 0,
            correct_count INTEGER DEFAULT 0,
            wrong_ids TEXT DEFAULT '[]'
        )
    """)

    # Bot state for clean chat
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()

# ─── Quizzes ──────────────────────────────────────────────────────────────────

def save_quiz(name: str, questions: list) -> int:
    """Save a quiz with its questions. Returns quiz_id."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("INSERT INTO quizzes (name) VALUES (?)", (name,))
    quiz_id = cursor.lastrowid

    for q in questions:
        cursor.execute(
            """INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation)
               VALUES (?, ?, ?, ?, ?)""",
            (
                quiz_id,
                q["question"],
                json.dumps(q["options"], ensure_ascii=False),
                q["answer"],
                q.get("explanation", ""),
            ),
        )

    # Schedule first review for tomorrow
    from spaced_repetition import next_review_date
    cursor.execute(
        "INSERT INTO quiz_reviews (quiz_id, stage, next_review_date) VALUES (?, 0, ?)",
        (quiz_id, next_review_date(0)),
    )

    conn.commit()
    conn.close()
    return quiz_id

def get_all_quizzes() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM quizzes ORDER BY created_at DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_quiz(quiz_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_quiz(quiz_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    # Enable foreign keys for SQLite so cascade works
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
    conn.commit()
    conn.close()

# ─── Questions ────────────────────────────────────────────────────────────────

def get_questions(quiz_id: int) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions WHERE quiz_id = ?", (quiz_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    for r in rows:
        r["options"] = json.loads(r["options"])
    return rows

def get_question(question_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions WHERE id = ?", (question_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        row = dict(row)
        row["options"] = json.loads(row["options"])
        return row
    return None

# ─── Quiz Reviews ─────────────────────────────────────────────────────────────

def get_due_quiz_reviews() -> list:
    """Returns quiz_reviews due today or earlier."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT qr.*, q.name as quiz_name
           FROM quiz_reviews qr
           JOIN quizzes q ON qr.quiz_id = q.id
           WHERE qr.next_review_date <= date('now')
           ORDER BY qr.next_review_date"""
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def advance_quiz_review(review_id: int):
    """Move to next stage or delete if completed all stages."""
    from spaced_repetition import next_review_date, REVIEW_INTERVALS
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM quiz_reviews WHERE id = ?", (review_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
        
    review = dict(row)
    new_stage = review["stage"] + 1

    if new_stage >= len(REVIEW_INTERVALS):
        cursor.execute("DELETE FROM quiz_reviews WHERE id = ?", (review_id,))
    else:
        cursor.execute(
            "UPDATE quiz_reviews SET stage = ?, next_review_date = ? WHERE id = ?",
            (new_stage, next_review_date(new_stage), review_id),
        )

    conn.commit()
    conn.close()

# ─── Weak Questions ───────────────────────────────────────────────────────────

def add_or_reset_weak_question(quiz_id: int, question_id: int):
    """Add a wrong question to weak list — always due today for immediate review."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO weak_questions (quiz_id, question_id, stage, next_review_date)
           VALUES (?, ?, 0, date('now'))
           ON CONFLICT(quiz_id, question_id)
           DO UPDATE SET stage = 0, next_review_date = date('now')""",
        (quiz_id, question_id),
    )
    conn.commit()
    conn.close()

def get_due_weak_questions() -> list:
    """Returns weak questions due today or earlier."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT wq.*, q.name as quiz_name
           FROM weak_questions wq
           JOIN quizzes q ON wq.quiz_id = q.id
           WHERE wq.next_review_date <= date('now')
           ORDER BY wq.next_review_date"""
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_weak_questions_by_quiz(quiz_id: int) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM weak_questions WHERE quiz_id = ?", (quiz_id,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def advance_weak_question(weak_id: int):
    """Move weak question to next stage or delete if mastered."""
    from spaced_repetition import next_review_date, REVIEW_INTERVALS
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM weak_questions WHERE id = ?", (weak_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
        
    wq = dict(row)
    new_stage = wq["stage"] + 1

    if new_stage >= len(REVIEW_INTERVALS):
        cursor.execute("DELETE FROM weak_questions WHERE id = ?", (weak_id,))
    else:
        cursor.execute(
            "UPDATE weak_questions SET stage = ?, next_review_date = ? WHERE id = ?",
            (new_stage, next_review_date(new_stage), weak_id),
        )

    conn.commit()
    conn.close()

def remove_weak_question(weak_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM weak_questions WHERE id = ?", (weak_id,))
    conn.commit()
    conn.close()

# ─── Active Session ───────────────────────────────────────────────────────────

def save_session(session_type: str, quiz_id: int, review_id: int | None,
                 question_ids: list, current_index: int = 0,
                 correct_count: int = 0, wrong_ids: list = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_session")
    cursor.execute(
        """INSERT INTO active_session
           (id, session_type, quiz_id, review_id, question_ids, current_index, correct_count, wrong_ids)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_type,
            quiz_id,
            review_id,
            json.dumps(question_ids),
            current_index,
            correct_count,
            json.dumps(wrong_ids or []),
        ),
    )
    conn.commit()
    conn.close()

def get_session() -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM active_session WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        row = dict(row)
        row["question_ids"] = json.loads(row["question_ids"])
        row["wrong_ids"] = json.loads(row["wrong_ids"])
        return row
    return None

def update_session(current_index: int, correct_count: int, wrong_ids: list):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE active_session
           SET current_index = ?, correct_count = ?, wrong_ids = ?
           WHERE id = 1""",
        (current_index, correct_count, json.dumps(wrong_ids)),
    )
    conn.commit()
    conn.close()

def clear_session():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_session")
    conn.commit()
    conn.close()

# ─── Bot State ────────────────────────────────────────────────────────────────

def set_last_message_id(chat_id: int, message_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO bot_state (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (f"last_msg_{chat_id}", str(message_id))
    )
    conn.commit()
    conn.close()

def get_last_message_id(chat_id: int) -> int | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_state WHERE key = ?", (f"last_msg_{chat_id}",))
    row = cursor.fetchone()
    conn.close()
    return int(row["value"]) if row else None
