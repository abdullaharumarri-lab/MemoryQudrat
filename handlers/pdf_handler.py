import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from ai_extractor import extract_questions_from_pdf
from utils import send_clean_message
import os
import tempfile


# ─── PDF Handler ──────────────────────────────────────────────────────────────

async def pdf_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle PDF file uploads from the user."""
    msg_obj = update.effective_message  # works for both message and channel_post
    doc = msg_obj.document

    msg = await msg_obj.reply_text(
        "⏳ جاري استخراج الأسئلة من الـ PDF بالذكاء الاصطناعي...\n"
        "قد يستغرق هذا دقيقة أو أكثر حسب حجم الملف."
    )

    tmp_path = None
    try:
        file = await doc.get_file()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        await file.download_to_drive(tmp_path)
        data = await extract_questions_from_pdf(tmp_path)

        questions = data.get("questions", [])
        quiz_name = data.get("quiz_name", doc.file_name.replace(".pdf", ""))

        if not questions:
            await msg.edit_text(
                "❌ لم أتمكن من استخراج أسئلة من هذا الملف.\n"
                "تأكد أن الملف يحتوي على أسئلة اختيار من متعدد.\n\n"
                "💡 يمكنك رفع ملف JSON مباشرة كبديل.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
            )
            return

        quiz_id = db.save_quiz_without_review(quiz_name, questions)

        await msg.edit_text(
            f"✅ *تم استخراج الكويز بنجاح!*\n\n"
            f"📋 *{quiz_name}*\n"
            f"📝 {len(questions)} سؤال\n\n"
            f"📅 *متى تبدأ أول مراجعة؟*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 اليوم (6 المساء)", callback_data=f"sched_today_{quiz_id}")],
                [InlineKeyboardButton("📅 الغد (6 المساء)", callback_data=f"sched_tomorrow_{quiz_id}")],
            ]),
            parse_mode="Markdown",
        )

    except Exception as e:
        await msg.edit_text(
            f"❌ خطأ في معالجة الـ PDF:\n`{str(e)[:200]}`\n\n"
            "💡 *بديل:* أرسل ملف JSON بصيغة صحيحة مباشرة.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ─── JSON Handler ─────────────────────────────────────────────────────────────

async def json_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle JSON file uploads — clean chat by deleting file and processing messages."""
    doc = update.effective_message.document  # works for both message and channel_post
    # Send temporary processing message, replacing any old messages
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

        if "questions" not in data or not isinstance(data["questions"], list):
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text="❌ صيغة JSON غير صحيحة! يجب أن يحتوي على مفتاح `questions`.\nأرسل /template للحصول على نموذج.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
            )
            return

        questions = data["questions"]
        quiz_name = data.get("quiz_name", doc.file_name.replace(".json", ""))

        if not questions:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text="❌ لا توجد أسئلة في الملف.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
            )
            return

        required_keys = {"question", "options", "answer"}
        for i, q in enumerate(questions):
            if not required_keys.issubset(q.keys()):
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=msg_id,
                    text=f"❌ السؤال رقم {i+1} ناقص. المطلوب: `question`, `options`, `answer`",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
                )
                return

        quiz_id = db.save_quiz_without_review(quiz_name, questions)

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg_id,
            text=(
                f"✅ *تم حفظ الكويز بنجاح!*\n\n"
                f"📋 *{quiz_name}*\n"
                f"📝 {len(questions)} سؤال\n\n"
                f"📅 *متى تبدأ أول مراجعة؟*"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 اليوم (6 المساء)", callback_data=f"sched_today_{quiz_id}")],
                [InlineKeyboardButton("📅 الغد (6 المساء)", callback_data=f"sched_tomorrow_{quiz_id}")],
            ]),
            parse_mode="Markdown",
        )

    except json.JSONDecodeError:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg_id,
            text="❌ الملف ليس JSON صحيحاً. تحقق من الصيغة.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        )
    except Exception as e:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg_id,
            text=f"❌ خطأ: `{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)



async def template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /template command."""
    template = {
        "quiz_name": "اسم الكويز",
        "questions": [
            {
                "question": "ما هو أكبر كوكب في المجموعة الشمسية؟",
                "options": ["الزحل", "المشتري", "نبتون", "أورانوس"],
                "answer": "المشتري",
                "explanation": "المشتري هو أكبر كوكب في المجموعة الشمسية"
            }
        ]
    }
    text = (
        "📄 *نموذج ملف JSON:*\n\n"
        f"```json\n{json.dumps(template, ensure_ascii=False, indent=2)}\n```\n\n"
        "أنشئ الملف بهذه الصيغة وأرسله للبوت ✅"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=markup)
