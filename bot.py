import socket
import logging
import pytz
from datetime import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

import database as db
from config import TELEGRAM_BOT_TOKEN
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
# Telegram IPs (api.telegram.org) — bypasses DNS blocking
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
    """Handle /start command."""
    chat_id = update.effective_chat.id

    # Schedule daily reminder at 6:00 PM Riyadh time
    if context.job_queue is not None:
        current_jobs = context.job_queue.get_jobs_by_name(f"reminder_{chat_id}")
        for job in current_jobs:
            job.schedule_removal()

        riyadh_tz = pytz.timezone("Asia/Riyadh")
        context.job_queue.run_daily(
            daily_reminder,
            time=time(18, 0, tzinfo=riyadh_tz),
            data=chat_id,
            name=f"reminder_{chat_id}",
        )

    # Send main menu using clean chat mechanism
    text = (
        "👋 أهلاً بك في *MemoryQudrat*!\n\n"
        "نظام مراجعة ذكي باستخدام التكرار المتباعد 🧠\n"
        "اختر ما تريد:"
    )
    from handlers.main_menu import main_menu_keyboard
    await send_clean_message(
        context, chat_id, text, update=update, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
    )


async def error_handler(update, context):
    """Log errors."""
    logger.error(f"Error: {context.error}")


def main():
    db.init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", main_menu_handler))
    app.add_handler(CommandHandler("template", template_command))

    # Document handlers
    app.add_handler(MessageHandler(filters.Document.PDF, pdf_document_handler))
    app.add_handler(MessageHandler(filters.Document.MimeType("application/json"), json_document_handler))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json"), json_document_handler))

    # Answer handlers — must be before generic button handler
    app.add_handler(CallbackQueryHandler(quiz_answer_handler, pattern=r"^ans_"))

    # All other button presses
    app.add_handler(CallbackQueryHandler(button_handler))

    # Error handler
    app.add_error_handler(error_handler)

    logger.info("MemoryQudrat bot started!")
    import os
    PORT = int(os.environ.get('PORT', 8000))
    APP_URL = os.environ.get('APP_URL')

    if APP_URL:
        # Run using Webhooks for Render/PaaS
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=APP_URL
        )
    else:
        # Run using Polling for local development
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
