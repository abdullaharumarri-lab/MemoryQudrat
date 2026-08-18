import html
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db


def build_question_keyboard(options: list, question_id: int, session_type: str) -> InlineKeyboardMarkup:
    """Build MCQ keyboard with options."""
    keyboard = []
    letters = ["أ", "ب", "ج", "د", "هـ", "و"]
    for idx, opt in enumerate(options):
        letter = letters[idx] if idx < len(letters) else str(idx+1)
        keyboard.append([InlineKeyboardButton(f"{letter}) {opt}", callback_data=f"ans_{session_type}_{question_id}_{idx}")])
    return InlineKeyboardMarkup(keyboard)


def format_progress(current: int, total: int, correct: int) -> str:
    filled = int((current / total) * 10) if total > 0 else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    return f"{bar}\n❓ {current}/{total} | ✅ {correct} صح"


import logging
from utils import safe_edit, strip_html_tags

logger = logging.getLogger(__name__)

# Alias for backward compatibility within quiz_handler
safe_edit_html = safe_edit



async def start_quiz_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quiz_id: int,
    session_type: str = "quiz",
    review_id: int = None,
):
    query = update.callback_query

    if session_type == "weakall":
        # ALL weak questions from ALL quizzes, newest first (not just due)
        weak_list = db.get_all_weak_questions_sorted_for_practice()
        if not weak_list:
            await safe_edit_html(
                query,
                "✅ لا توجد أسئلة ضعيفة مسجلة!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
                context=context
            )
            return
        question_ids = [w["question_id"] for w in weak_list]
        db.save_session(
            session_type="weakall",
            quiz_id=0,
            review_id=None,
            question_ids=question_ids,
            current_index=0,
            correct_count=0,
            wrong_ids=[],
        )
        await safe_edit_html(
            query,
            f"❌ <b>مراجعة جميع الأسئلة الضعيفة</b>\n📝 {len(question_ids)} سؤال (الأحدث أولاً)\n\nجاري تحميل أول سؤال...",
            context=context
        )
        await show_next_question(update, context)
        return

    if session_type == "weak":
        weak_list = db.get_due_weak_questions()
        # Sort: newest first (highest id first)
        weak_list_quiz = sorted(
            [w for w in weak_list if w["quiz_id"] == quiz_id],
            key=lambda x: x["id"], reverse=True
        )
        question_ids = [w["question_id"] for w in weak_list_quiz]
        if not question_ids:
            await safe_edit_html(
                query,
                "✅ لا توجد أسئلة ضعيفة مستحقة لهذا الكويز اليوم!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
                context=context
            )
            return
        title = "❌ مراجعة الأسئلة الضعيفة"

    elif session_type == "weakpractice":
        all_weak = db.get_all_weak_questions_sorted_for_practice()
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
    title_safe = html.escape(quiz.get('name', 'كويز')) if quiz else "كويز"
    await safe_edit_html(
        query,
        f"{title}\n📋 <b>{title_safe}</b>\n📝 {len(question_ids)} سؤال\n\nجاري تحميل أول سؤال...",
        context=context
    )

    await show_next_question(update, context)


async def send_next_question(update, context, session):
    q_id = session["question_ids"][session["current_index"]]
    question = db.get_question(q_id)
    if not question:
        # Question missing or deleted, skip ahead safely
        logger.warning("Question ID %s not found in DB, skipping...", q_id)
        new_index = session["current_index"] + 1
        db.update_session(new_index, session["correct_count"], session["wrong_ids"], session.get("poll_id"))
        session["current_index"] = new_index
        if session["current_index"] >= len(session["question_ids"]):
            await finish_session(update, context, session)
        else:
            await send_next_question(update, context, session)
        return

    options = question.get("options") or []
    if not options:
        options = ["نعم", "لا"]
    
    # Determine correct index
    correct_str = str(question.get("correct_answer", "")).strip()
    correct_idx = 0
    for idx, opt in enumerate(options):
        if str(opt).strip() == correct_str:
            correct_idx = idx
            break

    q_text = str(question.get("question_text", ""))
    explanation = str(question.get("explanation", ""))
    if len(explanation) > 195:
        explanation = explanation[:195] + "..."

    chat_id = None
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        chat_id = update.effective_chat.id
    elif update and update.poll_answer and update.poll_answer.user:
        chat_id = update.poll_answer.user.id
    elif context.user_data.get("chat_id"):
        chat_id = context.user_data["chat_id"]
            
    if not chat_id:
        logger.error("Could not determine chat_id to send poll.")
        return
    else:
        context.user_data["chat_id"] = chat_id

    # Check Telegram Poll limits
    needs_context_message = False
    if len(q_text) > 290:
        needs_context_message = True

    for opt in options:
        if len(str(opt)) > 95:
            needs_context_message = True
            break

    poll_options = []
    letters = ["أ", "ب", "ج", "د", "هـ", "و"]
    if needs_context_message:
        context_text = f"📝 <b>السؤال {session['current_index'] + 1} من {len(session['question_ids'])}</b>\n\n"
        context_text += f"<b>{html.escape(q_text)}</b>\n"
        for idx, opt in enumerate(options):
            letter = letters[idx] if idx < len(letters) else str(idx+1)
            context_text += f"\n<b>{letter})</b> {html.escape(str(opt))}"
            poll_options.append(letter)
        
        poll_question = f"السؤال {session['current_index'] + 1} (اختر الإجابة من الرسالة أعلاه):"
        await context.bot.send_message(chat_id=chat_id, text=context_text, parse_mode="HTML")
    else:
        poll_question = q_text
        poll_options = [str(o) for o in options]

    # Limit options length just in case
    poll_options = [opt[:99] for opt in poll_options]

    poll_msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=poll_question,
        options=poll_options,
        type="quiz",
        correct_option_id=correct_idx,
        explanation=explanation,
        is_anonymous=False, # Must be False to track who answered in PollAnswerHandler
    )

    db.update_session(
        session["current_index"], 
        session["correct_count"], 
        session["wrong_ids"], 
        poll_msg.poll.id
    )


async def show_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update else None
    try:
        session = db.get_session()
        if not session:
            if query:
                await safe_edit_html(
                    query,
                    "⚠️ انتهت الجلسة أو لا توجد جلسة نشطة.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
                    context=context
                )
            return

        if session["current_index"] >= len(session["question_ids"]):
            await finish_session(update, context, session)
            return

        await send_next_question(update, context, session)
    except Exception as e:
        logger.exception("Error in show_next_question: %s", e)
        if query:
            await safe_edit_html(
                query,
                f"❌ حدث خطأ أثناء تحميل السؤال.\nيرجى العودة للقائمة الرئيسية.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
                context=context
            )


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    selected_options = answer.option_ids

    session = db.get_session()
    if not session or session.get("poll_id") != poll_id:
        return

    if session["current_index"] >= len(session["question_ids"]):
        await finish_session(update, context, session)
        return

    q_id = session["question_ids"][session["current_index"]]
    question = db.get_question(q_id)
    if not question:
        new_index = session["current_index"] + 1
        db.update_session(new_index, session["correct_count"], session["wrong_ids"], None)
        await show_next_question(update, context)
        return

    options = question.get("options") or []
    if not options:
        options = ["نعم", "لا"]

    correct_str = str(question.get("correct_answer", "")).strip()
    correct_idx = 0
    for idx, opt in enumerate(options):
        if str(opt).strip() == correct_str:
            correct_idx = idx
            break

    user_option_id = selected_options[0] if selected_options else -1
    is_correct = user_option_id == correct_idx

    new_correct = session["correct_count"] + (1 if is_correct else 0)
    new_wrong = list(session.get("wrong_ids", []))
    if not is_correct and q_id not in new_wrong:
        new_wrong.append(q_id)

    new_index = session["current_index"] + 1
    db.update_session(new_index, new_correct, new_wrong, None)

    # Delay slightly to let the user see the poll result
    import asyncio
    await asyncio.sleep(1.0)
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
            if session_type == "weakall":
                # For weakall, look up the question's own quiz_id
                q = db.get_question(wq_id)
                if q:
                    db.add_or_reset_weak_question(q["quiz_id"], wq_id)
            else:
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

    elif session_type == "weakall":
        # Advance correctly answered weak questions (from all questions pool)
        all_weak = db.get_all_weak_questions_sorted_for_practice()
        all_weak_map = {w["question_id"]: w for w in all_weak}
        correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
        for qid in correct_ids:
            if qid in all_weak_map:
                db.advance_weak_question(all_weak_map[qid]["id"])
        sr_text = f"✅ {len(correct_ids)} سؤال تم تقدمهم في التكرار المتباعد."

    elif session_type == "practice":
        sr_text = "🎮 وضع التجربة — لم يتم احتساب هذه الجلسة في المراجعات."

    elif session_type == "weakpractice":
        sr_text = "🛠 تدريب على الأخطاء — هذه الجلسة لم تؤثر على جداول التكرار."

    # Log session for weekly stats (exclude practice modes)
    if session_type not in ("practice", "weakpractice"):
        db.log_session(quiz_id if quiz_id != 0 else None, session_type, total, correct, len(wrong_ids))

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
    if session_type != "weakall" and quiz_id != 0:
        keyboard.insert(0, [
            InlineKeyboardButton("🛠 تعديل أسئلة الكويز", callback_data=f"fixstage_qlist_{quiz_id}_0")
        ])

    db.clear_session()
    chat_id = None
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        chat_id = update.effective_chat.id
    elif context.user_data.get("chat_id"):
        chat_id = context.user_data["chat_id"]
        
    if chat_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    elif query:
        await safe_edit_html(query, result_text, InlineKeyboardMarkup(keyboard), context=context)
