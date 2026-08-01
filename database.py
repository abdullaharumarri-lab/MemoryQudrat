import psycopg2
import psycopg2.extras
import json
from config import DATABASE_URL
from datetime import date

def get_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in environment variables")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Initialize all database tables for PostgreSQL."""
    conn = get_connection()
    cursor = conn.cursor()

    # Quizzes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Questions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id SERIAL PRIMARY KEY,
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
            id SERIAL PRIMARY KEY,
            quiz_id INTEGER NOT NULL,
            stage INTEGER DEFAULT 0,
            next_review_date DATE NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        )
    """)

    # Weak (wrong) questions with their own spaced repetition
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weak_questions (
            id SERIAL PRIMARY KEY,
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
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("INSERT INTO quizzes (name) VALUES (%s) RETURNING id", (name,))
    quiz_id = cursor.fetchone()['id']

    for q in questions:
        cursor.execute(
            """INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation)
               VALUES (%s, %s, %s, %s, %s)""",
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
        "INSERT INTO quiz_reviews (quiz_id, stage, next_review_date) VALUES (%s, 0, %s)",
        (quiz_id, next_review_date(0)),
    )

    conn.commit()
    conn.close()
    return quiz_id

def get_all_quizzes() -> list:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM quizzes ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_quiz(quiz_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM quizzes WHERE id = %s", (quiz_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def delete_quiz(quiz_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM quizzes WHERE id = %s", (quiz_id,))
    conn.commit()
    conn.close()

# ─── Questions ────────────────────────────────────────────────────────────────

def get_questions(quiz_id: int) -> list:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM questions WHERE quiz_id = %s", (quiz_id,))
    rows = cursor.fetchall()
    conn.close()
    for r in rows:
        r["options"] = json.loads(r["options"])
    return rows

def get_question(question_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM questions WHERE id = %s", (question_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        row["options"] = json.loads(row["options"])
    return row

# ─── Quiz Reviews ─────────────────────────────────────────────────────────────

def get_due_quiz_reviews() -> list:
    """Returns quiz_reviews due today or earlier."""
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        """SELECT qr.*, q.name as quiz_name
           FROM quiz_reviews qr
           JOIN quizzes q ON qr.quiz_id = q.id
           WHERE qr.next_review_date <= CURRENT_DATE
           ORDER BY qr.next_review_date"""
    )
    rows = cursor.fetchall()
    conn.close()
    return rows

def advance_quiz_review(review_id: int):
    """Move to next stage or delete if completed all stages."""
    from spaced_repetition import next_review_date, REVIEW_INTERVALS
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("SELECT * FROM quiz_reviews WHERE id = %s", (review_id,))
    review = cursor.fetchone()
    new_stage = review["stage"] + 1

    if new_stage >= len(REVIEW_INTERVALS):
        cursor.execute("DELETE FROM quiz_reviews WHERE id = %s", (review_id,))
    else:
        cursor.execute(
            "UPDATE quiz_reviews SET stage = %s, next_review_date = %s WHERE id = %s",
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
           VALUES (%s, %s, 0, CURRENT_DATE)
           ON CONFLICT(quiz_id, question_id)
           DO UPDATE SET stage = 0, next_review_date = CURRENT_DATE""",
        (quiz_id, question_id),
    )
    conn.commit()
    conn.close()

def get_due_weak_questions() -> list:
    """Returns weak questions due today or earlier."""
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        """SELECT wq.*, q.name as quiz_name
           FROM weak_questions wq
           JOIN quizzes q ON wq.quiz_id = q.id
           WHERE wq.next_review_date <= CURRENT_DATE
           ORDER BY wq.next_review_date"""
    )
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_weak_questions_by_quiz(quiz_id: int) -> list:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        "SELECT * FROM weak_questions WHERE quiz_id = %s", (quiz_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows

def advance_weak_question(weak_id: int):
    """Move weak question to next stage or delete if mastered."""
    from spaced_repetition import next_review_date, REVIEW_INTERVALS
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("SELECT * FROM weak_questions WHERE id = %s", (weak_id,))
    wq = cursor.fetchone()
    new_stage = wq["stage"] + 1

    if new_stage >= len(REVIEW_INTERVALS):
        cursor.execute("DELETE FROM weak_questions WHERE id = %s", (weak_id,))
    else:
        cursor.execute(
            "UPDATE weak_questions SET stage = %s, next_review_date = %s WHERE id = %s",
            (new_stage, next_review_date(new_stage), weak_id),
        )

    conn.commit()
    conn.close()

def remove_weak_question(weak_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM weak_questions WHERE id = %s", (weak_id,))
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
           VALUES (1, %s, %s, %s, %s, %s, %s, %s)""",
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
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM active_session WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        row["question_ids"] = json.loads(row["question_ids"])
        row["wrong_ids"] = json.loads(row["wrong_ids"])
    return row

def update_session(current_index: int, correct_count: int, wrong_ids: list):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE active_session
           SET current_index = %s, correct_count = %s, wrong_ids = %s
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
        """INSERT INTO bot_state (key, value) VALUES (%s, %s)
           ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value""",
        (f"last_msg_{chat_id}", str(message_id))
    )
    conn.commit()
    conn.close()

def get_last_message_id(chat_id: int) -> int | None:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT value FROM bot_state WHERE key = %s", (f"last_msg_{chat_id}",))
    row = cursor.fetchone()
    conn.close()
    return int(row["value"]) if row else None
