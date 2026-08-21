import html
import logging
from datetime import date, timedelta, datetime
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def build_create_upload_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Generates the main 'Create & Upload Quiz' menu with all 4 options."""
    text = (
        "➕ <b>إنشاء ورفع كويز / مادة تدريبية</b> 🧠\n\n"
        "اختر الطريقة التي تفضلها لإضافة كويز أو مادة للمراجعة في التكرار المتباعد:\n\n"
        "1️⃣ <b>إنشاء كويز يدوياً:</b> كتابة الأسئلة والخيارات مباشرة خطوة بخطوة ✍️\n"
        "2️⃣ <b>رفع ملف JSON:</b> استيراد كويز جاهز بصيغة JSON 📋\n"
        "3️⃣ <b>إضافة رابط اختبار:</b> إدراج رابط Google Forms أو منصة أخرى 🔗\n"
        "4️⃣ <b>إدراج ملف / صورة / مذكرة:</b> تكرار ملخصات، صور قوانين، أو مستندات 📁\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ 1- إنشاء كويز يدوياً (سؤال بسؤال)", callback_data="create_manual_quiz")],
        [InlineKeyboardButton("📋 2- رفع كويز عبر ملف JSON", callback_data="upload_json")],
        [InlineKeyboardButton("🔗 3- إضافة كويز كرابط (Forms)", callback_data="upload_url")],
        [InlineKeyboardButton("📁 4- إدراج صورة / ملف / ملخص للتكرار", callback_data="upload_media_note")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")],
    ])
    return text, kb


# ─── 1. Manual Quiz Creation ──────────────────────────────────────────────────

def build_manual_quiz_dashboard(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    manual_quiz = context.user_data.get("manual_quiz", {})
    name = manual_quiz.get("name", "كويز جديد")
    questions = manual_quiz.get("questions", [])
    
    text = (
        f"✍️ <b>منشئ الكويزات اليدوي</b>\n\n"
        f"📌 اسم الكويز: <b>{html.escape(name)}</b>\n"
        f"📝 عدد الأسئلة المضافة: <b>{len(questions)}</b> سؤال\n\n"
    )
    if questions:
        text += "<b>قائمة الأسئلة:</b>\n"
        for i, q in enumerate(questions, 1):
            q_prev = q["question"][:40] + ("..." if len(q["question"]) > 40 else "")
            text += f"{i}. {html.escape(q_prev)} (✅ {html.escape(q['answer'])})\n"
        text += "\n"
    text += "اختر الإجراء التالي:"

    buttons = [
        [InlineKeyboardButton("➕ إضافة سؤال جديد", callback_data="manual_add_q")],
    ]
    if questions:
        buttons.append([InlineKeyboardButton(f"✅ حفظ وإنهاء الكويز ({len(questions)} سؤال)", callback_data="manual_save_quiz")])
    buttons.append([InlineKeyboardButton("❌ إلغاء وإنهاء", callback_data="create_upload_menu")])

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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="create_upload_menu")]])
        await safe_edit(query, text, kb)

    elif data == "manual_add_q":
        context.user_data["manual_state"] = "awaiting_q_text"
        context.user_data["current_q"] = {"question": "", "options": [], "answer": ""}
        text = (
            "✍️ <b>إضافة سؤال — الخطوة 1/3</b>\n\n"
            "أرسل الآن <b>نص السؤال</b> في رسالة نصية:"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء هذا السؤال", callback_data="manual_dashboard")]])
        await safe_edit(query, text, kb)

    elif data.startswith("manual_set_correct_"):
        idx = int(data.split("_")[-1])
        current_q = context.user_data.get("current_q")
        if not current_q or not current_q.get("options") or idx >= len(current_q["options"]):
            await query.answer("⚠️ حدث خطأ في تحديد الإجابة.", show_alert=True)
            return

        chosen_opt = current_q["options"][idx]
        current_q["answer"] = chosen_opt
        
        # Add to questions list
        context.user_data.setdefault("manual_quiz", {}).setdefault("questions", []).append(current_q)
        context.user_data.pop("current_q", None)
        context.user_data["manual_state"] = "idle"

        text, kb = build_manual_quiz_dashboard(context)
        await safe_edit(query, f"✅ <b>تمت إضافة السؤال بنجاح!</b>\n\n" + text, kb)

    elif data == "manual_dashboard":
        context.user_data["manual_state"] = "idle"
        context.user_data.pop("current_q", None)
        text, kb = build_manual_quiz_dashboard(context)
        await safe_edit(query, text, kb)

    elif data == "manual_save_quiz":
        manual_quiz = context.user_data.pop("manual_quiz", None)
        context.user_data.pop("manual_state", None)
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


# ─── 4. Media & Notes Review Item ─────────────────────────────────────────────

async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles uploaded photos or documents when user is creating a Spaced Repetition media note."""
    if not context.user_data.get("waiting_for_media_note"):
        return False

    context.user_data.pop("waiting_for_media_note", None)
    user = update.effective_user
    u_id = user.id if user else 6099429826
    msg = update.message

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
        f"سيقوم البوت بإرسالها لك في مواعيد المراجعة الذكية لتثبيتها في الذاكرة 💪."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 جدول مراجعاتي", callback_data="review_schedule")],
        [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
    ])
    await msg.reply_text(text, parse_mode="HTML", reply_markup=kb)
    return True


# ─── Text Input Router for Creation ──────────────────────────────────────────

async def handle_creation_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles text input related to manual quiz building or media text notes."""
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
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return True

    # 2. Manual Quiz: Awaiting Quiz Name
    state = context.user_data.get("manual_state")
    if state == "awaiting_quiz_name":
        context.user_data.setdefault("manual_quiz", {})["name"] = msg
        context.user_data["manual_state"] = "idle"
        text, kb = build_manual_quiz_dashboard(context)
        await update.message.reply_text(f"✅ تم تحديد اسم الكويز:\n\n" + text, parse_mode="HTML", reply_markup=kb)
        return True

    # 3. Manual Quiz: Awaiting Question Text
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
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return True

    # 4. Manual Quiz: Awaiting Options
    if state == "awaiting_q_options":
        lines = [line.strip() for line in msg.splitlines() if line.strip()]
        if len(lines) < 2:
            await update.message.reply_text("⚠️ يرجى إرسال خيارين على الأقل (كل خيار في سطر مستقل). أعد إرسال الخيارات:")
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
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return True

    return False
