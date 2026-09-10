import os
import io
import html
import base64
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from utils import safe_edit, strip_html_tags, find_correct_option_index

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
            all_weak = db.get_weak_questions_by_quiz(quiz_id, user_id=user_id)
            question_ids = [w["question_id"] for w in all_weak]
        if not question_ids:
            await safe_edit_html(
                query,
                "✅ لا توجد أسئلة ضعيفة مسجلة لهذا الكويز في حسابك!",
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

    if session_type == "review" and not review_id and quiz_id:
        with db.get_connection() as conn:
            r = conn.execute("SELECT id FROM quiz_reviews WHERE quiz_id = ? AND user_id = ?", (quiz_id, user_id)).fetchone()
        if r:
            review_id = r["id"]

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
    
    # Determine correct index with universal multi-tier matcher
    correct_str = str(question.get("correct_answer", "")).strip()
    correct_idx = find_correct_option_index(options, correct_str)

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
    elif update and getattr(update, "callback_query", None) and update.callback_query.message:
        chat_id = update.callback_query.message.chat.id
    elif context.user_data.get("chat_id"):
        chat_id = context.user_data["chat_id"]
    elif user_id:
        chat_id = user_id
            
    if not chat_id:
        chat_id = user_id
    context.user_data["chat_id"] = chat_id
    msg_ids = session.get("session_message_ids", [])

    passage_text = None
    clean_q_prompt = q_text

    # 1. Robust Passage and Question Separation
    if "📄" in q_text and ("\n\n❓" in q_text or "\n❓" in q_text or "❓" in q_text):
        if "\n\n❓" in q_text:
            parts = q_text.split("\n\n❓", 1)
        elif "\n❓" in q_text:
            parts = q_text.split("\n❓", 1)
        else:
            parts = q_text.rsplit("❓", 1)
        passage_text = parts[0].replace("📄", "").strip()
        clean_q_prompt = parts[1].strip()
    elif "\n\n" in q_text:
        parts = q_text.split("\n\n", 1)
        first_block = parts[0].strip()
        second_block = parts[1].strip()
        if len(first_block) > 40 and (len(second_block) > 0 or any(w in first_block for w in ["النص", "القطعة", "الجملة", "الفقرة", "المقال", "القصة"])):
            passage_text = first_block.replace("📄", "").strip()
            clean_q_prompt = second_block.lstrip("❓: \n\t").strip()

    # Clean any duplicated passage text inside clean_q_prompt
    if passage_text:
        if passage_text in clean_q_prompt:
            clean_q_prompt = clean_q_prompt.replace(passage_text, "").strip()
        elif len(passage_text) > 25 and passage_text[:25] in clean_q_prompt:
            idx = clean_q_prompt.find(passage_text[:25])
            if idx != -1:
                clean_q_prompt = clean_q_prompt[idx + len(passage_text):].strip()
        clean_q_prompt = clean_q_prompt.lstrip("❓: \n\t").strip()

    # ── Reading Passage Deduplication & Display (Image or Text) ──
    passage_image = question.get("passage_image")
    last_passage = context.user_data.get(f"active_passage_{chat_id}")

    has_photo = False
    photo_payload = None

    if passage_image:
        if os.path.exists(passage_image):
            has_photo = True
            photo_payload = passage_image
        elif isinstance(passage_image, str) and (len(passage_image) > 50 or "base64" in passage_image):
            try:
                raw_b64 = passage_image
                if "," in raw_b64 and ("data:image" in raw_b64[:35] or "base64" in raw_b64[:35]):
                    raw_b64 = raw_b64.split(",", 1)[1]
                img_bytes = base64.b64decode(raw_b64)
                if len(img_bytes) > 50:
                    has_photo = True
                    photo_payload = io.BytesIO(img_bytes)
                    photo_payload.name = "passage.png"
            except Exception as b64_err:
                logger.warning("Could not decode base64 passage image: %s", b64_err)

    if has_photo:
        passage_key = passage_image if len(str(passage_image)) < 200 else str(hash(passage_image))
        if last_passage != passage_key:
            try:
                if isinstance(photo_payload, str):
                    with open(photo_payload, "rb") as p_file:
                        p_msg = await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=p_file,
                            caption="📄 <b>[قطعة القراءة]</b>\n<i>اقرأ القطعة في الصورة أعلاه ثم أجب عن السؤال التالي ⬇️</i>",
                            parse_mode="HTML"
                        )
                else:
                    photo_payload.seek(0)
                    p_msg = await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_payload,
                        caption="📄 <b>[قطعة القراءة]</b>\n<i>اقرأ القطعة في الصورة أعلاه ثم أجب عن السؤال التالي ⬇️</i>",
                        parse_mode="HTML"
                    )
                msg_ids.append(p_msg.message_id)
                db.track_chat_message(chat_id, p_msg.message_id)
                context.user_data[f"active_passage_{chat_id}"] = passage_key
            except Exception as pe:
                logger.error("Could not send passage photo: %s", pe)
            await asyncio.sleep(0.4)
        else:
            if not clean_q_prompt.startswith("📖 (تابع"):
                clean_q_prompt = "📖 (تابع لقطعة القراءة في الصورة أعلاه ☝️)\n" + clean_q_prompt
    elif passage_text:
        safe_passage = passage_text
        if len(safe_passage) > 3800:
            safe_passage = safe_passage[:3750] + "\n\n...(تم اختصار النص لطوله)"

        if last_passage != passage_text:
            passage_msg_text = (
                f"📄 <b>[نص / قطعة القراءة]:</b>\n\n"
                f"<blockquote>{html.escape(safe_passage)}</blockquote>"
            )
            try:
                p_msg = await context.bot.send_message(chat_id=chat_id, text=passage_msg_text, parse_mode="HTML")
                msg_ids.append(p_msg.message_id)
                db.track_chat_message(chat_id, p_msg.message_id)
                context.user_data[f"active_passage_{chat_id}"] = passage_text
            except Exception as e:
                logger.warning("Failed sending passage with HTML blockquote: %s", e)
                try:
                    clean_p = f"📄 [نص / قطعة القراءة]:\n\n{safe_passage}"
                    p_msg = await context.bot.send_message(chat_id=chat_id, text=clean_p)
                    msg_ids.append(p_msg.message_id)
                    db.track_chat_message(chat_id, p_msg.message_id)
                    context.user_data[f"active_passage_{chat_id}"] = passage_text
                except Exception as e2:
                    logger.error("Could not send reading passage: %s", e2)
            
            # Rate-limit safety: small delay after sending passage before poll
            await asyncio.sleep(0.4)
        else:
            # Same passage as previous question: do not duplicate text, add clean badge
            if not clean_q_prompt.startswith("📖 (تابع"):
                clean_q_prompt = "📖 (تابع لقطعة القراءة أعلاه)\n" + clean_q_prompt
    else:
        context.user_data.pop(f"active_passage_{chat_id}", None)

    # ── Question Diagram / Image Display (e.g. Geometry figures, graphs) ──
    q_img_path = question.get("image") or question.get("question_image")
    if q_img_path:
        try:
            if os.path.exists(q_img_path):
                with open(q_img_path, "rb") as qf:
                    q_msg = await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=qf,
                        caption=f"🖼️ <b>[صورة السؤال {session['current_index'] + 1}]</b>",
                        parse_mode="HTML"
                    )
                    msg_ids.append(q_msg.message_id)
                    db.track_chat_message(chat_id, q_msg.message_id)
            elif isinstance(q_img_path, str) and q_img_path.startswith("http"):
                q_msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=q_img_path,
                    caption=f"🖼️ <b>[صورة السؤال {session['current_index'] + 1}]</b>",
                    parse_mode="HTML"
                )
                msg_ids.append(q_msg.message_id)
                db.track_chat_message(chat_id, q_msg.message_id)
        except Exception as qie:
            logger.warning("Could not send question diagram: %s", qie)
        await asyncio.sleep(0.3)

    # Check Telegram Poll limits (Question max 300, Option max 100)
    long_question = len(clean_q_prompt) > 250
    long_options = any(len(opt) > 90 for opt in options)

    poll_options = []
    letters = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]

    if long_question or long_options:
        context_text = f"📝 <b>السؤال {session['current_index'] + 1} من {len(session['question_ids'])}</b>\n\n"
        context_text += f"<b>{html.escape(clean_q_prompt)}</b>\n"

        if long_options:
            for idx, opt in enumerate(options):
                letter = letters[idx] if idx < len(letters) else str(idx+1)
                context_text += f"\n<b>{letter})</b> {html.escape(opt)}"
                poll_options.append(f"الخيار ({letter})")
            poll_question = f"السؤال {session['current_index'] + 1} (اختر الإجابة من الخيارات أعلاه):"
        else:
            poll_question = f"السؤال {session['current_index'] + 1} (نص السؤال في الرسالة أعلاه):"
            poll_options = list(options)

        if len(context_text) > 3800:
            context_text = context_text[:3750] + "\n\n...(تم اختصار النص لطوله)"

        try:
            ctx_msg = await context.bot.send_message(chat_id=chat_id, text=context_text, parse_mode="HTML")
            msg_ids.append(ctx_msg.message_id)
            db.track_chat_message(chat_id, ctx_msg.message_id)
        except Exception as e:
            logger.warning("Failed sending context message with HTML, falling back to plain text: %s", e)
            clean_ctx = strip_html_tags(context_text)
            try:
                ctx_msg = await context.bot.send_message(chat_id=chat_id, text=clean_ctx)
                msg_ids.append(ctx_msg.message_id)
                db.track_chat_message(chat_id, ctx_msg.message_id)
            except Exception as e2:
                logger.error("Could not send context message: %s", e2)
        
        # Rate-limit safety: small delay after context message before poll
        await asyncio.sleep(0.3)
    else:
        poll_question = clean_q_prompt
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
    
    correct_idx = max(0, min(correct_idx, len(poll_options) - 1))

    poll_question_clean = str(poll_question).strip()
    if not poll_question_clean:
        poll_question_clean = "اختر الإجابة الصحيحة:"
    if len(poll_question_clean) > 280:
        poll_question_clean = poll_question_clean[:275] + "..."

    poll_kwargs = {
        "chat_id": chat_id,
        "question": poll_question_clean,
        "options": poll_options,
        "type": "quiz",
        "correct_option_id": correct_idx,
        "is_anonymous": False,
    }
    if explanation:
        clean_exp = strip_html_tags(str(explanation).strip())[:190]
        if clean_exp:
            poll_kwargs["explanation"] = clean_exp

    poll_msg = None
    try:
        poll_msg = await context.bot.send_poll(**poll_kwargs)
    except Exception as poll_err:
        logger.warning("send_poll with explanation failed: %s. Retrying without explanation...", poll_err)
        poll_kwargs.pop("explanation", None)
        try:
            poll_msg = await context.bot.send_poll(**poll_kwargs)
        except Exception as poll_err2:
            logger.warning("send_poll failed again: %s. Trying clean fallback poll...", poll_err2)
            fallback_options = [f"الخيار ({letters[i]})" if i < len(letters) else f"خيار {i+1}" for i in range(len(poll_options))]
            if len(fallback_options) < 2:
                fallback_options = ["الخيار (أ)", "الخيار (ب)"]
            fb_correct = max(0, min(correct_idx, len(fallback_options) - 1))
            fallback_question = f"السؤال {session['current_index'] + 1} (اختر الإجابة):"
            try:
                poll_msg = await context.bot.send_poll(
                    chat_id=chat_id,
                    question=fallback_question,
                    options=fallback_options,
                    type="quiz",
                    correct_option_id=fb_correct,
                    is_anonymous=False
                )
            except Exception as poll_err3:
                logger.error("All send_poll attempts failed: %s", poll_err3)

    # If all poll sending attempts failed, advance safely so session NEVER hangs
    if not poll_msg:
        logger.error("Could not send poll for question %s, advancing safely...", q_id)
        new_index = session["current_index"] + 1
        db.update_session(
            new_index,
            session["correct_count"],
            session["wrong_ids"],
            None,
            msg_ids,
            user_id=user_id,
        )
        session["current_index"] = new_index
        if session["current_index"] >= len(session["question_ids"]):
            await finish_session(update, context, session)
        else:
            await send_next_question(update, context, session)
        return

    msg_ids.append(poll_msg.message_id)
    db.track_chat_message(chat_id, poll_msg.message_id)

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

        err_text = "⚠️ واجه السؤال مشكلة غير متوقعة في التنسيق وتم تخطيه تلقائياً.\nاضغط 'استكمال الكويز' لمتابعة بقية الأسئلة."
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ استكمال الكويز", callback_data="resume_quiz")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        if query:
            await safe_edit_html(query, err_text, reply_markup=reply_markup, context=context)
        else:
            chat_id = context.user_data.get("chat_id") or user_id
            if chat_id:
                try:
                    err_msg = await context.bot.send_message(chat_id=chat_id, text=err_text, reply_markup=reply_markup)
                    if err_msg:
                        db.track_chat_message(chat_id, err_msg.message_id)
                except Exception:
                    pass


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if not answer:
        return
    poll_id = answer.poll_id
    user = answer.user
    user_id = user.id if user else None
    selected_options = answer.option_ids

    # Find session matching this user_id or this poll_id
    session = db.get_session(user_id=user_id, poll_id=poll_id)
    if not session:
        session = db.get_session(poll_id=poll_id) or db.get_session(user_id=user_id)
    if not session:
        logger.warning("No active session found for user_id=%s or poll_id=%s", user_id, poll_id)
        return

    sess_user_id = session.get("user_id", user_id or 6099429826)
    context.user_data["user_id"] = sess_user_id
    context.user_data["chat_id"] = sess_user_id

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
    correct_idx = find_correct_option_index(options, correct_str)

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

    # Always record mistakes into weak questions
    for wq_id in wrong_ids:
        if session_type == "weakall":
            q = db.get_question(wq_id)
            if q:
                db.add_or_reset_weak_question(q["quiz_id"], wq_id, user_id=user_id)
        elif quiz_id:
            db.add_or_reset_weak_question(quiz_id, wq_id, user_id=user_id)

    sr_text = ""
    # Check spaced repetition based on session_type
    if session_type == "review":
        # Explicit scheduled review session
        advanced = False
        review_id = session.get("review_id")
        if review_id:
            db.advance_quiz_review(review_id, user_id=user_id)
            advanced = True
        elif quiz_id:
            advanced = db.advance_quiz_review_for_quiz(quiz_id, user_id=user_id)

        if advanced:
            sr_text = "✅ تم تسجيل إتمام المراجعة وتقدم الكويز للمرحلة التالية في التكرار المتباعد 🧠!"
        else:
            sr_text = "✅ تم إنجاز مراجعة الكويز بنجاح!"

        # Resolve weak questions answered correctly
        if quiz_id:
            user_weak_list = db.get_weak_questions_by_quiz(quiz_id, user_id=user_id)
            if user_weak_list:
                user_weak_map = {w["question_id"]: w for w in user_weak_list}
                correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
                resolved_count = 0
                for qid in correct_ids:
                    if qid in user_weak_map:
                        db.advance_weak_question(user_weak_map[qid]["id"])
                        resolved_count += 1
                if resolved_count > 0:
                    sr_text += f"\n🎯 تم ترقية وإتقان {resolved_count} سؤال ضعيف في هذا الكويز!"

    elif session_type == "quiz":
        # Initial solve or practice via "ابدأ الكويز"
        existing_rev = db.get_quiz_review(quiz_id, user_id=user_id)
        if not existing_rev:
            # First solve / initial study: schedule review 1 for tomorrow at Stage 0
            db.schedule_first_review(quiz_id, user_id=user_id, start_today=False)
            sr_text = "📅 <b>تم إدراج الكويز في جدول التكرار المتباعد!</b>\nستصلك أول مراجعة (المرحلة 1) غداً لترسيخ المعلومات 🧠."
        else:
            from spaced_repetition import days_until
            due_d = days_until(existing_rev.get("next_review_date", ""))
            if due_d <= 0:
                # Review was due today, count solve as advancing the review
                db.advance_quiz_review(existing_rev["id"], user_id=user_id)
                sr_text = "✅ تم إنجاز المراجعة المستحقة بنجاح وتقدم الكويز للمرحلة التالية 🧠!"
            else:
                # Review not due yet, this is free practice
                sr_text = f"✨ تم تسجيل نتيجتك وتحديث الأخطاء (تمرين إضافي - موعد مراجعتك القادمة بعد {due_d} يوم)."

        # When solving a full quiz: also advance/resolve any weak questions that were answered correctly
        if quiz_id:
            user_weak_list = db.get_weak_questions_by_quiz(quiz_id, user_id=user_id)
            if user_weak_list:
                user_weak_map = {w["question_id"]: w for w in user_weak_list}
                correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
                resolved_count = 0
                for qid in correct_ids:
                    if qid in user_weak_map:
                        db.advance_weak_question(user_weak_map[qid]["id"])
                        resolved_count += 1
                if resolved_count > 0:
                    sr_text += f"\n🎯 تم ترقية وإتقان {resolved_count} سؤال ضعيف في هذا الكويز!"

    elif session_type == "practice":
        sr_text = "🎮 تم تسجيل جلسة التمرين بنجاح!"

    elif session_type == "weak":
        weak_all = db.get_due_weak_questions(user_id=user_id)
        quiz_weak = {w["question_id"]: w for w in weak_all if w["quiz_id"] == quiz_id}
        correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
        for qid in correct_ids:
            if qid in quiz_weak:
                db.advance_weak_question(quiz_weak[qid]["id"])
        sr_text = f"✅ تم تثبيت إجاباتك وتقدم {len(correct_ids)} سؤال في التكرار المتباعد."

    elif session_type == "weakall":
        all_weak = db.get_all_weak_questions_sorted_for_practice(user_id=user_id)
        all_weak_map = {w["question_id"]: w for w in all_weak}
        correct_ids = [qid for qid in session["question_ids"] if qid not in wrong_ids]
        for qid in correct_ids:
            if qid in all_weak_map:
                db.advance_weak_question(all_weak_map[qid]["id"])
        sr_text = f"✅ تم تثبيت إجاباتك وجدولة التكرار لـ {len(correct_ids)} سؤال."

    # ── Weak Quiz Mastery Tracking (5 consecutive 100% runs) ──
    if quiz_id and session_type not in ("weakall", "weak", "weakpractice"):
        is_perfect = (len(wrong_ids) == 0 and total > 0)
        streak, is_mastered = db.record_quiz_mastery_run(quiz_id, user_id=user_id, is_perfect=is_perfect)
        if is_mastered:
            sr_text += "\n🏆 <b>إنجاز استثنائي!</b> حققت الدرجة الكاملة 5 مرات متتالية، تم إتقان هذا الكويز وإخراجه من قائمة الأخطاء بنجاح! 🌟"
        elif is_perfect and streak > 0:
            sr_text += f"\n🔥 <b>إتقان الكويز:</b> {streak}/5 مرات متتالية بالدرجة الكاملة."

    # Log session for stats
    db.log_session(quiz_id if quiz_id != 0 else None, session_type, total, correct, len(wrong_ids), user_id=user_id)

    result_text = (
        f"🎉 <b>انتهى الكويز!</b>\n\n"
        f"{rating}\n\n"
        f"📊 <b>النتيجة:</b> {correct}/{total} ({score}%)\n"
        f"✅ صح: {correct} | ❌ خطأ: {len(wrong_ids)}\n\n"
        f"{sr_text}"
    )

    keyboard = []
    if wrong_ids and quiz_id != 0:
        keyboard.append([
            InlineKeyboardButton(f"❌ راجع الأسئلة الخاطئة ({len(wrong_ids)})", callback_data=f"start_weak_{quiz_id}")
        ])

    # If quiz is not in review schedule, allow adding it
    if quiz_id and session_type not in ("weakall", "weak", "weakpractice"):
        with db.get_connection() as conn:
            has_rev = conn.execute("SELECT 1 FROM quiz_reviews WHERE quiz_id = ? AND user_id = ?", (quiz_id, user_id)).fetchone()
        if not has_rev:
            keyboard.append([
                InlineKeyboardButton("🔁 أضف لجدول مراجعاتي", callback_data=f"add_to_schedule_{quiz_id}")
            ])

    if is_admin(user_id) and session_type != "weakall" and quiz_id != 0:
        keyboard.append([
            InlineKeyboardButton("🛠 تعديل أسئلة الكويز", callback_data=f"fixstage_qlist_{quiz_id}_0")
        ])

    if quiz_id != 0:
        keyboard.append([
            InlineKeyboardButton("📋 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_id}"),
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")])

    db.clear_session(user_id=user_id)
    chat_id = None
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        chat_id = update.effective_chat.id
    elif context.user_data.get("chat_id"):
        chat_id = context.user_data["chat_id"]

    if chat_id:
        context.user_data.pop(f"active_passage_{chat_id}", None)
        
    res_msg = None
    if chat_id:
        try:
            res_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=result_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("finish_session HTML send failed: %s, falling back to plain text", e)
            clean_res = strip_html_tags(result_text)
            try:
                res_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=clean_res,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e2:
                logger.error("finish_session send failed: %s", e2)

        session_message_ids = list(session.get("session_message_ids", []))
        if res_msg:
            session_message_ids.append(res_msg.message_id)
            db.track_chat_message(chat_id, res_msg.message_id)
            db.set_last_message_id(chat_id, res_msg.message_id)

        if session_message_ids:
            clean_ids = list(dict.fromkeys(session_message_ids))
            context.user_data["cleanup_message_ids"] = clean_ids
            for mid in clean_ids:
                db.track_chat_message(chat_id, mid)
    elif query:
        await safe_edit_html(query, result_text, InlineKeyboardMarkup(keyboard), context=context)


async def cleanup_quiz_messages(chat_id, context):
    msg_ids = context.user_data.pop("cleanup_message_ids", [])
    user_id = context.user_data.get("user_id")
    if user_id:
        try:
            active_sess = db.get_session(user_id=user_id)
            if active_sess:
                s_ids = active_sess.get("session_message_ids", [])
                if s_ids:
                    msg_ids.extend(s_ids)
        except Exception:
            pass
    if not msg_ids or not chat_id:
        return
    from utils import delete_messages_bulk
    await delete_messages_bulk(context, chat_id, msg_ids)
