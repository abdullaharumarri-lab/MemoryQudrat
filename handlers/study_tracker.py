# -*- coding: utf-8 -*-
"""
handlers/study_tracker.py — رفيق المذاكرة الذكي بالتكرار المتباعد
تسجيل مواضيع المذاكرة اليومية وتتبع مراجعاتها بضغطة زر وبدقة 100%
"""

import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import ADMIN_USER_ID
from spaced_repetition import stage_label, days_until
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


async def start_log_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates the study log flow: prompts the user to enter the topic/subject title."""
    context.user_data["study_state"] = "awaiting_topic_title"
    context.user_data.pop("study_topic_title", None)

    chat_id = update.effective_chat.id
    query = update.callback_query

    text = (
        "📖 <b>سجلت مذاكرة جديدة — الخطوة 1/2</b> 🧠\n\n"
        "أرسل الآن <b>عنوان الموضوع أو الدرس</b> الذي ذاكرته اليوم:\n\n"
        "<i>أمثلة:</i>\n"
        "• قوانين الحركة والسرعة المتوسطة\n"
        "• التناظر اللفظي - تجميع 1446\n"
        "• النموذج 105 - قسم الهندسة والزوايا\n"
        "• الكسور والجذور والمقارنات"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_study_log")]])

    if query:
        await safe_edit(query, text, kb)
    else:
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)


async def handle_study_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles text input during the study log flow. Returns True if handled, False otherwise."""
    state = context.user_data.get("study_state")
    if not state:
        return False

    if not update.message or not update.message.text:
        return False

    msg = update.message.text.strip()
    chat_id = update.effective_chat.id

    if state == "awaiting_topic_title":
        if len(msg) < 2:
            await send_clean_message(context, chat_id, "⚠️ يرجى كتابة عنوان واضح للموضوع (أكثر من حرفين). أعد كتابة العنوان:", update=update)
            return True

        context.user_data["study_topic_title"] = msg
        context.user_data["study_state"] = "awaiting_topic_notes"

        text = (
            f"📝 <b>ملاحظات أو ملخص الدرس — الخطوة 2/2 (اختياري)</b>\n\n"
            f"📌 الموضوع: <b>{html.escape(msg)}</b>\n\n"
            f"أرسل الآن <b>أهم القوانين أو النقاط الرئيسية</b> لتتذكرها عند المراجعة.\n\n"
            f"<i>(أو اضغط زر [تخطي الملاحظات] بالأسفل إذا أردت جدولة الموضوع فقط بدون ملخص)</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ تخطي الملاحظات (جدولة فوراً)", callback_data="skip_study_notes")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_study_log")]
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    elif state == "awaiting_topic_notes":
        await save_completed_study_topic(update, context, notes=msg)
        return True

    return False


async def save_completed_study_topic(update: Update, context: ContextTypes.DEFAULT_TYPE, notes: str = ""):
    """Saves the study topic into the database and schedules Spaced Repetition reviews."""
    title = context.user_data.pop("study_topic_title", "موضوع مذاكرة")
    context.user_data.pop("study_state", None)

    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id if user else chat_id

    # Save into DB and schedule first review for tomorrow
    _ = db.save_study_topic(
        name=title,
        notes=notes,
        user_id=user_id,
        start_today=False
    )

    notes_block = f"\n📝 <b>الملاحظات المسجلة:</b>\n<blockquote>{html.escape(notes)}</blockquote>\n" if notes else ""

    text = (
        f"🎉 <b>تم تسجيل موضوع المذاكرة وجدولته بنجاح!</b> 🧠\n\n"
        f"📌 <b>{html.escape(title)}</b>\n"
        f"{notes_block}\n"
        f"📅 <b>جدول المراجعات الذكية (منحنى النسيان والتكرار المتباعد):</b>\n"
        f"• <b>المراجعة 1:</b> غداً (المرحلة 1)\n"
        f"• <b>المراجعة 2:</b> بعد 3 أيام\n"
        f"• <b>المراجعة 3:</b> بعد أسبوع\n"
        f"• <b>المراجعة 4:</b> بعد أسبوعين\n"
        f"• <b>المراجعة 5:</b> بعد شهر (تثبيت دائم في الذاكرة طويلة المدى)\n\n"
        f"🔔 سيتولى البوت تذكيرك يومياً في مواعيد مراجعة هذا الموضوع لترسيخه تلقائياً! 💪"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")
        ],
        [InlineKeyboardButton("➕ تسجيل موضوع آخر", callback_data="log_study_new")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]
    ])

    query = update.callback_query
    if query:
        await safe_edit(query, text, kb)
    else:
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)


async def handle_study_tracker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles callback queries related to study tracking and topic reviews."""
    query = update.callback_query
    if not query or not query.data:
        return False

    data = query.data
    user = update.effective_user
    user_id = user.id if user else (update.effective_chat.id if update.effective_chat else ADMIN_USER_ID)

    if data == "log_study_new":
        await start_log_study(update, context)
        return True

    elif data == "cancel_study_log":
        context.user_data.pop("study_state", None)
        context.user_data.pop("study_topic_title", None)
        from handlers.main_menu import main_menu_keyboard
        await safe_edit(
            query,
            "🏠 <b>القائمة الرئيسية</b> — تم إلغاء تسجيل المذاكرة.",
            reply_markup=main_menu_keyboard(user_id=user_id)
        )
        return True

    elif data == "skip_study_notes":
        await save_completed_study_topic(update, context, notes="")
        return True

    # ── View Topic Review Card ──
    elif data.startswith("view_topic_review_"):
        parts = data.split("_")
        topic_id = int(parts[3])
        review_id = int(parts[4])

        topic = db.get_study_topic(topic_id)
        if not topic:
            await query.answer("❌ الموضوع غير موجود.", show_alert=True)
            return True

        review = db.get_quiz_review(topic_id, user_id=user_id)
        stage_idx = review["stage"] if review else 0
        lbl = stage_label(stage_idx)

        name = html.escape(topic.get("name", "موضوع مذاكرة"))
        notes = topic.get("notes") or ""
        notes_display = f"\n📝 <b>ملخصك وملاحظاتك:</b>\n<blockquote>{html.escape(notes)}</blockquote>\n" if notes else "\n<i>(لا توجد ملاحظات مسجلة لهذا الموضوع)</i>\n"

        text = (
            f"📖 <b>مراجعة موضوع مذاكرة</b> 🧠\n\n"
            f"📌 <b>{name}</b>\n"
            f"📊 المرحلة الحالية: <b>{lbl}</b>\n"
            f"{notes_display}\n"
            f"👇 <b>بعد مراجعة الموضوع في ملخصك، قيّم فهمك وإتقانك:</b>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ راجعت وأتقنت (ترقية للمرحلة التالية)", callback_data=f"topic_advance_{review_id}_{topic_id}")],
            [InlineKeyboardButton("🔁 واجهت صعوبة (إعادة لغداً)", callback_data=f"topic_retry_{review_id}_{topic_id}")],
            [
                InlineKeyboardButton("🗑️ حذف الموضوع", callback_data=f"topic_delete_{topic_id}"),
                InlineKeyboardButton("🔙 مراجعات اليوم", callback_data="due_reviews")
            ]
        ])
        await safe_edit(query, text, kb)
        return True

    # ── Advance Topic Review (Mastered) ──
    elif data.startswith("topic_advance_"):
        parts = data.split("_")
        review_id = int(parts[2])
        topic_id = int(parts[3])

        topic = db.get_study_topic(topic_id)
        name = html.escape(topic["name"]) if topic else "الموضوع"

        # Advance review stage
        db.advance_quiz_review(review_id, user_id=user_id)

        # Check new state
        new_rev = db.get_quiz_review(topic_id, user_id=user_id)
        if not new_rev:
            sr_status = "🏆 <b>مبروك! أتممت جميع مراحل التكرار المتباعد لهذا الموضوع بنجاح وتم تثبيته نهائياً في الذاكرة طويلة المدى! 🌟</b>"
        else:
            d = days_until(new_rev.get("next_review_date", ""))
            lbl = stage_label(new_rev.get("stage", 1))
            sr_status = f"✅ تم ترقية الموضوع إلى <b>{lbl}</b>!\n📅 موعد مراجعتك القادمة بعد <b>{d}</b> يوم."

        text = (
            f"🌟 <b>إنجاز رائع!</b>\n\n"
            f"📌 <b>{name}</b>\n\n"
            f"{sr_status}"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
                InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")
            ],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return True

    # ── Retry Topic Review (Faced Difficulty) ──
    elif data.startswith("topic_retry_"):
        parts = data.split("_")
        review_id = int(parts[2])
        topic_id = int(parts[3])

        topic = db.get_study_topic(topic_id)
        name = html.escape(topic["name"]) if topic else "الموضوع"

        db.reset_quiz_review(review_id, user_id=user_id)

        text = (
            f"🔁 <b>تمت إعادة جدولة الموضوع لغداً!</b>\n\n"
            f"📌 <b>{name}</b>\n\n"
            f"لا تقلق، مراجعة النقاط الصعبة هي أساس التفوق وترسيخ الذاكرة 💪.\n"
            f"ستظهر لك مراجعته غداً لتثبيته بنجاح!"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
                InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")
            ],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return True

    # ── Delete Topic ──
    elif data.startswith("topic_delete_"):
        topic_id = int(data.split("_")[-1])
        db.delete_quiz_or_topic(topic_id, user_id=user_id)

        text = "🗑️ <b>تم حذف موضوع المذاكرة وإلغاء مراجعاته المجدولة بنجاح.</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return True

    return False
