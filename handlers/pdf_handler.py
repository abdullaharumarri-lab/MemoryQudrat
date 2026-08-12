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
        "wrong": [2],
        "questions": [
            {
                "question": "سؤال صحته صح 100%",
                "options": ["صح", "خطأ", "ربما", "لا شيء"],
                "answer": "صح",
                "explanation": "هذا توضيح اختياري."
            },
            {
                "question": "سؤال أخطأت فيه (رقمه 2 في 'wrong')",
                "options": ["خطأ", "صح", "ربما", "لا شيء"],
                "answer": "صح",
                "explanation": ""
            }
        ]
    }
    txt = (
        "📄 <b>نموذج JSON</b>\n\n"
        "🔹 <b>quiz_name</b>: اسم الكويز\n"
        "🔹 <b>wrong</b> (اختياري): أرقام الأسئلة التي أخطأت فيها (1، 2، 3...). البوت يضيفها تلقائياً لقائمة الضعيفة.\n"
        "🔹 <b>questions</b>: قائمة الأسئلة (كل سؤال فيه question و options و answer)\n\n"
        "ℹ️ عند رفع الكويز، بوتك يضيف الأسئلة الموجودة في wrong مباشرةً إلى الأسئلة الضعيفة بدون حاجة لحل الكويز من جديد."
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(template, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name

    try:
        if update.message:
            await update.message.reply_document(document=open(tmp_path, "rb"), filename="template.json", caption=txt, parse_mode="HTML")
        elif update.effective_message:
            await update.effective_message.reply_document(document=open(tmp_path, "rb"), filename="template.json", caption=txt, parse_mode="HTML")
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

        if "quiz_name" not in data and "name" not in data:
            raise ValueError("الملف لا يحتوي على 'quiz_name' أو 'name'.")
        if "questions" not in data:
            raise ValueError("الملف لا يحتوي على 'questions'.")

        for q in data["questions"]:
            if "question" not in q or "options" not in q or "answer" not in q:
                raise ValueError("تأكد من وجود question, options, answer في كل سؤال.")

        quiz_upgrade_id = context.user_data.pop("waiting_for_json_upgrade", None)

        
        if quiz_upgrade_id:
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE quizzes SET url = NULL WHERE id = ?", (quiz_upgrade_id,))
            for q in data["questions"]:
                cursor.execute(
                    """INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        quiz_upgrade_id,
                        q["question"],
                        json.dumps(q["options"], ensure_ascii=False),
                        q["answer"],
                        q.get("explanation", ""),
                    ),
                )
            conn.commit()
            conn.close()

            # Process wrong field in upgrade path too
            wrong_indices = data.get("wrong", [])
            wrong_count = 0
            if wrong_indices:
                saved_questions = db.get_questions(quiz_upgrade_id)
                for idx in wrong_indices:
                    try:
                        real_idx = int(idx) - 1
                        if 0 <= real_idx < len(saved_questions):
                            q = saved_questions[real_idx]
                            db.add_or_reset_weak_question(quiz_upgrade_id, q["id"])
                            wrong_count += 1
                    except (ValueError, TypeError):
                        continue

            quiz = db.get_quiz(quiz_upgrade_id)
            name_safe = html.escape(quiz['name'])
            wrong_note = f"\n❌ تمت إضافة <b>{wrong_count}</b> سؤال للأسئلة الضعيفة تلقائياً." if wrong_count else ""
            text = (
                f"✅ <b>تمت الترقية بنجاح!</b>\n\n"
                f"📋 <b>{name_safe}</b>\n"
                f"تمت إضافة {len(data['questions'])} سؤال تفاعلي للكويز.{wrong_note}\n\n"
                f"<i>سيستمر نظام التكرار المتباعد حسب جدولك السابق!</i>"
            )
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
            
        else:
            quiz_name = data.get("quiz_name") or data.get("name", "كويز بدون اسم")
            quiz_id = db.save_quiz_without_review(quiz_name, data["questions"])
            quiz = db.get_quiz(quiz_id)
            name_safe = html.escape(quiz['name'])

            # Auto-mark wrong questions from "wrong" field
            wrong_indices = data.get("wrong", [])
            wrong_count = 0
            
            if wrong_indices:
                saved_questions = db.get_questions(quiz_id)
                total_q = len(saved_questions)
                for idx in wrong_indices:
                    try:
                        real_idx = int(idx) - 1
                        if 0 <= real_idx < total_q:
                            q = saved_questions[real_idx]
                            db.add_or_reset_weak_question(quiz_id, q["id"])
                            wrong_count += 1
                    except (ValueError, TypeError):
                        continue

            if wrong_count:
                wrong_note = f"\n❌ تمت إضافة <b>{wrong_count}</b> سؤال للأسئلة الضعيفة تلقائياً."
            elif wrong_indices:
                wrong_note = f"\n⚠️ وُجد حقل 'wrong' لكن الأرقام {list(wrong_indices)} لم تطابق أي سؤال. تأكد أن الأرقام بين 1 و{len(data['questions'])}."
            else:
                wrong_note = ""

            text = (
                f"✅ <b>تمت إضافة الكويز بنجاح!</b>\n\n"
                f"📋 <b>{name_safe}</b>\n"
                f"📝 {len(data['questions'])} سؤال{wrong_note}\n\n"
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
