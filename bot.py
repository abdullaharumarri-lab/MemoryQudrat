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
    filters,
)

import database as db
from config import TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, TIMEZONE
from handlers.main_menu import (
    start_command,
    review_command,
    folders_command,
    stats_command,
    backup_command,
    button_router,
)
from handlers.capture_handler import (
    handle_photo_capture,
    handle_text_and_url_capture,
    handle_document_capture,
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
    """Sends the daily active recall nudge to the user."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    stats = db.get_dashboard_stats()
    due = stats["due_count"]
    folders_info = stats["folders_breakdown"]

    if due > 0:
        lines = [
            "🌅 <b>صباح الخير والهمة العالية! 🧠</b>\n",
            f"لديك اليوم <b>{due}</b> مادة مستحقة للمراجعة في جدول التكرار المتباعد:\n",
        ]
        for f in folders_info:
            if f["due_items"] > 0:
                lines.append(f"• {f['icon']} {f['name']}: <b>{f['due_items']}</b> مستحق")

        lines.append("\n💪 <i>10 دقائق فقط الآن تحمي معلوماتك من النسيان بنسبة 100%!</i>")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"▶️ ابدأ مراجعات اليوم ({due})", callback_data="start_review_all")],
            [InlineKeyboardButton("📁 استعراض المجلدات", callback_data="folders_menu")],
        ])
        await send_clean_message(context, chat_id, "\n".join(lines), reply_markup=kb)
    else:
        text = (
            "🌅 <b>صباح الخير يا بطل! 🌟</b>\n\n"
            "أحسنت! جدولك نظيف تماماً ولا توجد مراجعات متراكمة عليك اليوم ✨\n"
            "استغل هذا الوقت لإضافة مسائل جديدة أو الاستراحة."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 استعراض مجلداتي", callback_data="folders_menu")],
            [InlineKeyboardButton("📊 تقريري وإحصائياتي", callback_data="stats_menu")],
        ])
        await send_clean_message(context, chat_id, text, reply_markup=kb)


async def daily_reminder_job(context):
    chat_id = context.job.data
    await send_daily_reminder_to_chat(context, chat_id)


# ─── Post Init Setup ──────────────────────────────────────────────────────────

async def post_init(application):
    logger.info("Initializing personal MemoryQudrat bot...")
    db.init_db()

    # Schedule personal reminder
    r_h = int(db.get_setting("reminder_hour", "5"))
    r_m = int(db.get_setting("reminder_minute", "0"))
    schedule_daily_reminder(application.job_queue, ADMIN_USER_ID, r_h, r_m)

    # Set Telegram Menu Commands
    commands = [
        BotCommand("start", "🏠 لوحة المذاكرة الرئيسية"),
        BotCommand("review", "▶️ ابدأ مراجعات اليوم"),
        BotCommand("folders", "📁 مجلداتي وأرشيف المسائل"),
        BotCommand("stats", "📊 تقريري الإحصائي والنبض"),
        BotCommand("backup", "💾 نسخة احتياطية للبيانات"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Personal bot commands and Menu Button configured successfully.")
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
    app.add_handler(CommandHandler("review", review_command))
    app.add_handler(CommandHandler("today", review_command))
    app.add_handler(CommandHandler("folders", folders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("backup", backup_command))

    # 2. Media Capture Handlers (Zero-friction)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_capture))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_capture))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_and_url_capture))

    # 3. Interactive Callbacks Router
    app.add_handler(CallbackQueryHandler(button_router))

    app.add_error_handler(error_handler)

    logger.info("MemoryQudrat Personal Active Recall OS started successfully!")

    port = int(os.environ.get("PORT", 8000))
    app_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL")
    if app_url:
        app.run_webhook(listen="0.0.0.0", port=port, webhook_url=app_url)
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES, timeout=20, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
