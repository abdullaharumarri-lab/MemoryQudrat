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
from handlers.main_menu import main_menu_handler, button_handler, url_text_handler, fixstage_command
from handlers.pdf_handler import json_document_handler, template_command
from handlers.quiz_handler import quiz_answer_handler
from utils import send_clean_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# DNS Patch
TELEGRAM_IPS = ["149.154.167.220","149.154.167.197","91.108.56.180","149.154.166.110"]
_orig = socket.getaddrinfo

def _patched(host, port, family=0, type=0, proto=0, flags=0):
    h = host.decode("utf-8") if isinstance(host, bytes) else str(host)
    if "telegram.org" in h:
        for ip in TELEGRAM_IPS:
            try:
                r = _orig(ip, port, socket.AF_INET, type, proto, flags)
                if r: return r
            except Exception: continue
    return _orig(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched


def schedule_reminder(job_queue, chat_id: int):
    if job_queue is None: return
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
    reviews = db.get_due_quiz_reviews()
    weak = db.get_due_weak_questions()
    if not reviews and not weak: return
    parts = []
    if reviews: parts.append(f"🔁 {len(reviews)} مراجعة كويز")
    if weak: parts.append(f"❌ {len(weak)} سؤال ضعيف")
    text = (
        "🌅 <b>تذكير يومي — MemoryQudrat</b>\n\n"
        "عندك مراجعات اليوم:\n" + "\n".join(f"• {p}" for p in parts) +
        "\n\nافتح البوت وابدأ المراجعة 💪"
    )
    chat_id = context.job.data
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ حل مراجعات اليوم", callback_data="due_reviews")
    ]])
    
    await send_clean_message(
        context=context, chat_id=chat_id, text=text, reply_markup=keyboard
    )


async def start_command(update: Update, context):
    chat_id = update.effective_chat.id
    db.save_chat_id(chat_id)
    schedule_reminder(context.job_queue, chat_id)
    text = (
        "👋 أهلاً بك في <b>MemoryQudrat</b>!\n\n"
        "نظام مراجعة ذكي باستخدام التكرار المتباعد 🧠\n"
        "اختر ما تريد:"
    )
    from handlers.main_menu import main_menu_keyboard
    await send_clean_message(
        context, chat_id, text, update=update,
        reply_markup=main_menu_keyboard()
    )


async def error_handler(update, context):
    err = str(context.error)
    ignored = [
        "Message is not modified",
        "Bad Request: message to edit not found",
        "Bad Request: message can't be deleted",
        "Bad Request: MESSAGE_ID_INVALID",
        "Query is too old",
    ]
    for i in ignored:
        if i in err:
            logger.warning(f"Ignored: {err}")
            if update and hasattr(update, "callback_query") and update.callback_query:
                try: await update.callback_query.answer()
                except Exception: pass
            return
    logger.error(f"Error: {context.error}", exc_info=context.error)


async def post_init(application):
    db.clear_session()
    logger.info("Cleared stale session.")
    for chat_id in db.get_all_chat_ids():
        schedule_reminder(application.job_queue, chat_id)
    logger.info("Reminders restored.")


def main():
    db.init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", main_menu_handler))
    app.add_handler(CommandHandler("template", template_command))
    app.add_handler(CommandHandler("fixstage", fixstage_command))

    app.add_handler(MessageHandler(
        filters.Document.MimeType("application/json"), json_document_handler
    ))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("json"), json_document_handler
    ))
    
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, url_text_handler
    ))

    # Catch-all to see if messages are even arriving
    async def debug_fallback(update, context):
        with open("bot_debug.log", "a", encoding="utf-8") as f:
            f.write(f"Received update: {update.to_dict()}\n")
            
    app.add_handler(MessageHandler(filters.ALL, debug_fallback))

    app.add_handler(CallbackQueryHandler(quiz_answer_handler, pattern=r"^ans_"))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_error_handler(error_handler)

    logger.info("MemoryQudrat bot started!")
    import os
    PORT = int(os.environ.get("PORT", 8000))
    APP_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL")
    if APP_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=APP_URL)
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
