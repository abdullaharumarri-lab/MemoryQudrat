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
from handlers.pdf_handler import json_document_handler, template_command, pdf_document_handler, excel_document_handler
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

    try:
        if hour is None or minute is None:
            user = db.get_user(chat_id)
            if user:
                hour = user.get("reminder_hour") or 4
                minute = user.get("reminder_minute") or 30
            else:
                hour = 4
                minute = 30
        if hour is None: hour = 4
        if minute is None: minute = 30

        riyadh_tz = pytz.timezone("Asia/Riyadh")
        job_queue.run_daily(
            daily_reminder,
            time=time(int(hour), int(minute), tzinfo=riyadh_tz),
            data=chat_id,
            name=f"reminder_{chat_id}",
        )
        logger.info("Scheduled daily reminder for chat_id=%s at %02d:%02d Riyadh time", chat_id, hour, minute)
    except Exception as e:
        logger.warning("Could not schedule reminder for chat_id=%s: %s", chat_id, e)


async def daily_reminder(context):
    try:
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
    except Exception as e:
        logger.warning("Error in daily_reminder: %s", e)


async def start_command(update: Update, context):
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id if user else None
    
    try:
        if user:
            db.save_or_update_user(user.id, user.username, user.full_name)
        db.save_chat_id(chat_id)
    except Exception as e:
        logger.warning("Could not save user/chat_id in start_command: %s", e)

    try:
        schedule_reminder(context.job_queue, chat_id)
    except Exception as e:
        logger.warning("Could not schedule reminder in start_command: %s", e)

    user_name = user.first_name if user and user.first_name else "صديقنا"
    text = (
        f"👋 أهلاً بك يا <b>{html.escape(user_name)}</b> في بوت <b>ذاكرة القدرات</b>! 🧠\n\n"
        f"📚 هنا ستجد كويزات القدرات مرتبة ومنظمة لتساعدك على المذاكرة الفعّالة.\n\n"
        f"✨ <b>كيف يعمل البوت؟</b>\n"
        f"• 📝 اختر كويزاً من <b>«📚 الكويزات»</b> وابدأ الحل فوراً\n"
        f"• ❌ أخطاؤك تُحفظ تلقائياً في <b>«❓ الأسئلة الضعيفة»</b> لإتقانها\n"
        f"• 🔁 اضغط <b>«أضف لمراجعاتي»</b> ليتولى البوت تذكيرك بالمواعيد الذكية\n"
        f"• 🔔 راجع مهامك اليومية من <b>«🔔 مراجعات اليوم»</b>\n\n"
        f"📢 <b>القناة الرسمية:</b> <a href=\"https://t.me/MemoryQudrat\">@MemoryQudrat</a>\n\n"
        f"اختر ما تريد من القائمة بالأسفل:"
    )
    from handlers.main_menu import main_menu_keyboard
    from utils import clean_entire_chat

    # Collect all message IDs to clean before showing fresh start screen
    extra = []
    user_msg_id = update.message.message_id if update.message else None
    if user_msg_id:
        extra.append(user_msg_id)

    quiz_cleanup_ids = context.user_data.pop("cleanup_message_ids", [])
    if quiz_cleanup_ids:
        extra.extend(quiz_cleanup_ids)

    if user_id:
        try:
            active_sess = db.get_session(user_id=user_id)
            if active_sess:
                sess_ids = active_sess.get("session_message_ids", [])
                if sess_ids:
                    extra.extend(sess_ids)
                db.clear_session(user_id=user_id)
        except Exception:
            pass

    context.user_data.pop(f"active_passage_{chat_id}", None)

    try:
        await clean_entire_chat(context, chat_id, keep_message_id=None, extra_ids=extra)
    except Exception as e:
        logger.warning("Could not clean chat in start_command: %s", e)

    try:
        await send_clean_message(
            context, chat_id, text,
            reply_markup=main_menu_keyboard(user_id=user_id)
        )
    except Exception as e:
        logger.exception("Could not send start_command message: %s", e)


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
    logger.info("Bot initializing post_init...")

    try:
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
    except Exception as e:
        logger.warning("Error scheduling reminders in post_init: %s", e)

    # ── Telegram Menu Button (≡) & Commands Setup with Admin Scoping ──────────
    try:
        from telegram import BotCommand, MenuButtonCommands, BotCommandScopeDefault, BotCommandScopeChat
        from config import ADMIN_IDS

        public_commands = [
            BotCommand("start", "🏠 القائمة الرئيسية"),
            BotCommand("menu", "📋 فتح القائمة الرئيسية"),
            BotCommand("today", "🔔 مراجعات اليوم"),
            BotCommand("schedule", "📅 جدول المراجعة"),
            BotCommand("weak", "❓ الأسئلة الضعيفة"),
            BotCommand("stats", "📊 إحصائياتي وتقدمي"),
            BotCommand("find", "🔍 البحث عن كويز"),
            BotCommand("help", "💡 شرح نظام التكرار المتباعد"),
        ]

        admin_commands = public_commands + [
            BotCommand("admin", "👑 لوحة تحكم المشرف"),
            BotCommand("fixstage", "🛠 ضبط مراحل الكويزات"),
            BotCommand("broadcast", "📢 إرسال إذاعة جماعية"),
        ]

        # 1. Default commands for all regular users (WITHOUT /admin)
        await application.bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())

        # 2. Admin-only commands with /admin visible ONLY in admin private chats
        for admin_id in ADMIN_IDS:
            try:
                await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as e:
                logger.debug("Could not set admin commands for %s: %s", admin_id, e)

        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Bot commands with admin scoping and ≡ Menu button set successfully.")
    except Exception as e:
        logger.warning("Could not set bot commands in post_init: %s", e)


def main():
    db.init_db()

    # Configure aggressive TCP keep-alive and HTTPX connection limits to prevent 
    # silent connection drops by the VPS NAT/firewall which cause extreme latency.
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
        httpx_kwargs={"limits": limits}
    )

    # Dedicated get_updates request with longer read_timeout to prevent polling disconnects
    get_updates_request = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=10.0,
        read_timeout=60.0,
        write_timeout=20.0,
        pool_timeout=10.0,
        socket_options=socket_options,
        httpx_kwargs={"limits": limits}
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

    from handlers.admin_handler import admin_command, admin_broadcast_command
    from handlers.main_menu import today_command, weak_command, schedule_command, stats_command, help_command, find_command

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", main_menu_handler))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("weak", weak_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("find", find_command))
    app.add_handler(CommandHandler("check", find_command))
    app.add_handler(CommandHandler("search", find_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("template", template_command))
    app.add_handler(CommandHandler("fixstage", fixstage_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_command))

    app.add_handler(MessageHandler(
        filters.Document.FileExtension("xlsx") | filters.Document.FileExtension("xls") | filters.Document.FileExtension("csv") |
        filters.Document.MimeType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") |
        filters.Document.MimeType("application/vnd.ms-excel") | filters.Document.MimeType("text/csv"),
        excel_document_handler
    ))
    app.add_handler(MessageHandler(
        filters.Document.MimeType("application/json") | filters.Document.FileExtension("json"),
        json_document_handler
    ))
    app.add_handler(MessageHandler(
        filters.Document.PDF | filters.Document.FileExtension("pdf"),
        pdf_document_handler
    ))

    from handlers.creation_handler import handle_media_upload, handle_incoming_poll
    app.add_handler(MessageHandler(
        filters.PHOTO, handle_media_upload
    ))
    app.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.Document.MimeType("application/json") & ~filters.Document.FileExtension("json") & ~filters.Document.PDF & ~filters.Document.FileExtension("pdf"), handle_media_upload
    ))
    
    # ── Native Telegram Poll / Quiz Handler ──────────────────────────────────
    app.add_handler(MessageHandler(
        filters.POLL, handle_incoming_poll
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
        app.run_polling(allowed_updates=Update.ALL_TYPES, timeout=20, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
