import socket
import logging
import pytz
import os
from datetime import time

from telegram import Update, BotCommand, MenuButtonCommands
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

import database as db
from config import TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, TIMEZONE
from handlers.main_menu import (
    start_command,
    today_command,
    subjects_command,
    backup_command,
    button_router,
)
from handlers.subscription_handler import (
    subscription_command,
    precheckout_callback,
    successful_payment_callback,
)
from handlers.admin_handler import (
    admin_command,
    gift_sub_command,
    broadcast_command,
    set_channel_command,
)
from utils import send_clean_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─── Daily Nudge & Reminders ──────────────────────────────────────────────────

def schedule_daily_reminder(job_queue, chat_id: int, hour: int = 5, minute: int = 0):
    if job_queue is None or not chat_id:
        return
    for job in job_queue.get_jobs_by_name(f"nudge_{chat_id}"):
        job.schedule_removal()

    try:
        try:
            from zoneinfo import ZoneInfo
            riyadh_tz = ZoneInfo(TIMEZONE)
        except ImportError:
            riyadh_tz = pytz.timezone(TIMEZONE)

        job_queue.run_daily(
            daily_reminder_job,
            time=time(int(hour), int(minute), tzinfo=riyadh_tz),
            data=chat_id,
            name=f"nudge_{chat_id}",
        )
        logger.info("Scheduled personal daily reminder for chat %s at %02d:%02d Riyadh time", chat_id, hour, minute)
    except Exception as e:
        logger.warning("Could not schedule daily reminder: %s", e)


async def send_daily_reminder_to_chat(context, chat_id: int):
    """Sends a friendly morning buddy nudge to the student."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    tasks = db.get_today_tasks(user_id=chat_id)
    new_count = len(tasks["new_lessons"])
    rev_count = len(tasks["due_reviews"])

    if new_count > 0 or rev_count > 0:
        lines = [
            "🌅 <b>صباح الروقان والهمة العالية يا بطل! ☕🧠</b>\n",
            "جاهز ننجز خطوة جديدة اليوم؟ جدولك لليوم فيه:\n",
        ]
        if new_count > 0:
            lines.append(f"• 📖 <b>{new_count}</b> دروس جديدة للمذاكرة")
        if rev_count > 0:
            lines.append(f"• 🔄 <b>{rev_count}</b> مراجعات لتثبيت الذاكرة")
        lines.append("\n💪 <i>خطوات بسيطة يومياً تصنع الـ 100% بإذن الله! اضغط بالأسفل وابدأ فوراً:</i>")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 افتح مهام اليوم", callback_data="today_tasks")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, "\n".join(lines), reply_markup=kb)
    else:
        subjects = db.get_user_subjects(chat_id)
        if subjects:
            text = (
                "🌅 <b>صباح الخير يا بطل! 🌟</b>\n\n"
                "كفو عليك! جدولك نظيف اليوم وما عندك أي دروس أو مراجعات متراكمة ✨\n"
                "استمتع بيومك، أو راجع موادك وجداولك وقت ما تحب!"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 موادي وجداولي", callback_data="subjects_menu")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
            ])
            await send_clean_message(context, chat_id, text, reply_markup=kb)


async def daily_reminder_job(context):
    chat_id = context.job.data
    await send_daily_reminder_to_chat(context, chat_id)


# ─── User Text & Media Handlers ──────────────────────────────────────────────

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes incoming text to wizard if active, or presents dashboard."""
    from handlers.schedule_wizard import handle_wizard_text_input
    if await handle_wizard_text_input(update, context):
        return

    # Outside wizard: send clean main dashboard
    chat_id = update.effective_chat.id
    from handlers.main_menu import build_main_dashboard
    user = update.effective_user
    text, kb = build_main_dashboard(user.id if user else chat_id)
    await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)


async def handle_incoming_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Friendly reminder that V3 is focused on study schedules and spaced repetition."""
    chat_id = update.effective_chat.id
    reply = (
        "💡 <b>يا هلا يا بطل!</b>\n\n"
        "أنا الآن رفيقك الذكي لجدولة المواد والمذاكرة بالتكرار المتباعد 📅✨\n"
        "ما يحتاج ترسل صور، اضغط بالأسفل ورتب جدولك للمذاكرة فوراً!"
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ سوي جدول جديد", callback_data="wizard_start")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
    ])
    await send_clean_message(context, chat_id, reply, update=update, reply_markup=kb)


# ─── Post Init Setup ──────────────────────────────────────────────────────────

async def post_init(application):
    logger.info("Initializing MemoryQudrat V3 bot...")
    db.init_db()

    # Schedule reminders for all users with their preferred time
    users = db.get_all_users_for_reminders()
    for u in users:
        u_id = u.get("id") or u.get("user_id")
        if not u_id:
            continue
        r_h = u.get("reminder_hour", 5)
        r_m = u.get("reminder_minute", 0)
        schedule_daily_reminder(application.job_queue, u_id, r_h, r_m)

    # Set Telegram Menu Commands
    commands = [
        BotCommand("start", "🏠 القائمة الرئيسية"),
        BotCommand("today", "📅 جدول اليوم وإنجازي"),
        BotCommand("subjects", "📚 موادي وجداولي"),
        BotCommand("subscribe", "⭐ باقات الاشتراك والترقية"),
        BotCommand("backup", "💾 نسخة احتياطية (للمالك)"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Bot commands and Menu Button configured successfully.")
    except Exception as e:
        logger.warning("Could not set bot commands: %s", e)


async def error_handler(update, context):
    err = str(context.error)
    ignored = [
        "Message is not modified",
        "Bad Request: message to edit not found",
        "Bad Request: message can't be deleted",
        "Query is too old",
    ]
    for ign in ignored:
        if ign in err:
            return
    logger.error("Bot encountered error: %s", context.error, exc_info=context.error)


# ─── Main Application Entry ───────────────────────────────────────────────────

def main():
    db.init_db()

    # Configure aggressive TCP keep-alive and HTTPX limits to prevent silent connection dropouts
    from telegram.request import HTTPXRequest
    import httpx

    socket_options = [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60),
        (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10),
        (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5),
    ]
    limits = httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    )

    request = HTTPXRequest(
        connection_pool_size=50,
        connect_timeout=10.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
        socket_options=socket_options,
        httpx_kwargs={"limits": limits},
    )

    get_updates_request = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=10.0,
        read_timeout=60.0,
        write_timeout=20.0,
        pool_timeout=10.0,
        socket_options=socket_options,
        httpx_kwargs={"limits": limits},
    )

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )

    # 1. Commands Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", start_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("subjects", subjects_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("subscribe", subscription_command))
    app.add_handler(CommandHandler("pricing", subscription_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("gift_sub", gift_sub_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("set_channel", set_channel_command))

    # 2. Telegram Stars In-App Payments
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # 3. Text and Media Handlers
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_incoming_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))

    # 4. Interactive Callbacks Router
    app.add_handler(CallbackQueryHandler(button_router))

    app.add_error_handler(error_handler)

    logger.info("MemoryQudrat V3 Active Recall & Schedule Bot started successfully!")

    port = int(os.environ.get("PORT", 8000))
    app_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL")
    if app_url:
        app.run_webhook(listen="0.0.0.0", port=port, webhook_url=app_url)
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES, timeout=20, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
