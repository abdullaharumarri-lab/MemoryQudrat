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
    """Initializes clean database schema without pre-made seed folders."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Folders table (with parent_id support for nested subfolders)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '📁',
                parent_id INTEGER DEFAULT NULL,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE
            )
        """)

        # Migration: ensure parent_id exists in folders table
        cursor.execute("PRAGMA table_info(folders)")
        cols = [row["name"] for row in cursor.fetchall()]
        if "parent_id" not in cols:
            cursor.execute("ALTER TABLE folders ADD COLUMN parent_id INTEGER DEFAULT NULL REFERENCES folders(id) ON DELETE CASCADE")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id)")

        # 2. Items / Flashcards table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER DEFAULT NULL,
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

        # Clean up legacy unedited empty seed folders if any exist
        cursor.execute("""
            DELETE FROM folders 
            WHERE name IN ('📐 قدرات كمي', '📖 قدرات لفظي', '💡 قوانين وقواعد', '🎯 تجميعات 1446')
            AND id NOT IN (SELECT DISTINCT folder_id FROM items WHERE folder_id IS NOT NULL)
        """)

        # Seed default settings (without forcing active_folder_id to 1)
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


def get_active_folder_id() -> int | None:
    val = get_setting("active_folder_id")
    if val:
        try:
            fid = int(val)
            if get_folder(fid):
                return fid
        except Exception:
            pass
    # Fallback to first existing folder, or None
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM folders ORDER BY sort_order ASC, id ASC LIMIT 1")
        row = c.fetchone()
        if row:
            set_setting("active_folder_id", str(row["id"]))
            return row["id"]
    return None


def set_active_folder_id(folder_id: int):
    set_setting("active_folder_id", str(folder_id) if folder_id else "")


# ─── Folders Management ───────────────────────────────────────────────────────

def get_folders(parent_id: int = None) -> list[dict]:
    """Retrieves folders at given hierarchy level (root if parent_id is None, or subfolders of parent_id)."""
    with get_db_connection() as conn:
        c = conn.cursor()
        if parent_id is None:
            c.execute("SELECT * FROM folders WHERE parent_id IS NULL ORDER BY sort_order ASC, id ASC")
        else:
            c.execute("SELECT * FROM folders WHERE parent_id = ? ORDER BY sort_order ASC, id ASC", (parent_id,))
        return [dict(r) for r in c.fetchall()]


def get_subfolders(parent_id: int) -> list[dict]:
    """Retrieves direct children subfolders of a given parent folder."""
    return get_folders(parent_id=parent_id)


def get_all_folders() -> list[dict]:
    """Retrieves all folders in the database regardless of hierarchy."""
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


def get_all_descendant_folder_ids(folder_id: int) -> list[int]:
    """Returns folder_id and all of its recursive descendant subfolder IDs."""
    if not folder_id:
        return []
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            WITH RECURSIVE subfolders AS (
                SELECT id FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id FROM folders f
                JOIN subfolders s ON f.parent_id = s.id
            )
            SELECT id FROM subfolders
        """, (folder_id,))
        return [row["id"] for row in c.fetchall()]


def get_folder_path(folder_id: int) -> list[dict]:
    """Returns ancestor folders path from root down to folder_id for breadcrumbs."""
    if not folder_id:
        return []
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            WITH RECURSIVE folder_path AS (
                SELECT id, name, icon, parent_id, 0 as depth FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id, f.name, f.icon, f.parent_id, fp.depth + 1
                FROM folders f
                JOIN folder_path fp ON f.id = fp.parent_id
            )
            SELECT id, name, icon, parent_id FROM folder_path ORDER BY depth DESC
        """, (folder_id,))
        return [dict(r) for r in c.fetchall()]


def get_folder_path_string(folder_id: int) -> str:
    path = get_folder_path(folder_id)
    if not path:
        return ""
    return " ❯ ".join(f"{f['icon']} {f['name']}" for f in path)


def create_folder(name: str, icon: str = "📁", parent_id: int = None) -> int:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO folders (name, icon, parent_id, sort_order) 
               VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM folders WHERE (parent_id = ? OR (parent_id IS NULL AND ? IS NULL))))""",
            (name.strip(), icon.strip() if icon else "📁", parent_id, parent_id, parent_id),
        )
        fid = c.lastrowid
        # If no active folder was set, set this newly created folder as active
        c.execute("SELECT value FROM settings WHERE key = 'active_folder_id'")
        curr_act = c.fetchone()
        if not curr_act or not curr_act["value"]:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('active_folder_id', ?)", (str(fid),))
        return fid


def rename_folder(folder_id: int, new_name: str) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE folders SET name = ? WHERE id = ?", (new_name.strip(), folder_id))
        return c.rowcount > 0


def delete_folder(folder_id: int) -> bool:
    """Deletes a folder and safely moves its items to parent folder, cascading subfolders."""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT parent_id FROM folders WHERE id = ?", (folder_id,))
        f_row = c.fetchone()
        parent_id = f_row["parent_id"] if f_row else None

        # Fetch descendant IDs
        c.execute("""
            WITH RECURSIVE subfolders AS (
                SELECT id FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id FROM folders f
                JOIN subfolders s ON f.parent_id = s.id
            )
            SELECT id FROM subfolders
        """, (folder_id,))
        descendants = [row["id"] for row in c.fetchall()]

        if descendants:
            placeholders = ",".join("?" for _ in descendants)
            # Move items in this folder and its descendants to parent_id
            c.execute(f"UPDATE items SET folder_id = ? WHERE folder_id IN ({placeholders})", [parent_id] + descendants)

        # Delete folder (subfolders cascade delete via foreign key)
        c.execute("DELETE FROM folders WHERE id = ?", (folder_id,))

        # Update active folder if it was deleted
        c.execute("SELECT value FROM settings WHERE key = 'active_folder_id'")
        act_row = c.fetchone()
        if act_row and act_row["value"]:
            try:
                curr_act_id = int(act_row["value"])
                if curr_act_id == folder_id or curr_act_id in descendants:
                    fallback_id = parent_id
                    if not fallback_id:
                        c.execute("SELECT id FROM folders ORDER BY sort_order ASC, id ASC LIMIT 1")
                        first = c.fetchone()
                        fallback_id = first["id"] if first else None
                    c.execute(
                        "INSERT INTO settings (key, value) VALUES ('active_folder_id', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (str(fallback_id) if fallback_id else "",),
                    )
            except Exception:
                pass
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


def get_due_items(folder_id: int = None, include_subfolders: bool = True) -> list[dict]:
    """Retrieves all items whose review is due today or overdue in Riyadh timezone."""
    today_iso = get_riyadh_today_iso()
    with get_db_connection() as conn:
        c = conn.cursor()
        if folder_id:
            if include_subfolders:
                descendants = get_all_descendant_folder_ids(folder_id)
                if not descendants:
                    return []
                placeholders = ",".join("?" for _ in descendants)
                query = f"""
                    SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                    FROM items i 
                    LEFT JOIN folders f ON i.folder_id = f.id 
                    WHERE i.folder_id IN ({placeholders})
                      AND i.next_review_date <= ? 
                      AND i.is_mastered = 0
                    ORDER BY i.next_review_date ASC, i.id ASC
                """
                c.execute(query, descendants + [today_iso])
            else:
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


def get_items_by_folder(folder_id: int, include_subfolders: bool = False, limit: int = 100) -> list[dict]:
    with get_db_connection() as conn:
        c = conn.cursor()
        if include_subfolders:
            descendants = get_all_descendant_folder_ids(folder_id)
            if not descendants:
                return []
            placeholders = ",".join("?" for _ in descendants)
            c.execute(
                f"""SELECT i.*, f.name as folder_name, f.icon as folder_icon 
                   FROM items i 
                   LEFT JOIN folders f ON i.folder_id = f.id 
                   WHERE i.folder_id IN ({placeholders})
                   ORDER BY i.id DESC LIMIT ?""",
                descendants + [limit],
            )
        else:
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
            SELECT f.id, f.name, f.icon, f.parent_id,
                   COUNT(i.id) as total_items,
                   SUM(CASE WHEN i.next_review_date <= ? AND i.is_mastered = 0 THEN 1 ELSE 0 END) as due_items,
                   (SELECT count(*) FROM folders sub WHERE sub.parent_id = f.id) as subfolders_count
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
