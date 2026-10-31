import sqlite3
import contextlib
import logging
from datetime import datetime, timedelta
import config
from spaced_repetition import (
    get_riyadh_today_iso,
    get_riyadh_now,
    get_initial_review_date,
    calculate_next_schedule,
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
    """Initializes clean multi-tenant database schema, handles migrations and seeds Super Admin."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Users table (Multi-tenancy and Subscription management)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                status TEXT DEFAULT 'trial',
                trial_started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                subscription_expires_at TIMESTAMP,
                is_admin INTEGER DEFAULT 0,
                active_folder_id INTEGER DEFAULT NULL,
                reminder_hour INTEGER DEFAULT 5,
                reminder_minute INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: Ensure all columns exist in users
        cursor.execute("PRAGMA table_info(users)")
        u_cols = [row["name"] for row in cursor.fetchall()]
        if "active_folder_id" not in u_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN active_folder_id INTEGER DEFAULT NULL")
        if "reminder_hour" not in u_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN reminder_hour INTEGER DEFAULT 5")
        if "reminder_minute" not in u_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN reminder_minute INTEGER DEFAULT 0")

        # 2. Folders table (with user_id and parent_id for nested subfolders)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '📁',
                parent_id INTEGER DEFAULT NULL REFERENCES folders(id) ON DELETE CASCADE,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: Ensure user_id and parent_id exist in folders
        cursor.execute("PRAGMA table_info(folders)")
        f_cols = [row["name"] for row in cursor.fetchall()]
        if "user_id" not in f_cols:
            cursor.execute("ALTER TABLE folders ADD COLUMN user_id INTEGER DEFAULT NULL REFERENCES users(id) ON DELETE CASCADE")
        if "parent_id" not in f_cols:
            cursor.execute("ALTER TABLE folders ADD COLUMN parent_id INTEGER DEFAULT NULL REFERENCES folders(id) ON DELETE CASCADE")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_user_parent ON folders(user_id, parent_id)")

        # 3. Items / Flashcards table (scoped by user_id)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                folder_id INTEGER DEFAULT NULL REFERENCES folders(id) ON DELETE SET NULL,
                item_type TEXT NOT NULL,
                review_mode TEXT DEFAULT 'qa',
                title TEXT,
                content TEXT NOT NULL,
                solution TEXT,
                stage INTEGER DEFAULT 0,
                next_review_date DATE NOT NULL,
                ease_factor REAL DEFAULT 2.5,
                lapses INTEGER DEFAULT 0,
                is_mastered INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: Ensure user_id exists in items
        cursor.execute("PRAGMA table_info(items)")
        i_cols = [row["name"] for row in cursor.fetchall()]
        if "user_id" not in i_cols:
            cursor.execute("ALTER TABLE items ADD COLUMN user_id INTEGER DEFAULT NULL REFERENCES users(id) ON DELETE CASCADE")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_user_due ON items(user_id, next_review_date, is_mastered)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_user_folder ON items(user_id, folder_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_user_type ON items(user_id, item_type)")

        # 4. Reviews Log table (scoped by user_id)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                rating TEXT NOT NULL,
                old_stage INTEGER NOT NULL,
                new_stage INTEGER NOT NULL,
                reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: Ensure user_id exists in reviews_log
        cursor.execute("PRAGMA table_info(reviews_log)")
        r_cols = [row["name"] for row in cursor.fetchall()]
        if "user_id" not in r_cols:
            cursor.execute("ALTER TABLE reviews_log ADD COLUMN user_id INTEGER DEFAULT NULL REFERENCES users(id) ON DELETE CASCADE")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_user_item ON reviews_log(user_id, item_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews_log(reviewed_at)")

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

        # 6. Daily Stats table (per user per day)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                user_id INTEGER NOT NULL,
                stat_date DATE NOT NULL,
                total_reviewed INTEGER DEFAULT 0,
                easy_count INTEGER DEFAULT 0,
                good_count INTEGER DEFAULT 0,
                hard_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, stat_date)
            )
        """)

        # Migration: check if legacy daily_stats had only stat_date as PK
        cursor.execute("PRAGMA table_info(daily_stats)")
        ds_cols = [row["name"] for row in cursor.fetchall()]
        if "user_id" not in ds_cols:
            cursor.execute("ALTER TABLE daily_stats ADD COLUMN user_id INTEGER DEFAULT 6099429826")

        # 7. Settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # 8. Chat History IDs table for clean UI
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history_ids (
                chat_id INTEGER,
                message_id INTEGER,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_hist ON chat_history_ids(chat_id)")

        # ── Seed Super Admin / Owner as Lifetime VIP ──
        cursor.execute("""
            INSERT INTO users (id, username, full_name, status, is_admin, subscription_expires_at)
            VALUES (?, 'Owner', 'Super Admin', 'lifetime', 1, '2099-12-31 23:59:59')
            ON CONFLICT(id) DO UPDATE SET 
                is_admin = 1, 
                status = 'lifetime', 
                subscription_expires_at = '2099-12-31 23:59:59'
        """, (config.ADMIN_USER_ID,))

        # Associate any unassigned legacy data with the owner
        cursor.execute("UPDATE folders SET user_id = ? WHERE user_id IS NULL", (config.ADMIN_USER_ID,))
        cursor.execute("UPDATE items SET user_id = ? WHERE user_id IS NULL", (config.ADMIN_USER_ID,))
        cursor.execute("UPDATE reviews_log SET user_id = ? WHERE user_id IS NULL", (config.ADMIN_USER_ID,))

        # Clean legacy empty seed folders
        cursor.execute("""
            DELETE FROM folders 
            WHERE name IN ('📐 قدرات كمي', '📖 قدرات لفظي', '💡 قوانين وقواعد', '🎯 تجميعات 1446')
            AND id NOT IN (SELECT DISTINCT folder_id FROM items WHERE folder_id IS NOT NULL)
        """)


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
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


# ─── Users & Subscription Management ─────────────────────────────────────────

def get_or_create_user(user_id: int, username: str = "", full_name: str = "") -> dict:
    """Registers new student with 3-day free trial or returns existing user."""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        if row:
            # Update username/full_name if changed
            if username or full_name:
                c.execute(
                    "UPDATE users SET username = COALESCE(?, username), full_name = COALESCE(?, full_name) WHERE id = ?",
                    (username or None, full_name or None, user_id),
                )
            return dict(row)

        # New user: start free trial
        now = get_riyadh_now()
        trial_exp = (now + timedelta(days=config.TRIAL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        status = "lifetime" if int(user_id) == config.ADMIN_USER_ID else "trial"
        is_adm = 1 if int(user_id) == config.ADMIN_USER_ID else 0
        exp_date = "2099-12-31 23:59:59" if is_adm else trial_exp

        c.execute("""
            INSERT INTO users (id, username, full_name, status, is_admin, trial_started_at, subscription_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, full_name, status, is_adm, now.strftime("%Y-%m-%d %H:%M:%S"), exp_date))

        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return dict(c.fetchone())


def get_user(user_id: int) -> dict | None:
    if not user_id:
        return None
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        return dict(row) if row else None


def check_user_subscription(user_id: int) -> dict:
    """
    Returns subscription health, active status, and remaining days.
    Owner is always Lifetime VIP.
    """
    user = get_user(user_id)
    if not user:
        user = get_or_create_user(user_id)

    if user.get("is_admin") or user.get("status") == "lifetime" or int(user_id) == config.ADMIN_USER_ID:
        return {
            "is_active": True,
            "is_admin": True,
            "is_trial": False,
            "status": "lifetime",
            "days_left": 9999,
            "expires_at": "مدى الحياة (المالك والمشرف)",
        }

    exp_str = user.get("subscription_expires_at")
    if not exp_str:
        return {
            "is_active": False,
            "is_admin": False,
            "is_trial": False,
            "status": "expired",
            "days_left": 0,
            "expires_at": "منتهي",
        }

    now = get_riyadh_now()
    try:
        exp_dt = datetime.strptime(exp_str[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            exp_dt = datetime.strptime(exp_str[:10], "%Y-%m-%d")
        except Exception:
            exp_dt = now

    diff = exp_dt - now.replace(tzinfo=None)
    days_left = max(0, diff.days + (1 if diff.seconds > 0 else 0))
    is_active = (diff.total_seconds() > 0)
    current_status = user.get("status", "trial")

    # If time ran out, update status to expired
    if not is_active and current_status != "expired":
        with get_db_connection() as conn:
            conn.execute("UPDATE users SET status = 'expired' WHERE id = ?", (user_id,))
        current_status = "expired"

    return {
        "is_active": is_active,
        "is_admin": bool(user.get("is_admin")),
        "is_trial": (current_status == "trial"),
        "status": current_status,
        "days_left": days_left,
        "expires_at": exp_dt.strftime("%Y-%m-%d"),
    }


def extend_user_subscription(
    user_id: int, days: int, stars_paid: int = 0, charge_id: str = None, payload: str = None
) -> dict:
    """Extends user's subscription by specified days, activates account, and records payment."""
    user = get_or_create_user(user_id)
    now = get_riyadh_now()

    curr_exp_str = user.get("subscription_expires_at")
    base_dt = now.replace(tzinfo=None)
    if curr_exp_str:
        try:
            exp_dt = datetime.strptime(curr_exp_str[:19], "%Y-%m-%d %H:%M:%S")
            if exp_dt > base_dt:
                base_dt = exp_dt
        except Exception:
            pass

    new_exp_dt = base_dt + timedelta(days=days)
    new_exp_str = new_exp_dt.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            UPDATE users 
            SET status = 'active', subscription_expires_at = ?
            WHERE id = ?
        """, (new_exp_str, user_id))

        if charge_id or stars_paid > 0:
            c.execute("""
                INSERT OR IGNORE INTO payments (user_id, charge_id, amount_stars, days, payload)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, charge_id or f"manual_{int(now.timestamp())}", stars_paid, days, payload or ""))

    logger.info("Extended subscription for user %s by %d days (until %s)", user_id, days, new_exp_str)
    return {
        "user_id": user_id,
        "new_expires_at": new_exp_dt.strftime("%Y-%m-%d"),
        "days_added": days,
    }


def update_user_reminder_time(user_id: int, hour: int, minute: int):
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET reminder_hour = ?, reminder_minute = ? WHERE id = ?", (hour, minute, user_id))


def get_all_users_for_reminders() -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, reminder_hour, reminder_minute, status FROM users WHERE status != 'banned'")
        return [dict(r) for r in c.fetchall()]


def get_admin_platform_stats() -> dict:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) as cnt FROM users")
        total_users = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM users WHERE status = 'active' OR status = 'lifetime'")
        active_subscribers = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM users WHERE status = 'trial'")
        trial_users = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM users WHERE status = 'expired'")
        expired_users = c.fetchone()["cnt"]

        c.execute("SELECT COALESCE(SUM(amount_stars), 0) as total FROM payments")
        total_stars = c.fetchone()["total"]

        c.execute("SELECT count(*) as cnt FROM items")
        total_items = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM reviews_log")
        total_reviews = c.fetchone()["cnt"]

    return {
        "total_users": total_users,
        "active_subscribers": active_subscribers,
        "trial_users": trial_users,
        "expired_users": expired_users,
        "total_stars": total_stars,
        "total_items": total_items,
        "total_reviews": total_reviews,
    }


# ─── Multi-Tenant Folders Management ──────────────────────────────────────────

def get_active_folder_id(user_id: int) -> int | None:
    if not user_id:
        return None
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT active_folder_id FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        if row and row["active_folder_id"]:
            fid = row["active_folder_id"]
            c.execute("SELECT id FROM folders WHERE id = ? AND user_id = ?", (fid, user_id))
            if c.fetchone():
                return fid

        # Fallback to user's first folder
        c.execute("SELECT id FROM folders WHERE user_id = ? ORDER BY sort_order ASC, id ASC LIMIT 1", (user_id,))
        first = c.fetchone()
        if first:
            fid = first["id"]
            c.execute("UPDATE users SET active_folder_id = ? WHERE id = ?", (fid, user_id))
            return fid
    return None


def set_active_folder_id(user_id: int, folder_id: int):
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET active_folder_id = ? WHERE id = ?", (folder_id, user_id))


def get_folders(user_id: int, parent_id: int = None) -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        if parent_id is None:
            c.execute("SELECT * FROM folders WHERE user_id = ? AND parent_id IS NULL ORDER BY sort_order ASC, id ASC", (user_id,))
        else:
            c.execute("SELECT * FROM folders WHERE user_id = ? AND parent_id = ? ORDER BY sort_order ASC, id ASC", (user_id, parent_id))
        return [dict(r) for r in c.fetchall()]


def get_subfolders(user_id: int, parent_id: int) -> list[dict]:
    return get_folders(user_id, parent_id=parent_id)


def get_all_folders(user_id: int) -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM folders WHERE user_id = ? ORDER BY sort_order ASC, id ASC", (user_id,))
        return [dict(r) for r in c.fetchall()]


def get_folder(folder_id: int, user_id: int = None) -> dict | None:
    if not folder_id:
        return None
    with get_db_connection() as conn:
        c = conn.cursor()
        if user_id:
            c.execute("SELECT * FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id))
        else:
            c.execute("SELECT * FROM folders WHERE id = ?", (folder_id,))
        row = c.fetchone()
        return dict(row) if row else None


def get_all_descendant_folder_ids(folder_id: int, user_id: int = None) -> list[int]:
    if not folder_id:
        return []
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            WITH RECURSIVE subfolders AS (
                SELECT id, user_id FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id, f.user_id FROM folders f
                JOIN subfolders s ON f.parent_id = s.id
            )
            SELECT id FROM subfolders WHERE (? IS NULL OR user_id = ?)
        """, (folder_id, user_id, user_id))
        return [row["id"] for row in c.fetchall()]


def get_folder_path(folder_id: int, user_id: int = None) -> list[dict]:
    if not folder_id:
        return []
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            WITH RECURSIVE folder_path AS (
                SELECT id, name, icon, parent_id, user_id, 0 as depth FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id, f.name, f.icon, f.parent_id, f.user_id, fp.depth + 1
                FROM folders f
                JOIN folder_path fp ON f.id = fp.parent_id
            )
            SELECT id, name, icon, parent_id FROM folder_path 
            WHERE (? IS NULL OR user_id = ?)
            ORDER BY depth DESC
        """, (folder_id, user_id, user_id))
        return [dict(r) for r in c.fetchall()]


def get_folder_path_string(folder_id: int, user_id: int = None) -> str:
    path = get_folder_path(folder_id, user_id)
    if not path:
        return ""
    return " ❯ ".join(f"{f['icon']} {f['name']}" for f in path)


def create_folder(user_id: int, name: str, icon: str = "📁", parent_id: int = None) -> int:
    get_or_create_user(user_id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO folders (user_id, name, icon, parent_id, sort_order) 
               VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM folders WHERE user_id = ? AND (parent_id = ? OR (parent_id IS NULL AND ? IS NULL))))""",
            (user_id, name.strip(), icon.strip() if icon else "📁", parent_id, user_id, parent_id, parent_id),
        )
        fid = c.lastrowid
        # If user has no active folder, set this one
        c.execute("SELECT active_folder_id FROM users WHERE id = ?", (user_id,))
        u = c.fetchone()
        if not u or not u["active_folder_id"]:
            c.execute("UPDATE users SET active_folder_id = ? WHERE id = ?", (fid, user_id))
        return fid


def rename_folder(folder_id: int, user_id: int, new_name: str) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE folders SET name = ? WHERE id = ? AND user_id = ?", (new_name.strip(), folder_id, user_id))
        return c.rowcount > 0


def delete_folder(folder_id: int, user_id: int) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT parent_id FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id))
        f_row = c.fetchone()
        if not f_row:
            return False
        parent_id = f_row["parent_id"]

        descendants = get_all_descendant_folder_ids(folder_id, user_id)
        if descendants:
            placeholders = ",".join("?" for _ in descendants)
            c.execute(f"UPDATE items SET folder_id = ? WHERE user_id = ? AND folder_id IN ({placeholders})", [parent_id, user_id] + descendants)

        c.execute("DELETE FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id))

        # Check user active folder
        c.execute("SELECT active_folder_id FROM users WHERE id = ?", (user_id,))
        u = c.fetchone()
        if u and u["active_folder_id"] in descendants:
            c.execute("UPDATE users SET active_folder_id = ? WHERE id = ?", (parent_id, user_id))
        return True


# ─── Multi-Tenant Items Management ───────────────────────────────────────────

def add_item(
    user_id: int,
    item_type: str,
    content: str,
    title: str = "",
    solution: str = "",
    folder_id: int = None,
    review_mode: str = "qa",
) -> int:
    """Adds a new study item scoped to user_id and schedules its first review."""
    get_or_create_user(user_id)
    if not folder_id:
        folder_id = get_active_folder_id(user_id)

    first_review = get_initial_review_date()

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO items (
                user_id, folder_id, item_type, review_mode, title, content, solution, stage, next_review_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                user_id,
                folder_id,
                item_type,
                review_mode,
                title.strip() if title else "",
                content.strip(),
                solution.strip() if solution else "",
                first_review,
            ),
        )
        return c.lastrowid


def get_item(item_id: int, user_id: int = None) -> dict | None:
    with get_db_connection() as conn:
        c = conn.cursor()
        if user_id:
            c.execute(
                """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.id = ? AND i.user_id = ?""",
                (item_id, user_id),
            )
        else:
            c.execute(
                """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.id = ?""",
                (item_id,),
            )
        row = c.fetchone()
        return dict(row) if row else None


def delete_item(item_id: int, user_id: int = None) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        if user_id:
            c.execute("DELETE FROM items WHERE id = ? AND user_id = ?", (item_id, user_id))
        else:
            c.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return c.rowcount > 0


def update_item_folder(item_id: int, user_id: int, folder_id: int) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE items SET folder_id = ? WHERE id = ? AND user_id = ?", (folder_id, item_id, user_id))
        return c.rowcount > 0


def get_due_items(user_id: int, folder_id: int = None, include_subfolders: bool = True) -> list[dict]:
    today_iso = get_riyadh_today_iso()
    with get_db_connection() as conn:
        c = conn.cursor()
        if folder_id:
            if include_subfolders:
                descendants = get_all_descendant_folder_ids(folder_id, user_id)
                if not descendants:
                    return []
                placeholders = ",".join("?" for _ in descendants)
                query = f"""
                    SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                    FROM items i 
                    LEFT JOIN folders f ON i.folder_id = f.id 
                    WHERE i.user_id = ? AND i.folder_id IN ({placeholders})
                      AND i.next_review_date <= ? 
                      AND i.is_mastered = 0
                    ORDER BY i.next_review_date ASC, i.id ASC
                """
                c.execute(query, [user_id] + descendants + [today_iso])
            else:
                c.execute(
                    """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                       FROM items i 
                       LEFT JOIN folders f ON i.folder_id = f.id 
                       WHERE i.user_id = ? AND i.next_review_date <= ? AND i.folder_id = ? AND i.is_mastered = 0
                       ORDER BY i.next_review_date ASC, i.id ASC""",
                    (user_id, today_iso, folder_id),
                )
        else:
            c.execute(
                """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.user_id = ? AND i.next_review_date <= ? AND i.is_mastered = 0
                   ORDER BY i.next_review_date ASC, i.id ASC""",
                (user_id, today_iso),
            )
        return [dict(r) for r in c.fetchall()]


def get_items_by_folder(folder_id: int, user_id: int, include_subfolders: bool = False, limit: int = 100) -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        if include_subfolders:
            descendants = get_all_descendant_folder_ids(folder_id, user_id)
            if not descendants:
                return []
            placeholders = ",".join("?" for _ in descendants)
            c.execute(
                f"""SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.user_id = ? AND i.folder_id IN ({placeholders})
                   ORDER BY i.id DESC LIMIT ?""",
                [user_id] + descendants + [limit],
            )
        else:
            c.execute(
                """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.user_id = ? AND i.folder_id = ?
                   ORDER BY i.id DESC LIMIT ?""",
                (user_id, folder_id, limit),
            )
        return [dict(r) for r in c.fetchall()]


def record_item_review(item_id: int, user_id: int, rating: str) -> dict:
    item = get_item(item_id, user_id)
    if not item:
        return {}

    sched = calculate_next_schedule(
        current_stage=item["stage"],
        rating=rating,
        ease_factor=item.get("ease_factor", 2.5),
    )

    today_iso = get_riyadh_today_iso()

    with get_db_connection() as conn:
        c = conn.cursor()
        new_lapses = item["lapses"] + (1 if sched["lapsed"] else 0)

        # 1. Update item
        c.execute("""
            UPDATE items 
            SET stage = ?, next_review_date = ?, ease_factor = ?, 
                is_mastered = ?, lapses = ?
            WHERE id = ? AND user_id = ?
        """, (sched["new_stage"], sched["next_review_date"], sched["ease_factor"], sched["is_mastered"], new_lapses, item_id, user_id))

        # 2. Log review event
        c.execute("""
            INSERT INTO reviews_log (user_id, item_id, rating, old_stage, new_stage)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, item_id, rating, item["stage"], sched["new_stage"]))

        # 3. Update daily stats per user
        col = f"{rating}_count"
        c.execute(f"""
            INSERT INTO daily_stats (user_id, stat_date, total_reviewed, {col})
            VALUES (?, ?, 1, 1)
            ON CONFLICT(user_id, stat_date) DO UPDATE SET 
                total_reviewed = total_reviewed + 1,
                {col} = {col} + 1
        """, (user_id, today_iso))

    sched["old_stage"] = item["stage"]
    return sched


# ─── Multi-Tenant Statistics & Dashboard Helpers ─────────────────────────────

def get_dashboard_stats(user_id: int) -> dict:
    today_iso = get_riyadh_today_iso()
    with get_db_connection() as conn:
        c = conn.cursor()

        # Due count for user
        c.execute("SELECT count(*) as cnt FROM items WHERE user_id = ? AND next_review_date <= ? AND is_mastered = 0", (user_id, today_iso))
        due_count = c.fetchone()["cnt"]

        # Total active vs mastered for user
        c.execute("SELECT count(*) as cnt FROM items WHERE user_id = ? AND is_mastered = 0", (user_id,))
        active_count = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM items WHERE user_id = ? AND is_mastered = 1", (user_id,))
        mastered_count = c.fetchone()["cnt"]

        # By item type for user
        c.execute("SELECT item_type, count(*) as cnt FROM items WHERE user_id = ? GROUP BY item_type", (user_id,))
        by_type = {r["item_type"]: r["cnt"] for r in c.fetchall()}

        # By stage for user
        c.execute("SELECT stage, count(*) as cnt FROM items WHERE user_id = ? AND is_mastered = 0 GROUP BY stage", (user_id,))
        by_stage = {r["stage"]: r["cnt"] for r in c.fetchall()}

        # Due by folder for user
        c.execute("""
            SELECT f.id, f.name, f.icon, f.parent_id,
                   COUNT(i.id) as total_items,
                   SUM(CASE WHEN i.next_review_date <= ? AND i.is_mastered = 0 THEN 1 ELSE 0 END) as due_items,
                   (SELECT count(*) FROM folders sub WHERE sub.parent_id = f.id AND sub.user_id = ?) as subfolders_count
            FROM folders f
            LEFT JOIN items i ON f.id = i.folder_id AND i.user_id = ?
            WHERE f.user_id = ?
            GROUP BY f.id
            ORDER BY f.sort_order ASC, f.id ASC
        """, (today_iso, user_id, user_id, user_id))
        folders_breakdown = [dict(r) for r in c.fetchall()]

        # Recent 7 days activity for user
        c.execute("SELECT * FROM daily_stats WHERE user_id = ? ORDER BY stat_date DESC LIMIT 7", (user_id,))
        recent_daily = [dict(r) for r in c.fetchall()]

    return {
        "due_count": due_count,
        "active_count": active_count,
        "mastered_count": mastered_count,
        "total_items": active_count + mastered_count,
        "by_type": by_type,
        "by_stage": by_stage,
        "folders_breakdown": folders_breakdown,
        "recent_daily": recent_daily,
    }


# ─── Clean Chat Message Tracking ──────────────────────────────────────────────

def track_chat_message(chat_id: int, message_id: int):
    if not chat_id or not message_id:
        return
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO chat_history_ids (chat_id, message_id) VALUES (?, ?)",
            (chat_id, message_id),
        )


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
