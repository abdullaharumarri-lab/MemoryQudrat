import json
import os
import tempfile
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from ai_extractor import extract_questions_from_pdf
from utils import send_clean_message


# ─── Template Command ─────────────────────────────────────────────────────────

async def template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    template = {
        "quiz_name": "نموذج كويز",
        "questions": [
            {
                "question": "ما عاصمة السعودية؟",
                "options": ["الرياض", "جدة", "الدمام", "مكة"],
                "answer": "الرياض",
                "explanation": "الرياض هي عاصمة المملكة العربية السعودية."
            }
        ]
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(template, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name

    try:
        if update.message:
            await update.message.reply_document(document=open(tmp_path, "rb"), filename="template.json")
        elif update.effective_message:
            await update.effective_message.reply_document(document=open(tmp_path, "rb"), filename="template.json")
    finally:
        os.unlink(tmp_path)


# ─── JSON Handler ─────────────────────────────────────────────────────────────

async def json_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.effective_message
    if not msg_obj or not msg_obj.document: return

    doc = msg_obj.document
    msg_id = await send_clean_message(
        context=context,
        chat_id=update.effective_chat.id,
        update=update,
        text="⏳ جاري معالجة الملف..."
    )

    tmp_path = None
    try:
        file = await doc.get_file()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "quiz_name" not in data or "questions" not in data:
            raise ValueError("الملف لا يحتوي على 'quiz_name' أو 'questions'.")

        for q in data["questions"]:
            if "question" not in q or "options" not in q or "answer" not in q:
                raise ValueError("تأكد من وجود question, options, answer في كل سؤال.")

        quiz_id = db.save_quiz_without_review(data["quiz_name"], data["questions"])
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz['name'])

        text = (
            f"✅ <b>تمت إضافة الكويز بنجاح!</b>\n\n"
            f"📋 <b>{name_safe}</b>\n"
            f"📝 {len(data['questions'])} سؤال\n\n"
            f"متى تريد أن تبدأ أول مراجعة لهذا الكويز؟"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 اليوم (الساعة 6 م)", callback_data=f"sched_today_{quiz_id}")],
            [InlineKeyboardButton("📅 غداً (الساعة 6 م)", callback_data=f"sched_tomorrow_{quiz_id}")],
        ])

        if msg_id:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    except json.JSONDecodeError:
        err = "❌ <b>خطأ:</b> ملف JSON غير صالح. تأكد من الصيغة."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=err, parse_mode="HTML")
    except ValueError as e:
        err = f"❌ <b>خطأ:</b> {str(e)}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=err, parse_mode="HTML")
    except Exception as e:
        err = f"❌ <b>حدث خطأ غير متوقع:</b> {str(e)}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=err, parse_mode="HTML")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
