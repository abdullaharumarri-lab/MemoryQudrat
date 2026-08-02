import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db


def build_question_keyboard(options: list, question_id: int, session_type: str) -> InlineKeyboardMarkup:
    """Build MCQ keyboard with shuffled options."""
    keyboard = []
    for opt in options:
        keyboard.append([
            InlineKeyboardButton(opt, callback_data=f"ans_{session_type}_{question_id}_{opt[:40]}")
        ])
    return InlineKeyboardMarkup(keyboard)


def format_progress(current: int, total: int, correct: int) -> str:
    filled = int((current / total) * 10) if total > 0 else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    return f"{bar}\n❓ {current}/{total} | ✅ {correct} صح"


async def start_quiz_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quiz_id: int,
    session_type: str = "quiz",
    review_id: int = None,
):
    """Initialize and start a quiz/review/weak session."""
    query = update.callback_query

    if session_type == "weak":
        # Get weak question IDs for this quiz
        weak_list = db.get_due_weak_questions()
        weak_for_quiz = [w for w in weak_list if w["quiz_id"] == quiz_id]
        if not weak_for_quiz:
            await query.edit_message_text(
                "✅ لا توجد أسئلة ضعيفة مستحقة لهذا الكويز اليوم!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
                ]),
            )
            return
        question_ids = [w["question_id"] for w in weak_for_quiz]
        title = "❌ مراجعة الأسئلة الضعيفة"
    else:
        questions = db.get_questions(quiz_id)
        if not questions:
            await query.edit_message_text("❌ لا توجد أسئلة في هذا الكويز.")
            return
        question_ids = [q["id"] for q in questions]
        if session_type == "practice":
            title = "🎮 تجربة"
        elif session_type == "review":
            title = "🔁 المراجعة"
        else:
            title = "▶️ الكويز"

    # Keep questions in original order (no shuffle)

    # Save session
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
    await query.edit_message_text(
        f"{title}\n📋 *{quiz['name']}*\n📝 {len(question_ids)} سؤال\n\nجاري تحميل أول سؤال...",
        parse_mode="Markdown",
    )

    # Show first question
    await show_next_question(update, context)


async def show_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display the current question."""
    session = db.get_session()
    if not session:
        return

    idx = session["current_index"]
    question_ids = session["question_ids"]
    total = len(question_ids)

    if idx >= total:
        await finish_session(update, context, session)
        return

    q_id = question_ids[idx]
    question = db.get_question(q_id)
    if not question:
        return

    progress = format_progress(idx + 1, total, session["correct_count"])
    text = (
        f"{progress}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"*السؤال {idx + 1}:*\n"
        f"{question['question_text']}"
    )

    keyboard = build_question_keyboard(
        question["options"], q_id, session["session_type"]
    )

    query = update.callback_query
    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def quiz_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle answer button presses during a quiz/review/weak session."""
    query = update.callback_query
    await query.answer()

    data = query.data  # ans_{session_type}_{question_id}_{answer}
    parts = data.split("_", 3)
    if len(parts) < 4:
        return

    _, session_type, q_id_str, user_answer = parts
    q_id = int(q_id_str)

    session = db.get_session()
    if not session or session["session_type"] != session_type:
        await query.edit_message_text(
            "⚠️ لا توجد جلسة نشطة.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
            ]),
        )
        return

    question = db.get_question(q_id)
    if not question:
        return

    correct = question["correct_answer"]
    is_correct = user_answer.strip() == correct.strip()

    # Update session stats
    new_correct = session["correct_count"] + (1 if is_correct else 0)
    new_wrong = session["wrong_ids"]
    if not is_correct and q_id not in new_wrong:
        new_wrong = new_wrong + [q_id]

    new_index = session["current_index"] + 1
    db.update_session(new_index, new_correct, new_wrong)

    # Build feedback message
    if is_correct:
        feedback = f"✅ *إجابة صحيحة!*\n\n"
    else:
        feedback = f"❌ *إجابة خاطئة!*\n\nالصواب: *{correct}*\n\n"

    if question.get("explanation"):
        feedback += f"💡 {question['explanation']}\n\n"

    feedback += "_جاري تحميل السؤال التالي..._"

    await query.edit_message_text(feedback, parse_mode="Markdown")

    # Show next question
    import asyncio
    await asyncio.sleep(1.5)
    await show_next_question(update, context)


async def finish_session(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict):
    """Handle end of quiz/review session and update spaced repetition."""
    query = update.callback_query
    total = len(session["question_ids"])
    correct = session["correct_count"]
    wrong_ids = session["wrong_ids"]
    score = int((correct / total) * 100) if total > 0 else 0
    quiz_id = session["quiz_id"]
    session_type = session["session_type"]

    # Emoji rating
    if score >= 80:
        rating = "🏆 ممتاز!"
    elif score >= 60:
        rating = "👍 جيد!"
    elif score >= 40:
        rating = "📚 تحتاج مراجعة"
    else:
        rating = "💪 استمر في المحاولة"

    # Update spaced repetition for wrong questions (not in practice mode)
    if session_type != "practice":
        for wq_id in wrong_ids:
            db.add_or_reset_weak_question(quiz_id, wq_id)

    # Advance quiz/review spaced repetition
    if session_type in ("quiz", "review") and session.get("review_id"):
        db.advance_quiz_review(session["review_id"])
        sr_text = "✅ تمت المراجعة وجُدوِّل الموعد التالي تلقائياً."
    elif session_type == "weak":
        # For each correct answer in weak session, advance its stage
        weak_all = db.get_due_weak_questions()
        quiz_weak = {w["question_id"]: w for w in weak_all if w["quiz_id"] == quiz_id}
        correct_ids = [
            qid for qid in session["question_ids"] if qid not in wrong_ids
        ]
        for qid in correct_ids:
            if qid in quiz_weak:
                db.advance_weak_question(quiz_weak[qid]["id"])
        sr_text = f"✅ {len(correct_ids)} سؤال تم تقدمهم في التكرار المتباعد."
    elif session_type == "practice":
        # Practice mode: don't update spaced repetition at all
        sr_text = "🎮 وضع التجربة — لم يتم احتساب هذه الجلسة في المراجعات."

    result_text = (
        f"🎉 *انتهى الكويز!*\n\n"
        f"{rating}\n\n"
        f"📊 *النتيجة:* {correct}/{total} ({score}%)\n"
        f"✅ صح: {correct} | ❌ خطأ: {len(wrong_ids)}\n\n"
        f"{sr_text}"
    )

    keyboard = [[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]]
    if wrong_ids:
        keyboard.insert(0, [
            InlineKeyboardButton(
                "❌ راجع الأسئلة الخاطئة", callback_data=f"start_weak_{quiz_id}"
            )
        ])

    db.clear_session()

    await query.edit_message_text(
        result_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
