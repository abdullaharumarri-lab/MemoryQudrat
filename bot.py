import socket
import logging
import pytz
import html
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
from utils import send_clean_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def schedule_reminder(job_queue, chat_id: int, hour: int = None, minute: int = None):
    if job_queue is None: return
    for job in job_queue.get_jobs_by_name(f"reminder_{chat_id}"):
        job.schedule_removal()

    if hour is None or minute is None:
        user = db.get_user(chat_id)
        if user:
            hour = user.get("reminder_hour", 4)
            minute = user.get("reminder_minute", 30)
        else:
            hour = 4
            minute = 30

    riyadh_tz = pytz.timezone("Asia/Riyadh")
    job_queue.run_daily(
        daily_reminder,
        time=time(hour, minute, tzinfo=riyadh_tz),
        data=chat_id,
        name=f"reminder_{chat_id}",
    )
    logger.info("Scheduled daily reminder for chat_id=%s at %02d:%02d Riyadh time", chat_id, hour, minute)


async def daily_reminder(context):
    chat_id = context.job.data
    user_id = chat_id  # In private chat, chat_id is the user_id
    reviews = db.get_due_quiz_reviews(user_id=user_id)
    weak = db.get_due_weak_questions(user_id=user_id)
    if not reviews and not weak: return
    parts = []
    if reviews: parts.append(f"🔁 {len(reviews)} مراجعة كويز")
    if weak: parts.append(f"❌ {len(weak)} سؤال ضعيف")
    text = (
        "🌅 <b>تذكير يومي — ذاكرة القدرات</b>\n\n"
        "لديك مهام مراجعة مستحقة اليوم:\n" + "\n".join(f"• {p}" for p in parts) +
        "\n\nافتح البوت وابدأ المراجعة لترسيخ معلوماتك 💪"
    )
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ حل مراجعات اليوم", callback_data="due_reviews")
    ]])
    
    await send_clean_message(
        context=context, chat_id=chat_id, text=text, reply_markup=keyboard
    )


async def start_command(update: Update, context):
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id if user else None
    if user:
        db.save_or_update_user(user.id, user.username, user.full_name)
    db.save_chat_id(chat_id)
    schedule_reminder(context.job_queue, chat_id)

    user_name = user.first_name if user and user.first_name else "صديقنا"
    text = (
        f"👋 أهلاً بك يا <b>{html.escape(user_name)}</b> في بوت <b>ذاكرة القدرات</b>! 🌟\n\n"
        f"منصتك الذكية للتدريب على اختبار <b>القدرات (كمي ولفظي)</b> باستخدام تقنية <b>التكرار المتباعد</b> لترسيخ الأفكار والقوانين في الذاكرة طويلة المدى.\n\n"
        f"✨ <b>كيف تبدأ؟</b>\n"
        f"1️⃣ اضغط <b>«📚 بنك الكويزات (العام)»</b> لاختيار نموذج والبدء بالتدريب.\n"
        f"2️⃣ اضغط <b>«🔁 إضافة لجدول مراجعاتي»</b> ليتولى البوت تذكيرك بالموعد المناسب لتثبيت حفظك.\n"
        f"3️⃣ أخطاؤك تُحفظ تلقائياً في <b>«❌ الأسئلة الضعيفة»</b> لتكرارها حتى تتقنها.\n"
        f"4️⃣ يمكنك تخصيص وقت تذكيرك اليومي من <b>«⚙️ الإعدادات»</b>.\n\n"
        f"📢 <b>قناة الشروحات والتحديثات:</b> <a href=\"https://t.me/MemoryQudrat\">@MemoryQudrat</a>\n\n"
        f"اختر ما تريد من القائمة بالأسفل:"
    )
    from handlers.main_menu import main_menu_keyboard
    from utils import clean_entire_chat
    clean_entire_chat(context, chat_id)
    await send_clean_message(
        context, chat_id, text, update=update,
        reply_markup=main_menu_keyboard(user_id=user_id)
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
    logger.info("Cleared stale sessions.")
    users = db.get_all_users()
    scheduled = set()
    for u in users:
        uid = u["user_id"]
        h = u.get("reminder_hour", 4)
        m = u.get("reminder_minute", 30)
        schedule_reminder(application.job_queue, uid, hour=h, minute=m)
        scheduled.add(uid)
    for chat_id in db.get_all_chat_ids():
        if chat_id not in scheduled:
            schedule_reminder(application.job_queue, chat_id)
            scheduled.add(chat_id)
    logger.info("Personalized reminders restored for %s users.", len(scheduled))


def main():
    db.init_db()

    # Configure aggressive TCP keep-alive and HTTPX connection limits to prevent 
    # silent connection drops by the VPS NAT/firewall which cause extreme latency.
    from telegram.request import HTTPXRequest
    import httpx
    
    socket_options = [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30),
        (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10),
        (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3),
    ]
    limits = httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=5.0,  # Drop idle connections quickly
    )
    
    request = HTTPXRequest(
        connection_pool_size=100,
        connect_timeout=5.0,    # Fail fast on connect
        read_timeout=30.0,
        write_timeout=20.0,
        pool_timeout=10.0,
        socket_options=socket_options,
        httpx_kwargs={"limits": limits}
    )

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    from handlers.admin_handler import admin_command, admin_broadcast_command

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", main_menu_handler))
    app.add_handler(CommandHandler("template", template_command))
    app.add_handler(CommandHandler("fixstage", fixstage_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_command))

    app.add_handler(MessageHandler(
        filters.Document.MimeType("application/json"), json_document_handler
    ))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("json"), json_document_handler
    ))

    from handlers.creation_handler import handle_media_upload
    app.add_handler(MessageHandler(
        filters.PHOTO, handle_media_upload
    ))
    app.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.Document.MimeType("application/json") & ~filters.Document.FileExtension("json"), handle_media_upload
    ))
    
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, url_text_handler
    ))

    from telegram.ext import PollAnswerHandler
    from handlers.quiz_handler import poll_answer_handler

    app.add_handler(PollAnswerHandler(poll_answer_handler))
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
