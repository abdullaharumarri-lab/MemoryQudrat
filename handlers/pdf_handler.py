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


import openpyxl
import csv

# ─── Template Command (Excel + JSON) ──────────────────────────────────────────

def create_excel_template_file() -> str:
    """Generates an elegant formatted .xlsx template with examples and instructions."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "كويز_قدرات"

    # Set Right-to-Left view
    ws.sheet_view.rightToLeft = True

    headers = ["السؤال", "الخيار (أ)", "الخيار (ب)", "الخيار (ج)", "الخيار (د)", "الإجابة الصحيحة", "الشرح (اختياري)", "خطأ (1/0)"]
    ws.append(headers)

    examples = [
        ["ما مرادف كلمة «شحيح»؟", "بخيل", "كريم", "شجاع", "غني", "بخيل", "الشح هو شدة البخل والحرص", "0"],
        ["ما ضد كلمة «جسور»؟", "جبان", "قوي", "سريع", "حكيم", "أ", "الجسور هو المقدام وضده الجبان", "1"],
        ["توفي الشاعر عام 1350 هـ وعمره 60 عاماً، في أي عام وُلد؟", "1290 هـ", "1300 هـ", "1310 هـ", "1320 هـ", "1290 هـ", "1350 - 60 = 1290 هـ", "0"],
    ]
    for ex in examples:
        ws.append(ex)

    # Style header column widths
    ws.column_dimensions['A'].width = 40
    ws.column_dimensions['B'].width = 20
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 20
    ws.column_dimensions['E'].width = 20
    ws.column_dimensions['F'].width = 22
    ws.column_dimensions['G'].width = 35
    ws.column_dimensions['H'].width = 15

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()
    wb.save(tmp_path)
    return tmp_path


async def template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends both Excel (.xlsx) and JSON templates with instructions."""
    xlsx_path = create_excel_template_file()
    txt = (
        "📊 <b>قالب رفع الكويزات (Excel)</b> 🌟\n\n"
        "أسهل وأدق طريقة لرفع الكويزات:\n"
        "1️⃣ افتح الملف المرفق في <b>Excel</b> أو <b>Google Sheets</b>.\n"
        "2️⃣ عبّئ الأسئلة والخيارات والإجابة الصحيحة.\n"
        "3️⃣ أعد إرسال الملف للبوت وسيتم حفظه وجدولته فوراً بدقة 100% 🎯!\n\n"
        "💡 <i>ملاحظة: يمكنك كتابة الإجابة كنص أو كحرف (أ، ب، ج، د).</i>"
    )

    try:
        msg = update.message or update.effective_message
        if msg:
            await msg.reply_document(
                document=open(xlsx_path, "rb"),
                filename="قالب_كويز_قدرات.xlsx",
                caption=txt,
                parse_mode="HTML"
            )
    finally:
        if os.path.exists(xlsx_path):
            os.unlink(xlsx_path)


# ─── Input Validation Helpers ─────────────────────────────────────────────────

def _validate_json_upload(doc, data: dict) -> None:
    """
    Validates and intelligently normalizes JSON quiz data in-place.
    Supports various key aliases (choices, answers, correct, etc.), dict/string options,
    True/False auto-fill, and letter/number answer index resolution.
    """
    # 1. File size guard
    if doc and hasattr(doc, "file_size") and doc.file_size and doc.file_size > MAX_JSON_FILE_SIZE_BYTES:
        size_kb = doc.file_size // 1024
        raise ValueError(
            f"حجم الملف ({size_kb} KB) يتجاوز الحد المسموح ({MAX_JSON_FILE_SIZE_BYTES // 1024} KB). "
            "قسّم الكويز إلى ملفات أصغر."
        )

    if not isinstance(data, dict):
        if isinstance(data, list):
            raise ValueError("الملف يجب أن يحتوي على كائن JSON رئيسي يحتوي على 'questions'.")
        raise ValueError("صيغة البيانات غير صحيحة.")

    # 2. Normalize quiz_name
    if "quiz_name" not in data:
        data["quiz_name"] = data.get("name") or data.get("title") or "كويز جديد"

    # 3. Normalize questions list key
    if "questions" not in data:
        for possible_key in ["Questions", "items", "quiz", "list"]:
            if possible_key in data and isinstance(data[possible_key], list):
                data["questions"] = data[possible_key]
                break

    if "questions" not in data or not isinstance(data["questions"], list):
        raise ValueError("البيانات لا تحتوي على قائمة أسئلة 'questions'.")

    if len(data["questions"]) == 0:
        raise ValueError("البيانات لا تحتوي على أي أسئلة.")

    if len(data["questions"]) > MAX_QUESTIONS_PER_QUIZ:
        raise ValueError(
            f"عدد الأسئلة ({len(data['questions'])}) يتجاوز الحد المسموح ({MAX_QUESTIONS_PER_QUIZ} سؤال). "
            "قسّم الكويز إلى أجزاء أصغر."
        )

    # 4. Per-question validation and auto-repair
    for i, q in enumerate(data["questions"], start=1):
        if not isinstance(q, dict):
            raise ValueError(f"السؤال رقم {i} ليس بصيغة صحيحة (يجب أن يكون كائن JSON).")

        # Normalize question text key
        if "question" not in q:
            for k in ["q", "question_text", "text", "title", "prompt"]:
                if k in q and q[k]:
                    q["question"] = str(q[k])
                    break
        if "question" not in q or not str(q["question"]).strip():
            raise ValueError(f"السؤال رقم {i} لا يحتوي على نص السؤال ('question').")

        # Normalize options key
        if "options" not in q:
            for k in ["choices", "answers", "options_list", "alternatives", "opts"]:
                if k in q:
                    q["options"] = q[k]
                    break

        # Handle options formats (dict, list of dicts, list of strings, comma/newline string)
        raw_opts = q.get("options")
        normalized_opts = []
        if isinstance(raw_opts, dict):
            normalized_opts = [str(v).strip() for v in raw_opts.values() if str(v).strip()]
        elif isinstance(raw_opts, list):
            for opt in raw_opts:
                if isinstance(opt, dict):
                    opt_val = opt.get("text") or opt.get("label") or opt.get("value") or str(opt)
                    if str(opt_val).strip():
                        normalized_opts.append(str(opt_val).strip())
                elif str(opt).strip():
                    normalized_opts.append(str(opt).strip())
        elif isinstance(raw_opts, str):
            if "\n" in raw_opts:
                normalized_opts = [line.strip() for line in raw_opts.splitlines() if line.strip()]
            elif "," in raw_opts:
                normalized_opts = [part.strip() for part in raw_opts.split(",") if part.strip()]
            elif raw_opts.strip():
                normalized_opts = [raw_opts.strip()]

        # Normalize answer key
        if "answer" not in q:
            for k in ["correct_answer", "correct", "right_answer", "ans", "solution"]:
                if k in q and q[k] is not None:
                    q["answer"] = str(q[k]).strip()
                    break

        ans = str(q.get("answer", "")).strip()

        # If options are fewer than 2: auto-repair gracefully
        if len(normalized_opts) == 0:
            if ans in ["صح", "خطأ", "True", "False", "نعم", "لا"]:
                normalized_opts = ["صح", "خطأ"] if ans in ["صح", "خطأ", "True", "False"] else ["نعم", "لا"]
                if not ans: ans = "صح"
            elif ans:
                normalized_opts = [ans, "خيار بديل"]
            else:
                normalized_opts = ["صح", "خطأ"]
                ans = "صح"
        elif len(normalized_opts) == 1:
            if normalized_opts[0] in ["صح", "خطأ"]:
                normalized_opts = ["صح", "خطأ"]
            elif normalized_opts[0] in ["نعم", "لا"]:
                normalized_opts = ["نعم", "لا"]
            else:
                normalized_opts.append("خيار بديل")

        # Resolve answer if given as index (0, 1 or "A", "B" or "أ", "ب")
        if not ans:
            ans = normalized_opts[0]
        elif ans not in normalized_opts:
            if ans.isdigit():
                idx = int(ans)
                if 0 <= idx < len(normalized_opts):
                    ans = normalized_opts[idx]
                elif 1 <= idx <= len(normalized_opts):
                    ans = normalized_opts[idx - 1]
            elif ans.upper() in ["A", "B", "C", "D", "E", "F", "G", "H"]:
                letter_idx = ord(ans.upper()) - ord("A")
                if 0 <= letter_idx < len(normalized_opts):
                    ans = normalized_opts[letter_idx]
            elif ans in ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح"]:
                arabic_letters = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح"]
                a_idx = arabic_letters.index(ans)
                if 0 <= a_idx < len(normalized_opts):
                    ans = normalized_opts[a_idx]
            else:
                # If still not found, add it to options so it's a valid option
                if len(normalized_opts) < 10:
                    normalized_opts.append(ans)
                else:
                    ans = normalized_opts[0]

        q["options"] = normalized_opts
        q["answer"] = ans


def audit_quiz_quality(questions: list) -> dict:
    """Performs comprehensive quality assurance and diagnostic audit on quiz questions."""
    total_q = len(questions)
    passages_count = 0
    explanations_count = 0
    option_count_dist = {}
    duplicate_option_qs = []
    missing_answer_qs = []
    long_option_qs = []

    for i, q in enumerate(questions, start=1):
        q_text = str(q.get("question", "")).strip()
        if "📄" in q_text or ("\n\n" in q_text and len(q_text.split("\n\n")[0]) > 25):
            passages_count += 1

        if q.get("explanation") and str(q.get("explanation")).strip():
            explanations_count += 1

        opts = q.get("options") or []
        num_opts = len(opts)
        option_count_dist[num_opts] = option_count_dist.get(num_opts, 0) + 1

        clean_opts = [str(o).strip() for o in opts if str(o).strip()]
        if len(set(clean_opts)) < len(clean_opts):
            duplicate_option_qs.append(i)

        if any(len(str(o).strip()) > 95 for o in opts):
            long_option_qs.append(i)

        ans = str(q.get("answer", "")).strip()
        if not ans:
            missing_answer_qs.append(i)

    score = 100
    notes = []
    if duplicate_option_qs:
        score -= min(20, len(duplicate_option_qs) * 5)
        notes.append(f"⚠️ خيارات مكررة في الأسئلة: {duplicate_option_qs[:5]}")
    if missing_answer_qs:
        score -= min(30, len(missing_answer_qs) * 10)
        notes.append(f"❌ إجابات مفقودة في الأسئلة: {missing_answer_qs[:5]}")
    if any(count < 2 for count in option_count_dist.keys()):
        score -= 20
        notes.append("⚠️ بعض الأسئلة تحتوي على أقل من خيارين")
    if total_q == 0:
        score = 0

    if score >= 95:
        verdict = "ممتاز ومثالي للنشر 🌟"
    elif score >= 80:
        verdict = "جيد جداً وجاهز للحل ✅"
    else:
        verdict = "يحتاج مراجعة وتدقيق ⚠️"

    return {
        "total": total_q,
        "passages": passages_count,
        "explanations": explanations_count,
        "option_counts": option_count_dist,
        "duplicate_options": duplicate_option_qs,
        "missing_answers": missing_answer_qs,
        "score": max(0, score),
        "verdict": verdict,
        "notes": notes,
    }


async def process_json_quiz_data(
    data: dict,
    user,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    update: Update,
    quiz_upgrade_id: int = None,
    quiz_update_id: int = None
):
    """Processes parsed JSON data for either updating an existing quiz, upgrading an existing URL quiz, or saving a new quiz."""
    u_id = user.id if user else 6099429826

    # 1. Update/Replace questions of an existing quiz (Preserves all Spaced Repetition reviews!)
    if quiz_update_id:
        new_name = data.get("quiz_name") or data.get("name")
        db.update_quiz_questions(quiz_update_id, data["questions"], new_name=new_name)

        # Process wrong field if present
        wrong_indices = data.get("wrong", [])
        wrong_count = 0
        if wrong_indices:
            saved_questions = db.get_questions(quiz_update_id)
            for idx in wrong_indices:
                try:
                    real_idx = int(idx) - 1
                    if 0 <= real_idx < len(saved_questions):
                        q = saved_questions[real_idx]
                        db.add_or_reset_weak_question(quiz_update_id, q["id"], user_id=u_id)
                        wrong_count += 1
                except (ValueError, TypeError):
                    continue

        quiz = db.get_quiz(quiz_update_id)
        name_safe = html.escape(quiz.get('name', 'كويز')) if quiz else "كويز"
        wrong_note = f"\n❌ تمت إضافة <b>{wrong_count}</b> سؤال للأسئلة الضعيفة." if wrong_count else ""
        
        audit = audit_quiz_quality(data["questions"])
        audit_summary = f"🎯 <b>جودة التدقيق:</b> {audit['score']}% — {audit['verdict']}\n"
        if audit["passages"] > 0:
            audit_summary += f"📄 نصوص وقطع قراءة: <b>{audit['passages']}</b> قطعة مدمجة\n"
        if audit["explanations"] > 0:
            audit_summary += f"💡 شروحات وتوضيحات: <b>{audit['explanations']}</b> شرح متوفر\n"
        if audit["notes"]:
            audit_summary += "\n" + "\n".join(audit["notes"]) + "\n"

        text = (
            f"✅ <b>تم تحديث وتدقيق أسئلة الكويز بنجاح!</b>\n\n"
            f"📋 <b>{name_safe}</b>\n"
            f"📝 تم تحديث <b>{len(data['questions'])}</b> سؤال بنجاح.{wrong_note}\n\n"
            f"{audit_summary}\n"
            f"🔁 <b>جدول التكرار المتباعد:</b> محفوظ ومستمر حسب جدولك السابق دون أي تغيير 🌟."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👁️ معاينة الأسئلة", callback_data=f"preview_quiz_{quiz_update_id}_0"),
                InlineKeyboardButton("🛠 تعديل الأسئلة", callback_data=f"fixstage_qlist_{quiz_update_id}_0")
            ],
            [
                InlineKeyboardButton("▶️ ابدأ الكويز المحدث", callback_data=f"start_quiz_{quiz_update_id}"),
                InlineKeyboardButton("📋 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_update_id}")
            ],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])

    # 2. Upgrade URL to JSON quiz
    elif quiz_upgrade_id:
        conn = db.get_connection()
        conn.execute("UPDATE quizzes SET url = NULL WHERE id = ?", (quiz_upgrade_id,))
        for q in data["questions"]:
            conn.execute(
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
            [
                InlineKeyboardButton("👁️ معاينة الأسئلة", callback_data=f"preview_quiz_{quiz_upgrade_id}_0"),
                InlineKeyboardButton("▶️ ابدأ حل الكويز الآن", callback_data=f"start_quiz_{quiz_upgrade_id}")
            ],
            [InlineKeyboardButton("📋 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_upgrade_id}")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
    else:
        is_pub = 1
        owner_id = u_id
        quiz_name = data.get("quiz_name") or data.get("name", "كويز جديد")
        quiz_id = db.save_quiz_without_review(quiz_name, data["questions"], owner_id=owner_id, is_public=is_pub)
        
        # Schedule first review for admin in Spaced Repetition
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
            wrong_note = f"\n⚠️ وُجد حقل 'wrong' لكن الأرقام {list(wrong_indices)} لم تطابق أي سؤال."
        else:
            wrong_note = ""

        audit = audit_quiz_quality(data["questions"])
        audit_lines = [
            f"🎯 <b>جودة التدقيق:</b> {audit['score']}% — {audit['verdict']}",
            f"📝 إجمالي الأسئلة: <b>{audit['total']}</b> سؤال",
        ]
        if audit["passages"] > 0:
            audit_lines.append(f"📄 نصوص وقطع قراءة: <b>{audit['passages']}</b> قطعة مدمجة")
        if audit["explanations"] > 0:
            audit_lines.append(f"💡 شروحات وتوضيحات: <b>{audit['explanations']}</b> شرح متوفر")
        if audit["notes"]:
            audit_lines.append("\n" + "\n".join(audit["notes"]))

        text = (
            f"✅ <b>تم تدقيق وإضافة الكويز بنجاح!</b>\n\n"
            f"📋 <b>{name_safe}</b>\n\n"
            + "\n".join(audit_lines) +
            f"{wrong_note}\n\n"
            f"الكويز متاح الآن في بنك الكويزات لجميع الطلاب 🌟."
        )

        kb = [
            [
                InlineKeyboardButton("👁️ معاينة الأسئلة", callback_data=f"preview_quiz_{quiz_id}_0"),
                InlineKeyboardButton("📁 نقل إلى مجلد", callback_data=f"move_quiz_{quiz_id}")
            ],
            [
                InlineKeyboardButton("▶️ ابدأ حل الكويز", callback_data=f"start_quiz_{quiz_id}"),
                InlineKeyboardButton("🛠 تعديل الأسئلة", callback_data=f"fixstage_qlist_{quiz_id}_0")
            ],
            [
                InlineKeyboardButton("📋 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_id}"),
                InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")
            ],
        ]
        keyboard = InlineKeyboardMarkup(kb)

    await send_clean_message(context, chat_id, text, update=update, reply_markup=keyboard)


# ─── JSON File Handler ─────────────────────────────────────────────────────────

async def json_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.effective_message
    if not msg_obj or not msg_obj.document: return

    user = update.effective_user
    if not user or not is_admin(user.id):
        await send_clean_message(
            context=context,
            chat_id=update.effective_chat.id,
            update=update,
            text="❌ رفع الكويزات متاح للمشرف فقط.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        )
        return

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
        quiz_update_id = context.user_data.pop("waiting_for_json_update", None)
        user = update.effective_user

        await process_json_quiz_data(
            data=data,
            user=user,
            context=context,
            chat_id=chat_id,
            update=update,
            quiz_upgrade_id=quiz_upgrade_id,
            quiz_update_id=quiz_update_id
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


# ─── Excel & CSV Quiz Parser ──────────────────────────────────────────────────

def parse_excel_or_csv_quiz(file_path: str, filename: str = '') -> dict:
    """
    Parses an Excel (.xlsx/.xls) or CSV file into standard quiz dictionary:
    {
        "quiz_name": "...",
        "wrong": [...],
        "questions": [...]
    }
    """
    ext = os.path.splitext(filename or file_path)[1].lower()
    raw_rows = []
    quiz_name = ''

    if ext in ['.xlsx', '.xlsm', '.xltx', '.xltm', '.xls']:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        sheet = wb.active
        if sheet.title and sheet.title not in ['Sheet', 'Sheet1', 'ورقة1', 'Sheet 1']:
            quiz_name = sheet.title.strip()
        for row in sheet.iter_rows(values_only=True):
            if any(cell is not None and str(cell).strip() != '' for cell in row):
                raw_rows.append([str(c).strip() if c is not None else '' for c in row])
    else:
        # CSV file (try multiple encodings)
        encodings = ['utf-8-sig', 'utf-8', 'cp1256', 'latin-1']
        for enc in encodings:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if any(cell.strip() for cell in row):
                            raw_rows.append([cell.strip() for cell in row])
                if raw_rows:
                    break
            except Exception:
                raw_rows = []

    if not raw_rows:
        raise ValueError("الملف فارغ أو لا يحتوي على صفوف بيانات صالحة.")

    if not quiz_name:
        base = os.path.basename(filename or file_path)
        quiz_name = os.path.splitext(base)[0].replace('_', ' ').strip()
    if not quiz_name or quiz_name.lower() in ['template', 'quiz', 'كويز', 'قالب']:
        quiz_name = 'كويز جديد'

    # Detect header row
    header_idx = -1
    col_map = {'q': -1, 'opts': [], 'ans': -1, 'exp': -1, 'wrong': -1}

    for idx, row in enumerate(raw_rows[:5]):
        row_str = ' '.join(c.lower() for c in row)
        if any(w in row_str for w in ['سؤال', 'question', 'نص', 'خيار', 'إجابة', 'اجابة', 'answer', 'opt']):
            header_idx = idx
            break

    if header_idx != -1:
        header_row = [c.lower() for c in raw_rows[header_idx]]
        data_rows = raw_rows[header_idx + 1:]

        for c_idx, h_text in enumerate(header_row):
            h_clean = h_text.replace(' ', '')
            if any(k in h_clean for k in ['سؤال', 'question', 'q_text', 'text']):
                if col_map['q'] == -1: col_map['q'] = c_idx
            elif any(k in h_clean for k in ['إجابة', 'اجابة', 'الصح', 'answer', 'correct', 'ans']):
                if col_map['ans'] == -1: col_map['ans'] = c_idx
            elif any(k in h_clean for k in ['شرح', 'توضيح', 'ملاحظات', 'explanation', 'exp', 'feedback']):
                if col_map['exp'] == -1: col_map['exp'] = c_idx
            elif any(k in h_clean for k in ['خطأ', 'خاطئ', 'wrong', 'is_wrong']):
                if col_map['wrong'] == -1: col_map['wrong'] = c_idx
            elif any(k in h_clean for k in ['خيار', 'اختيار', 'option', 'choice', 'opt', 'أ', 'ب', 'ج', 'د', '(أ)', '(ب)', '(ج)', '(د)']):
                col_map['opts'].append(c_idx)
    else:
        data_rows = raw_rows
        col_map['q'] = 0
        col_map['opts'] = [1, 2, 3, 4] if len(raw_rows[0]) >= 5 else list(range(1, len(raw_rows[0]) - 1))
        col_map['ans'] = col_map['opts'][-1] + 1 if len(raw_rows[0]) > len(col_map['opts']) + 1 else -1
        col_map['exp'] = col_map['ans'] + 1 if col_map['ans'] != -1 and len(raw_rows[0]) > col_map['ans'] + 1 else -1

    if col_map['q'] == -1:
        col_map['q'] = 0
    if not col_map['opts']:
        col_map['opts'] = [i for i in range(len(data_rows[0])) if i != col_map['q'] and i != col_map['ans'] and i != col_map['exp']]

    questions = []
    wrong_indices = []

    for r_idx, row in enumerate(data_rows):
        if not any(row):
            continue
        q_text = row[col_map['q']] if col_map['q'] < len(row) else ''
        if not q_text.strip():
            continue

        opts = []
        for o_idx in col_map['opts']:
            if o_idx < len(row) and row[o_idx].strip():
                val = row[o_idx].strip()
                if val not in opts:
                    opts.append(val)

        if len(opts) < 2:
            continue

        ans_raw = row[col_map['ans']] if col_map['ans'] != -1 and col_map['ans'] < len(row) else ''
        ans = ans_raw.strip()

        # Map letter/number answers
        if ans in ['أ', 'A', 'a', '1', '١', '(أ)'] and len(opts) >= 1:
            ans = opts[0]
        elif ans in ['ب', 'B', 'b', '2', '٢', '(ب)'] and len(opts) >= 2:
            ans = opts[1]
        elif ans in ['ج', 'C', 'c', '3', '٣', '(ج)'] and len(opts) >= 3:
            ans = opts[2]
        elif ans in ['د', 'D', 'd', '4', '٤', '(د)'] and len(opts) >= 4:
            ans = opts[3]
        elif ans not in opts:
            match = next((o for o in opts if ans.lower() in o.lower() or o.lower() in ans.lower()), None)
            if match:
                ans = match
            else:
                ans = opts[0]

        exp = row[col_map['exp']] if col_map['exp'] != -1 and col_map['exp'] < len(row) else ''

        q_num = len(questions) + 1
        if col_map['wrong'] != -1 and col_map['wrong'] < len(row):
            w_val = row[col_map['wrong']].strip().lower()
            if w_val in ['1', 'true', 'نعم', 'صح', 'خطأ', 'خاطئ', 'x']:
                wrong_indices.append(q_num)

        questions.append({
            'question': q_text,
            'options': opts,
            'answer': ans,
            'explanation': exp
        })

    if not questions:
        raise ValueError("لم يتم العثور على أي أسئلة صالحة في ملف الإكسل.")

    return {
        'quiz_name': quiz_name,
        'wrong': wrong_indices,
        'questions': questions
    }


# ─── Excel / CSV Document Handler ─────────────────────────────────────────────

async def excel_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.effective_message
    if not msg_obj or not msg_obj.document:
        return

    user = update.effective_user
    if not user or not is_admin(user.id):
        await send_clean_message(
            context=context,
            chat_id=update.effective_chat.id,
            update=update,
            text="❌ رفع الكويزات متاح للمشرف فقط.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        )
        return

    doc = msg_obj.document
    chat_id = update.effective_chat.id
    if update.message:
        db.track_chat_message(chat_id, update.message.message_id)

    msg_id = await send_clean_message(
        context=context,
        chat_id=chat_id,
        update=update,
        text="📊 <b>جاري قراءة وتدقيق ملف الإكسل...</b> ⏳"
    )

    tmp_path = None
    try:
        ext = os.path.splitext(doc.file_name or "quiz.xlsx")[1].lower()
        if ext not in [".xlsx", ".xls", ".csv"]:
            ext = ".xlsx"

        file = await doc.get_file()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        data = parse_excel_or_csv_quiz(tmp_path, filename=doc.file_name or "")

        # Centralised validation
        _validate_json_upload(doc, data)

        quiz_upgrade_id = context.user_data.pop("waiting_for_json_upgrade", None)
        quiz_update_id = context.user_data.pop("waiting_for_json_update", None)

        await process_json_quiz_data(
            data=data,
            user=user,
            context=context,
            chat_id=chat_id,
            update=update,
            quiz_upgrade_id=quiz_upgrade_id,
            quiz_update_id=quiz_update_id
        )

    except ValueError as e:
        err = f"❌ <b>خطأ في محتوى ملف الإكسل:</b>\n{str(e)}"
        await send_clean_message(context, chat_id, err, update=update)
    except Exception as e:
        err = f"❌ <b>حدث خطأ غير متوقع أثناء معالجة الإكسل:</b>\n{str(e)}"
        await send_clean_message(context, chat_id, err, update=update)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ─── PDF File Handler (AI Extractor) ──────────────────────────────────────────

async def pdf_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles PDF uploads (e.g. Google Forms results printed to PDF or test PDFs).
    Uses Gemini AI to extract questions, correct answers, and wrong question indices.
    """
    msg_obj = update.effective_message
    if not msg_obj or not msg_obj.document: return

    doc = msg_obj.document
    chat_id = update.effective_chat.id
    if update.message:
        db.track_chat_message(chat_id, update.message.message_id)

    await send_clean_message(
        context=context,
        chat_id=chat_id,
        update=update,
        text="🤖 <b>جاري قراءة وتدقيق واستخراج الأسئلة بالذكاء الاصطناعي...</b> ⏳\n<i>(سيتم حل المسائل وتحديد الإجابات الصحيحة وأخطائك تلقائياً)</i>"
    )

    tmp_path = None
    try:
        from ai_extractor import extract_questions_from_pdf
        file = await doc.get_file()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        data = await extract_questions_from_pdf(tmp_path)

        # Validate & normalize
        _validate_json_upload(None, data)

        user = update.effective_user
        quiz_upgrade_id = context.user_data.pop("waiting_for_json_upgrade", None)
        quiz_update_id = context.user_data.pop("waiting_for_json_update", None)

        await process_json_quiz_data(
            data=data,
            user=user,
            context=context,
            chat_id=chat_id,
            update=update,
            quiz_upgrade_id=quiz_upgrade_id,
            quiz_update_id=quiz_update_id
        )

    except Exception as e:
        err = f"❌ <b>تعذر استخراج الأسئلة من ملف PDF:</b>\n{str(e)}"
        await send_clean_message(context, chat_id, err, update=update)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
