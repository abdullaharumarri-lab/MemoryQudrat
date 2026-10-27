import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_authorized
from spaced_repetition import get_riyadh_now
from utils import safe_edit, send_clean_message, clean_entire_chat

logger = logging.getLogger(__name__)


# ─── Main Dashboard Builder ───────────────────────────────────────────────────

def build_main_dashboard() -> tuple[str, InlineKeyboardMarkup]:
    """Builds the single, clutter-free primary dashboard."""
    stats = db.get_dashboard_stats()
    due_cnt = stats["due_count"]
    total_cnt = stats["total_items"]
    folders_info = stats["folders_breakdown"]

    now = get_riyadh_now()
    arabic_days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    day_name = arabic_days[now.weekday()]
    date_str = now.strftime("%Y-%m-%d")

    active_fid = db.get_active_folder_id()
    active_folder = db.get_folder(active_fid) if active_fid else None
    if active_folder:
        path_str = db.get_folder_path_string(active_fid)
        f_title = path_str or f"{active_folder['icon']} {active_folder['name']}"
    else:
        f_title = None

    text_lines = [
        "🧠 <b>ذاكرة القدرات — رفيق المذاكرة الشخصي</b>\n",
        f"📅 <b>اليوم:</b> {day_name} {date_str}",
    ]

    if due_cnt > 0:
        text_lines.append(f"🔔 <b>المستحق للمراجعة اليوم:</b> <b>{due_cnt}</b> مادة دراسية:")
        for f in folders_info:
            if f.get("due_items", 0) > 0:
                text_lines.append(f"  • {f['icon']} {html.escape(f['name'])}: <b>{f['due_items']}</b> مستحق")
    elif total_cnt > 0:
        text_lines.append("✅ <b>جدولك نظيف اليوم!</b> أنهيت مراجعاتك بامتياز 🌟")
    else:
        text_lines.append("💡 <b>ابدأ المذاكرة:</b> أرسل صورة سؤال أو ملخص لإدراجه في التكرار المتباعد.")

    if f_title:
        text_lines.append(f"\n📁 <b>المجلد النشط للإيداع:</b> [ <b>{html.escape(f_title)}</b> ]")
    else:
        text_lines.append("\n📁 <b>المجلدات:</b> لا توجد مجلدات بعد (اضغط مجلداتي للبدء).")

    kb = []
    # 1. Primary Action: Start due reviews if available
    if due_cnt > 0:
        kb.append([InlineKeyboardButton(f"▶️ ابدأ مراجعات اليوم ({due_cnt})", callback_data="start_review_all")])

    # 2. Distinct, non-redundant controls
    kb.append([
        InlineKeyboardButton("📁 مجلداتي ومسائلي", callback_data="folders_menu"),
        InlineKeyboardButton("📊 إحصائياتي والتنبيه", callback_data="stats_menu"),
    ])

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


# ─── Commands Handlers ────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /start and /menu."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    text, kb = build_main_dashboard()
    await clean_entire_chat(context, chat_id)
    await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /review and /today."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return
    from handlers.review_handler import start_review_session
    await start_review_session(update, context, folder_id=None)


async def folders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /folders."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return
    from handlers.folders_handler import build_folders_menu
    text, kb = build_folders_menu()
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /stats."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return
    from handlers.stats_handler import build_stats_view
    text, kb = build_stats_view()
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /backup: exports DB and media into personal chat."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    from config import DB_PATH
    import os

    status_msg = await update.message.reply_text("⏳ جاري تجهيز النسخة الاحتياطية الكاملة...")
    try:
        # Send database file directly
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "rb") as db_file:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=db_file,
                    filename="memory_qudrat_backup.db",
                    caption="💾 <b>نسخة احتياطية لقاعدة البيانات</b>",
                    parse_mode="HTML"
                )
        await status_msg.edit_text("✅ تم إرسال النسخة الاحتياطية بنجاح!")
    except Exception as e:
        logger.error("Backup failed: %s", e)
        await status_msg.edit_text(f"⚠️ تعذر إنشاء النسخة الاحتياطية: {e}")


# ─── Central Callback Router ─────────────────────────────────────────────────

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central router for all inline button clicks."""
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user or not is_authorized(user.id):
        await query.answer("❌ غير مصرح.")
        return

    data = query.data

    # Return Home
    if data == "main_menu":
        text, kb = build_main_dashboard()
        await safe_edit(query, text, kb)
        return

    # Start all due reviews
    if data == "start_review_all":
        from handlers.review_handler import start_review_session
        await start_review_session(update, context, folder_id=None)
        return

    # 1. Review Handler Callbacks
    from handlers.review_handler import handle_review_callback
    if await handle_review_callback(update, context):
        return

    # 2. Folders Handler Callbacks
    from handlers.folders_handler import handle_folders_callback
    if await handle_folders_callback(update, context):
        return

    # 3. Capture Handler Callbacks
    from handlers.capture_handler import handle_capture_callback
    if await handle_capture_callback(update, context):
        return

    # 4. Stats Handler Callbacks
    from handlers.stats_handler import handle_stats_callback
    if await handle_stats_callback(update, context):
        return

    logger.warning("Unhandled callback data: %s", data)
