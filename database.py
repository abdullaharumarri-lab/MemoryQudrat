import sqlite3
import json
import os
import shutil
import logging
import pytz
from datetime import datetime, date, timedelta
from config import DB_PATH, ADMIN_USER_ID

logger = logging.getLogger(__name__)


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
    conn.execute("PRAGMA cache_size = 10000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize all database tables, perform safe automatic multi-user migration, and setup indexes."""
    # ── Safe Pre-Migration Backup ──
    if os.path.exists(DB_PATH):
        backup_path = f"{DB_PATH}.backup_pre_multiuser"
        if not os.path.exists(backup_path):
            try:
                shutil.copyfile(DB_PATH, backup_path)
                logger.info("Automatic database backup created at %s", backup_path)
            except Exception as e:
                logger.warning("Could not create database backup: %s", e)

    conn = get_connection()
    cursor = conn.cursor()

    # ── 1. Users Table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reminder_hour INTEGER DEFAULT 4,
            reminder_minute INTEGER DEFAULT 30
        )
    """)

    # ── 2. Categories Table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            intervals_json TEXT NOT NULL DEFAULT '[1, 3, 7, 14, 30]',
            parent_id INTEGER,
            is_public INTEGER DEFAULT 1,
            icon TEXT DEFAULT '📁',
            sort_order INTEGER DEFAULT 0,
            owner_id INTEGER DEFAULT NULL,
            FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE SET NULL
        )
    """)

    # ── 3. Quizzes Table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT,
            category_id INTEGER,
            owner_id INTEGER,
            is_public INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
        )
    """)

    # ── 4. Questions Table ──
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

    # ── 5. Quiz Spaced Repetition Reviews ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 6099429826,
            quiz_id INTEGER NOT NULL,
            stage INTEGER DEFAULT 0,
            next_review_date DATE NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        )
    """)

    # ── 6. Weak (Wrong) Questions Spaced Repetition ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weak_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 6099429826,
            quiz_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            stage INTEGER DEFAULT 0,
            next_review_date DATE NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        )
    """)

    # ── 7. Active Quiz Session (Scoped to user_id) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_session (
            user_id INTEGER PRIMARY KEY,
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

    # ── 8. Quiz Sessions Log for Stats ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_sessions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 6099429826,
            quiz_id INTEGER,
            session_type TEXT NOT NULL,
            total INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            wrong INTEGER NOT NULL,
            session_date DATE DEFAULT (date('now'))
        )
    """)

    # ── 9. Bot State Table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ── 10. Quiz Weak Mastery (5 consecutive perfect scores to master) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_weak_mastery (
            user_id INTEGER DEFAULT 6099429826,
            quiz_id INTEGER NOT NULL,
            consecutive_perfect INTEGER DEFAULT 0,
            is_mastered INTEGER DEFAULT 0,
            mastered_at TIMESTAMP,
            PRIMARY KEY (user_id, quiz_id),
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        )
    """)

    # ── AUTO-MIGRATIONS FOR EXISTING PRODUCTION DATABASES ──

    # Quizzes: add owner_id, is_public, url, category_id if missing
    try:
        cursor.execute("ALTER TABLE quizzes ADD COLUMN url TEXT")
    except sqlite3.OperationalError: pass

    try:
        cursor.execute("ALTER TABLE quizzes ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
    except sqlite3.OperationalError: pass

    try:
        cursor.execute("ALTER TABLE quizzes ADD COLUMN owner_id INTEGER")
    except sqlite3.OperationalError: pass

    try:
        cursor.execute("ALTER TABLE quizzes ADD COLUMN is_public INTEGER DEFAULT 1")
    except sqlite3.OperationalError: pass

    cursor.execute("UPDATE quizzes SET is_public = 1 WHERE is_public IS NULL")
    cursor.execute("UPDATE quizzes SET owner_id = ? WHERE owner_id IS NULL", (ADMIN_USER_ID,))

    # Categories: add parent_id, is_public, icon, sort_order, owner_id if missing
    try:
        cursor.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE categories ADD COLUMN is_public INTEGER DEFAULT 1")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE categories ADD COLUMN icon TEXT DEFAULT '📁'")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE categories ADD COLUMN sort_order INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE categories ADD COLUMN owner_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError: pass

    # Quiz Reviews: add user_id if missing
    try:
        cursor.execute("ALTER TABLE quiz_reviews ADD COLUMN user_id INTEGER DEFAULT 6099429826")
    except sqlite3.OperationalError: pass
    cursor.execute("UPDATE quiz_reviews SET user_id = 6099429826 WHERE user_id IS NULL")

    # Weak Questions: add user_id if missing
    try:
        cursor.execute("ALTER TABLE weak_questions ADD COLUMN user_id INTEGER DEFAULT 6099429826")
    except sqlite3.OperationalError: pass
    cursor.execute("UPDATE weak_questions SET user_id = 6099429826 WHERE user_id IS NULL")

    # Quiz Sessions Log: add user_id if missing
    try:
        cursor.execute("ALTER TABLE quiz_sessions_log ADD COLUMN user_id INTEGER DEFAULT 6099429826")
    except sqlite3.OperationalError: pass
    cursor.execute("UPDATE quiz_sessions_log SET user_id = 6099429826 WHERE user_id IS NULL")

    # Active Session: ensure it has user_id as primary key
    active_cols = [c[1] for c in cursor.execute("PRAGMA table_info(active_session)").fetchall()]
    if "user_id" not in active_cols:
        cursor.execute("DROP TABLE IF EXISTS active_session")
        cursor.execute("""
            CREATE TABLE active_session (
                user_id INTEGER PRIMARY KEY,
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

    # ── High Performance Indexes ──
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_questions_quiz_id ON questions(quiz_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_public ON quizzes(is_public)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_owner ON quizzes(owner_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_cat_owner_pub ON quizzes(category_id, owner_id, is_public)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_reviews_user_quiz ON quiz_reviews(user_id, quiz_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_reviews_user_due ON quiz_reviews(user_id, next_review_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_reviews_user_due_id ON quiz_reviews(user_id, next_review_date, id DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weak_questions_user_quiz ON weak_questions(user_id, quiz_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weak_questions_user_due ON weak_questions(user_id, next_review_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weak_questions_user_due_id ON weak_questions(user_id, next_review_date, id DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_log_user ON quiz_sessions_log(user_id, session_date)")

    # ── 10. Chat History IDs Table for Complete Chat Cleaning ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history_ids (
            chat_id INTEGER,
            message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, message_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_chat_id ON chat_history_ids(chat_id)")

    # If any quiz references a deleted category, safely reset it to NULL (Root level)
    cursor.execute("UPDATE quizzes SET category_id = NULL WHERE category_id IS NOT NULL AND category_id NOT IN (SELECT id FROM categories)")

    # Clean up empty/phantom quizzes (0 questions and no URL) and orphaned reviews
    try:
        cursor.execute("DELETE FROM quiz_reviews WHERE quiz_id NOT IN (SELECT id FROM quizzes)")
        cursor.execute("""
            DELETE FROM quiz_reviews 
            WHERE quiz_id IN (
                SELECT id FROM quizzes 
                WHERE (url IS NULL OR url = '') 
                AND NOT EXISTS (SELECT 1 FROM questions WHERE quiz_id = quizzes.id)
            )
        """)
        cursor.execute("""
            DELETE FROM quizzes 
            WHERE (url IS NULL OR url = '') 
            AND NOT EXISTS (SELECT 1 FROM questions WHERE quiz_id = quizzes.id)
        """)
    except Exception as e:
        logger.warning("Error cleaning empty/orphaned quizzes: %s", e)

    conn.commit()
    conn.close()
    logger.info("Database initialized cleanly with multi-user support.")


def format_database_from_roots(keep_admin_user: bool = True):
    """
    Completely formats and resets the bot database from its roots.
    Wipes all quizzes, questions, categories, reviews, weak questions, sessions,
    logs, mastery, chat history, and bot state cleanly.
    Resets SQLite auto-increment sequences and vacuums database file.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Disable foreign keys temporarily during wipe
    cursor.execute("PRAGMA foreign_keys = OFF;")

    tables_to_wipe = [
        "quizzes",
        "questions",
        "categories",
        "quiz_reviews",
        "weak_questions",
        "active_session",
        "quiz_sessions_log",
        "quiz_weak_mastery",
        "chat_history_ids",
        "bot_state",
    ]

    for tbl in tables_to_wipe:
        try:
            cursor.execute(f"DELETE FROM [{tbl}]")
        except Exception as e:
            logger.warning("Could not wipe table %s: %s", tbl, e)

    # Reset SQLite auto-increment sequences
    try:
        cursor.execute("DELETE FROM sqlite_sequence")
    except Exception:
        pass

    # Clean users table except admin if requested
    if keep_admin_user:
        cursor.execute("DELETE FROM users WHERE user_id != ?", (ADMIN_USER_ID,))
    else:
        cursor.execute("DELETE FROM users")

    cursor.execute("PRAGMA foreign_keys = ON;")
    conn.commit()
    conn.close()

    # Vacuum database to shrink file and optimize
    try:
        raw_conn = sqlite3.connect(DB_PATH)
        raw_conn.execute("VACUUM;")
        raw_conn.close()
    except Exception as e:
        logger.warning("VACUUM failed after format: %s", e)

    logger.info("Bot database formatted completely from roots. Pristine state established.")


def reset_non_admin_data(admin_id: int = ADMIN_USER_ID):
    """
    Wipes all user data except the admin's.
    Keeps: categories, quizzes, questions, users, admin's quiz_reviews & weak_questions.
    Clears: non-admin quiz_reviews, non-admin weak_questions, all sessions, logs, mastery, bot_state.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Keep admin reviews, delete everyone else's
    cursor.execute("DELETE FROM quiz_reviews WHERE user_id != ?", (admin_id,))

    # Keep admin weak questions, delete everyone else's
    cursor.execute("DELETE FROM weak_questions WHERE user_id != ?", (admin_id,))

    # Wipe all active sessions
    cursor.execute("DELETE FROM active_session")

    # Wipe session log
    cursor.execute("DELETE FROM quiz_sessions_log WHERE user_id != ?", (admin_id,))

    # Wipe mastery for non-admins
    cursor.execute("DELETE FROM quiz_weak_mastery WHERE user_id != ?", (admin_id,))

    # Wipe bot_state (message tracking)
    cursor.execute("DELETE FROM bot_state")

    conn.commit()
    conn.close()
    logger.info("Non-admin user data reset complete. Admin data preserved.")



# ─── Users ────────────────────────────────────────────────────────────────────

def save_or_update_user(user_id: int, username: str = None, full_name: str = None):
    """Register new user or update their username/full_name."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO users (user_id, username, full_name)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               username = coalesce(excluded.username, users.username),
               full_name = coalesce(excluded.full_name, users.full_name)""",
        (user_id, username, full_name),
    )
    conn.commit()
    conn.close()


def get_user(user_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_reminder(user_id: int, hour: int, minute: int):
    """Set custom daily reminder time for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET reminder_hour = ?, reminder_minute = ? WHERE user_id = ?",
        (hour, minute, user_id),
    )
    conn.commit()
    conn.close()


def get_all_users() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY joined_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_total_users_count() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    cnt = cursor.fetchone()["cnt"]
    conn.close()
    return cnt


def get_all_broadcast_recipients() -> list[int]:
    """Returns a deduplicated list of all user IDs for broadcast."""
    conn = get_connection()
    cursor = conn.cursor()
    recipients = set()
    try:
        cursor.execute("SELECT user_id FROM users")
        for r in cursor.fetchall():
            if r["user_id"]:
                recipients.add(int(r["user_id"]))
    except Exception:
        pass
    conn.close()
    return list(recipients)


def get_platform_stats() -> dict:
    """Computes comprehensive platform-wide analytics for Admin."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # User counts
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    total_users = cursor.fetchone()["cnt"]
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) as cnt FROM quiz_sessions_log WHERE session_date = date('now')")
    active_today = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(DISTINCT user_id) as cnt FROM quiz_sessions_log WHERE session_date >= date('now', '-7 days')")
    active_7days = cursor.fetchone()["cnt"]

    # Sessions and questions
    cursor.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(total), 0) as total_q, COALESCE(SUM(correct), 0) as total_c FROM quiz_sessions_log")
    sess_row = cursor.fetchone()
    total_sessions = sess_row["cnt"]
    total_q_solved = sess_row["total_q"]
    total_c_solved = sess_row["total_c"]
    accuracy = int((total_c_solved / total_q_solved) * 100) if total_q_solved > 0 else 0

    # Public bank content
    cursor.execute("SELECT COUNT(*) as cnt FROM quizzes WHERE is_public = 1")
    total_public_quizzes = cursor.fetchone()["cnt"]

    cursor.execute("""
        SELECT COUNT(*) as cnt 
        FROM questions q 
        JOIN quizzes z ON q.quiz_id = z.id 
        WHERE z.is_public = 1
    """)
    total_public_questions = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM categories")
    total_categories = cursor.fetchone()["cnt"]

    # Active schedules & weak questions across all users
    cursor.execute("SELECT COUNT(*) as cnt FROM quiz_reviews")
    total_active_reviews = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM weak_questions")
    total_active_weak = cursor.fetchone()["cnt"]

    conn.close()
    return {
        "total_users": max(total_users, len(get_all_broadcast_recipients())),
        "active_today": active_today,
        "active_7days": active_7days,
        "total_sessions": total_sessions,
        "total_questions_solved": total_q_solved,
        "total_correct_solved": total_c_solved,
        "accuracy": accuracy,
        "total_public_quizzes": total_public_quizzes,
        "total_public_questions": total_public_questions,
        "total_categories": total_categories,
        "total_active_reviews": total_active_reviews,
        "total_active_weak": total_active_weak,
    }


def get_riyadh_today_iso() -> str:
    """Returns today's date in Asia/Riyadh timezone formatted as YYYY-MM-DD."""
    try:
        riyadh_tz = pytz.timezone("Asia/Riyadh")
        return datetime.now(riyadh_tz).date().isoformat()
    except Exception:
        return date.today().isoformat()


# ─── Categories ───────────────────────────────────────────────────────────────

def create_category(name: str, intervals_json: str = "[1, 3, 7, 14, 30]", parent_id: int = None, icon: str = "📁", owner_id: int = None, is_public: int = 1) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO categories (name, intervals_json, parent_id, icon, owner_id, is_public) VALUES (?, ?, ?, ?, ?, ?)",
        (name, intervals_json, parent_id, icon, owner_id, is_public),
    )
    cat_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return cat_id


def get_categories(parent_id: int = None, user_id: int = None, is_public: int = 1) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    if is_public == 1:
        if parent_id is None:
            cursor.execute("SELECT * FROM categories WHERE is_public = 1 AND parent_id IS NULL ORDER BY sort_order ASC, id DESC")
        else:
            cursor.execute("SELECT * FROM categories WHERE is_public = 1 AND parent_id = ? ORDER BY sort_order ASC, id DESC", (parent_id,))
    else:
        if parent_id is None:
            cursor.execute("SELECT * FROM categories WHERE is_public = 0 AND owner_id = ? AND parent_id IS NULL ORDER BY id DESC", (user_id,))
        else:
            cursor.execute("SELECT * FROM categories WHERE is_public = 0 AND owner_id = ? AND parent_id = ? ORDER BY id DESC", (user_id, parent_id))
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


def update_category_name(cat_id: int, name: str, icon: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    if icon is not None:
        cursor.execute("UPDATE categories SET name = ?, icon = ? WHERE id = ?", (name, icon, cat_id))
    else:
        cursor.execute("UPDATE categories SET name = ? WHERE id = ?", (name, cat_id))
    conn.commit()
    conn.close()


def delete_category(cat_id: int, user_id: int = None):
    """Delete category and move its child quizzes to uncategorized / root."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE quizzes SET category_id = NULL WHERE category_id = ?", (cat_id,))
    cursor.execute("UPDATE categories SET parent_id = NULL WHERE parent_id = ?", (cat_id,))
    if user_id is not None:
        cursor.execute("DELETE FROM categories WHERE id = ? AND owner_id = ? AND is_public = 0", (cat_id, user_id))
    else:
        cursor.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    conn.commit()
    conn.close()


def get_quizzes_by_category(category_id: int = None, user_id: int = None, is_public: int = 1) -> list:
    """Returns quizzes belonging to a specific category, ordered by recency (newest first)."""
    from utils import quiz_sort_key_desc
    conn = get_connection()
    cursor = conn.cursor()
    from config import is_admin
    valid_filter = "((url IS NOT NULL AND url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = quizzes.id))"
    if is_public == 1:
        if category_id is not None:
            cursor.execute(f"SELECT * FROM quizzes WHERE is_public = 1 AND category_id = ? AND {valid_filter} ORDER BY id DESC", (category_id,))
        else:
            cursor.execute(f"SELECT * FROM quizzes WHERE is_public = 1 AND category_id IS NULL AND {valid_filter} ORDER BY id DESC")
    else:
        if user_id is not None and is_admin(user_id):
            if category_id is not None:
                cursor.execute(f"SELECT * FROM quizzes WHERE (owner_id = ? OR owner_id IS NULL) AND category_id = ? AND {valid_filter} ORDER BY id DESC", (user_id, category_id))
            else:
                cursor.execute(f"SELECT * FROM quizzes WHERE (owner_id = ? OR owner_id IS NULL) AND category_id IS NULL AND {valid_filter} ORDER BY id DESC", (user_id,))
        else:
            if category_id is not None:
                cursor.execute(f"SELECT * FROM quizzes WHERE is_public = 0 AND owner_id = ? AND category_id = ? AND {valid_filter} ORDER BY id DESC", (user_id, category_id))
            else:
                cursor.execute(f"SELECT * FROM quizzes WHERE is_public = 0 AND owner_id = ? AND category_id IS NULL AND {valid_filter} ORDER BY id DESC", (user_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    cur_cat = get_category(category_id) if category_id else None
    cat_name = cur_cat.get("name", "") if cur_cat else ""
    if any(w in cat_name for w in ["جدول", "الضرب", "أساسيات", "درس", "مستوى"]):
        rows.sort(key=quiz_sort_key_desc, reverse=False)
    else:
        rows.sort(key=quiz_sort_key_desc, reverse=True)
    return rows


def get_category_quizzes_count(category_id: int, user_id: int = None, is_public: int = 1) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    from config import is_admin
    valid_filter = "((url IS NOT NULL AND url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = quizzes.id))"
    if is_public == 1:
        cursor.execute(f"SELECT COUNT(*) as cnt FROM quizzes WHERE category_id = ? AND is_public = 1 AND {valid_filter}", (category_id,))
    elif user_id is not None and is_admin(user_id):
        cursor.execute(f"SELECT COUNT(*) as cnt FROM quizzes WHERE category_id = ? AND (owner_id = ? OR owner_id IS NULL) AND {valid_filter}", (category_id, user_id))
    elif user_id is not None:
        cursor.execute(f"SELECT COUNT(*) as cnt FROM quizzes WHERE category_id = ? AND owner_id = ? AND is_public = 0 AND {valid_filter}", (category_id, user_id))
    else:
        cursor.execute(f"SELECT COUNT(*) as cnt FROM quizzes WHERE category_id = ? AND is_public = 1 AND {valid_filter}", (category_id,))
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0


def move_quiz_to_category(quiz_id: int, new_category_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE quizzes SET category_id = ? WHERE id = ?", (new_category_id, quiz_id))
    conn.commit()
    conn.close()


# ─── Quizzes ──────────────────────────────────────────────────────────────────

def save_quiz_without_review(name: str, questions: list, category_id: int = None, owner_id: int = None, is_public: int = 1, url: str = None) -> int:
    """Save quiz and questions only — no review scheduled yet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO quizzes (name, url, category_id, owner_id, is_public) VALUES (?, ?, ?, ?, ?)",
        (name, url, category_id, owner_id, is_public),
    )
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

    conn.commit()
    conn.close()
    return quiz_id


def update_quiz_questions(quiz_id: int, questions: list, new_name: str = None) -> bool:
    """
    Replaces questions of an existing quiz with updated ones while keeping
    all quiz_reviews, categories, and spaced repetition schedules intact.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if new_name:
        cursor.execute("UPDATE quizzes SET name = ?, url = NULL WHERE id = ?", (new_name, quiz_id))
    else:
        cursor.execute("UPDATE quizzes SET url = NULL WHERE id = ?", (quiz_id,))

    # Delete previous questions for this quiz
    cursor.execute("DELETE FROM questions WHERE quiz_id = ?", (quiz_id,))

    # Insert new questions
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
    return True


def update_quiz_name(quiz_id: int, new_name: str) -> bool:
    """Updates the title/name of a quiz."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE quizzes SET name = ? WHERE id = ?", (new_name.strip(), quiz_id))
    conn.commit()
    conn.close()
    return True


def rename_quiz(quiz_id: int, new_name: str):
    """Alias for update_quiz_name used by the new main_menu."""
    update_quiz_name(quiz_id, new_name)


def rename_category(cat_id: int, new_name: str):
    """Rename a category by id."""
    conn = get_connection()
    conn.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name.strip(), cat_id))
    conn.commit()
    conn.close()


def set_quiz_category(quiz_id: int, category_id):
    """Move a quiz to a different category (None = root)."""
    conn = get_connection()
    conn.execute("UPDATE quizzes SET category_id = ? WHERE id = ?", (category_id, quiz_id))
    conn.commit()
    conn.close()


def get_all_public_quizzes() -> list:
    """Returns all public quizzes with valid content, ordered by name."""
    from utils import quiz_sort_key_desc
    conn = get_connection()
    valid_filter = "((url IS NOT NULL AND url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = quizzes.id))"
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM quizzes WHERE is_public = 1 AND {valid_filter} ORDER BY id DESC"
    ).fetchall()]
    conn.close()
    rows.sort(key=quiz_sort_key_desc, reverse=True)
    return rows


def save_quiz_url(name: str, url: str, category_id: int = None, user_id: int = 6099429826, is_public: int = 0) -> int:
    """Save a URL-only quiz (like Google Forms) and schedule its first review for today."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO quizzes (name, url, category_id, owner_id, is_public) VALUES (?, ?, ?, ?, ?)",
        (name, url, category_id, user_id, is_public),
    )
    quiz_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    schedule_first_review(quiz_id, user_id=user_id, start_today=True)
    return quiz_id


def save_quiz(name: str, questions: list, category_id: int = None, user_id: int = 6099429826, is_public: int = 0) -> int:
    """Save quiz and schedule its first review for today in Riyadh timezone."""
    quiz_id = save_quiz_without_review(name, questions, category_id, owner_id=user_id, is_public=is_public)
    schedule_first_review(quiz_id, user_id=user_id, start_today=True)
    return quiz_id


def get_all_quizzes(user_id: int = None) -> list:
    """
    Returns public quizzes + user's own private quizzes if user_id is provided.
    Ordered by recency (newest first).
    """
    from utils import quiz_sort_key_desc
    conn = get_connection()
    cursor = conn.cursor()
    valid_filter = "((url IS NOT NULL AND url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = quizzes.id))"
    if user_id is not None:
        cursor.execute(
            f"""SELECT * FROM quizzes
               WHERE (is_public = 1 OR owner_id = ?) AND {valid_filter}
               ORDER BY id DESC""",
            (user_id,),
        )
    else:
        cursor.execute(f"SELECT * FROM quizzes WHERE {valid_filter} ORDER BY id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    rows.sort(key=quiz_sort_key_desc, reverse=True)
    return rows


def get_public_quizzes(category_id: int = None) -> list:
    """Returns quizzes in the public bank, ordered by recency (newest first)."""
    from utils import quiz_sort_key_desc
    conn = get_connection()
    cursor = conn.cursor()
    valid_filter = "((url IS NOT NULL AND url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = quizzes.id))"
    if category_id is not None:
        cursor.execute(
            f"SELECT * FROM quizzes WHERE is_public = 1 AND category_id = ? AND {valid_filter} ORDER BY id DESC",
            (category_id,),
        )
    else:
        cursor.execute(f"SELECT * FROM quizzes WHERE is_public = 1 AND {valid_filter} ORDER BY id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    rows.sort(key=quiz_sort_key_desc, reverse=True)
    return rows


def get_user_private_quizzes(user_id: int) -> list:
    """Returns quizzes uploaded privately by the given user, ordered by recency (newest first)."""
    from utils import quiz_sort_key_desc
    conn = get_connection()
    cursor = conn.cursor()
    from config import is_admin
    valid_filter = "((url IS NOT NULL AND url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = quizzes.id))"
    if is_admin(user_id):
        cursor.execute(
            f"SELECT * FROM quizzes WHERE (owner_id = ? OR owner_id IS NULL) AND {valid_filter} ORDER BY id DESC",
            (user_id,),
        )
    else:
        cursor.execute(
            f"SELECT * FROM quizzes WHERE owner_id = ? AND is_public = 0 AND {valid_filter} ORDER BY id DESC",
            (user_id,),
        )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    rows.sort(key=quiz_sort_key_desc, reverse=True)
    return rows


def get_quiz(quiz_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def copy_quiz_to_user(quiz_id: int, user_id: int) -> int:
    """Clone a public quiz and its questions to the user's private library."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
    orig = cursor.fetchone()
    if not orig:
        conn.close()
        return 0

    orig_dict = dict(orig)
    name = orig_dict["name"]
    url = orig_dict.get("url")

    # Insert new private quiz for this user
    cursor.execute(
        "INSERT INTO quizzes (name, url, category_id, owner_id, is_public) VALUES (?, ?, NULL, ?, 0)",
        (name, url, user_id),
    )
    new_quiz_id = cursor.lastrowid

    # Copy questions if it's not a URL/media quiz
    cursor.execute("SELECT * FROM questions WHERE quiz_id = ? ORDER BY id ASC", (quiz_id,))
    questions = cursor.fetchall()
    for q in questions:
        q_dict = dict(q)
        cursor.execute(
            """INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation)
               VALUES (?, ?, ?, ?, ?)""",
            (
                new_quiz_id,
                q_dict["question_text"],
                q_dict["options"],
                q_dict["correct_answer"],
                q_dict.get("explanation", ""),
            ),
        )
    conn.commit()
    conn.close()

    # Schedule first review for this user immediately today
    schedule_first_review(new_quiz_id, user_id=user_id, start_today=True)
    return new_quiz_id


def delete_quiz(quiz_id: int, user_id: int = None):
    """
    Delete a quiz. If user_id is provided and user is not admin,
    it only deletes if owner_id matches user_id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    if user_id is not None and user_id != ADMIN_USER_ID:
        cursor.execute("DELETE FROM quizzes WHERE id = ? AND owner_id = ?", (quiz_id, user_id))
    else:
        cursor.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
    conn.commit()
    conn.close()


# ─── Questions ────────────────────────────────────────────────────────────────

def get_questions(quiz_id: int) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions WHERE quiz_id = ? ORDER BY id ASC", (quiz_id,))
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


def update_question_correct_answer(question_id: int, new_answer: str) -> bool:
    """Updates the correct answer of a specific question."""
    conn = get_connection()
    conn.execute("UPDATE questions SET correct_answer = ? WHERE id = ?", (new_answer.strip(), question_id))
    conn.commit()
    conn.close()
    return True


def update_question_text(question_id: int, new_text: str) -> bool:
    """Updates the question text of a specific question."""
    conn = get_connection()
    conn.execute("UPDATE questions SET question_text = ? WHERE id = ?", (new_text.strip(), question_id))
    conn.commit()
    conn.close()
    return True


def update_question_explanation(question_id: int, new_exp: str) -> bool:
    """Updates the explanation of a specific question."""
    conn = get_connection()
    conn.execute("UPDATE questions SET explanation = ? WHERE id = ?", (new_exp.strip(), question_id))
    conn.commit()
    conn.close()
    return True


# ─── Quiz Reviews ─────────────────────────────────────────────────────────────

def schedule_first_review(quiz_id: int, user_id: int = 6099429826, start_today: bool = True):
    """Schedule the first review for a user in Riyadh timezone."""
    conn = get_connection()
    cursor = conn.cursor()
    today_str = get_riyadh_today_iso()
    if start_today:
        review_date = today_str
    else:
        review_date = (date.fromisoformat(today_str) + timedelta(days=1)).isoformat()
    
    # Check if a review already exists for this (user_id, quiz_id)
    cursor.execute("SELECT id FROM quiz_reviews WHERE user_id = ? AND quiz_id = ?", (user_id, quiz_id))
    existing = cursor.fetchone()
    if existing:
        cursor.execute("UPDATE quiz_reviews SET stage = 0, next_review_date = ? WHERE id = ?", (review_date, existing["id"]))
    else:
        cursor.execute(
            """INSERT INTO quiz_reviews (user_id, quiz_id, stage, next_review_date)
               VALUES (?, ?, 0, ?)""",
            (user_id, quiz_id, review_date),
        )
    conn.commit()
    conn.close()


def get_due_quiz_reviews(user_id: int = None) -> list:
    """Returns quiz_reviews due today or earlier for a user in Riyadh timezone, ordered by recency (newest first)."""
    from utils import quiz_sort_key_desc
    conn = get_connection()
    cursor = conn.cursor()
    today_iso = get_riyadh_today_iso()
    valid_filter = "((q.url IS NOT NULL AND q.url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = q.id))"
    if user_id is not None:
        cursor.execute(
            f"""SELECT qr.*, q.name as quiz_name
               FROM quiz_reviews qr
               JOIN quizzes q ON qr.quiz_id = q.id
               WHERE qr.user_id = ? AND qr.next_review_date <= ? AND {valid_filter}
               ORDER BY qr.id DESC""",
            (user_id, today_iso),
        )
    else:
        cursor.execute(
            f"""SELECT qr.*, q.name as quiz_name
               FROM quiz_reviews qr
               JOIN quizzes q ON qr.quiz_id = q.id
               WHERE qr.next_review_date <= ? AND {valid_filter}
               ORDER BY qr.id DESC""",
            (today_iso,),
        )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    rows.sort(key=quiz_sort_key_desc, reverse=True)
    return rows


def get_all_quiz_reviews(user_id: int = None) -> list:
    """Returns all scheduled quiz_reviews with quiz names, ordered by recency (newest first)."""
    from utils import quiz_sort_key_desc
    conn = get_connection()
    cursor = conn.cursor()
    valid_filter = "((q.url IS NOT NULL AND q.url != '') OR EXISTS (SELECT 1 FROM questions qs WHERE qs.quiz_id = q.id))"
    if user_id is not None:
        cursor.execute(
            f"""SELECT qr.*, q.name as quiz_name
               FROM quiz_reviews qr
               JOIN quizzes q ON qr.quiz_id = q.id
               WHERE qr.user_id = ? AND {valid_filter}
               ORDER BY qr.id DESC""",
            (user_id,),
        )
    else:
        cursor.execute(
            f"""SELECT qr.*, q.name as quiz_name
               FROM quiz_reviews qr
               JOIN quizzes q ON qr.quiz_id = q.id
               WHERE {valid_filter}
               ORDER BY qr.id DESC"""
        )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    rows.sort(key=quiz_sort_key_desc, reverse=True)
    return rows


def advance_quiz_review(review_id: int, user_id: int = None):
    """Move to next stage or delete if completed all stages."""
    from spaced_repetition import next_review_date, DEFAULT_REVIEW_INTERVALS
    conn = get_connection()
    cursor = conn.cursor()

    if user_id is not None:
        cursor.execute("""
            SELECT qr.*, q.category_id 
            FROM quiz_reviews qr 
            JOIN quizzes q ON qr.quiz_id = q.id 
            WHERE qr.id = ? AND qr.user_id = ?
        """, (review_id, user_id))
    else:
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


def advance_quiz_review_for_quiz(quiz_id: int, user_id: int = 6099429826) -> bool:
    """
    Finds the active review for this quiz and user (if any) and advances it to next stage.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM quiz_reviews WHERE quiz_id = ? AND user_id = ?", (quiz_id, user_id))
    row = cursor.fetchone()
    conn.close()
    if row:
        advance_quiz_review(row["id"], user_id=user_id)
        return True
    return False


# ─── Weak Questions ───────────────────────────────────────────────────────────

def add_or_reset_weak_question(quiz_id: int, question_id: int, user_id: int = 6099429826):
    """Add a wrong question to weak list for a specific user — always due today for immediate review."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id FROM weak_questions WHERE user_id = ? AND quiz_id = ? AND question_id = ?""",
        (user_id, quiz_id, question_id),
    )
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            """UPDATE weak_questions SET stage = 0, next_review_date = date('now')
               WHERE id = ?""",
            (existing["id"],),
        )
    else:
        cursor.execute(
            """INSERT INTO weak_questions (user_id, quiz_id, question_id, stage, next_review_date)
               VALUES (?, ?, ?, 0, date('now'))""",
            (user_id, quiz_id, question_id),
        )
    conn.commit()
    conn.close()


def get_due_weak_questions(user_id: int = None) -> list:
    """Returns weak questions due today or earlier for a user in Riyadh timezone."""
    conn = get_connection()
    cursor = conn.cursor()
    today_iso = get_riyadh_today_iso()
    if user_id is not None:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               WHERE wq.user_id = ? AND wq.next_review_date <= ?
               ORDER BY wq.next_review_date""",
            (user_id, today_iso),
        )
    else:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               WHERE wq.next_review_date <= ?
               ORDER BY wq.next_review_date""",
            (today_iso,)
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_weak_questions(user_id: int = None) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               WHERE wq.user_id = ?
               ORDER BY q.name""",
            (user_id,),
        )
    else:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               ORDER BY q.name"""
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_due_all_weak_questions_sorted(user_id: int = None) -> list:
    """Returns all due weak questions in Riyadh timezone sorted: newest added (highest id) first."""
    conn = get_connection()
    cursor = conn.cursor()
    today_iso = get_riyadh_today_iso()
    if user_id is not None:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               WHERE wq.user_id = ? AND wq.next_review_date <= ?
               ORDER BY wq.id DESC""",
            (user_id, today_iso),
        )
    else:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               WHERE wq.next_review_date <= ?
               ORDER BY wq.id DESC""",
            (today_iso,)
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_weak_questions_sorted_for_practice(user_id: int = None) -> list:
    """Returns ALL weak questions sorted newest first, for practice."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               WHERE wq.user_id = ?
               ORDER BY wq.id DESC""",
            (user_id,),
        )
    else:
        cursor.execute(
            """SELECT wq.*, q.name as quiz_name
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               ORDER BY wq.id DESC"""
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_weak_questions_by_quiz(quiz_id: int, user_id: int = None) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute(
            "SELECT * FROM weak_questions WHERE quiz_id = ? AND user_id = ?",
            (quiz_id, user_id),
        )
    else:
        cursor.execute("SELECT * FROM weak_questions WHERE quiz_id = ?", (quiz_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_quizzes_with_weak_questions(user_id: int = None) -> list:
    """Returns list of quizzes that have active weak questions for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute(
            """SELECT q.id as quiz_id, q.name as quiz_name, COUNT(wq.id) as count
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               WHERE wq.user_id = ?
               GROUP BY q.id, q.name
               ORDER BY count DESC""",
            (user_id,),
        )
    else:
        cursor.execute(
            """SELECT q.id as quiz_id, q.name as quiz_name, COUNT(wq.id) as count
               FROM weak_questions wq
               JOIN quizzes q ON wq.quiz_id = q.id
               GROUP BY q.id, q.name
               ORDER BY count DESC"""
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def advance_weak_question(weak_id: int):
    """Advance weak question for daily spaced repetition (repeats daily, never deleted)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM weak_questions WHERE id = ?", (weak_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
        
    wq = dict(row)
    new_stage = (wq.get("stage", 0) or 0) + 1
    # Schedule for tomorrow so weak questions repeat daily
    next_date = (date.today() + timedelta(days=1)).isoformat()

    cursor.execute(
        "UPDATE weak_questions SET stage = ?, next_review_date = ? WHERE id = ?",
        (new_stage, next_date, weak_id),
    )
    conn.commit()
    conn.close()


def remove_weak_question(weak_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM weak_questions WHERE id = ?", (weak_id,))
    conn.commit()
    conn.close()


# ─── Active Session (Scoped to user_id) ───────────────────────────────────────

def save_session(session_type: str, quiz_id: int, review_id: int | None,
                 question_ids: list, current_index: int = 0,
                 correct_count: int = 0, wrong_ids: list = None, poll_id: str = None,
                 session_message_ids: list = None, user_id: int = 6099429826):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO active_session
           (user_id, session_type, quiz_id, review_id, question_ids, current_index, correct_count, wrong_ids, poll_id, session_message_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               session_type = excluded.session_type,
               quiz_id = excluded.quiz_id,
               review_id = excluded.review_id,
               question_ids = excluded.question_ids,
               current_index = excluded.current_index,
               correct_count = excluded.correct_count,
               wrong_ids = excluded.wrong_ids,
               poll_id = excluded.poll_id,
               session_message_ids = excluded.session_message_ids""",
        (
            user_id,
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


def get_session(user_id: int = None, poll_id: str = None) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute("SELECT * FROM active_session WHERE user_id = ?", (user_id,))
    elif poll_id is not None:
        cursor.execute("SELECT * FROM active_session WHERE poll_id = ?", (poll_id,))
    else:
        # Fallback to first session if neither is given
        cursor.execute("SELECT * FROM active_session LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        row = dict(row)
        row["question_ids"] = json.loads(row["question_ids"])
        row["wrong_ids"] = json.loads(row["wrong_ids"])
        try:
            row["session_message_ids"] = json.loads(row.get("session_message_ids") or "[]")
        except Exception:
            row["session_message_ids"] = []
        return row
    return None


def update_session(current_index: int, correct_count: int, wrong_ids: list, poll_id: str = None,
                   session_message_ids: list = None, user_id: int = 6099429826):
    conn = get_connection()
    cursor = conn.cursor()
    if session_message_ids is None:
        cursor.execute(
            """UPDATE active_session
               SET current_index = ?, correct_count = ?, wrong_ids = ?, poll_id = ?
               WHERE user_id = ?""",
            (current_index, correct_count, json.dumps(wrong_ids), poll_id, user_id),
        )
    else:
        cursor.execute(
            """UPDATE active_session
               SET current_index = ?, correct_count = ?, wrong_ids = ?, poll_id = ?, session_message_ids = ?
               WHERE user_id = ?""",
            (current_index, correct_count, json.dumps(wrong_ids), poll_id, json.dumps(session_message_ids), user_id),
        )
    conn.commit()
    conn.close()


def clear_session(user_id: int = None):
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is not None:
        cursor.execute("DELETE FROM active_session WHERE user_id = ?", (user_id,))
    else:
        cursor.execute("DELETE FROM active_session")
    conn.commit()
    conn.close()


# ─── Sessions Log & Stats ─────────────────────────────────────────────────────

def log_session(quiz_id, session_type: str, total: int, correct: int, wrong: int, user_id: int = 6099429826):
    """Log a completed session for stats."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO quiz_sessions_log (user_id, quiz_id, session_type, total, correct, wrong)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, quiz_id, session_type, total, correct, wrong),
    )
    conn.commit()
    conn.close()


def get_my_stats(user_id: int = None) -> dict:
    """Returns comprehensive stats scoped to a user (or all if None)."""
    conn = get_connection()
    cursor = conn.cursor()

    user_clause = "AND user_id = ?" if user_id is not None else ""
    params = (user_id,) if user_id is not None else ()

    # All-time totals
    cursor.execute(
        f"""SELECT COUNT(*) as sessions, COALESCE(SUM(total), 0) as total, 
                  COALESCE(SUM(correct), 0) as correct, COALESCE(SUM(wrong), 0) as wrong
           FROM quiz_sessions_log
           WHERE session_type NOT IN ('practice') {user_clause}""",
        params,
    )
    alltime = dict(cursor.fetchone())
    total_q = alltime["total"] or 0
    correct_q = alltime["correct"] or 0
    accuracy = int((correct_q / total_q) * 100) if total_q > 0 else 0

    # Last 7 days breakdown
    cursor.execute(
        f"""SELECT session_date, 
                  COALESCE(SUM(total), 0) as day_total, 
                  COALESCE(SUM(correct), 0) as day_correct
           FROM quiz_sessions_log
           WHERE session_date >= date('now', '-6 days') {user_clause}
           GROUP BY session_date
           ORDER BY session_date ASC""",
        params,
    )
    daily_rows = {r["session_date"]: dict(r) for r in cursor.fetchall()}
    
    weekly = []
    # Build continuous 7 days list ending today
    today = date.today()
    arabic_days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.isoformat()
        day_info = daily_rows.get(d_str, {"day_total": 0, "day_correct": 0})
        d_tot = day_info["day_total"]
        d_cor = day_info["day_correct"]
        d_pct = int((d_cor / d_tot) * 100) if d_tot > 0 else 0
        day_name = arabic_days[d.weekday()]
        weekly.append({
            "date": d_str,
            "day_name": day_name,
            "total": d_tot,
            "correct": d_cor,
            "accuracy": d_pct
        })

    # Weak questions count
    if user_id is not None:
        cursor.execute("SELECT COUNT(*) as cnt FROM weak_questions WHERE user_id = ?", (user_id,))
        total_weak = cursor.fetchone()["cnt"]
        cursor.execute("SELECT COUNT(*) as cnt FROM weak_questions WHERE user_id = ? AND next_review_date <= date('now')", (user_id,))
        due_weak = cursor.fetchone()["cnt"]
    else:
        cursor.execute("SELECT COUNT(*) as cnt FROM weak_questions")
        total_weak = cursor.fetchone()["cnt"]
        cursor.execute("SELECT COUNT(*) as cnt FROM weak_questions WHERE next_review_date <= date('now')")
        due_weak = cursor.fetchone()["cnt"]

    # Spaced Repetition reviews count & stage breakdown
    stage_breakdown = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    if user_id is not None:
        cursor.execute("SELECT COUNT(*) as cnt FROM quiz_reviews WHERE user_id = ?", (user_id,))
        active_reviews = cursor.fetchone()["cnt"]
        cursor.execute("SELECT COUNT(*) as cnt FROM quiz_reviews WHERE user_id = ? AND next_review_date <= date('now')", (user_id,))
        due_reviews = cursor.fetchone()["cnt"]
        cursor.execute("SELECT stage, COUNT(*) as cnt FROM quiz_reviews WHERE user_id = ? GROUP BY stage", (user_id,))
        for r in cursor.fetchall():
            stage_breakdown[r["stage"]] = r["cnt"]
    else:
        cursor.execute("SELECT COUNT(*) as cnt FROM quiz_reviews")
        active_reviews = cursor.fetchone()["cnt"]
        cursor.execute("SELECT COUNT(*) as cnt FROM quiz_reviews WHERE next_review_date <= date('now')")
        due_reviews = cursor.fetchone()["cnt"]
        cursor.execute("SELECT stage, COUNT(*) as cnt FROM quiz_reviews GROUP BY stage")
        for r in cursor.fetchall():
            stage_breakdown[r["stage"]] = r["cnt"]

    # Mastered quizzes count
    if user_id is not None:
        cursor.execute("SELECT COUNT(*) as cnt FROM quiz_weak_mastery WHERE user_id = ? AND is_mastered = 1", (user_id,))
    else:
        cursor.execute("SELECT COUNT(*) as cnt FROM quiz_weak_mastery WHERE is_mastered = 1")
    mastered_count = cursor.fetchone()["cnt"]

    # Total public quizzes count
    cursor.execute("SELECT COUNT(*) as cnt FROM quizzes WHERE is_public = 1")
    total_quizzes = cursor.fetchone()["cnt"]

    conn.close()

    return {
        "sessions": alltime["sessions"] or 0,
        "total": total_q,
        "correct": correct_q,
        "wrong": alltime["wrong"] or 0,
        "accuracy": accuracy,
        "weekly": weekly,
        "total_weak": total_weak,
        "due_weak": due_weak,
        "active_reviews": active_reviews,
        "due_reviews": due_reviews,
        "mastered_count": mastered_count,
        "stage_breakdown": stage_breakdown,
        "total_quizzes": total_quizzes,
    }


# ─── Bot State & Chat IDs ─────────────────────────────────────────────────────

def set_last_message_id(chat_id: int, message_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO bot_state (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (f"last_msg_{chat_id}", str(message_id)),
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
        (f"chat_{chat_id}", str(chat_id)),
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


# ─── Chat Message Tracking & Bulk Cleaning ────────────────────────────────────

def track_chat_message(chat_id: int, message_id: int):
    """Record a message_id (user or bot) for this chat so it can be cleaned up later."""
    if not chat_id or not message_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO chat_history_ids (chat_id, message_id) VALUES (?, ?)",
            (chat_id, message_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def get_and_clear_chat_messages(chat_id: int, keep_message_id: int = None) -> list[int]:
    """Retrieve all tracked message IDs for this chat (except keep_message_id) and clear them from DB."""
    if not chat_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    if keep_message_id:
        cursor.execute("SELECT message_id FROM chat_history_ids WHERE chat_id = ? AND message_id != ?", (chat_id, keep_message_id))
    else:
        cursor.execute("SELECT message_id FROM chat_history_ids WHERE chat_id = ?", (chat_id,))
    rows = cursor.fetchall()
    msg_ids = [row["message_id"] for row in rows]
    
    if keep_message_id:
        cursor.execute("DELETE FROM chat_history_ids WHERE chat_id = ? AND message_id != ?", (chat_id, keep_message_id))
    else:
        cursor.execute("DELETE FROM chat_history_ids WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()
    return msg_ids


def clear_chat_history(chat_id: int):
    """Clear all tracked message IDs for a chat."""
    if not chat_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history_ids WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


# ─── Quiz Weak Mastery (5 Consecutive Perfect Scores) ─────────────────────────

def record_quiz_mastery_run(quiz_id: int, user_id: int = 6099429826, is_perfect: bool = False) -> tuple[int, bool]:
    """
    Updates consecutive perfect runs for a quiz.
    If is_perfect is True, increments streak. If streak >= 5, sets is_mastered = 1.
    If is_perfect is False, resets streak to 0 and unsets is_mastered = 0.
    Returns (current_streak, is_mastered_now).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT consecutive_perfect, is_mastered FROM quiz_weak_mastery WHERE user_id = ? AND quiz_id = ?",
        (user_id, quiz_id)
    )
    row = cursor.fetchone()
    
    if not row:
        streak = 1 if is_perfect else 0
        mastered = 1 if streak >= 5 else 0
        cursor.execute(
            """INSERT INTO quiz_weak_mastery (user_id, quiz_id, consecutive_perfect, is_mastered, mastered_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (user_id, quiz_id, streak, mastered)
        )
    else:
        prev_streak = row["consecutive_perfect"] or 0
        if is_perfect:
            streak = prev_streak + 1
            mastered = 1 if streak >= 5 else 0
        else:
            streak = 0
            mastered = 0
            
        cursor.execute(
            """UPDATE quiz_weak_mastery 
               SET consecutive_perfect = ?, is_mastered = ?, mastered_at = CURRENT_TIMESTAMP
               WHERE user_id = ? AND quiz_id = ?""",
            (streak, mastered, user_id, quiz_id)
        )
        
    conn.commit()
    conn.close()
    return streak, bool(mastered)


def get_mastered_weak_quiz_ids(user_id: int = 6099429826) -> set[int]:
    """Returns set of quiz_ids that are mastered (5 consecutive 100% scores)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT quiz_id FROM quiz_weak_mastery WHERE user_id = ? AND is_mastered = 1",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return {r["quiz_id"] for r in rows}


def get_quiz_mastery_info(quiz_id: int, user_id: int = 6099429826) -> dict:
    """Returns mastery streak and status for a quiz."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT consecutive_perfect, is_mastered FROM quiz_weak_mastery WHERE user_id = ? AND quiz_id = ?",
        (user_id, quiz_id)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"streak": row["consecutive_perfect"] or 0, "is_mastered": bool(row["is_mastered"])}
    return {"streak": 0, "is_mastered": False}


