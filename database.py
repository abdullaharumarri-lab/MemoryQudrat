import sqlite3
import json
import pytz
from datetime import datetime, date, timedelta
from config import DB_PATH


def get_first_review_date() -> str:
    """Smart first review: before 4:30 AM Riyadh → today, after 4:30 AM → tomorrow."""
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    now_riyadh = datetime.now(riyadh_tz)
    if now_riyadh.hour < 4 or (now_riyadh.hour == 4 and now_riyadh.minute < 30):
        return date.today().isoformat()
    else:
        return (date.today() + timedelta(days=1)).isoformat()

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize all database tables and indexes for SQLite."""
    conn = get_connection()
    cursor = conn.cursor()

    # Categories table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            intervals_json TEXT NOT NULL DEFAULT '[1, 3, 7, 14, 30]'
        )
    """)

    # Quizzes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT,
            category_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
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
            wrong_ids TEXT DEFAULT '[]',
            poll_id TEXT,
            session_message_ids TEXT DEFAULT '[]'
        )
    """)

    # Bot state for clean chat
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Indexes for fast querying
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_questions_quiz_id ON questions(quiz_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_reviews_quiz_id ON quiz_reviews(quiz_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_reviews_due ON quiz_reviews(next_review_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weak_questions_quiz_id ON weak_questions(quiz_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weak_questions_due ON weak_questions(next_review_date)")

    # Auto-migrate: Add url column to quizzes if it doesn't exist
    try:
        cursor.execute("ALTER TABLE quizzes ADD COLUMN url TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Auto-migrate: Add category_id column to quizzes if it doesn't exist
    try:
        cursor.execute("ALTER TABLE quizzes ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Auto-migrate: Add poll_id column to active_session if it doesn't exist
    try:
        cursor.execute("ALTER TABLE active_session ADD COLUMN poll_id TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Auto-migrate: Add session_message_ids column to active_session if it doesn't exist
    try:
        cursor.execute("ALTER TABLE active_session ADD COLUMN session_message_ids TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Quiz sessions log for weekly stats
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_sessions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            session_type TEXT NOT NULL,
            total INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            wrong INTEGER NOT NULL,
            session_date DATE DEFAULT (date('now'))
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_log_quiz ON quiz_sessions_log(quiz_id)")

    # Default category
    cursor.execute("SELECT COUNT(*) FROM categories")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO categories (name, intervals_json) VALUES ('عام', '[1, 3, 7, 14, 30]')")

    conn.commit()
    conn.close()

# ─── Categories ───────────────────────────────────────────────────────────────

def create_category(name: str, intervals_json: str = "[1, 3, 7, 14, 30]") -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO categories (name, intervals_json) VALUES (?, ?)", (name, intervals_json))
    cat_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return cat_id

def get_categories() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories ORDER BY name")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_category(cat_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories WHERE id = ?", (cat_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_category_intervals(cat_id: int, intervals_json: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE categories SET intervals_json = ? WHERE id = ?", (intervals_json, cat_id))
    conn.commit()
    conn.close()

# ─── Quizzes ──────────────────────────────────────────────────────────────────

def save_quiz_without_review(name: str, questions: list, category_id: int = None) -> int:
    """Save quiz and questions only — no review scheduled yet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO quizzes (name, category_id) VALUES (?, ?)", (name, category_id))
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
    conn.commit()
    conn.close()
    return quiz_id


def save_quiz_url(name: str, url: str, category_id: int = None) -> int:
    """Save quiz with a URL and smart first review date."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO quizzes (name, url, category_id) VALUES (?, ?, ?)", (name, url, category_id))
    quiz_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    now_riyadh = datetime.now(riyadh_tz)
    start_today = now_riyadh.hour < 4 or (now_riyadh.hour == 4 and now_riyadh.minute < 30)
    schedule_first_review(quiz_id, start_today=start_today)
    return quiz_id


def schedule_first_review(quiz_id: int, start_today: bool = True):
    """Schedule the first review. start_today=True → today, False → tomorrow."""
    conn = get_connection()
    cursor = conn.cursor()
    if start_today:
        review_date = date.today().isoformat()
    else:
        review_date = (date.today() + timedelta(days=1)).isoformat()
    cursor.execute(
        "INSERT OR IGNORE INTO quiz_reviews (quiz_id, stage, next_review_date) VALUES (?, 0, ?)",
        (quiz_id, review_date),
    )
    conn.commit()
    conn.close()


def save_quiz(name: str, questions: list, category_id: int = None) -> int:
    """Save quiz with smart first review date (today if before 4:30 AM, else tomorrow)."""
    quiz_id = save_quiz_without_review(name, questions, category_id)
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    now_riyadh = datetime.now(riyadh_tz)
    start_today = now_riyadh.hour < 4 or (now_riyadh.hour == 4 and now_riyadh.minute < 30)
    schedule_first_review(quiz_id, start_today=start_today)
    return quiz_id

def get_all_quizzes() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM quizzes ORDER BY id DESC")
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
        opts = r.get("options")
        if opts:
            try:
                r["options"] = json.loads(opts) if isinstance(opts, str) else opts
            except Exception:
                r["options"] = []
        else:
            r["options"] = []
    return rows

def get_question(question_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions WHERE id = ?", (question_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        row = dict(row)
        opts = row.get("options")
        if opts:
            try:
                row["options"] = json.loads(opts) if isinstance(opts, str) else opts
            except Exception:
                row["options"] = []
        else:
            row["options"] = []
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

def get_all_quiz_reviews() -> list:
    """Returns all scheduled quiz_reviews with quiz names, ordered by next date."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT qr.*, q.name as quiz_name
           FROM quiz_reviews qr
           JOIN quizzes q ON qr.quiz_id = q.id
           ORDER BY qr.next_review_date"""
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def advance_quiz_review(review_id: int):
    """Move to next stage or delete if completed all stages."""
    from spaced_repetition import next_review_date, DEFAULT_REVIEW_INTERVALS
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT qr.*, q.category_id 
        FROM quiz_reviews qr 
        JOIN quizzes q ON qr.quiz_id = q.id 
        WHERE qr.id = ?
    """, (review_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
        
    review = dict(row)
    
    intervals = DEFAULT_REVIEW_INTERVALS
    if review.get("category_id"):
        cursor.execute("SELECT intervals_json FROM categories WHERE id = ?", (review["category_id"],))
        cat_row = cursor.fetchone()
        if cat_row:
            try:
                intervals = json.loads(cat_row["intervals_json"])
            except Exception:
                pass

    new_stage = review["stage"] + 1

    if new_stage >= len(intervals):
        cursor.execute("DELETE FROM quiz_reviews WHERE id = ?", (review_id,))
    else:
        new_date_str = next_review_date(new_stage, review["next_review_date"], intervals=intervals)
        cursor.execute(
            "UPDATE quiz_reviews SET stage = ?, next_review_date = ? WHERE id = ?",
            (new_stage, new_date_str, review_id),
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
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_weak_questions() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT wq.*, q.name as quiz_name
           FROM weak_questions wq
           JOIN quizzes q ON wq.quiz_id = q.id
           ORDER BY q.name"""
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_due_all_weak_questions_sorted() -> list:
    """Returns all due weak questions sorted: newest added (lowest next_review_date) first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT wq.*, q.name as quiz_name
           FROM weak_questions wq
           JOIN quizzes q ON wq.quiz_id = q.id
           WHERE wq.next_review_date <= date('now')
           ORDER BY wq.id DESC"""
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_weak_questions_sorted_for_practice() -> list:
    """Returns ALL weak questions (not just due) sorted newest first, for weakpractice."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT wq.*, q.name as quiz_name
           FROM weak_questions wq
           JOIN quizzes q ON wq.quiz_id = q.id
           ORDER BY wq.id DESC"""
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

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
    from spaced_repetition import next_review_date, DEFAULT_REVIEW_INTERVALS
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT wq.*, q.category_id 
        FROM weak_questions wq 
        JOIN quizzes q ON wq.quiz_id = q.id 
        WHERE wq.id = ?
    """, (weak_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
        
    wq = dict(row)
    
    intervals = DEFAULT_REVIEW_INTERVALS
    if wq.get("category_id"):
        cursor.execute("SELECT intervals_json FROM categories WHERE id = ?", (wq["category_id"],))
        cat_row = cursor.fetchone()
        if cat_row:
            try:
                intervals = json.loads(cat_row["intervals_json"])
            except Exception:
                pass

    new_stage = wq["stage"] + 1

    if new_stage >= len(intervals):
        # Never delete weak questions. Once they finish spaced repetition, keep them on a 1-day interval.
        next_date = date.today() + timedelta(days=1)
        cursor.execute(
            "UPDATE weak_questions SET next_review_date = ? WHERE id = ?",
            (next_date.isoformat(), weak_id)
        )
    else:
        new_date_str = next_review_date(new_stage, wq["next_review_date"], intervals=intervals)
        cursor.execute(
            "UPDATE weak_questions SET stage = ?, next_review_date = ? WHERE id = ?",
            (new_stage, new_date_str, weak_id),
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
                 correct_count: int = 0, wrong_ids: list = None, poll_id: str = None, session_message_ids: list = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_session")
    cursor.execute(
        """INSERT INTO active_session
           (id, session_type, quiz_id, review_id, question_ids, current_index, correct_count, wrong_ids, poll_id, session_message_ids)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_type,
            quiz_id,
            review_id,
            json.dumps(question_ids),
            current_index,
            correct_count,
            json.dumps(wrong_ids or []),
            poll_id,
            json.dumps(session_message_ids or []),
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
        try:
            row["session_message_ids"] = json.loads(row.get("session_message_ids") or "[]")
        except:
            row["session_message_ids"] = []
        return row
    return None

def update_session(current_index: int, correct_count: int, wrong_ids: list, poll_id: str = None, session_message_ids: list = None):
    conn = get_connection()
    cursor = conn.cursor()
    if session_message_ids is None:
        # Keep old session_message_ids if not provided (though we will provide it)
        cursor.execute(
            """UPDATE active_session
               SET current_index = ?, correct_count = ?, wrong_ids = ?, poll_id = ?
               WHERE id = 1""",
            (current_index, correct_count, json.dumps(wrong_ids), poll_id),
        )
    else:
        cursor.execute(
            """UPDATE active_session
               SET current_index = ?, correct_count = ?, wrong_ids = ?, poll_id = ?, session_message_ids = ?
               WHERE id = 1""",
            (current_index, correct_count, json.dumps(wrong_ids), poll_id, json.dumps(session_message_ids)),
        )
    conn.commit()
    conn.close()

def clear_session():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_session")
    conn.commit()
    conn.close()


def log_session(quiz_id, session_type: str, total: int, correct: int, wrong: int):
    """Log a completed session for weekly stats."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO quiz_sessions_log (quiz_id, session_type, total, correct, wrong)
           VALUES (?, ?, ?, ?, ?)""",
        (quiz_id, session_type, total, correct, wrong)
    )
    conn.commit()
    conn.close()


def get_my_stats() -> dict:
    """Returns comprehensive all-time and monthly stats."""
    conn = get_connection()
    cursor = conn.cursor()

    # All-time totals
    cursor.execute(
        """SELECT COUNT(*) as sessions, SUM(total) as total, SUM(correct) as correct, SUM(wrong) as wrong
           FROM quiz_sessions_log
           WHERE session_type NOT IN ('practice')"""
    )
    alltime = dict(cursor.fetchone())

    # Current month name
    cursor.execute("SELECT strftime('%m', 'now') as month, strftime('%Y', 'now') as year")
    current = dict(cursor.fetchone())

    # Per-month breakdown (last 6 months)
    cursor.execute(
        """SELECT strftime('%Y-%m', session_date) as month_key,
                  SUM(total) as total, SUM(correct) as correct, SUM(wrong) as wrong,
                  COUNT(*) as sessions
           FROM quiz_sessions_log
           WHERE session_type NOT IN ('practice')
           AND session_date >= date('now', '-5 months', 'start of month')
           GROUP BY month_key
           ORDER BY month_key DESC"""
    )
    monthly = [dict(r) for r in cursor.fetchall()]

    # Total weak questions currently in DB
    cursor.execute("SELECT COUNT(*) as cnt FROM weak_questions")
    total_weak = dict(cursor.fetchone())["cnt"]

    # Total quizzes count
    cursor.execute("SELECT COUNT(*) as cnt FROM quizzes")
    total_quizzes = dict(cursor.fetchone())["cnt"]

    # Best month
    cursor.execute(
        """SELECT strftime('%Y-%m', session_date) as month_key,
                  SUM(correct)*100/MAX(SUM(total),1) as pct
           FROM quiz_sessions_log
           WHERE session_type NOT IN ('practice')
           GROUP BY month_key
           ORDER BY pct DESC
           LIMIT 1"""
    )
    best_row = cursor.fetchone()
    best_month = dict(best_row) if best_row else None

    conn.close()

    return {
        "sessions": alltime["sessions"] or 0,
        "total": alltime["total"] or 0,
        "correct": alltime["correct"] or 0,
        "wrong": alltime["wrong"] or 0,
        "monthly": monthly,
        "total_weak": total_weak,
        "total_quizzes": total_quizzes,
        "best_month": best_month,
        "current_month": f"{current['year']}-{current['month']}",
    }

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

def save_chat_id(chat_id: int):
    """Save a user's chat_id so the bot can send reminders after restart."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO bot_state (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO NOTHING""",
        (f"chat_{chat_id}", str(chat_id))
    )
    conn.commit()
    conn.close()

def get_all_chat_ids() -> list:
    """Retrieve all saved chat_ids to schedule reminders on startup."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_state WHERE key LIKE 'chat_%'")
    rows = cursor.fetchall()
    conn.close()
    return [int(row["value"]) for row in rows]
