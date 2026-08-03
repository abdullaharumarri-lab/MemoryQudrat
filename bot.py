import socket
import logging
import pytz
from datetime import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

import database as db
from config import TELEGRAM_BOT_TOKEN, ALLOWED_CHANNEL_ID
from handlers.main_menu import main_menu_handler, button_handler
from handlers.pdf_handler import pdf_document_handler, json_document_handler, template_command
from handlers.quiz_handler import quiz_answer_handler
from utils import send_clean_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── DNS Patch for blocked networks ───────────────────────────────────────────
TELEGRAM_IPS = [
    "149.154.167.220",
    "149.154.167.197",
    "91.108.56.180",
    "149.154.166.110",
]

_original_getaddrinfo = socket.getaddrinfo

def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    host_str = host.decode("utf-8") if isinstance(host, bytes) else str(host)
    if "telegram.org" in host_str:
        for ip in TELEGRAM_IPS:
            try:
                result = _original_getaddrinfo(ip, port, socket.AF_INET, type, proto, flags)
                if result:
                    logger.debug(f"DNS patch: {host_str} -> {ip}")
                    return result
            except Exception:
                continue
    return _original_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo
logger.info("DNS patch applied for Telegram connectivity.")
# ──────────────────────────────────────────────────────────────────────────────


def schedule_reminder(job_queue, chat_id: int):
    """Schedule (or reschedule) the daily 6 PM reminder for a chat_id."""
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(f"reminder_{chat_id}"):
        job.schedule_removal()
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    job_queue.run_daily(
        daily_reminder,
        time=time(18, 0, tzinfo=riyadh_tz),
        data=chat_id,
        name=f"reminder_{chat_id}",
    )


async def daily_reminder(context):
    """Send a daily reminder if there are due reviews."""
    reviews = db.get_due_quiz_reviews()
    weak = db.get_due_weak_questions()

    if not reviews and not weak:
        return

    parts = []
    if reviews:
        parts.append(f"🔁 {len(reviews)} مراجعة كويز")
    if weak:
        parts.append(f"❌ {len(weak)} سؤال ضعيف")

    text = (
        "🌅 *تذكير يومي — MemoryQudrat*\n\n"
        "عندك مراجعات اليوم:\n" + "\n".join(f"• {p}" for p in parts) +
        "\n\nافتح البوت وابدأ المراجعة 💪"
    )

    chat_id = context.job.data
    await send_clean_message(context=context, chat_id=chat_id, text=text, parse_mode="Markdown")


async def start_command(update: Update, context):
    """Handle /start command — works in both DMs and channels."""
    chat_id = update.effective_chat.id
    msg = update.effective_message  # works for both message and channel_post

    # If channel mode is ON and this is NOT the allowed channel → show redirect
    if ALLOWED_CHANNEL_ID != 0 and chat_id != ALLOWED_CHANNEL_ID:
        await msg.reply_text(
            "🔒 *MemoryQudrat — نظام المراجعة الذكي*\n\n"
            "هذا البوت يعمل فقط داخل قناة خاصة.\n\n"
            "📌 *طريقة التفعيل:*\n"
            "1️⃣ أنشئ قناة خاصة في تيليجرام\n"
            "2️⃣ أضف البوت كمشرف في القناة\n"
            "3️⃣ أرسل /start داخل القناة\n\n"
            "بعد التفعيل يمكنك رفع ملفات الكويز ومتابعة مراجعاتك 🧠",
            parse_mode="Markdown",
        )
        return

    db.save_chat_id(chat_id)
    schedule_reminder(context.job_queue, chat_id)

    text = (
        "👋 أهلاً بك في *MemoryQudrat*!\n\n"
        "نظام مراجعة ذكي باستخدام التكرار المتباعد 🧠\n"
        "اختر ما تريد:"
    )
    from handlers.main_menu import main_menu_keyboard
    await send_clean_message(
        context, chat_id, text, update=update, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
    )


async def channel_guard(update: Update) -> bool:
    """Return True if the update is from the allowed channel (or no restriction set)."""
    if ALLOWED_CHANNEL_ID == 0:
        return True
    chat_id = update.effective_chat.id if update.effective_chat else None
    return chat_id == ALLOWED_CHANNEL_ID


async def guarded_button_handler(update: Update, context):
    if not await channel_guard(update):
        return
    await button_handler(update, context)


async def guarded_quiz_answer_handler(update: Update, context):
    if not await channel_guard(update):
        return
    await quiz_answer_handler(update, context)


async def guarded_json_handler(update: Update, context):
    if not await channel_guard(update):
        return
    await json_document_handler(update, context)


async def guarded_pdf_handler(update: Update, context):
    if not await channel_guard(update):
        return
    await pdf_document_handler(update, context)


async def guarded_menu_handler(update: Update, context):
    if not await channel_guard(update):
        return
    await main_menu_handler(update, context)


async def guarded_template_handler(update: Update, context):
    if not await channel_guard(update):
        return
    await template_command(update, context)


async def error_handler(update, context):
    """Log errors — silently ignore stale message/button errors."""
    error_str = str(context.error)

    ignored_errors = [
        "Button_data_invalid",
        "Message is not modified",
        "Bad Request: message to edit not found",
        "Bad Request: message can't be deleted",
        "Bad Request: MESSAGE_ID_INVALID",
        "Query is too old",
    ]
    for ignored in ignored_errors:
        if ignored in error_str:
            logger.warning(f"Ignored stale error: {error_str}")
            if update and hasattr(update, 'callback_query') and update.callback_query:
                try:
                    await update.callback_query.answer()
                except Exception:
                    pass
            return

    logger.error(f"Error: {context.error}")


async def post_init(application):
    """Restore daily reminders for all known users after bot restarts."""
    db.clear_session()
    logger.info("Cleared stale session on startup.")

    chat_ids = db.get_all_chat_ids()
    for chat_id in chat_ids:
        schedule_reminder(application.job_queue, chat_id)
    logger.info(f"Restored reminders for {len(chat_ids)} user(s).")

    if ALLOWED_CHANNEL_ID != 0:
        logger.info(f"Channel mode: ACTIVE — only responding to channel {ALLOWED_CHANNEL_ID}")
    else:
        logger.info("Channel mode: DISABLED — responding to all chats")


def main():
    db.init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # /start — allowed everywhere (DM shows redirect, channel runs bot)
    # CommandHandler handles DMs; MessageHandler handles channel posts
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POSTS & filters.Regex(r'^/start'),
        start_command
    ))

    # /menu and /template — channel posts only
    app.add_handler(CommandHandler("menu", guarded_menu_handler))
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POSTS & filters.Regex(r'^/menu'),
        guarded_menu_handler
    ))
    app.add_handler(CommandHandler("template", guarded_template_handler))
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POSTS & filters.Regex(r'^/template'),
        guarded_template_handler
    ))

    # Document handlers
    app.add_handler(MessageHandler(filters.Document.PDF, guarded_pdf_handler))
    app.add_handler(MessageHandler(filters.Document.MimeType("application/json"), guarded_json_handler))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json"), guarded_json_handler))

    # Quiz answer buttons
    app.add_handler(CallbackQueryHandler(guarded_quiz_answer_handler, pattern=r"^ans_"))

    # All other buttons
    app.add_handler(CallbackQueryHandler(guarded_button_handler))

    # Error handler
    app.add_error_handler(error_handler)

    logger.info("MemoryQudrat bot started!")
    import os
    PORT = int(os.environ.get('PORT', 8000))
    APP_URL = os.environ.get('RENDER_EXTERNAL_URL') or os.environ.get('APP_URL')

    if APP_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=APP_URL
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

