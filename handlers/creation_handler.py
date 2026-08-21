import html
import asyncio
import logging
from datetime import date, timedelta, datetime
import pytz

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, KeyboardButtonPollType, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def get_poll_keyboard() -> ReplyKeyboardMarkup:
    """Returns the native Telegram quiz creation keyboard."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📝 إنشاء سؤال (الواجهة الرسمية)", request_poll=KeyboardButtonPollType(type="quiz"))]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def build_create_upload_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Generates the main 'Create & Upload Quiz' menu with all 4 options."""
    text = (
        "➕ <b>إنشاء ورفع كويز / مادة تدريبية</b> 🧠\n\n"
        "اختر الطريقة التي تفضلها لإضافة كويز أو مادة للمراجعة في التكرار المتباعد:\n\n"
        "1️⃣ <b>إنشاء كويز يدوياً (الواجهة الرسمية):</b> كتابة الأسئلة والخيارات مباشرة عبر واجهة تيليجرام ✍️\n"
        "2️⃣ <b>رفع ملف JSON:</b> استيراد كويز جاهز بصيغة JSON 📋\n"
        "3️⃣ <b>إضافة رابط اختبار:</b> إدراج رابط Google Forms أو منصة أخرى 🔗\n"
        "4️⃣ <b>إدراج ملف / صورة / مذكرة:</b> تكرار ملخصات، صور قوانين، أو مستندات 📁\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ 1- إنشاء كويز يدوياً (الواجهة الرسمية)", callback_data="create_manual_quiz")],
        [InlineKeyboardButton("📋 2- رفع كويز عبر ملف JSON", callback_data="upload_json")],
        [InlineKeyboardButton("🔗 3- إضافة كويز كرابط (Forms)", callback_data="upload_url")],
        [InlineKeyboardButton("📁 4- إدراج صورة / ملف / ملخص للتكرار", callback_data="upload_media_note")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")],
    ])
    return text, kb


# ─── 1. Manual Quiz Creation via Native Telegram Poll ─────────────────────────

def build_manual_quiz_dashboard(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    manual_quiz = context.user_data.get("manual_quiz", {})
    name = manual_quiz.get("name", "كويز جديد")
    questions = manual_quiz.get("questions", [])
    
    text = (
        f"✍️ <b>منشئ الكويزات الذكي (الواجهة الرسمية)</b>\n\n"
        f"📌 اسم الكويز: <b>{html.escape(name)}</b>\n"
        f"📝 عدد الأسئلة المضافة: <b>{len(questions)}</b> سؤال\n\n"
    )
    if questions:
        text += "<b>قائمة الأسئلة المضافة:</b>\n"
        for i, q in enumerate(questions, 1):
            q_prev = q["question"][:40] + ("..." if len(q["question"]) > 40 else "")
            text += f"{i}. {html.escape(q_prev)} (✅ {html.escape(q['answer'])})\n"
        text += "\n"

    text += (
        "👇 <b>كيف تضيف سؤالاً؟</b>\n"
        "اضغط على زر <b>[📝 إنشاء سؤال]</b> بالأسفل لفتح نافذة تيليجرام الرسمية لكتابة السؤال وخياراته وتحديد الإجابة الصحيحة مباشرة! ✨"
    )

    buttons = []
    if questions:
        buttons.append([InlineKeyboardButton(f"✅ حفظ وإنهاء الكويز ({len(questions)} سؤال)", callback_data="manual_save_quiz")])
    buttons.append([InlineKeyboardButton("❌ إلغاء وإنهاء", callback_data="manual_cancel")])

    return text, InlineKeyboardMarkup(buttons)


async def handle_manual_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "create_manual_quiz":
        context.user_data["manual_state"] = "awaiting_quiz_name"
        context.user_data["manual_quiz"] = {"name": "", "questions": []}
        text = (
            "✍️ <b>إنشاء كويز يدوياً — الخطوة 1/2</b>\n\n"
            "أرسل الآن <b>اسم أو عنوان الكويز الجديد</b> في رسالة نصية:\n"
            "<i>(مثال: كويز قوانين السرعة والمسافة 1)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="manual_cancel")]])
        await safe_edit(query, text, kb)

    elif data == "manual_cancel":
        context.user_data.pop("manual_quiz", None)
        context.user_data.pop("manual_state", None)
        chat_id = update.effective_chat.id
        try:
            # Remove custom reply keyboard
            rm_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="تم إلغاء منشئ الكويزات.",
                reply_markup=ReplyKeyboardRemove()
            )
            asyncio.create_task(rm_msg.delete())
        except Exception:
            pass
        from handlers.main_menu import main_menu_handler
        await main_menu_handler(update, context)

    elif data == "manual_save_quiz":
        manual_quiz = context.user_data.pop("manual_quiz", None)
        context.user_data.pop("manual_state", None)
        chat_id = update.effective_chat.id
        
        try:
            rm_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="⏳ جاري حفظ الكويز...",
                reply_markup=ReplyKeyboardRemove()
            )
            asyncio.create_task(rm_msg.delete())
        except Exception:
            pass

        if not manual_quiz or not manual_quiz.get("questions"):
            await query.answer("⚠️ لا توجد أسئلة لحفظها.", show_alert=True)
            return

        user = update.effective_user
        u_id = user.id if user else 6099429826
        name = manual_quiz.get("name", "كويز بدون اسم")
        questions = manual_quiz.get("questions", [])

        is_pub = 1 if is_admin(u_id) else 0
        quiz_id = db.save_quiz(name, questions, user_id=u_id, is_public=is_pub)

        text = (
            f"🎉 <b>تم إنشاء الكويز وحفظه بنجاح!</b>\n\n"
            f"📋 <b>{html.escape(name)}</b>\n"
            f"📝 عدد الأسئلة: <b>{len(questions)}</b> سؤال\n\n"
            f"تمت جدولة هذا الكويز في نظام <b>التكرار المتباعد</b> لتصلك مراجعاته الدورية تلقائياً 🧠."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ ابدأ حل الكويز الآن", callback_data=f"start_practice_{quiz_id}")],
            [InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
        await safe_edit(query, text, kb)


async def handle_incoming_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles polls/quizzes created by the user via the native Telegram poll creation modal."""
    poll = update.message.poll if update.message else None
    if not poll:
        return False

    chat_id = update.effective_chat.id
    # Delete the user's sent poll message from chat to keep it clean
    if update.message:
        db.track_chat_message(chat_id, update.message.message_id)
        asyncio.create_task(update.message.delete())

    manual_quiz = context.user_data.get("manual_quiz")
    if manual_quiz is None:
        # Auto-initialize manual quiz if user just sent a poll directly
        context.user_data["manual_quiz"] = {"name": "كويز مخصص", "questions": []}
        manual_quiz = context.user_data["manual_quiz"]

    q_text = poll.question
    options = [opt.text for opt in poll.options]
    correct_idx = poll.correct_option_id
    if correct_idx is not None and 0 <= correct_idx < len(options):
        correct_answer = options[correct_idx]
    else:
        correct_answer = options[0] if options else ""
    explanation = getattr(poll, "explanation", "") or ""

    question_entry = {
        "question": q_text,
        "options": options,
        "answer": correct_answer,
        "explanation": explanation
    }
    manual_quiz.setdefault("questions", []).append(question_entry)
    context.user_data["manual_state"] = "awaiting_poll_questions"

    text, kb = build_manual_quiz_dashboard(context)
    ack_text = f"✅ <b>تمت إضافة السؤال رقم {len(manual_quiz['questions'])} بنجاح!</b> 🎯\n\n" + text
    await send_clean_message(context, chat_id, ack_text, reply_markup=kb)
    return True


# ─── 4. Media & Notes Review Item ─────────────────────────────────────────────

async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles uploaded photos or documents when user is creating a Spaced Repetition media note."""
    if not context.user_data.get("waiting_for_media_note"):
        return False

    context.user_data.pop("waiting_for_media_note", None)
    user = update.effective_user
    chat_id = update.effective_chat.id
    u_id = user.id if user else 6099429826
    msg = update.message

    if msg:
        db.track_chat_message(chat_id, msg.message_id)

    if msg.photo:
        file_id = msg.photo[-1].file_id
        caption = msg.caption or "صورة مراجعة وتلخيص 📸"
        url = f"media:photo:{file_id}"
        name = caption
    elif msg.document:
        file_id = msg.document.file_id
        name = msg.document.file_name or "ملف مراجعة 📄"
        url = f"media:doc:{file_id}"
    else:
        return False

    quiz_id = db.save_quiz_url(name=name, url=url, user_id=u_id, is_public=0)

    text = (
        f"✅ <b>تمت إضافة مادة المراجعة بنجاح!</b> 🧠\n\n"
        f"📌 العنوان: <b>{html.escape(name)}</b>\n\n"
        f"تم إدراجها في نظام <b>التكرار المتباعد</b>.\n"
        f"سيقوم البوت بتذكيرك بمراجعتها في المواعيد الذكية لتثبيتها في الذاكرة 💪."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 جدول مراجعاتي", callback_data="review_schedule")],
        [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
    ])
    await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
    return True


# ─── Text Input Router for Creation ──────────────────────────────────────────

async def handle_creation_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles text input related to manual quiz building, user private folders, or media text notes."""
    msg = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    u_id = user.id if user else chat_id

    # 1. Media Note Text
    if context.user_data.get("waiting_for_media_note"):
        context.user_data.pop("waiting_for_media_note", None)
        first_line = msg.splitlines()[0][:35]
        name = f"ملاحظة: {first_line}..."
        url = f"media:text:{msg}"
        
        quiz_id = db.save_quiz_url(name=name, url=url, user_id=u_id, is_public=0)
        text = (
            f"✅ <b>تم حفظ الملاحظة في التكرار المتباعد بنجاح!</b> 📝\n\n"
            f"📌 العنوان: <b>{html.escape(name)}</b>\n\n"
            f"سيقوم البوت بتذكيرك بمراجعة هذه الملاحظة دورياً لتثبيت حفظك 🧠."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 جدول مراجعاتي", callback_data="review_schedule")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    # 2. User Private Folder Creation
    if context.user_data.get("waiting_for_user_folder") is not None:
        parent_id = context.user_data.pop("waiting_for_user_folder")
        folder_name = msg.strip()
        cat_id = db.create_category(
            name=folder_name,
            parent_id=parent_id if parent_id != 0 else None,
            owner_id=u_id,
            is_public=0
        )
        text = (
            f"✅ <b>تم إنشاء المجلد الخاص بنجاح!</b>\n\n"
            f"📁 <b>{html.escape(folder_name)}</b>\n\n"
            f"يمكنك الآن نقل كويزاتك الخاصة إليه وتنظيم دراستك 🌟."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 فتح المجلد", callback_data=f"my_cat_{cat_id}_1")],
            [InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    # 3. Manual Quiz: Awaiting Quiz Name
    state = context.user_data.get("manual_state")
    if state == "awaiting_quiz_name":
        context.user_data.setdefault("manual_quiz", {})["name"] = msg
        context.user_data["manual_state"] = "awaiting_poll_questions"
        text, kb = build_manual_quiz_dashboard(context)
        
        # Send clean message with inline dashboard
        await send_clean_message(context, chat_id, f"✅ <b>تم تحديد اسم الكويز بنجاح!</b>\n\n" + text, update=update, reply_markup=kb)
        
        # Send reply keyboard with native Poll creation button
        poll_kb = get_poll_keyboard()
        poll_prompt = await context.bot.send_message(
            chat_id=chat_id,
            text="👇 <b>اضغط على زر [📝 إنشاء سؤال (الواجهة الرسمية)] بالأسفل لبدء إضافة الأسئلة:</b>",
            reply_markup=poll_kb,
            parse_mode="HTML"
        )
        db.track_chat_message(chat_id, poll_prompt.message_id)
        return True

    # 4. Manual Quiz: Awaiting Question Text
    if state == "awaiting_q_text":
        context.user_data.setdefault("current_q", {})["question"] = msg
        context.user_data["manual_state"] = "awaiting_q_options"
        text = (
            "✍️ <b>إضافة الخيارات — الخطوة 2/3</b>\n\n"
            "أرسل الآن خيارات الإجابة في رسالة واحدة، <b>كل خيار في سطر مستقل</b>:\n\n"
            "<i>مثال:</i>\n"
            "20\n"
            "25\n"
            "30\n"
            "35"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء السؤال", callback_data="manual_dashboard")]])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    # 5. Manual Quiz: Awaiting Options
    if state == "awaiting_q_options":
        lines = [line.strip() for line in msg.splitlines() if line.strip()]
        if len(lines) < 2:
            await send_clean_message(context, chat_id, "⚠️ يرجى إرسال خيارين على الأقل (كل خيار في سطر مستقل). أعد إرسال الخيارات:", update=update)
            return True
        if len(lines) > 6:
            lines = lines[:6]

        context.user_data.setdefault("current_q", {})["options"] = lines
        context.user_data["manual_state"] = "awaiting_correct_selection"

        q_text = context.user_data["current_q"].get("question", "")
        letters = ["أ", "ب", "ج", "د", "هـ", "و"]
        
        opt_preview = "\n".join(f"<b>{letters[i]})</b> {html.escape(opt)}" for i, opt in enumerate(lines))
        text = (
            "✍️ <b>تحديد الإجابة الصحيحة — الخطوة 3/3</b>\n\n"
            f"❓ السؤال: <b>{html.escape(q_text)}</b>\n\n"
            f"<b>الخيارات:</b>\n{opt_preview}\n\n"
            f"👇 <b>اضغط على الزر المطابق للإجابة الصحيحة:</b>"
        )
        btn_row = [
            InlineKeyboardButton(f"{letters[i]})", callback_data=f"manual_set_correct_{i}")
            for i in range(len(lines))
        ]
        kb = InlineKeyboardMarkup([
            btn_row,
            [InlineKeyboardButton("❌ إلغاء السؤال", callback_data="manual_dashboard")]
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    return False
