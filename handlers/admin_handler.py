import html
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest, TelegramError

import database as db
from config import is_admin
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def build_admin_dashboard() -> tuple[str, InlineKeyboardMarkup]:
    """Generates platform analytics text and action keyboard for Admin."""
    stats = db.get_platform_stats()

    text = (
        "👑 <b>لوحة تحكم المشرف — ذاكرة القدرات 🧠</b>\n\n"
        "👥 <b>إحصائيات الطلاب والنمو:</b>\n"
        f"• إجمالي الطلاب المسجلين: <b>{stats['total_users']}</b> طالب\n"
        f"• النشطون اليوم: <b>{stats['active_today']}</b> طالب\n"
        f"• النشطون خلال آخر 7 أيام: <b>{stats['active_7days']}</b> طالب\n"
        f"• إجمالي الجلسات المكتملة: <b>{stats['total_sessions']}</b> جلسة\n"
        f"• إجمالي الأسئلة المحلولة: <b>{stats['total_questions_solved']}</b> (دقة عامة: {stats['accuracy']}%)\n\n"
        "📚 <b>إحصائيات المحتوى والبنك العام:</b>\n"
        f"• الكويزات العامة: <b>{stats['total_public_quizzes']}</b> كويز\n"
        f"• بنك الأسئلة المتاحة: <b>{stats['total_public_questions']}</b> سؤال\n"
        f"• المجلدات والأقسام: <b>{stats['total_categories']}</b> مجلد\n"
        f"• جداول المراجعة النشطة: <b>{stats['total_active_reviews']}</b> مراجعة\n"
        f"• الأسئلة الضعيفة النشطة: <b>{stats['total_active_weak']}</b> سؤال\n\n"
        "اختر الإجراء المطلوب:"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 إرسال إذاعة عامة (Broadcast)", callback_data="admin_broadcast_prompt"),
            InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="admin_refresh_stats"),
        ],
        [
            InlineKeyboardButton("🔧 تعديل مراحل الكويزات (/fixstage)", callback_data="fixstage_page_1"),
            InlineKeyboardButton("📚 تصفح بنك الكويزات", callback_data="public_bank_root"),
        ],
        [
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu"),
        ]
    ])
    return text, kb


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /admin command."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        err_msg = f"❌ هذا الأمر متاح للمشرف فقط.\n(معرّفك: <code>{user.id if user else 'غير معروف'}</code>)"
        if update.message:
            await update.message.reply_text(err_msg, parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.answer("❌ غير مصرح.", show_alert=True)
        return

    text, kb = build_admin_dashboard()
    chat_id = update.effective_chat.id
    if update.message:
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
    elif update.callback_query:
        await safe_edit(update.callback_query, text, kb)


async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /broadcast command."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("❌ هذا الأمر متاح للمشرف فقط.", parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.answer("❌ غير مصرح.", show_alert=True)
        return

    context.user_data["waiting_for_broadcast"] = True
    recipients = db.get_all_broadcast_recipients()

    text = (
        "📢 <b>إرسال إذاعة جماعية (Broadcast)</b>\n\n"
        f"المستلمون المحتملون: <b>{len(recipients)}</b> مستخدم.\n\n"
        "أرسل نص الرسالة أو الإعلان الآن الذي تريد نشره لجميع مستخدمي البوت:\n"
        "<i>(يدعم تنسيقات HTML والروابط)</i>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء الإذاعة", callback_data="admin_cancel_broadcast")]])
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.callback_query:
        await safe_edit(update.callback_query, text, kb)


async def handle_broadcast_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles incoming text when admin is composing a broadcast. Returns True if consumed."""
    if not context.user_data.get("waiting_for_broadcast"):
        return False

    user = update.effective_user
    if not user or not is_admin(user.id):
        context.user_data.pop("waiting_for_broadcast", None)
        return False

    # Guard: only accept text messages, not photos/files/stickers
    if not update.message or not update.message.text:
        if update.message:
            await update.message.reply_text(
                "⚠️ <b>يرجى إرسال نص فقط</b> لرسالة الإذاعة (لا تُدعم الصور أو الملفات).",
                parse_mode="HTML"
            )
        return True  # consumed but rejected

    broadcast_text = update.message.text
    context.user_data["waiting_for_broadcast"] = False
    context.user_data["pending_broadcast_text"] = broadcast_text

    recipients = db.get_all_broadcast_recipients()
    
    preview = (
        "📢 <b>معاينة رسالة الإذاعة:</b>\n\n"
        "────────────────────\n"
        f"{broadcast_text}\n"
        "────────────────────\n\n"
        f"👥 سيتم الإرسال إلى <b>{len(recipients)}</b> مستخدم.\n"
        "هل تؤكد الإرسال الآن؟"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نعم، أرسل الآن", callback_data="admin_confirm_broadcast"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel_broadcast"),
        ]
    ])
    await update.message.reply_text(preview, parse_mode="HTML", reply_markup=kb)
    return True


async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all callback queries starting with admin_"""
    query = update.callback_query
    data = query.data
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.answer("❌ هذا الإجراء متاح للمشرف فقط.", show_alert=True)
        return

    # ── Refresh Dashboard ──
    if data == "admin_refresh_stats":
        text, kb = build_admin_dashboard()
        await safe_edit(query, text, kb)
        await query.answer("🔄 تم تحديث الإحصائيات!")

    # ── Broadcast Prompt ──
    elif data == "admin_broadcast_prompt":
        context.user_data["waiting_for_broadcast"] = True
        recipients = db.get_all_broadcast_recipients()
        text = (
            "📢 <b>إرسال إذاعة جماعية (Broadcast)</b>\n\n"
            f"المستلمون المحتملون: <b>{len(recipients)}</b> مستخدم.\n\n"
            "أرسل نص الرسالة أو الإعلان الآن في رسالة نصية:\n"
            "<i>(يدعم تنسيقات HTML والروابط)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel_broadcast")]])
        await safe_edit(query, text, kb)

    # ── Cancel Broadcast ──
    elif data == "admin_cancel_broadcast":
        context.user_data.pop("waiting_for_broadcast", None)
        context.user_data.pop("pending_broadcast_text", None)
        text, kb = build_admin_dashboard()
        await safe_edit(query, "❌ تم إلغاء الإذاعة.\n\n" + text, kb)

    # ── Confirm and Execute Broadcast ──
    elif data == "admin_confirm_broadcast":
        broadcast_text = context.user_data.pop("pending_broadcast_text", None)
        if not broadcast_text:
            await query.answer("⚠️ لم يتم العثور على نص الإذاعة.", show_alert=True)
            return

        recipients = db.get_all_broadcast_recipients()
        total = len(recipients)
        
        await safe_edit(query, f"⏳ <b>جاري إرسال الإذاعة إلى {total} مستخدم...</b>\nيرجى الانتظار...", None)
        
        sent_count = 0
        blocked_count = 0
        failed_count = 0

        for chat_id in recipients:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📢 <b>تنبيه من إدارة ذاكرة القدرات 🧠:</b>\n\n{broadcast_text}",
                    parse_mode="HTML",
                )
                sent_count += 1
            except Forbidden:
                # User blocked the bot
                blocked_count += 1
                logger.info("Broadcast: User %s has blocked the bot.", chat_id)
            except Exception as e:
                failed_count += 1
                logger.warning("Broadcast: Failed sending to %s: %s", chat_id, e)

            # Throttle slightly to respect Telegram rate limits (~25 msgs/sec)
            await asyncio.sleep(0.04)

        report = (
            "📢 <b>اكتملت الإذاعة الجماعية بنجاح!</b>\n\n"
            f"✅ تم الإرسال بنجاح: <b>{sent_count}</b>\n"
            f"🚫 قاموا بحظر البوت: <b>{blocked_count}</b>\n"
            f"❌ فشل لسبب آخر: <b>{failed_count}</b>\n"
            f"👥 إجمالي المحاولات: <b>{total}</b>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 لوحة الأدمن", callback_data="admin_refresh_stats")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
        await query.message.reply_text(report, parse_mode="HTML", reply_markup=kb)
