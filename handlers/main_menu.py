import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
import config
from spaced_repetition import get_riyadh_now
from utils import safe_edit, send_clean_message, clean_entire_chat

logger = logging.getLogger(__name__)


def build_main_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the single, ultra-clean friendly dashboard for user_id."""
    sub = db.check_user_subscription(user_id)
    user = db.get_user(user_id) or {}
    name = (user.get("full_name") or user.get("username") or "يا بطل").strip()

    now = get_riyadh_now()
    arabic_days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    day_name = arabic_days[now.weekday()]
    date_str = now.strftime("%Y-%m-%d")

    # Subscription badge
    if sub.get("status") == "lifetime" or sub.get("is_admin"):
        sub_badge = "👑 <b>اشتراك VIP دائم (المالك)</b>"
    elif sub.get("is_trial"):
        sub_badge = f"🎁 <b>تجربة مجانية نشطة (متبقي {sub['days_left']} أيام)</b>"
    elif sub.get("is_active"):
        sub_badge = f"⭐ <b>مشترك نشط (متبقي {sub['days_left']} يوم)</b>"
    else:
        sub_badge = "🔒 <b>انتهت الفترة التجريبية (جدد اشتراكك بالأسفل)</b>"

    tasks = db.get_today_tasks(user_id)
    new_count = len(tasks["new_lessons"])
    rev_count = len(tasks["due_reviews"])

    text_lines = [
        f"هلا والله {html.escape(name)}! 🧠 جاهز ننجز اليوم؟",
        f"💳 {sub_badge}",
        f"📅 <b>اليوم:</b> {day_name} {date_str}\n",
    ]

    if new_count > 0 or rev_count > 0:
        text_lines.append("🎯 <b>خطة إنجازك اليوم:</b>")
        if new_count > 0:
            text_lines.append(f"• 📖 دروس جديدة للمذاكرة: <b>{new_count}</b> درس")
        if rev_count > 0:
            text_lines.append(f"• 🔄 مراجعات لتثبيت الذاكرة: <b>{rev_count}</b> درس")
        text_lines.append("\n💪 <i>خطوات بسيطة يومياً تصنع الـ 100% بإذن الله!</i>")
    else:
        subjects = db.get_user_subjects(user_id)
        if subjects:
            text_lines.append("🎉 <b>كفو عليك يا وحش!</b> جدولك نظيف وأنهيت كل مهام اليوم بامتياز ✨")
        else:
            text_lines.append("👋 <b>أهلاً بك!</b> لسه ما عندك أي جدول دراسي.\nاضغط زر «➕ سوي جدول جديد» بالأسفل ونرتب لك خطتك في ثواني!")

    kb = [
        [InlineKeyboardButton("📅 جدول اليوم وإنجازي", callback_data="today_tasks")],
        [
            InlineKeyboardButton("📚 موادي وجداولي", callback_data="subjects_menu"),
            InlineKeyboardButton("➕ سوي جدول جديد", callback_data="wizard_start"),
        ],
        [InlineKeyboardButton("⭐ اشتراكي", callback_data="subscription_menu")],
    ]

    if config.is_admin(user_id):
        kb.append([InlineKeyboardButton("👑 لوحة المالك", callback_data="admin_menu")])

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


# ─── Commands Handlers ────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /start and /menu."""
    user = update.effective_user
    if not user:
        return

    # Clear any wizard state
    context.user_data.pop("wizard_state", None)

    db.get_or_create_user(user.id, user.username or "", user.full_name or "")
    chat_id = update.effective_chat.id
    text, kb = build_main_dashboard(user.id)
    await clean_entire_chat(context, chat_id)
    await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    from handlers.today_handler import show_today_tasks
    await show_today_tasks(update, context)


async def subjects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    from handlers.subjects_handler import show_subjects_menu
    await show_subjects_menu(update, context)


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /backup to export SQLite DB."""
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return
    chat_id = update.effective_chat.id
    import os
    if os.path.exists(config.DB_PATH):
        with open(config.DB_PATH, "rb") as db_file:
            await context.bot.send_document(
                chat_id=chat_id,
                document=db_file,
                filename="memory_qudrat_v3.db",
                caption="💾 <b>نسخة احتياطية لقاعدة البيانات V3</b>",
                parse_mode="HTML"
            )


# ─── Central Callback Router ─────────────────────────────────────────────────

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central router for all interactive button clicks."""
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
        context.user_data.pop("wizard_state", None)
        text, kb = build_main_dashboard(user.id)
        await safe_edit(query, text, kb)
        return

    # 1. Wizard Callbacks
    from handlers.schedule_wizard import handle_wizard_callback
    if await handle_wizard_callback(update, context):
        return

    # 2. Today Tasks Callbacks
    from handlers.today_handler import handle_today_callback
    if await handle_today_callback(update, context):
        return

    # 3. Subjects & Schedules Callbacks
    from handlers.subjects_handler import handle_subjects_callback
    if await handle_subjects_callback(update, context):
        return

    # 4. Subscription Callbacks
    from handlers.subscription_handler import handle_subscription_callback
    if await handle_subscription_callback(update, context):
        return

    # 5. Admin Callbacks
    from handlers.admin_handler import handle_admin_callback
    if await handle_admin_callback(update, context):
        return

    logger.warning("Unhandled callback: %s", data)
