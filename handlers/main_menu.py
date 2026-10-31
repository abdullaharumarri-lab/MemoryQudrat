import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
import config
from spaced_repetition import get_riyadh_now
from utils import safe_edit, send_clean_message, clean_entire_chat

logger = logging.getLogger(__name__)


# ─── Main Dashboard Builder ───────────────────────────────────────────────────

def build_main_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the single, clutter-free primary dashboard scoped to user_id."""
    sub = db.check_user_subscription(user_id)
    stats = db.get_dashboard_stats(user_id)
    due_cnt = stats["due_count"]
    total_cnt = stats["total_items"]
    folders_info = stats["folders_breakdown"]

    now = get_riyadh_now()
    arabic_days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    day_name = arabic_days[now.weekday()]
    date_str = now.strftime("%Y-%m-%d")

    active_fid = db.get_active_folder_id(user_id)
    active_folder = db.get_folder(active_fid, user_id) if active_fid else None
    if active_folder:
        path_str = db.get_folder_path_string(active_fid, user_id)
        f_title = path_str or f"{active_folder['icon']} {active_folder['name']}"
    else:
        f_title = None

    # Status badge
    if sub.get("status") == "lifetime" or sub.get("is_admin"):
        sub_badge = "👑 <b>اشتراك VIP دائم (المالك)</b>"
    elif sub.get("is_trial"):
        sub_badge = f"🎁 <b>تجربة مجانية (متبقي {sub['days_left']} يوم)</b>"
    elif sub.get("is_active"):
        sub_badge = f"⭐ <b>مشترك نشط (متبقي {sub['days_left']} يوم)</b>"
    else:
        sub_badge = "🔒 <b>انتهت التجربة (اضغط زر الاشتراك للتجديد)</b>"

    text_lines = [
        "🧠 <b>ذاكرة القدرات — رفيق المذاكرة الشخصي</b>",
        f"💳 {sub_badge}",
        f"📅 <b>اليوم:</b> {day_name} {date_str}\n",
    ]

    if due_cnt > 0:
        text_lines.append(f"🔔 <b>المستحق للمراجعة اليوم:</b> <b>{due_cnt}</b> مادة دراسية:")
        for f in folders_info:
            if f.get("due_items", 0) > 0:
                text_lines.append(f"  • {f['icon']} {html.escape(f['name'])}: <b>{f['due_items']}</b> مستحق")
    elif total_cnt > 0:
        text_lines.append("✅ <b>جدولك نظيف اليوم!</b> أنهيت مراجعاتك بامتياز 🌟")
    else:
        text_lines.append("💡 <b>ابدأ المذاكرة:</b> أرسل صورة مسألة أو ملخص لحفظه في جدول التكرار المتباعد.")

    if f_title:
        text_lines.append(f"\n📁 <b>المجلد النشط للإيداع:</b> [ <b>{html.escape(f_title)}</b> ]")
    else:
        text_lines.append("\n📁 <b>المجلدات:</b> لا توجد مجلدات بعد (اضغط مجلداتي للبدء).")

    kb = []
    # 1. Primary Action: Start due reviews if available
    if due_cnt > 0:
        kb.append([InlineKeyboardButton(f"▶️ ابدأ مراجعات اليوم ({due_cnt})", callback_data="start_review_all")])

    # 2. Main functional buttons
    kb.append([
        InlineKeyboardButton("📁 مجلداتي ومسائلي", callback_data="folders_menu"),
        InlineKeyboardButton("📊 إحصائياتي والتنبيه", callback_data="stats_menu"),
    ])

    # 3. Subscription & Admin controls
    sub_row = [InlineKeyboardButton("⭐ الاشتراك والترقية", callback_data="subscription_menu")]
    if config.is_admin(user_id):
        sub_row.append(InlineKeyboardButton("👑 لوحة المالك", callback_data="admin_menu"))
    kb.append(sub_row)

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


# ─── Commands Handlers ────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /start and /menu."""
    user = update.effective_user
    if not user:
        return

    # Register/fetch student
    db.get_or_create_user(user.id, user.username or "", user.full_name or "")

    chat_id = update.effective_chat.id
    text, kb = build_main_dashboard(user.id)
    await clean_entire_chat(context, chat_id)
    await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /review and /today."""
    user = update.effective_user
    if not user:
        return
    from handlers.review_handler import start_review_session
    await start_review_session(update, context, folder_id=None)


async def folders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /folders."""
    user = update.effective_user
    if not user:
        return
    from handlers.folders_handler import build_folders_menu
    text, kb = build_folders_menu(user.id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /stats."""
    user = update.effective_user
    if not user:
        return
    from handlers.stats_handler import build_stats_view
    text, kb = build_stats_view(user.id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /backup: exports DB into Super Admin chat."""
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return

    chat_id = update.effective_chat.id
    import os

    status_msg = await update.message.reply_text("⏳ جاري تجهيز النسخة الاحتياطية الكاملة...")
    try:
        if os.path.exists(config.DB_PATH):
            with open(config.DB_PATH, "rb") as db_file:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=db_file,
                    filename="memory_qudrat_commercial.db",
                    caption="💾 <b>نسخة احتياطية لقاعدة البيانات التجارية</b>",
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
    if not user:
        await query.answer("❌ غير مصرح.")
        return

    data = query.data

    # Return Home
    if data == "main_menu":
        # 1. Clean up any pending invoice message
        old_inv_id = context.user_data.pop("last_invoice_id", None)
        if old_inv_id:
            try:
                await context.bot.delete_message(chat_id=query.message.chat_id, message_id=old_inv_id)
            except Exception:
                pass

        # 2. Clean up any other auxiliary messages to keep chat perfectly clean
        aux_ids = db.get_and_clear_chat_messages(query.message.chat_id, keep_message_id=query.message.message_id)
        for mid in aux_ids:
            try:
                await context.bot.delete_message(chat_id=query.message.chat_id, message_id=mid)
            except Exception:
                pass

        text, kb = build_main_dashboard(user.id)
        await safe_edit(query, text, kb)
        return

    # Dismiss Broadcast and Open Dashboard
    if data == "dismiss_broadcast":
        chat_id = query.message.chat_id
        try:
            await query.message.delete()
        except Exception:
            pass
        await clean_entire_chat(context, chat_id)
        text, kb = build_main_dashboard(user.id)
        await send_clean_message(context, chat_id, text, reply_markup=kb)
        return

    # Start all due reviews
    if data == "start_review_all":
        from handlers.review_handler import start_review_session
        await start_review_session(update, context, folder_id=None)
        return

    # 1. Subscription & Telegram Stars Callbacks
    from handlers.subscription_handler import handle_subscription_callback
    if await handle_subscription_callback(update, context):
        return

    # 2. Super Admin Callbacks
    from handlers.admin_handler import handle_admin_callback
    if await handle_admin_callback(update, context):
        return

    # 3. Review Handler Callbacks
    from handlers.review_handler import handle_review_callback
    if await handle_review_callback(update, context):
        return

    # 4. Folders Handler Callbacks
    from handlers.folders_handler import handle_folders_callback
    if await handle_folders_callback(update, context):
        return

    # 5. Capture Handler Callbacks
    from handlers.capture_handler import handle_capture_callback
    if await handle_capture_callback(update, context):
        return

    # 6. Stats Handler Callbacks
    from handlers.stats_handler import handle_stats_callback
    if await handle_stats_callback(update, context):
        return

    logger.warning("Unhandled callback data: %s", data)
