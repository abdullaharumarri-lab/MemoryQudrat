import html
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db


def build_question_keyboard(options: list, question_id: int, session_type: str) -> InlineKeyboardMarkup:
    """Build MCQ keyboard with options."""
    keyboard = []
    row = []
    letters = ["أ", "ب", "ج", "د", "هـ", "و"]
    for idx, opt in enumerate(options):
        letter = letters[idx] if idx < len(letters) else str(idx+1)
        row.append(InlineKeyboardButton(f"[{letter}]", callback_data=f"ans_{session_type}_{question_id}_{idx}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def format_progress(current: int, total: int, correct: int) -> str:
    filled = int((current / total) * 10) if total > 0 else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    return f"{bar}\n❓ {current}/{total} | ✅ {correct} صح"


async def safe_edit_html(query, text, reply_markup=None, context=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        try:
            if context:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        except Exception:
            pass


async def start_quiz_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quiz_id: int,
    session_type: str = "quiz",
    review_id: int = None,
):
    query = update.callback_query

    if session_type == "weak":
        weak_list = db.get_due_weak_questions()
        question_ids = [w["question_id"] for w in weak_list if w["quiz_id"] == quiz_id]
        if not question_ids:
            await safe_edit_html(query, "✅ لا توجد أسئلة ضعيفة مستحقة لهذا الكويز اليوم!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]), context=context)
            return
        title = "❌ مراجعة الأسئلة الضعيفة"
    elif session_type == "weakpractice":
        all_weak = db.get_all_weak_questions()
        question_ids = [w["question_id"] for w in all_weak if w["quiz_id"] == quiz_id]
        if not question_ids:
            await safe_edit_html(query, "✅ لا توجد أخطاء للتدرب عليها!", context=context)
            return
        title = "🛠 تدريب على الأخطاء"
    else:
        questions = db.get_questions(quiz_id)
        if not questions:
            await safe_edit_html(query, "❌ لا توجد أسئلة في هذا الكويز.", context=context)
            return
        question_ids = [q["id"] for q in questions]
        if session_type == "practice":
            title = "🎮 تجربة"
        elif session_type == "review":
            title = "🔁 المراجعة"
        else:
            title = "▶️ الكويز"

    db.save_session(
        session_type=session_type,
        quiz_id=quiz_id,
        review_id=review_id,
        question_ids=question_ids,
        current_index=0,
        correct_count=0,
        wrong_ids=[],
    )

    quiz = db.get_quiz(quiz_id)
    title_safe = html.escape(quiz['name'])
    await safe_edit_html(
        query,
        f"{title}\n📋 <b>{title_safe}</b>\n📝 {len(question_ids)} سؤال\n\nجاري تحميل أول سؤال...",
        context=context
    )

    await show_next_question(update, context)


async def send_next_question(query, context, session):
    q_id = session["question_ids"][session["current_index"]]
    question = db.get_question(q_id)

    letters = ["أ", "ب", "ج", "د", "هـ", "و"]
    options_text = ""
    for idx, opt in enumerate(question["options"]):
        letter = letters[idx] if idx < len(letters) else str(idx+1)
        options_text += f"\n<b>{letter})</b> {html.escape(opt)}\n"

    text = (
        f"📝 <b>السؤال {session['current_index'] + 1} من {len(session['question_ids'])}</b>\n\n"
        f"<b>{html.escape(question['question_text'])}</b>\n"
        f"{options_text}"
    )

    kb = build_question_keyboard(question["options"], q_id, session["session_type"])
    await safe_edit_html(query, text, kb, context=context)


async def show_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = db.get_session()
    if not session: return

    if session["current_index"] >= len(session["question_ids"]):
        await finish_session(update, context, session)
        return

    await send_next_question(update.callback_query, context, session)


async def quiz_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split("_", 3)
    if len(parts) < 4: return

    _, session_type, q_id_str, opt_idx_str = parts
    q_id = int(q_id_str)
    
    try:
        opt_idx = int(opt_idx_str)
    except ValueError:
        return

    session = db.get_session()
    if not session or session["session_type"] != session_type:
        await safe_edit_html(
            query,
            "⚠️ لا توجد جلسة نشطة.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
            context=context
        )
        return

    if session["current_index"] >= len(session["question_ids"]): return
    current_q_id = session["question_ids"][session["current_index"]]
    if q_id != current_q_id: return

    question = db.get_question(q_id)
    if not question: return

    try:
        user_answer = question["options"][opt_idx]
    except IndexError:
        return

    correct = question["correct_answer"]
    is_correct = user_answer.strip() == correct.strip()

    new_correct = session["correct_count"] + (1 if is_correct else 0)
    new_wrong = session["wrong_ids"]
    if not is_correct and q_id not in new_wrong:
        new_wrong = new_wrong + [q_id]

    new_index = session["current_index"] + 1
    db.update_session(new_index, new_correct, new_wrong)

    correct_safe = html.escape(correct)
    if is_correct:
        feedback = "✅ <b>إجابة صحيحة!</b>\n\n"
    else:
        feedback = f"❌ <b>إجابة خاطئة!</b>\n\nالصواب: <b>{correct_safe}</b>\n\n"

    if question.get("explanation"):
        expl_safe = html.escape(question['explanation'])
        feedback += f"💡 {expl_safe}\n\n"

    feedback += "<i>جاري تحميل السؤال التالي...</i>"

    await safe_edit_html(query, feedback, context=context)

    await asyncio.sleep(1.5)
    await show_next_question(update, context)


async def finish_session(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict):
    query = update.callback_query
    total = len(session["question_ids"])
    correct = session["correct_count"]
    wrong_ids = session["wrong_ids"]
    score = int((correct / total) * 100) if total > 0 else 0
    quiz_id = session["quiz_id"]
    session_type = session["session_type"]

    if score >= 80: rating = "🏆 ممتاز!"
    elif score >= 60: rating = "👍 جيد!"
    elif score >= 40: rating = "📚 تحتاج مراجعة"
    else: rating = "💪 استمر في المحاولة"

    if session_type != "practice":
        for wq_id in wrong_ids:
            db.add_or_reset_weak_question(quiz_id, wq_id)

    sr_text = ""
    if session_type in ("quiz", "review") and session.get("review_id"):
        db.advance_quiz_review(session["review_id"])
        sr_text = "✅ تمت المراجعة وجُدوِّل الموعد التالي تلقائياً."
    elif session_type == "weak":
        weak_all = db.get_due_weak_questions()
        quiz_weak = {w["question_id"]: w for w in weak_all if w["quiz_id"] == quiz_id}
        correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
        for qid in correct_ids:
            if qid in quiz_weak:
                db.advance_weak_question(quiz_weak[qid]["id"])
        sr_text = f"✅ {len(correct_ids)} سؤال تم تقدمهم في التكرار المتباعد."
    elif session_type == "practice":
        sr_text = "🎮 وضع التجربة — لم يتم احتساب هذه الجلسة في المراجعات."
    elif session_type == "weakpractice":
        sr_text = "🛠 تدريب على الأخطاء — هذه الجلسة لم تؤثر على جداول التكرار."

    result_text = (
        f"🎉 <b>انتهى الكويز!</b>\n\n"
        f"{rating}\n\n"
        f"📊 <b>النتيجة:</b> {correct}/{total} ({score}%)\n"
        f"✅ صح: {correct} | ❌ خطأ: {len(wrong_ids)}\n\n"
        f"{html.escape(sr_text)}"
    )

    keyboard = [[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]]
    if wrong_ids:
        keyboard.insert(0, [
            InlineKeyboardButton("❌ راجع الأسئلة الخاطئة", callback_data=f"start_weak_{quiz_id}")
        ])

    db.clear_session()
    await safe_edit_html(query, result_text, InlineKeyboardMarkup(keyboard), context=context)
