import html
import asyncio
import logging
from datetime import date, timedelta, datetime
import pytz

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, KeyboardButtonPollType, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def get_poll_keyboard() -> ReplyKeyboardMarkup:
    """Returns the native Telegram quiz creation keyboard."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📝 إنشاء سؤال", request_poll=KeyboardButtonPollType(type="quiz"))]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def build_create_upload_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Generates the main 'Create & Upload Quiz' menu with all options."""
    text = (
        "➕ <b>إنشاء ورفع كويز / مادة تدريبية</b> 🧠\n\n"
        "اختر الطريقة التي تفضلها لإضافة كويز أو مادة للمراجعة في التكرار المتباعد:\n\n"
        "🎲 <b>1- كويزات @QuizBot (الأسرع بنقرة واحدة):</b>\n"
        "حوّل بطاقة أي كويز من @QuizBot أو أرسل رابطه وسيتعرف عليه البوت فوراً ويجدوله في التكرار المتباعد!\n\n"
        "🎯 <b>2- تحويل أسئلة تيليجرام (دقة 100%):</b>\n"
        "حوّل أي أسئلة كويز من أي قناة وسيحفظها البوت بأجوبتها الرسمية وشروحاتها للحل الداخلي!\n\n"
        "✍️ <b>3- إنشاء كويز يدوياً:</b> كتابة الأسئلة والخيارات مباشرة ✍️\n"
        "📊 <b>4- استيراد ملف (Excel / CSV):</b> عبر القالب المعتمد 📊\n"
        "🔗 <b>5- إضافة كويز كرابط:</b> إدراج أي رابط كويز خارجي 🔗"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 1- كويزات @QuizBot (شرح الطريقة)", callback_data="explain_quizbot")],
        [InlineKeyboardButton("🎯 2- تحويل أسئلة تيليجرام (شرح الطريقة)", callback_data="explain_poll_forward")],
        [InlineKeyboardButton("✍️ 3- إنشاء كويز يدوياً", callback_data="create_manual_quiz")],
        [InlineKeyboardButton("📊 4- رفع ملف Excel / CSV", callback_data="upload_excel")],
        [InlineKeyboardButton("🔗 5- إضافة كويز كرابط", callback_data="upload_url")],
        [InlineKeyboardButton("📁 إدارة المجلدات", callback_data="admin_cat_0")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")],
    ])
    return text, kb



# ─── 1. Manual Quiz Creation via Native Telegram Poll ─────────────────────────

def build_manual_quiz_dashboard(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    manual_quiz = context.user_data.get("manual_quiz", {})
    name = manual_quiz.get("name", "كويز تيليجرام")
    questions = manual_quiz.get("questions", [])
    total_q = len(questions)

    text = (
        f"🎯 <b>كويزات تيليجرام</b>\n\n"
        f"📌 اسم الكويز: <b>{html.escape(name)}</b>\n"
        f"📝 عدد الأسئلة المضافة: <b>{total_q}</b> سؤال\n\n"
    )
    if questions:
        text += "<b>آخر الأسئلة المضافة:</b>\n"
        start_preview = max(0, total_q - 4)
        for i in range(start_preview, total_q):
            q = questions[i]
            q_prev = q["question"][:45] + ("..." if len(q["question"]) > 45 else "")
            ans_prev = q.get("answer", "")
            ans_mark = f"✅ {html.escape(ans_prev)}" if ans_prev else "⚠️ لم تُحدد إجابة"
            text += f"{i+1}. {html.escape(q_prev)} ({ans_mark})\n"
        if total_q > 4:
            text += f"<i>...و {total_q - 4} أسئلة سابقة أخرى.</i>\n"
        text += "\n"

    text += (
        "💡 <b>الخيارات المتاحة:</b>\n"
        "• يمكنك تحويل المزيد من الأسئلة الآن من أي قناة وسيتم ضمها تلقائياً.\n"
        "• أو اختر من الأزرار بالأسفل للحفظ أو بدء الحل فوراً:"
    )

    buttons = []
    if questions:
        buttons.append([InlineKeyboardButton(f"▶️ حفظ وبدء الحل الآن ({total_q} سؤال)", callback_data="manual_save_and_start")])
        buttons.append([
            InlineKeyboardButton("💾 حفظ فقط", callback_data="manual_save_quiz"),
            InlineKeyboardButton("✏️ تغيير اسم الكويز", callback_data="manual_rename_quiz")
        ])
    buttons.append([InlineKeyboardButton("❌ إلغاء وتفريغ", callback_data="manual_cancel")])

    return text, InlineKeyboardMarkup(buttons)


async def handle_manual_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "explain_quizbot":
        text = (
            "🎲 <b>طريقة إضافة كويزات @QuizBot بنقرة واحدة</b> ⚡\n\n"
            "يمكنك إضافة أي كويز من بوت تيليجرام الشهير @QuizBot في ثانية واحدة وبدون أي تعقيد:\n\n"
            "1️⃣ <b>افتح أي قناة أو محادثة</b> فيها كويز من @QuizBot (الرسالة التي تحتوي على زر <i>Start this quiz</i>).\n"
            "2️⃣ <b>اضغط تحويل (Forward)</b> للرسالة وأرسلها مباشرة لهذا البوت.\n"
            "3️⃣ <i>أو انسخ رابط الكويز</i> (مثال: <code>https://t.me/QuizBot?start=...</code>) والصقه هنا.\n\n"
            "🎉 <b>سيتعرف البوت فوراً على:</b>\n"
            "• اسم الكويز\n"
            "• عدد الأسئلة والوقت\n"
            "• ويجدوله تلقائياً في التكرار المتباعد لتصلك مراجعته غداً ثم بعد 3، 7، 14، 30 يوماً!\n\n"
            "👇 <b>جرب الآن:</b> حوّل أي كويز من @QuizBot إلى هنا مباشرة!"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع لقائمة الإضافة", callback_data="create_upload_menu")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return

    elif data == "explain_poll_forward":
        text = (
            "🎯 <b>طريقة تحويل كويزات تيليجرام بدقة 100% وبدون AI</b>\n\n"
            "هذه أسرع طريقة لمذاكرة القدرات بدون أي تعقيد وبدون أي إضافات:\n\n"
            "1️⃣ <b>افتح أي قناة أو قروب قدرات</b> يحتوي على كويزات (Telegram Polls).\n"
            "2️⃣ <b>حدد سؤالاً أو مجموعة أسئلة دفعة واحدة</b> (حتى 50 سؤالاً).\n"
            "3️⃣ اضغط على <b>تحويل (Forward)</b> وأرسلها مباشرة إلى هذا البوت.\n"
            "4️⃣ سيتعرف البوت عليها تلقائياً ويسحب:\n"
            "   • نص السؤال كاملاً\n"
            "   • الخيارات الأربعة\n"
            "   • الإجابة النموذجية الصحيحة المبرمجة رسمياً\n"
            "   • الشرح وتوضيح الحل إن وجد\n"
            "5️⃣ اضغط فوراً على <b>[▶️ حفظ وبدء الحل الآن]</b> لتتدرب عليها ويدخلها البوت في جدول مراجعاتك الذكية!\n\n"
            "👇 <b>جرب الآن:</b> حوّل أي سؤال كويز من أي قناة إلى هنا مباشرة!"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع لقائمة الإضافة", callback_data="create_upload_menu")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return

    elif data == "create_manual_quiz":
        context.user_data["manual_state"] = "awaiting_quiz_name"
        context.user_data["manual_quiz"] = {"name": "", "questions": []}
        text = (
            "✍️ <b>إنشاء كويز يدوياً — الخطوة 1/2</b>\n\n"
            "أرسل الآن <b>اسم أو عنوان الكويز الجديد</b> في رسالة نصية:\n"
            "<i>(مثال: كويز قوانين السرعة والمسافة 1)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="manual_cancel")]])
        await safe_edit(query, text, kb)

    elif data == "manual_cancel":
        context.user_data.pop("manual_quiz", None)
        context.user_data.pop("manual_state", None)
        text, kb = build_create_upload_menu()
        await safe_edit(query, text, kb)

    elif data == "manual_rename_quiz":
        context.user_data["manual_state"] = "awaiting_quiz_name"
        text = (
            "✏️ <b>تغيير اسم الكويز</b>\n\n"
            "أرسل الآن الاسم الجديد للكويز في رسالة نصية:\n"
            "<i>(مثال: كويز التناظر اللفظي - نموذج 1446)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manual_dashboard")]])
        await safe_edit(query, text, kb)

    elif data == "manual_save_and_start":
        manual_quiz = context.user_data.pop("manual_quiz", None)
        context.user_data.pop("manual_state", None)
        chat_id = update.effective_chat.id

        try:
            rm_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="⏳ جاري حفظ الكويز وبدء الجلسة...",
                reply_markup=ReplyKeyboardRemove()
            )
            asyncio.create_task(rm_msg.delete())
        except Exception:
            pass

        if not manual_quiz or not manual_quiz.get("questions"):
            await query.answer("⚠️ لا توجد أسئلة لحفظها.", show_alert=True)
            return

        user = update.effective_user
        u_id = user.id if user else (update.effective_chat.id if update.effective_chat else None)
        if not u_id:
            await query.answer("❌ تعذر التعرف على المستخدم.", show_alert=True)
            return
        name = manual_quiz.get("name", "كويز تيليجرام")
        questions = manual_quiz.get("questions", [])

        is_public_val = 1 if is_admin(u_id) else 0
        quiz_id = db.save_quiz(name, questions, user_id=u_id, is_public=is_public_val)

        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="quiz")

    elif data == "manual_save_quiz":
        manual_quiz = context.user_data.pop("manual_quiz", None)
        context.user_data.pop("manual_state", None)
        chat_id = update.effective_chat.id
        
        try:
            rm_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="⏳ جاري حفظ الكويز...",
                reply_markup=ReplyKeyboardRemove()
            )
            asyncio.create_task(rm_msg.delete())
        except Exception:
            pass

        if not manual_quiz or not manual_quiz.get("questions"):
            await query.answer("⚠️ لا توجد أسئلة لحفظها.", show_alert=True)
            return

        user = update.effective_user
        u_id = user.id if user else (update.effective_chat.id if update.effective_chat else None)
        if not u_id:
            await query.answer("❌ تعذر التعرف على المستخدم.", show_alert=True)
            return
        name = manual_quiz.get("name", "كويز بدون اسم")
        questions = manual_quiz.get("questions", [])

        # Admin-created quizzes are public; user-created are private
        is_public_val = 1 if is_admin(u_id) else 0
        quiz_id = db.save_quiz(name, questions, user_id=u_id, is_public=is_public_val)

        categories = db.get_categories(is_public=1)
        folder_prompt = "\n\n📁 <b>اختر المجلد الذي ترغب بإضافة الكويز إليه:</b>" if categories else ""
        text = (
            f"🎉 <b>تم إنشاء الكويز وحفظه بنجاح!</b>\n\n"
            f"📋 <b>{html.escape(name)}</b>\n"
            f"📝 عدد الأسئلة: <b>{len(questions)}</b> سؤال"
            f"{folder_prompt}"
        )
        kb_rows = []
        if categories:
            cat_row = []
            for c in categories:
                icon = c.get("icon", "📁")
                cat_row.append(InlineKeyboardButton(f"{icon} {c['name']}", callback_data=f"set_quiz_cat_{quiz_id}_{c['id']}"))
                if len(cat_row) == 2:
                    kb_rows.append(cat_row)
                    cat_row = []
            if cat_row:
                kb_rows.append(cat_row)
        kb_rows.append([
            InlineKeyboardButton("▶️ ابدأ حل الكويز الآن", callback_data=f"start_quiz_{quiz_id}"),
            InlineKeyboardButton("📁 نقل إلى مجلد", callback_data=f"move_quiz_{quiz_id}")
        ])
        kb_rows.append([
            InlineKeyboardButton("📚 تصفح الكويزات", callback_data="browse_root"),
            InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")
        ])
        kb = InlineKeyboardMarkup(kb_rows)
        await safe_edit(query, text, kb)
    elif data.startswith("manual_set_correct_"):
        correct_idx = int(data.split("_")[-1])
        current_q = context.user_data.pop("current_q", {})
        options = current_q.get("options", [])
        if 0 <= correct_idx < len(options):
            current_q["answer"] = options[correct_idx]
            manual_quiz = context.user_data.setdefault("manual_quiz", {"name": "كويز تيليجرام", "questions": []})
            manual_quiz.setdefault("questions", []).append(current_q)
            context.user_data["manual_state"] = "awaiting_poll_questions"
            text, kb = build_manual_quiz_dashboard(context)
            ack_text = f"✅ <b>تمت إضافة السؤال رقم {len(manual_quiz['questions'])} بنجاح!</b> 🎯\n\n" + text
            await safe_edit(query, ack_text, kb)
        else:
            await query.answer("⚠️ خيار غير صالح.", show_alert=True)

    elif data == "manual_dashboard":
        context.user_data.pop("current_q", None)
        context.user_data["manual_state"] = "awaiting_poll_questions"
        text, kb = build_manual_quiz_dashboard(context)
        await safe_edit(query, text, kb)


async def handle_incoming_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles polls/quizzes forwarded or sent by the user with 100% precision and zero AI."""
    poll = update.message.poll if update.message else None
    if not poll:
        return False

    chat_id = update.effective_chat.id

    # Track & delete user's forwarded poll message from chat to keep it clean
    if update.message:
        db.track_chat_message(chat_id, update.message.message_id)
        asyncio.create_task(update.message.delete())

    # Detect channel or bot title if forwarded
    channel_title = None
    fwd_chat = getattr(update.message, "forward_from_chat", None)
    fwd_user = getattr(update.message, "forward_from", None)
    via_bot = getattr(update.message, "via_bot", None)

    if fwd_chat and hasattr(fwd_chat, "title") and fwd_chat.title:
        channel_title = fwd_chat.title
    elif fwd_user and getattr(fwd_user, "username", "").lower() == "quizbot":
        channel_title = "QuizBot"
    elif fwd_user and getattr(fwd_user, "first_name", None):
        channel_title = fwd_user.first_name
    elif via_bot and getattr(via_bot, "username", "").lower() == "quizbot":
        channel_title = "QuizBot"
    elif getattr(update.message, "forward_sender_name", None):
        channel_title = update.message.forward_sender_name

    manual_quiz = context.user_data.get("manual_quiz")
    if manual_quiz is None:
        default_name = f"كويز: {channel_title}" if channel_title else "كويز تيليجرام"
        context.user_data["manual_quiz"] = {"name": default_name, "questions": []}
        manual_quiz = context.user_data["manual_quiz"]
    elif channel_title and manual_quiz.get("name") in ("كويز مخصص", "كويز تيليجرام", "كويز جديد"):
        manual_quiz["name"] = f"كويز: {channel_title}"

    q_text = poll.question
    options = [opt.text for opt in poll.options]
    correct_idx = poll.correct_option_id
    explanation = getattr(poll, "explanation", "") or ""

    # If it's a regular poll (not quiz type) without predefined correct answer
    if correct_idx is None or correct_idx < 0 or correct_idx >= len(options):
        current_q = {
            "question": q_text,
            "options": options,
            "answer": "",
            "explanation": explanation
        }
        context.user_data["current_q"] = current_q
        kb_opts = []
        for i, opt in enumerate(options):
            lbl = f"({chr(0x623 + i) if i < 4 else i + 1}) {opt[:25]}"
            kb_opts.append([InlineKeyboardButton(lbl, callback_data=f"manual_set_correct_{i}")])
        kb_opts.append([InlineKeyboardButton("❌ تخطي هذا السؤال", callback_data="manual_dashboard")])
        q_clean = html.escape(q_text)
        prompt_text = (
            f"❓ <b>سؤال استبيان عادي (بدون إجابة مسبقة):</b>\n\n"
            f"<blockquote>{q_clean}</blockquote>\n\n"
            f"👇 <b>اختر الإجابة الصحيحة لهذا السؤال:</b>"
        )
        await send_clean_message(context, chat_id, prompt_text, reply_markup=InlineKeyboardMarkup(kb_opts))
        return True

    # Telegram Quiz with 100% official verified answer
    correct_answer = options[correct_idx]
    question_entry = {
        "question": q_text,
        "options": options,
        "answer": correct_answer,
        "explanation": explanation
    }
    manual_quiz.setdefault("questions", []).append(question_entry)
    context.user_data["manual_state"] = "awaiting_poll_questions"

    # Batch debounce: Wait 0.4s so multiple forwarded polls accumulate smoothly
    batch_token = context.user_data.get("poll_batch_token", 0) + 1
    context.user_data["poll_batch_token"] = batch_token

    await asyncio.sleep(0.4)

    # If a newer poll arrived during sleep, let the latest poll update the UI
    if context.user_data.get("poll_batch_token") != batch_token:
        return True

    text, kb = build_manual_quiz_dashboard(context)
    ack_text = f"🎯 <b>تم استلام وتوثيق الأسئلة بنجاح!</b>\n\n" + text
    await send_clean_message(context, chat_id, ack_text, reply_markup=kb)
    return True


# ─── 4. Media & Notes Review Item ─────────────────────────────────────────────

async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles uploaded photos or documents when user is creating a Spaced Repetition media note."""
    if not context.user_data.get("waiting_for_media_note"):
        return False

    context.user_data.pop("waiting_for_media_note", None)
    user = update.effective_user
    chat_id = update.effective_chat.id
    u_id = user.id if user else chat_id
    if not u_id:
        return False
    msg = update.message

    if msg:
        db.track_chat_message(chat_id, msg.message_id)

    if msg.photo:
        file_id = msg.photo[-1].file_id
        caption = msg.caption or "صورة مراجعة وتلخيص 📸"
        url = f"media:photo:{file_id}"
        name = caption
    elif msg.document:
        file_id = msg.document.file_id
        name = msg.document.file_name or "ملف مراجعة 📄"
        url = f"media:doc:{file_id}"
    else:
        return False

    quiz_id = db.save_quiz_url(name=name, url=url, user_id=u_id, is_public=0)

    text = (
        f"✅ <b>تمت إضافة مادة المراجعة بنجاح!</b> 🧠\n\n"
        f"📌 العنوان: <b>{html.escape(name)}</b>\n\n"
        f"تم إدراجها في نظام <b>التكرار المتباعد</b>.\n"
        f"سيقوم البوت بتذكيرك بمراجعتها في المواعيد الذكية لتثبيتها في الذاكرة 💪."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 جدول مراجعاتي", callback_data="review_schedule")],
        [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
    ])
    await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
    return True


# ─── Text Input Router for Creation ──────────────────────────────────────────

async def handle_creation_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles text input related to manual quiz building, user private folders, or media text notes."""
    if not update.message or not update.message.text:
        return False
    msg = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    u_id = user.id if user else chat_id

    # 1. Media Note Text
    if context.user_data.get("waiting_for_media_note"):
        context.user_data.pop("waiting_for_media_note", None)
        first_line = msg.splitlines()[0][:35]
        name = f"ملاحظة: {first_line}..."
        url = f"media:text:{msg}"
        
        quiz_id = db.save_quiz_url(name=name, url=url, user_id=u_id, is_public=0)
        text = (
            f"✅ <b>تم حفظ الملاحظة في التكرار المتباعد بنجاح!</b> 📝\n\n"
            f"📌 العنوان: <b>{html.escape(name)}</b>\n\n"
            f"سيقوم البوت بتذكيرك بمراجعة هذه الملاحظة دورياً لتثبيت حفظك 🧠."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 جدول مراجعاتي", callback_data="review_schedule")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    # 2. User Private Folder Creation
    if context.user_data.get("waiting_for_user_folder") is not None:
        parent_id = context.user_data.pop("waiting_for_user_folder")
        folder_name = msg.strip()
        cat_id = db.create_category(
            name=folder_name,
            parent_id=parent_id if parent_id != 0 else None,
            owner_id=u_id,
            is_public=0
        )
        text = (
            f"✅ <b>تم إنشاء المجلد الخاص بنجاح!</b>\n\n"
            f"📁 <b>{html.escape(folder_name)}</b>\n\n"
            f"يمكنك الآن نقل كويزاتك الخاصة إليه وتنظيم دراستك 🌟."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 فتح المجلد", callback_data=f"browse_cat_{cat_id}_1")],
            [InlineKeyboardButton("📚 تصفح الكويزات", callback_data="browse_root")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    # 3. Manual Quiz: Awaiting Quiz Name
    state = context.user_data.get("manual_state")
    if state == "awaiting_quiz_name":
        context.user_data.setdefault("manual_quiz", {})["name"] = msg
        context.user_data["manual_state"] = "awaiting_poll_questions"
        text, kb = build_manual_quiz_dashboard(context)
        
        # Send clean message with inline dashboard
        await send_clean_message(context, chat_id, f"✅ <b>تم تحديد اسم الكويز بنجاح!</b>\n\n" + text, update=update, reply_markup=kb)
        
        # Send reply keyboard with native Poll creation button
        poll_kb = get_poll_keyboard()
        poll_prompt = await context.bot.send_message(
            chat_id=chat_id,
            text="👇 <b>اضغط على زر [📝 إنشاء سؤال] بالأسفل لبدء إضافة الأسئلة:</b>",
            reply_markup=poll_kb,
            parse_mode="HTML"
        )
        db.track_chat_message(chat_id, poll_prompt.message_id)
        return True

    # 4. Manual Quiz: Awaiting Question Text
    if state == "awaiting_q_text":
        context.user_data.setdefault("current_q", {})["question"] = msg
        context.user_data["manual_state"] = "awaiting_q_options"
        text = (
            "✍️ <b>إضافة الخيارات — الخطوة 2/3</b>\n\n"
            "أرسل الآن خيارات الإجابة في رسالة واحدة، <b>كل خيار في سطر مستقل</b>:\n\n"
            "<i>مثال:</i>\n"
            "20\n"
            "25\n"
            "30\n"
            "35"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء السؤال", callback_data="manual_dashboard")]])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    # 5. Manual Quiz: Awaiting Options
    if state == "awaiting_q_options":
        lines = [line.strip() for line in msg.splitlines() if line.strip()]
        if len(lines) < 2:
            await send_clean_message(context, chat_id, "⚠️ يرجى إرسال خيارين على الأقل (كل خيار في سطر مستقل). أعد إرسال الخيارات:", update=update)
            return True
        if len(lines) > 6:
            lines = lines[:6]

        context.user_data.setdefault("current_q", {})["options"] = lines
        context.user_data["manual_state"] = "awaiting_correct_selection"

        q_text = context.user_data["current_q"].get("question", "")
        letters = ["أ", "ب", "ج", "د", "هـ", "و"]
        
        opt_preview = "\n".join(f"<b>{letters[i]})</b> {html.escape(opt)}" for i, opt in enumerate(lines))
        text = (
            "✍️ <b>تحديد الإجابة الصحيحة — الخطوة 3/3</b>\n\n"
            f"❓ السؤال: <b>{html.escape(q_text)}</b>\n\n"
            f"<b>الخيارات:</b>\n{opt_preview}\n\n"
            f"👇 <b>اضغط على الزر المطابق للإجابة الصحيحة:</b>"
        )
        btn_row = [
            InlineKeyboardButton(f"{letters[i]})", callback_data=f"manual_set_correct_{i}")
            for i in range(len(lines))
        ]
        kb = InlineKeyboardMarkup([
            btn_row,
            [InlineKeyboardButton("❌ إلغاء السؤال", callback_data="manual_dashboard")]
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    return False
