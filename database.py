import sqlite3
import contextlib
import logging
from datetime import datetime, timedelta
import config
from spaced_repetition import (
    get_riyadh_today,
    get_riyadh_today_iso,
    get_riyadh_now,
    distribute_lessons,
    calculate_next_review,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def get_db_connection():
    """Thread-safe context manager for SQLite with WAL mode and foreign keys."""
    conn = sqlite3.connect(config.DB_PATH, timeout=20.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initializes clean V3 multi-tenant database schema and seeds Super Admin."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Users table (Multi-tenancy and Subscription management)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                status TEXT DEFAULT 'trial',
                trial_started_at TIMESTAMP DEFAULT NULL,
                subscription_expires_at TIMESTAMP DEFAULT NULL,
                is_admin INTEGER DEFAULT 0,
                reminder_hour INTEGER DEFAULT 5,
                reminder_minute INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("PRAGMA table_info(users)")
        u_cols = [row["name"] for row in cursor.fetchall()]
        if "user_id" in u_cols and "id" not in u_cols:
            cursor.execute("ALTER TABLE users RENAME COLUMN user_id TO id")
            cursor.execute("PRAGMA table_info(users)")
            u_cols = [row["name"] for row in cursor.fetchall()]

        user_migrations = [
            ("status", "TEXT DEFAULT 'trial'"),
            ("trial_started_at", "TIMESTAMP DEFAULT NULL"),
            ("subscription_expires_at", "TIMESTAMP DEFAULT NULL"),
            ("is_admin", "INTEGER DEFAULT 0"),
            ("reminder_hour", "INTEGER DEFAULT 5"),
            ("reminder_minute", "INTEGER DEFAULT 0"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ]
        for col_name, col_def in user_migrations:
            if col_name not in u_cols:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

        # 2. Subjects table (Subjects and curricula)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '📖',
                total_lessons INTEGER NOT NULL,
                target_days INTEGER NOT NULL,
                start_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_user ON subjects(user_id)")

        # 3. Lessons table (Sequential lesson roadmap)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                lesson_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                scheduled_day INTEGER NOT NULL,
                is_completed INTEGER DEFAULT 0,
                completed_at TIMESTAMP DEFAULT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_subject ON lessons(subject_id, lesson_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_user_status ON lessons(user_id, is_completed, scheduled_day)")

        # 4. Reviews table (Conditional Spaced Repetition generated upon completion)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                stage INTEGER NOT NULL,
                due_date DATE NOT NULL,
                is_completed INTEGER DEFAULT 0,
                completed_at TIMESTAMP DEFAULT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_due ON reviews(user_id, due_date, is_completed)")

        # 5. Telegram Stars & Payments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                charge_id TEXT UNIQUE,
                amount_stars INTEGER NOT NULL,
                days INTEGER NOT NULL,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)")

        # 6. Settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # 7. Chat History IDs table for clean UI
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history_ids (
                chat_id INTEGER,
                message_id INTEGER,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_hist ON chat_history_ids(chat_id)")

        # Seed Super Admin / Owner as Lifetime VIP
        cursor.execute("""
            INSERT INTO users (id, username, full_name, status, is_admin, subscription_expires_at)
            VALUES (?, 'Owner', 'Super Admin', 'lifetime', 1, '2099-12-31 23:59:59')
            ON CONFLICT(id) DO UPDATE SET 
                is_admin = 1, 
                status = 'lifetime', 
                subscription_expires_at = '2099-12-31 23:59:59'
        """, (config.ADMIN_USER_ID,))

        # Drop legacy v1/v2 unused tables safely
        legacy_tables = [
            "items", "folders", "daily_stats", "active_session", "bot_state",
            "categories", "question_reports", "quiz_reviews", "weak_questions",
            "quiz_weak_mastery", "quiz_sessions_log", "questions", "quizzes",
            "reviews_log"
        ]
        cursor.execute("PRAGMA foreign_keys = OFF")
        for lt in legacy_tables:
            try:
                cursor.execute(f"DROP TABLE IF EXISTS [{lt}]")
            except Exception:
                pass
        cursor.execute("PRAGMA foreign_keys = ON")


# ─── Settings Helpers ─────────────────────────────────────────────────────────

def get_setting(key: str, default: str = None) -> str:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


# ─── User & Subscription Management (Anti-Exploit) ───────────────────────────

def get_or_create_user(user_id: int, username: str = "", full_name: str = "") -> dict:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        if row:
            if username or full_name:
                c.execute(
                    "UPDATE users SET username = COALESCE(NULLIF(?, ''), username), full_name = COALESCE(NULLIF(?, ''), full_name) WHERE id = ?",
                    (username, full_name, user_id),
                )
            return dict(row)

        c.execute(
            """INSERT INTO users (id, username, full_name, status, is_admin)
               VALUES (?, ?, ?, 'trial', ?)""",
            (user_id, username, full_name, 1 if user_id == config.ADMIN_USER_ID else 0),
        )
        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return dict(c.fetchone())


def get_user(user_id: int) -> dict | None:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        return dict(row) if row else None


def check_user_subscription(user_id: int) -> dict:
    """
    Checks user subscription status.
    Anti-exploit rule: Trial lasts 7 days starting from trial_started_at.
    """
    user = get_or_create_user(user_id)
    if user.get("is_admin") or user_id == config.ADMIN_USER_ID:
        return {"status": "lifetime", "is_active": True, "is_trial": False, "days_left": 9999, "is_admin": True}

    status = user.get("status", "trial")
    trial_start = user.get("trial_started_at")
    sub_expires = user.get("subscription_expires_at")
    now = get_riyadh_now()

    # Lifetime subscription
    if status == "lifetime":
        return {"status": "lifetime", "is_active": True, "is_trial": False, "days_left": 9999, "is_admin": False}

    # Active paid subscription
    if status == "active" and sub_expires:
        exp_dt = datetime.fromisoformat(str(sub_expires)[:19]).replace(tzinfo=now.tzinfo)
        if exp_dt > now:
            days_left = max(1, (exp_dt.date() - now.date()).days)
            return {"status": "active", "is_active": True, "is_trial": False, "days_left": days_left, "is_admin": False}

    # Trial mode
    if trial_start:
        start_dt = datetime.fromisoformat(str(trial_start)[:19]).replace(tzinfo=now.tzinfo)
        trial_end = start_dt + timedelta(days=7)
        if now < trial_end:
            days_left = max(1, (trial_end.date() - now.date()).days)
            return {"status": "trial", "is_active": True, "is_trial": True, "days_left": days_left, "is_admin": False}
        else:
            return {"status": "expired", "is_active": False, "is_trial": False, "days_left": 0, "is_admin": False}
    else:
        # User has not started their trial yet
        return {"status": "trial", "is_active": True, "is_trial": True, "days_left": 7, "is_admin": False, "not_started": True}


def activate_trial_if_needed(user_id: int):
    """
    Locks trial_started_at upon creating the first schedule.
    Anti-exploit: never resets if already set!
    """
    now_iso = get_riyadh_now().strftime("%Y-%m-%d %H:%M:%S")
    exp_iso = (get_riyadh_now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            UPDATE users 
            SET trial_started_at = ?, subscription_expires_at = ?
            WHERE id = ? AND trial_started_at IS NULL
        """, (now_iso, exp_iso, user_id))


def extend_user_subscription(user_id: int, days: int, stars_paid: int = 0, payload: str = "") -> dict:
    """Extends active subscription by N days and records payment."""
    now = get_riyadh_now()
    user = get_or_create_user(user_id)
    cur_exp = user.get("subscription_expires_at")
    
    if cur_exp:
        try:
            cur_dt = datetime.fromisoformat(str(cur_exp)[:19]).replace(tzinfo=now.tzinfo)
            base_dt = cur_dt if cur_dt > now else now
        except Exception:
            base_dt = now
    else:
        base_dt = now

    new_exp_dt = base_dt + timedelta(days=days)
    new_exp_str = new_exp_dt.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            UPDATE users 
            SET status = 'active', subscription_expires_at = ? 
            WHERE id = ?
        """, (new_exp_str, user_id))

        if stars_paid > 0:
            import uuid
            charge_id = f"star_{uuid.uuid4().hex[:12]}"
            c.execute("""
                INSERT INTO payments (user_id, charge_id, amount_stars, days, payload)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, charge_id, stars_paid, days, payload))

    return {"user_id": user_id, "new_expires_at": new_exp_str, "days_added": days}


def update_user_reminder_time(user_id: int, hour: int, minute: int):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET reminder_hour = ?, reminder_minute = ? WHERE id = ?", (hour, minute, user_id))


def get_all_users_for_reminders() -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, reminder_hour, reminder_minute FROM users WHERE id IS NOT NULL")
        return [dict(r) for r in c.fetchall()]


def get_admin_platform_stats() -> dict:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) as cnt FROM users")
        total_users = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM subjects")
        total_subjects = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM lessons")
        total_lessons = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM lessons WHERE is_completed = 1")
        completed_lessons = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM reviews WHERE is_completed = 1")
        completed_reviews = c.fetchone()["cnt"]

        c.execute("SELECT COALESCE(sum(amount_stars), 0) as total FROM payments")
        total_stars = c.fetchone()["total"]

        c.execute("SELECT count(*) as cnt FROM users WHERE status = 'active' OR is_admin = 1")
        active_subscribers = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM users WHERE status = 'trial'")
        trial_users = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM users WHERE status = 'expired'")
        expired_users = c.fetchone()["cnt"]

    return {
        "total_users": total_users,
        "active_subscribers": active_subscribers,
        "trial_users": trial_users,
        "expired_users": expired_users,
        "total_subjects": total_subjects,
        "total_lessons": total_lessons,
        "completed_lessons": completed_lessons,
        "completed_reviews": completed_reviews,
        "total_stars": total_stars,
    }


# ─── Subjects & Lessons Management ───────────────────────────────────────────

def create_subject(
    user_id: int,
    name: str,
    total_lessons: int,
    target_days: int,
    lesson_titles: list[str] = None,
    icon: str = "📖",
) -> int:
    """
    Creates a new subject and distributes lessons across target_days.
    Automatically ensures user exists and locks anti-exploit trial start.
    """
    get_or_create_user(user_id)
    activate_trial_if_needed(user_id)
    today_iso = get_riyadh_today_iso()
    distribution = distribute_lessons(total_lessons, target_days)

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO subjects (user_id, name, icon, total_lessons, target_days, start_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, name, icon, total_lessons, target_days, today_iso))
        subject_id = c.lastrowid

        # Insert lessons
        for i in range(total_lessons):
            order = i + 1
            if lesson_titles and i < len(lesson_titles) and lesson_titles[i].strip():
                title = lesson_titles[i].strip()
            else:
                title = f"الدرس {order}"
            
            day = distribution[i]
            c.execute("""
                INSERT INTO lessons (subject_id, user_id, lesson_order, title, scheduled_day)
                VALUES (?, ?, ?, ?, ?)
            """, (subject_id, user_id, order, title, day))

    return subject_id


def get_user_subjects(user_id: int) -> list[dict]:
    today = get_riyadh_today()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT s.*,
                   COUNT(l.id) as lessons_count,
                   SUM(CASE WHEN l.is_completed = 1 THEN 1 ELSE 0 END) as completed_count
            FROM subjects s
            LEFT JOIN lessons l ON s.id = l.subject_id
            WHERE s.user_id = ?
            GROUP BY s.id
            ORDER BY s.id DESC
        """, (user_id,))
        rows = c.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                s_date = datetime.fromisoformat(d["start_date"]).date()
                days_passed = (today - s_date).days
                d["current_day"] = max(1, days_passed + 1)
                d["days_remaining"] = max(0, d["target_days"] - days_passed)
            except Exception:
                d["current_day"] = 1
                d["days_remaining"] = d["target_days"]
            result.append(d)
        return result


def get_subject(subject_id: int, user_id: int) -> dict | None:
    today = get_riyadh_today()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT s.*,
                   COUNT(l.id) as lessons_count,
                   SUM(CASE WHEN l.is_completed = 1 THEN 1 ELSE 0 END) as completed_count
            FROM subjects s
            LEFT JOIN lessons l ON s.id = l.subject_id
            WHERE s.id = ? AND s.user_id = ?
            GROUP BY s.id
        """, (subject_id, user_id))
        row = c.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            s_date = datetime.fromisoformat(d["start_date"]).date()
            days_passed = (today - s_date).days
            d["current_day"] = max(1, days_passed + 1)
            d["days_remaining"] = max(0, d["target_days"] - days_passed)
        except Exception:
            d["current_day"] = 1
            d["days_remaining"] = d["target_days"]
        return d


def get_subject_lessons(subject_id: int, user_id: int) -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT l.*,
                   (SELECT count(*) FROM reviews r WHERE r.lesson_id = l.id) as review_count
            FROM lessons l
            WHERE l.subject_id = ? AND l.user_id = ?
            ORDER BY l.lesson_order ASC
        """, (subject_id, user_id))
        return [dict(r) for r in c.fetchall()]


def delete_subject(subject_id: int, user_id: int) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM subjects WHERE id = ? AND user_id = ?", (subject_id, user_id))
        return c.rowcount > 0


# ─── Today Tasks & Conditional Active Recall ─────────────────────────────────

def get_today_tasks(user_id: int) -> dict:
    """
    Calculates today's actionable study load:
    1. New lessons scheduled for today (or prior overdue uncompleted lessons).
    2. Due active recall reviews for previously completed lessons.
    """
    today_iso = get_riyadh_today_iso()
    today = get_riyadh_today()

    with get_db_connection() as conn:
        c = conn.cursor()

        # 1. New lessons to study today
        c.execute("SELECT * FROM subjects WHERE user_id = ?", (user_id,))
        subjects = [dict(s) for s in c.fetchall()]

        today_lessons = []
        for s in subjects:
            try:
                s_date = datetime.fromisoformat(s["start_date"]).date()
                cur_day = (today - s_date).days + 1
            except Exception:
                cur_day = 1

            # Fetch lessons for current day (or pending overdue lessons)
            c.execute("""
                SELECT l.*, s.name as subject_name, s.icon as subject_icon
                FROM lessons l
                JOIN subjects s ON l.subject_id = s.id
                WHERE l.subject_id = ? AND l.user_id = ?
                  AND l.scheduled_day <= ?
                  AND l.is_completed = 0
                ORDER BY l.lesson_order ASC
            """, (s["id"], user_id, cur_day))
            today_lessons.extend([dict(r) for r in c.fetchall()])

        # 2. Due active recall reviews
        c.execute("""
            SELECT r.*, l.title as lesson_title, l.lesson_order, s.name as subject_name, s.icon as subject_icon
            FROM reviews r
            JOIN lessons l ON r.lesson_id = l.id
            JOIN subjects s ON l.subject_id = s.id
            WHERE r.user_id = ?
              AND r.due_date <= ?
              AND r.is_completed = 0
            ORDER BY r.due_date ASC, r.id ASC
        """, (user_id, today_iso))
        due_reviews = [dict(r) for r in c.fetchall()]

    return {
        "new_lessons": today_lessons,
        "due_reviews": due_reviews,
        "total_tasks": len(today_lessons) + len(due_reviews),
    }


def complete_lesson(lesson_id: int, user_id: int) -> dict:
    """
    Marks a lesson as completed and triggers its first conditional review.
    Anti-exploit / smart logic: review is scheduled ONLY AFTER actual completion!
    """
    now_iso = get_riyadh_now().strftime("%Y-%m-%d %H:%M:%S")
    review_info = calculate_next_review(current_stage=0)

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            UPDATE lessons 
            SET is_completed = 1, completed_at = ? 
            WHERE id = ? AND user_id = ?
        """, (now_iso, lesson_id, user_id))

        if c.rowcount == 0:
            return {}

        # Insert first conditional active recall review (+3 days)
        c.execute("""
            INSERT INTO reviews (lesson_id, user_id, stage, due_date, is_completed)
            VALUES (?, ?, ?, ?, 0)
        """, (lesson_id, user_id, review_info["next_stage"], review_info["due_date"]))

        c.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,))
        lesson = dict(c.fetchone())

    return {
        "lesson": lesson,
        "next_review_stage": review_info["next_stage"],
        "next_review_due": review_info["due_date"],
    }


def complete_review(review_id: int, user_id: int) -> dict:
    """Marks review completed and schedules next stage (if any)."""
    now_iso = get_riyadh_now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM reviews WHERE id = ? AND user_id = ?", (review_id, user_id))
        rev = c.fetchone()
        if not rev:
            return {}

        rev_dict = dict(rev)
        c.execute("""
            UPDATE reviews 
            SET is_completed = 1, completed_at = ? 
            WHERE id = ? AND user_id = ?
        """, (now_iso, review_id, user_id))

        cur_stage = rev_dict["stage"]
        next_info = calculate_next_review(cur_stage)

        if not next_info["is_mastered"]:
            c.execute("""
                INSERT INTO reviews (lesson_id, user_id, stage, due_date, is_completed)
                VALUES (?, ?, ?, ?, 0)
            """, (rev_dict["lesson_id"], user_id, next_info["next_stage"], next_info["due_date"]))

    return {
        "completed_stage": cur_stage,
        "next_stage": next_info["next_stage"],
        "is_mastered": next_info["is_mastered"],
        "next_due": next_info.get("due_date"),
    }


def reschedule_subject(subject_id: int, user_id: int, target_days: int = None) -> dict:
    """
    Smart catch-up / re-align algorithm:
    Redistributes remaining uncompleted lessons across available days starting today.
    """
    today_iso = get_riyadh_today_iso()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM subjects WHERE id = ? AND user_id = ?", (subject_id, user_id))
        subject = c.fetchone()
        if not subject:
            return {}
        s_dict = dict(subject)

        c.execute("""
            SELECT * FROM lessons 
            WHERE subject_id = ? AND user_id = ? AND is_completed = 0
            ORDER BY lesson_order ASC
        """, (subject_id, user_id))
        remaining_lessons = [dict(r) for r in c.fetchall()]

        if not remaining_lessons:
            return {"remaining": 0, "message": "جميع الدروس مكتملة بالفعل!"}

        days = target_days or s_dict["target_days"]
        days = max(1, days)
        new_distribution = distribute_lessons(len(remaining_lessons), days)

        for idx, l in enumerate(remaining_lessons):
            new_day = new_distribution[idx]
            c.execute("UPDATE lessons SET scheduled_day = ? WHERE id = ?", (new_day, l["id"]))

        c.execute("UPDATE subjects SET start_date = ?, target_days = ? WHERE id = ?", (today_iso, days, subject_id))

    return {
        "remaining_count": len(remaining_lessons),
        "new_target_days": days,
        "message": f"تمت إعادة توزيع {len(remaining_lessons)} درس متبقي على {days} يوم بنجاح! 🔄",
    }


# ─── Chat Message Tracking for Single-Screen UI ──────────────────────────────

def track_chat_message(chat_id: int, message_id: int):
    if not chat_id or not message_id:
        return
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO chat_history_ids (chat_id, message_id) VALUES (?, ?)", (chat_id, message_id))


def get_and_clear_chat_messages(chat_id: int, keep_message_id: int = None) -> list[int]:
    if not chat_id:
        return []
    with get_db_connection() as conn:
        c = conn.cursor()
        if keep_message_id:
            c.execute(
                "SELECT message_id FROM chat_history_ids WHERE chat_id = ? AND message_id != ?",
                (chat_id, keep_message_id),
            )
        else:
            c.execute("SELECT message_id FROM chat_history_ids WHERE chat_id = ?", (chat_id,))
        rows = c.fetchall()
        msg_ids = [row["message_id"] for row in rows]

        if keep_message_id:
            c.execute(
                "DELETE FROM chat_history_ids WHERE chat_id = ? AND message_id != ?",
                (chat_id, keep_message_id),
            )
        else:
            c.execute("DELETE FROM chat_history_ids WHERE chat_id = ?", (chat_id,))

    return msg_ids
