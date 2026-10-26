import sqlite3
import contextlib
import logging
import config
from spaced_repetition import (
    get_riyadh_today_iso,
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
    """Initializes clean database schema and seeds initial folders if empty."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Folders table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '📁',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Items / Flashcards table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER,
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_due ON items(next_review_date, is_mastered)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_type ON items(item_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_mode ON items(review_mode)")

        # 3. Reviews Log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                rating TEXT NOT NULL,
                old_stage INTEGER NOT NULL,
                new_stage INTEGER NOT NULL,
                reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_item ON reviews_log(item_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews_log(reviewed_at)")

        # 4. Daily Stats table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                stat_date DATE PRIMARY KEY,
                total_reviewed INTEGER DEFAULT 0,
                easy_count INTEGER DEFAULT 0,
                good_count INTEGER DEFAULT 0,
                hard_count INTEGER DEFAULT 0
            )
        """)

        # 5. Settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # 6. Chat History IDs table for clean UI
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history_ids (
                chat_id INTEGER,
                message_id INTEGER,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_hist ON chat_history_ids(chat_id)")

        # ── Seed Default Recommended Folders if empty ──
        cursor.execute("SELECT count(*) as cnt FROM folders")
        if cursor.fetchone()["cnt"] == 0:
            default_folders = [
                ("📐 قدرات كمي", "📐", 1),
                ("📖 قدرات لفظي", "📖", 2),
                ("💡 قوانين وقواعد", "💡", 3),
                ("🎯 تجميعات 1446", "🎯", 4),
            ]
            for name, icon, order in default_folders:
                cursor.execute(
                    "INSERT INTO folders (name, icon, sort_order) VALUES (?, ?, ?)",
                    (name, icon, order),
                )
            logger.info("Seeded default study folders.")

        # Seed default settings
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('active_folder_id', '1')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('reminder_hour', '5')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('reminder_minute', '0')")


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


def get_active_folder_id() -> int:
    val = get_setting("active_folder_id", "1")
    try:
        return int(val)
    except Exception:
        return 1


def set_active_folder_id(folder_id: int):
    set_setting("active_folder_id", str(folder_id))


# ─── Folders Management ───────────────────────────────────────────────────────

def get_folders() -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM folders ORDER BY sort_order ASC, id ASC")
        return [dict(r) for r in c.fetchall()]


def get_folder(folder_id: int) -> dict | None:
    if not folder_id:
        return None
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM folders WHERE id = ?", (folder_id,))
        row = c.fetchone()
        return dict(row) if row else None


def create_folder(name: str, icon: str = "📁") -> int:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO folders (name, icon, sort_order) VALUES (?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM folders))",
            (name.strip(), icon.strip() if icon else "📁"),
        )
        return c.lastrowid


def rename_folder(folder_id: int, new_name: str) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE folders SET name = ? WHERE id = ?", (new_name.strip(), folder_id))
        return c.rowcount > 0


def delete_folder(folder_id: int) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        # If deleted folder was active, fallback to first available folder
        c.execute("SELECT id FROM folders ORDER BY id ASC LIMIT 1")
        first = c.fetchone()
        if first:
            c.execute(
                "INSERT INTO settings (key, value) VALUES ('active_folder_id', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(first["id"]),),
            )
        return True


# ─── Items Management ─────────────────────────────────────────────────────────

def add_item(
    item_type: str,
    content: str,
    title: str = "",
    solution: str = "",
    folder_id: int = None,
    review_mode: str = "qa",
) -> int:
    """Adds a new study item and schedules its first review for tomorrow."""
    if not folder_id:
        folder_id = get_active_folder_id()

    first_review = get_initial_review_date()

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO items (
                folder_id, item_type, review_mode, title, content, solution, stage, next_review_date
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (
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


def get_item(item_id: int) -> dict | None:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
               FROM items i 
               LEFT JOIN folders f ON i.folder_id = f.id 
               WHERE i.id = ?""",
            (item_id,),
        )
        row = c.fetchone()
        return dict(row) if row else None


def delete_item(item_id: int) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return c.rowcount > 0


def update_item_folder(item_id: int, folder_id: int) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE items SET folder_id = ? WHERE id = ?", (folder_id, item_id))
        return c.rowcount > 0


def get_due_items(folder_id: int = None) -> list[dict]:
    """Retrieves all items whose review is due today or overdue in Riyadh timezone."""
    today_iso = get_riyadh_today_iso()
    with get_db_connection() as conn:
        c = conn.cursor()
        if folder_id:
            c.execute(
                """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.next_review_date <= ? AND i.folder_id = ? AND i.is_mastered = 0
                   ORDER BY i.next_review_date ASC, i.id ASC""",
                (today_iso, folder_id),
            )
        else:
            c.execute(
                """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.next_review_date <= ? AND i.is_mastered = 0
                   ORDER BY i.next_review_date ASC, i.id ASC""",
                (today_iso,),
            )
        return [dict(r) for r in c.fetchall()]


def get_items_by_folder(folder_id: int, limit: int = 100) -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT i.*, f.name as folder_name, f.icon as folder_icon 
               FROM items i 
               LEFT JOIN folders f ON i.folder_id = f.id 
               WHERE i.folder_id = ?
               ORDER BY i.id DESC LIMIT ?""",
            (folder_id, limit),
        )
        return [dict(r) for r in c.fetchall()]


def record_item_review(item_id: int, rating: str) -> dict:
    """Updates item stage and next review date, logs review history, and updates daily stats."""
    item = get_item(item_id)
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
        c.execute(
            """UPDATE items 
               SET stage = ?, next_review_date = ?, ease_factor = ?, 
                   is_mastered = ?, lapses = ?
               WHERE id = ?""",
            (
                sched["new_stage"],
                sched["next_review_date"],
                sched["ease_factor"],
                sched["is_mastered"],
                new_lapses,
                item_id,
            ),
        )

        # 2. Log review event
        c.execute(
            """INSERT INTO reviews_log (item_id, rating, old_stage, new_stage)
               VALUES (?, ?, ?, ?)""",
            (item_id, rating, item["stage"], sched["new_stage"]),
        )

        # 3. Update daily stats
        col = f"{rating}_count"
        c.execute(
            f"""INSERT INTO daily_stats (stat_date, total_reviewed, {col})
               VALUES (?, 1, 1)
               ON CONFLICT(stat_date) DO UPDATE SET 
                   total_reviewed = total_reviewed + 1,
                   {col} = {col} + 1""",
            (today_iso,),
        )

    sched["old_stage"] = item["stage"]
    return sched


# ─── Statistics & Dashboard Helpers ───────────────────────────────────────────

def get_dashboard_stats() -> dict:
    today_iso = get_riyadh_today_iso()
    with get_db_connection() as conn:
        c = conn.cursor()

        # Due count
        c.execute("SELECT count(*) as cnt FROM items WHERE next_review_date <= ? AND is_mastered = 0", (today_iso,))
        due_count = c.fetchone()["cnt"]

        # Total active vs mastered
        c.execute("SELECT count(*) as cnt FROM items WHERE is_mastered = 0")
        active_count = c.fetchone()["cnt"]

        c.execute("SELECT count(*) as cnt FROM items WHERE is_mastered = 1")
        mastered_count = c.fetchone()["cnt"]

        # By item type
        c.execute("SELECT item_type, count(*) as cnt FROM items GROUP BY item_type")
        by_type = {r["item_type"]: r["cnt"] for r in c.fetchall()}

        # By stage
        c.execute("SELECT stage, count(*) as cnt FROM items WHERE is_mastered = 0 GROUP BY stage")
        by_stage = {r["stage"]: r["cnt"] for r in c.fetchall()}

        # Due by folder
        c.execute("""
            SELECT f.id, f.name, f.icon, 
                   COUNT(i.id) as total_items,
                   SUM(CASE WHEN i.next_review_date <= ? AND i.is_mastered = 0 THEN 1 ELSE 0 END) as due_items
            FROM folders f
            LEFT JOIN items i ON f.id = i.folder_id
            GROUP BY f.id
            ORDER BY f.sort_order ASC, f.id ASC
        """, (today_iso,))
        folders_breakdown = [dict(r) for r in c.fetchall()]

        # Recent 7 days activity
        c.execute("SELECT * FROM daily_stats ORDER BY stat_date DESC LIMIT 7")
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
