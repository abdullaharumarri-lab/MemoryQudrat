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

logger = logging.getLogger(__name__)

def truncate_text(text: str, max_len: int = 3800) -> str:
    """Safely truncate text to avoid Telegram 4096 char limit."""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len - 30] + "\n\n...(تم اختصار النص لطوله)"

async def safe_edit_html(query, text, reply_markup=None, context=None):
    if not query:
        return
    text = truncate_text(text, 3800)
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.warning("safe_edit_html HTML edit failed: %s", e)
    
    # Try editing without HTML
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.warning("safe_edit_html plain edit failed: %s", e)

    # Fallback: send message
    if context and query.message:
        try:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception:
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=reply_markup
                )
            except Exception as e2:
                logger.error("safe_edit_html fallback send_message failed: %s", e2)



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


async def send_next_question(query, context, session):
    q_id = session["question_ids"][session["current_index"]]
    question = db.get_question(q_id)
    if not question:
        # Question missing or deleted, skip ahead safely
        logger.warning("Question ID %s not found in DB, skipping...", q_id)
        new_index = session["current_index"] + 1
        db.update_session(new_index, session["correct_count"], session["wrong_ids"])
        session["current_index"] = new_index
        if session["current_index"] >= len(session["question_ids"]):
            await finish_session(query._update if hasattr(query, "_update") else None, context, session)
        else:
            await send_next_question(query, context, session)
        return

    options = question.get("options") or []
    letters = ["أ", "ب", "ج", "د", "هـ", "و"]
    options_text = ""
    for idx, opt in enumerate(options):
        letter = letters[idx] if idx < len(letters) else str(idx+1)
        options_text += f"\n<b>{letter})</b> {html.escape(str(opt))}\n"

    q_text = str(question.get("question_text", ""))
    if len(q_text) > 3200:
        q_text = q_text[:3170] + "\n...(تم اختصار نص السؤال لطوله)"

    text = (
        f"📝 <b>السؤال {session['current_index'] + 1} من {len(session['question_ids'])}</b>\n\n"
        f"<b>{html.escape(q_text)}</b>\n"
        f"{options_text}"
    )

    kb = build_question_keyboard(options, q_id, session["session_type"])
    await safe_edit_html(query, text, kb, context=context)


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

        await send_next_question(query, context, session)
    except Exception as e:
        logger.exception("Error in show_next_question: %s", e)
        if query:
            await safe_edit_html(
                query,
                f"❌ حدث خطأ أثناء تحميل السؤال.\nيرجى العودة للقائمة الرئيسية.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
                context=context
            )


async def quiz_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        import asyncio
        asyncio.create_task(query.answer())
    except Exception:
        pass

    data = query.data
    parts = data.split("_", 3)
    if len(parts) < 4:
        return

    _, session_type, q_id_str, opt_idx_str = parts
    try:
        q_id = int(q_id_str)
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

    if session["current_index"] >= len(session["question_ids"]):
        await finish_session(update, context, session)
        return

    current_q_id = session["question_ids"][session["current_index"]]
    if q_id != current_q_id:
        return

    question = db.get_question(q_id)
    if not question:
        new_index = session["current_index"] + 1
        db.update_session(new_index, session["correct_count"], session["wrong_ids"])
        await show_next_question(update, context)
        return

    options = question.get("options") or []
    if opt_idx < 0 or opt_idx >= len(options):
        return

    user_answer = str(options[opt_idx]).strip()
    correct = str(question.get("correct_answer", "")).strip()
    is_correct = user_answer == correct

    new_correct = session["correct_count"] + (1 if is_correct else 0)
    new_wrong = list(session.get("wrong_ids", []))
    if not is_correct and q_id not in new_wrong:
        new_wrong.append(q_id)

    new_index = session["current_index"] + 1
    db.update_session(new_index, new_correct, new_wrong)

    correct_safe = html.escape(correct)
    if is_correct:
        feedback = "✅ <b>إجابة صحيحة!</b>\n\n"
    else:
        feedback = f"❌ <b>إجابة خاطئة!</b>\n\nالصواب: <b>{correct_safe}</b>\n\n"

    explanation = question.get("explanation")
    if explanation:
        expl_safe = html.escape(str(explanation))
        feedback += f"💡 {expl_safe}\n\n"

    feedback += "<i>جاري تحميل السؤال التالي...</i>"

    await safe_edit_html(query, feedback, context=context)

    await asyncio.sleep(0.3)
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

    db.clear_session()
    await safe_edit_html(query, result_text, InlineKeyboardMarkup(keyboard), context=context)
