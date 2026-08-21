import html
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from utils import safe_edit, strip_html_tags

logger = logging.getLogger(__name__)

# Alias for backward compatibility within quiz_handler
safe_edit_html = safe_edit


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


async def start_quiz_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quiz_id: int,
    session_type: str = "quiz",
    review_id: int = None,
):
    query = update.callback_query
    user = update.effective_user
    user_id = user.id if user else (context.user_data.get("user_id") or 6099429826)
    context.user_data["user_id"] = user_id

    chat_id = None
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        chat_id = update.effective_chat.id
    elif context.user_data.get("chat_id"):
        chat_id = context.user_data["chat_id"]
        
    if chat_id:
        context.user_data["chat_id"] = chat_id
        await cleanup_quiz_messages(chat_id, context)

    if session_type == "weakall":
        # ALL weak questions from ALL quizzes for this user, newest first
        weak_list = db.get_all_weak_questions_sorted_for_practice(user_id=user_id)
        if not weak_list:
            await safe_edit_html(
                query,
                "✅ لا توجد أسئلة ضعيفة مسجلة في حسابك!",
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
            user_id=user_id,
        )
        await safe_edit_html(
            query,
            f"❌ <b>مراجعة جميع الأسئلة الضعيفة</b>\n📝 {len(question_ids)} سؤال (الأحدث أولاً)\n\nجاري تحميل أول سؤال...",
            context=context
        )
        await show_next_question(update, context)
        return

    if session_type == "weak":
        weak_list = db.get_due_weak_questions(user_id=user_id)
        # Sort: newest first (highest id first)
        weak_list_quiz = sorted(
            [w for w in weak_list if w["quiz_id"] == quiz_id],
            key=lambda x: x["id"], reverse=True
        )
        question_ids = [w["question_id"] for w in weak_list_quiz]
        if not question_ids:
            await safe_edit_html(
                query,
                "✅ لا توجد أسئلة ضعيفة مستحقة لهذا الكويز اليوم في حسابك!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
                context=context
            )
            return
        title = "❌ مراجعة الأسئلة الضعيفة"

    elif session_type == "weakpractice":
        all_weak = db.get_all_weak_questions_sorted_for_practice(user_id=user_id)
        question_ids = [w["question_id"] for w in all_weak if w["quiz_id"] == quiz_id]
        if not question_ids:
            await safe_edit_html(query, "✅ لا توجد أخطاء للتدرب عليها في هذا الكويز!", context=context)
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
        user_id=user_id,
    )

    try:
        if query and query.message:
            await query.message.delete()
    except Exception:
        pass

    await show_next_question(update, context)


async def send_next_question(update, context, session):
    user_id = session.get("user_id", 6099429826)
    q_id = session["question_ids"][session["current_index"]]
    question = db.get_question(q_id)
    if not question:
        # Question missing or deleted, skip ahead safely
        logger.warning("Question ID %s not found in DB, skipping...", q_id)
        new_index = session["current_index"] + 1
        db.update_session(
            new_index,
            session["correct_count"],
            session["wrong_ids"],
            session.get("poll_id"),
            session.get("session_message_ids", []),
            user_id=user_id,
        )
        session["current_index"] = new_index
        if session["current_index"] >= len(session["question_ids"]):
            await finish_session(update, context, session)
        else:
            await send_next_question(update, context, session)
        return

    raw_options = question.get("options") or []
    options = []
    for idx, opt in enumerate(raw_options):
        opt_str = str(opt).strip()
        if not opt_str:
            opt_str = f"(خيار {idx + 1})"
        options.append(opt_str)

    if len(options) < 2:
        options = options + [f"(خيار {i + 1})" for i in range(len(options), 2)]
    if len(options) > 10:
        options = options[:10]
    
    # Determine correct index
    correct_str = str(question.get("correct_answer", "")).strip()
    correct_idx = 0
    for idx, opt in enumerate(options):
        if opt == correct_str or str(raw_options[idx] if idx < len(raw_options) else "").strip() == correct_str:
            correct_idx = idx
            break

    if correct_idx < 0 or correct_idx >= len(options):
        correct_idx = 0

    q_text = str(question.get("question_text", "")).strip()
    if not q_text:
        q_text = "سؤال بدون نص"
        
    explanation = str(question.get("explanation", "")).strip()
    if len(explanation) > 190:
        explanation = explanation[:187] + "..."
    if not explanation:
        explanation = None

    chat_id = None
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        chat_id = update.effective_chat.id
    elif update and getattr(update, "poll_answer", None) and update.poll_answer.user:
        chat_id = update.poll_answer.user.id
    elif context.user_data.get("chat_id"):
        chat_id = context.user_data["chat_id"]
            
    if not chat_id:
        logger.error("Could not determine chat_id to send poll.")
        return
    else:
        context.user_data["chat_id"] = chat_id

    # Check Telegram Poll limits (Question max 300, Option max 100)
    long_question = len(q_text) > 250
    long_options = any(len(opt) > 90 for opt in options)

    poll_options = []
    letters = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]
    msg_ids = session.get("session_message_ids", [])
    
    if long_question or long_options:
        context_text = f"📝 <b>السؤال {session['current_index'] + 1} من {len(session['question_ids'])}</b>\n\n"
        context_text += f"<b>{html.escape(q_text)}</b>\n"
        
        if long_options:
            for idx, opt in enumerate(options):
                letter = letters[idx] if idx < len(letters) else str(idx+1)
                context_text += f"\n<b>{letter})</b> {html.escape(opt)}"
                poll_options.append(letter)
            poll_question = f"السؤال {session['current_index'] + 1} (اختر الإجابة من الرسالة أعلاه):"
        else:
            poll_question = f"السؤال {session['current_index'] + 1} (نص السؤال في الرسالة أعلاه):"
            poll_options = list(options)
            
        if len(context_text) > 3800:
            context_text = context_text[:3750] + "\n\n...(تم اختصار النص لطوله)"

        try:
            ctx_msg = await context.bot.send_message(chat_id=chat_id, text=context_text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Failed sending context message with HTML, falling back to plain text: %s", e)
            clean_ctx = strip_html_tags(context_text)
            ctx_msg = await context.bot.send_message(chat_id=chat_id, text=clean_ctx)
        msg_ids.append(ctx_msg.message_id)
    else:
        poll_question = q_text
        poll_options = list(options)

    # Limit options length and ensure strictly unique and non-empty options
    seen = set()
    unique_poll_options = []
    for idx, opt in enumerate(poll_options):
        opt_str = str(opt).strip()[:95]
        if not opt_str:
            opt_str = f"خيار {idx + 1}"
        new_opt = opt_str
        counter = 1
        while new_opt in seen:
            suffix = f" ({counter})"
            new_opt = opt_str[:95 - len(suffix)] + suffix
            counter += 1
        seen.add(new_opt)
        unique_poll_options.append(new_opt)

    poll_options = unique_poll_options
    if len(poll_options) < 2:
        poll_options = ["(خيار 1)", "(خيار 2)"]
    if len(poll_options) > 10:
        poll_options = poll_options[:10]
    if correct_idx >= len(poll_options) or correct_idx < 0:
        correct_idx = 0

    poll_question_clean = str(poll_question).strip()
    if not poll_question_clean:
        poll_question_clean = "اختر الإجابة الصحيحة:"
    if len(poll_question_clean) > 295:
        poll_question_clean = poll_question_clean[:290] + "..."

    poll_kwargs = {
        "chat_id": chat_id,
        "question": poll_question_clean,
        "options": poll_options,
        "type": "quiz",
        "correct_option_id": correct_idx,
        "is_anonymous": False,
    }
    if explanation:
        poll_kwargs["explanation"] = explanation

    try:
        poll_msg = await context.bot.send_poll(**poll_kwargs)
    except Exception as poll_err:
        logger.warning("send_poll failed with error: %s. Trying sanitized fallback poll...", poll_err)
        fallback_options = [f"الخيار {letters[i]}" if i < len(letters) else f"خيار {i+1}" for i in range(len(poll_options))]
        fallback_question = f"السؤال {session['current_index'] + 1} (اختر الإجابة):"
        poll_msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=fallback_question,
            options=fallback_options,
            type="quiz",
            correct_option_id=correct_idx,
            is_anonymous=False
        )

    msg_ids.append(poll_msg.message_id)

    db.update_session(
        session["current_index"], 
        session["correct_count"], 
        session["wrong_ids"], 
        poll_msg.poll.id,
        msg_ids,
        user_id=user_id,
    )


async def show_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update else None
    user = update.effective_user
    user_id = user.id if user else (context.user_data.get("user_id") or 6099429826)

    try:
        session = db.get_session(user_id=user_id)
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
        # Advance index to skip the corrupted question so user isn't stuck forever
        session = db.get_session(user_id=user_id)
        if session and session["current_index"] < len(session["question_ids"]):
            new_index = session["current_index"] + 1
            db.update_session(
                new_index,
                session["correct_count"],
                session["wrong_ids"],
                None,
                session.get("session_message_ids", []),
                user_id=user_id,
            )
            session["current_index"] = new_index

        err_text = f"⚠️ واجه السؤال مشكلة غير متوقعة في التنسيق وتم تخطيه تلقائياً.\nاضغط 'استكمال الكويز' لمتابعة بقية الأسئلة."
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ استكمال الكويز", callback_data="resume_quiz")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        if query:
            await safe_edit_html(query, err_text, reply_markup=reply_markup, context=context)
        else:
            chat_id = context.user_data.get("chat_id")
            if chat_id:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=err_text, reply_markup=reply_markup)
                except Exception:
                    pass


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user = answer.user
    user_id = user.id if user else None
    selected_options = answer.option_ids

    # Find session matching this user_id or this poll_id
    session = db.get_session(user_id=user_id, poll_id=poll_id)
    if not session or session.get("poll_id") != poll_id:
        return

    sess_user_id = session.get("user_id", user_id or 6099429826)

    if session["current_index"] >= len(session["question_ids"]):
        await finish_session(update, context, session)
        return

    q_id = session["question_ids"][session["current_index"]]
    question = db.get_question(q_id)
    if not question:
        new_index = session["current_index"] + 1
        db.update_session(
            new_index,
            session["correct_count"],
            session["wrong_ids"],
            None,
            session.get("session_message_ids", []),
            user_id=sess_user_id,
        )
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
    db.update_session(
        new_index,
        new_correct,
        new_wrong,
        None,
        session.get("session_message_ids", []),
        user_id=sess_user_id,
    )

    # Delay slightly to let the user see the poll result
    await asyncio.sleep(1.0)
    await show_next_question(update, context)


async def finish_session(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict):
    query = update.callback_query
    user_id = session.get("user_id", 6099429826)
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
                    db.add_or_reset_weak_question(q["quiz_id"], wq_id, user_id=user_id)
            else:
                db.add_or_reset_weak_question(quiz_id, wq_id, user_id=user_id)

    sr_text = ""
    if session_type in ("quiz", "review") and session.get("review_id"):
        db.advance_quiz_review(session["review_id"], user_id=user_id)
        sr_text = "✅ تمت المراجعة وجُدوِّل الموعد التالي تلقائياً."

    elif session_type == "weak":
        weak_all = db.get_due_weak_questions(user_id=user_id)
        quiz_weak = {w["question_id"]: w for w in weak_all if w["quiz_id"] == quiz_id}
        correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
        for qid in correct_ids:
            if qid in quiz_weak:
                db.advance_weak_question(quiz_weak[qid]["id"])
        sr_text = f"✅ {len(correct_ids)} سؤال تم تقدمهم في التكرار المتباعد."

    elif session_type == "weakall":
        all_weak = db.get_all_weak_questions_sorted_for_practice(user_id=user_id)
        all_weak_map = {w["question_id"]: w for w in all_weak}
        correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
        for qid in correct_ids:
            if qid in all_weak_map:
                db.advance_weak_question(all_weak_map[qid]["id"])
        sr_text = f"✅ تم تثبيت إجاباتك وجدولة التكرار اليومي لـ {len(correct_ids)} سؤال (محفوظة دائماً وتتكرر يومياً)."

    elif session_type == "practice":
        sr_text = "🎮 وضع التجربة — لم يتم احتساب هذه الجلسة في المراجعات."

    elif session_type == "weakpractice":
        sr_text = "🛠 تدريب على الأخطاء — هذه الجلسة لم تؤثر على جداول التكرار."

    # Log session for stats (exclude practice modes)
    if session_type not in ("practice", "weakpractice"):
        db.log_session(quiz_id if quiz_id != 0 else None, session_type, total, correct, len(wrong_ids), user_id=user_id)

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

    db.clear_session(user_id=user_id)
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
        session_message_ids = session.get("session_message_ids", [])
        if session_message_ids:
            context.user_data["cleanup_message_ids"] = session_message_ids
    elif query:
        await safe_edit_html(query, result_text, InlineKeyboardMarkup(keyboard), context=context)


async def cleanup_quiz_messages(chat_id, context):
    msg_ids = context.user_data.pop("cleanup_message_ids", [])
    if not msg_ids:
        return
        
    try:
        # delete_messages can take up to 100 ids at a time
        for i in range(0, len(msg_ids), 100):
            chunk = msg_ids[i:i+100]
            await context.bot.delete_messages(chat_id=chat_id, message_ids=chunk)
    except Exception as e:
        logger.warning("Could not delete_messages: %s, falling back to single deletions", e)
        for msg_id in msg_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
