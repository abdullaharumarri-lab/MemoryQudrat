import json
import logging
import os
import re
import tempfile
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from utils import send_clean_message
from config import MAX_JSON_FILE_SIZE_BYTES, MAX_QUESTIONS_PER_QUIZ, is_admin

logger = logging.getLogger(__name__)


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


# ─── Input Validation Helpers ─────────────────────────────────────────────────

def _validate_json_upload(doc, data: dict) -> None:
    """
    Raises ValueError with an Arabic-friendly message if the uploaded
    JSON file or its contents fail any security / format check.
    """
    # 1. File size guard
    if doc and hasattr(doc, "file_size") and doc.file_size and doc.file_size > MAX_JSON_FILE_SIZE_BYTES:
        size_kb = doc.file_size // 1024
        raise ValueError(
            f"حجم الملف ({size_kb} KB) يتجاوز الحد المسموح ({MAX_JSON_FILE_SIZE_BYTES // 1024} KB). "
            "قسّم الكويز إلى ملفات أصغر."
        )

    # 2. Required top-level keys
    if "quiz_name" not in data and "name" not in data:
        raise ValueError("البيانات لا تحتوي على 'quiz_name' أو 'name'.")
    if "questions" not in data or not isinstance(data["questions"], list):
        raise ValueError("البيانات لا تحتوي على 'questions' بصيغة قائمة.")

    # 3. Question count guard
    if len(data["questions"]) == 0:
        raise ValueError("البيانات لا تحتوي على أي أسئلة.")
    if len(data["questions"]) > MAX_QUESTIONS_PER_QUIZ:
        raise ValueError(
            f"عدد الأسئلة ({len(data['questions'])}) يتجاوز الحد المسموح ({MAX_QUESTIONS_PER_QUIZ} سؤال). "
            "قسّم الكويز إلى أجزاء أصغر."
        )

    # 4. Per-question validation
    for i, q in enumerate(data["questions"], start=1):
        if not isinstance(q, dict):
            raise ValueError(f"السؤال رقم {i} ليس بصيغة صحيحة.")
        if "question" not in q:
            raise ValueError(f"السؤال رقم {i} لا يحتوي على حقل 'question'.")
        if "options" not in q or not isinstance(q.get("options"), list) or len(q["options"]) < 2:
            raise ValueError(f"السؤال رقم {i} يحتاج حقل 'options' بقائمة تحتوي خيارَين على الأقل.")
        if "answer" not in q:
            raise ValueError(f"السؤال رقم {i} لا يحتوي على حقل 'answer'.")


async def process_json_quiz_data(
    data: dict,
    user,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    update: Update,
    quiz_upgrade_id: int = None
):
    """Processes parsed JSON data for either upgrading an existing quiz or saving a new quiz."""
    u_id = user.id if user else 6099429826

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
                        db.add_or_reset_weak_question(quiz_upgrade_id, q["id"], user_id=u_id)
                        wrong_count += 1
                except (ValueError, TypeError):
                    continue

        quiz = db.get_quiz(quiz_upgrade_id)
        name_safe = html.escape(quiz.get('name', 'كويز')) if quiz else "كويز"
        wrong_note = f"\n❌ تمت إضافة <b>{wrong_count}</b> سؤال للأسئلة الضعيفة تلقائياً." if wrong_count else ""
        text = (
            f"✅ <b>تمت الترقية بنجاح!</b>\n\n"
            f"📋 <b>{name_safe}</b>\n"
            f"تمت إضافة {len(data['questions'])} سؤال تفاعلي للكويز.{wrong_note}\n\n"
            f"<i>سيستمر نظام التكرار المتباعد حسب جدولك السابق!</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ ابدأ حل الكويز الآن", callback_data=f"start_practice_{quiz_upgrade_id}")],
            [InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
    else:
        is_pub = 0
        owner_id = u_id
        quiz_name = data.get("quiz_name") or data.get("name", "كويز جديد")
        quiz_id = db.save_quiz_without_review(quiz_name, data["questions"], owner_id=owner_id, is_public=is_pub)
        
        # Schedule first review for this user in Spaced Repetition
        db.schedule_first_review(quiz_id, user_id=u_id, start_today=True)
        
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz.get('name', quiz_name)) if quiz else html.escape(quiz_name)

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
                        db.add_or_reset_weak_question(quiz_id, q["id"], user_id=u_id)
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
            f"✅ <b>تمت إضافة الكويز وجدولته بنجاح!</b>\n\n"
            f"📋 <b>{name_safe}</b>\n"
            f"📝 {len(data['questions'])} سؤال{wrong_note}\n\n"
            f"تمت جدولة هذا الكويز في نظام <b>التكرار المتباعد</b> لتصلك مراجعاته الدورية 🧠.\n"
            f"هل ترغب بنقله إلى أحد مجلداتك؟"
        )

        cats = db.get_categories(user_id=u_id, is_public=0)
        kb = []
        for c in cats:
            kb.append([InlineKeyboardButton(f"📁 {c['name']}", callback_data=f"my_set_quiz_cat_{quiz_id}_{c['id']}")])
        
        kb.append([InlineKeyboardButton("▶️ ابدأ حل الكويز الآن", callback_data=f"start_practice_{quiz_id}")])
        kb.append([InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes")])
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])

        keyboard = InlineKeyboardMarkup(kb)

    await send_clean_message(context, chat_id, text, update=update, reply_markup=keyboard)


# ─── JSON File Handler ─────────────────────────────────────────────────────────

async def json_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.effective_message
    if not msg_obj or not msg_obj.document: return

    doc = msg_obj.document

    # ── Early size check before downloading ──────────────────────────────────
    if doc.file_size and doc.file_size > MAX_JSON_FILE_SIZE_BYTES:
        size_kb = doc.file_size // 1024
        await send_clean_message(
            context=context,
            chat_id=update.effective_chat.id,
            update=update,
            text=(
                f"❌ <b>حجم الملف كبير جداً</b> ({size_kb} KB).\n"
                f"الحد المسموح هو <b>{MAX_JSON_FILE_SIZE_BYTES // 1024} KB</b>.\n"
                "قسّم الكويز إلى ملفات أصغر وأرفعها بشكل منفصل."
            )
        )
        return

    chat_id = update.effective_chat.id
    if update.message:
        db.track_chat_message(chat_id, update.message.message_id)

    msg_id = await send_clean_message(
        context=context,
        chat_id=chat_id,
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

        # ── Centralised security + format validation ──────────────────────────
        _validate_json_upload(doc, data)

        quiz_upgrade_id = context.user_data.pop("waiting_for_json_upgrade", None)
        user = update.effective_user

        await process_json_quiz_data(
            data=data,
            user=user,
            context=context,
            chat_id=chat_id,
            update=update,
            quiz_upgrade_id=quiz_upgrade_id
        )

    except json.JSONDecodeError:
        err = "❌ <b>خطأ:</b> ملف JSON غير صالح. تأكد من الصيغة."
        await send_clean_message(context, update.effective_chat.id, err, update=update)
    except ValueError as e:
        err = f"❌ <b>خطأ:</b> {str(e)}"
        await send_clean_message(context, update.effective_chat.id, err, update=update)
    except Exception as e:
        err = f"❌ <b>حدث خطأ غير متوقع:</b> {str(e)}"
        await send_clean_message(context, update.effective_chat.id, err, update=update)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
