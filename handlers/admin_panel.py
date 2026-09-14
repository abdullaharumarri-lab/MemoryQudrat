# -*- coding: utf-8 -*-
"""
handlers/admin_panel.py — لوحة تحكم المشرف الموحدة (MemoryQudrat)
حصر الإضافة والإدارة في المشرف فقط بدقة 100% وبدون أي اعتماد على الذكاء الاصطناعي أو الإكسل.
"""

import re
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


# ─── 1. Admin Dashboard View ──────────────────────────────────────────────────

def build_admin_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the main control panel for the bot administrator."""
    users_count = len(db.get_all_users())
    quizzes = db.get_all_quizzes()
    quizzes_count = len(quizzes)
    pending_reports = db.get_pending_reports()
    reports_count = len(pending_reports)
    categories = db.get_categories()

    rep_badge = f" ({reports_count} ⚠️)" if reports_count > 0 else ""

    text = (
        "👑 <b>لوحة تحكم المشرف — ذاكرة القدرات</b> 🧠\n\n"
        "أهلاً بك يا مدير المنصة! يمكنك من هنا إدارة الكويزات، الأقسام، وبلاغات الطلاب بكل سلاسة:\n\n"
        f"📊 <b>إحصائيات عامة سريعة:</b>\n"
        f"• 👥 عدد الطلاب المسجلين: <b>{users_count}</b>\n"
        f"• 📚 عدد الكويزات المنشورة: <b>{quizzes_count}</b>\n"
        f"• 📁 عدد الأقسام المعتمدة: <b>{len(categories)}</b>\n"
        f"• 🚩 بلاغات الأسئلة المعلقة: <b>{reports_count}</b>\n\n"
        "اختر الإجراء الذي تريد القيام به:"
    )

    kb = [
        [InlineKeyboardButton("➕ إضافة كويز جديد", callback_data="admin_create_quiz")],
        [InlineKeyboardButton("📋 إدارة وتعديل الكويزات", callback_data="admin_manage_quizzes_1")],
        [InlineKeyboardButton("📁 إدارة الأقسام والتصنيفات", callback_data="admin_manage_cats")],
        [InlineKeyboardButton(f"🚩 بلاغات الطلاب الواردة{rep_badge}", callback_data="admin_view_reports")],
        [InlineKeyboardButton("📢 إرسال إذاعة جماعية للطلاب", callback_data="admin_broadcast_prompt")],
        [InlineKeyboardButton("🧹 فرمتة وتنظيف قاعدة البيانات", callback_data="admin_confirm_format")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="main_menu")],
    ]
    return text, InlineKeyboardMarkup(kb)


# ─── 2. Interactive Quiz Creator Wizard (Admin Only) ─────────────────────────

def build_draft_dashboard(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the live review dashboard for a quiz currently being built by the Admin."""
    draft = context.user_data.get("admin_quiz_draft", {})
    name = draft.get("name", "كويز جديد")
    cat_id = draft.get("category_id")
    cat_name = "غير محدد"
    if cat_id:
        categories = db.get_categories()
        for c in categories:
            if c["id"] == cat_id:
                cat_name = c["name"]
                break

    questions = draft.get("questions", [])
    q_count = len(questions)

    text = (
        f"🎯 <b>منشئ الكويزات التفاعلي للمشرف</b> ⚡\n\n"
        f"📋 <b>اسم الكويز:</b> {html.escape(name)}\n"
        f"📁 <b>القسم:</b> {html.escape(cat_name)}\n"
        f"📝 <b>عدد الأسئلة المضافة:</b> <b>{q_count} سؤال</b>\n\n"
        f"👇 <b>أضف أسئلتك الآن بأي من هاتين الطريقتين المباشرتين:</b>\n\n"
        f"1️⃣ <b>إرسال كويز تيليجرام رسمي (Poll):</b>\n"
        f"اضغط مشبك الإرفاق 📎 في تيليجرام ثم <i>استطلاع (Poll)</i>، فعّل خيار <b>وضع الاختبار (Quiz)</b> وحدد الإجابة والشرح وأرسله هنا، أو حوّل أي كويز من قنواتك!\n\n"
        f"2️⃣ <b>أو أرسل نص السؤال مباشرة:</b>\n"
        f"انسخ والصق السؤال بهذا الشكل:\n"
        f"<code>السؤال: ما ناتج 15 × 12 ؟\n"
        f"أ) 160\n"
        f"ب) 180 *\n"
        f"ج) 170\n"
        f"د) 190\n"
        f"الشرح: 15 × 12 = 180</code>\n"
        f"<i>(ضع نجمة * بجانب الإجابة الصحيحة أو حددها بالأزرار)</i>"
    )

    kb = []
    if q_count > 0:
        kb.append([InlineKeyboardButton(f"🚀 اعتماد ونشر الكويز للطلاب ({q_count} سؤال)", callback_data="admin_publish_draft")])
        kb.append([InlineKeyboardButton("👁️ معاينة وتدقيق الأسئلة", callback_data="admin_preview_draft_0")])

    kb.append([InlineKeyboardButton("✏️ تعديل اسم الكويز", callback_data="admin_edit_draft_name")])
    kb.append([InlineKeyboardButton("❌ إلغاء وحذف هذه المسودة", callback_data="admin_cancel_draft")])
    return text, InlineKeyboardMarkup(kb)


def parse_smart_text_question(raw_text: str) -> dict | None:
    """
    Deterministically parses a question from plain text.
    Expected format:
      السؤال: ...
      أ) ...
      ب) ... *
      ج) ...
      د) ...
      الشرح: ...
    """
    if not raw_text or len(raw_text.strip()) < 10:
        return None

    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    if len(lines) < 3:
        return None

    question_text = None
    options = []
    correct_answer = None
    explanation = None

    # Line by line extraction
    opt_pattern = re.compile(r'^(?:[أ-يa-zA-Z0-9]|[1-4]|[١-٤])[\)\.\-:]\s*(.+)$')
    q_pattern = re.compile(r'^(?:السؤال|س)[\s:]*(.+)$', re.IGNORECASE)
    exp_pattern = re.compile(r'^(?:الشرح|شرح|التوضيح|توضيح)[\s:]*(.+)$', re.IGNORECASE)
    ans_pattern = re.compile(r'^(?:الإجابة|الاجابة|الحل)[\s:]*(.+)$', re.IGNORECASE)

    non_opt_lines = []

    for line in lines:
        # Check explanation
        exp_match = exp_pattern.match(line)
        if exp_match:
            explanation = exp_match.group(1).strip()
            continue

        # Check explicit answer line: e.g. الإجابة: ب أو الإجابة: 180
        ans_match = ans_pattern.match(line)
        if ans_match:
            correct_answer = ans_match.group(1).strip()
            continue

        # Check option line
        opt_match = opt_pattern.match(line)
        if opt_match:
            opt_val = opt_match.group(1).strip()
            # Check if option has an asterisk or checkmark indicating it is correct
            if "*" in opt_val or "✓" in opt_val or "✔" in opt_val or "(صح)" in opt_val:
                clean_opt = opt_val.replace("*", "").replace("✓", "").replace("✔", "").replace("(صح)", "").strip()
                options.append(clean_opt)
                correct_answer = clean_opt
            else:
                options.append(opt_val)
            continue

        # Otherwise it's part of the question
        q_match = q_pattern.match(line)
        if q_match:
            non_opt_lines.append(q_match.group(1).strip())
        else:
            non_opt_lines.append(line)

    if non_opt_lines:
        question_text = " ".join(non_opt_lines).strip()

    if not question_text or len(options) < 2:
        return None

    # Clean explanation if needed
    if explanation:
        explanation = explanation[:200]

    return {
        "question": question_text,
        "options": options[:10],
        "answer": correct_answer or "",
        "explanation": explanation or ""
    }


# ─── 3. Handling Incoming Messages in Admin Creation Mode ────────────────────

async def handle_admin_creation_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles text messages when Admin is creating or editing a quiz."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return False

    chat_id = update.effective_chat.id
    raw_text = (update.message.text or "").strip()
    state = context.user_data.get("admin_quiz_state")

    # 1. Setting quiz name
    if state == "awaiting_name":
        if len(raw_text) < 2:
            await update.message.reply_text("⚠️ يرجى كتابة عنوان مناسب للكويز.")
            return True

        draft = context.user_data.setdefault("admin_quiz_draft", {})
        draft["name"] = raw_text
        context.user_data["admin_quiz_state"] = "awaiting_category"

        # Ask to choose category
        categories = db.get_categories()
        kb = []
        row = []
        for cat in categories:
            row.append(InlineKeyboardButton(f"{cat.get('icon', '📁')} {cat['name']}", callback_data=f"admin_set_cat_{cat['id']}"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([InlineKeyboardButton("➕ قسم جديد", callback_data="admin_add_new_cat")])
        kb.append([InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel")])

        await update.message.reply_text(
            f"✅ تم تحديد الاسم: <b>{html.escape(raw_text)}</b>\n\n👇 اختر القسم الذي ينتمي له هذا الكويز:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="HTML"
        )
        return True

    # 2. Setting new category name
    if state == "awaiting_new_cat_name":
        if len(raw_text) < 2:
            await update.message.reply_text("⚠️ يرجى كتابة اسم صحيح للقسم.")
            return True
        new_cat_id = db.create_category(name=raw_text, icon="📁", owner_id=user.id)
        draft = context.user_data.setdefault("admin_quiz_draft", {})
        draft["category_id"] = new_cat_id
        context.user_data["admin_quiz_state"] = "adding_questions"
        text, kb = build_draft_dashboard(context)
        await send_clean_message(context, chat_id, f"✅ تم إنشاء القسم «{html.escape(raw_text)}» بنجاح!\n\n" + text, reply_markup=kb)
        return True

    # 3. Adding questions via smart text form
    if state == "adding_questions":
        parsed = parse_smart_text_question(raw_text)
        if parsed:
            draft = context.user_data.setdefault("admin_quiz_draft", {})
            questions = draft.setdefault("questions", [])

            # If correct answer is not specified in text, prompt admin to pick it
            if not parsed["answer"] or parsed["answer"] not in parsed["options"]:
                context.user_data["admin_pending_q"] = parsed
                kb_opts = []
                letters = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]
                for i, opt in enumerate(parsed["options"]):
                    lbl = f"({letters[i] if i < len(letters) else i+1}) {opt[:25]}"
                    kb_opts.append([InlineKeyboardButton(lbl, callback_data=f"admin_pick_correct_{i}")])
                kb_opts.append([InlineKeyboardButton("❌ إلغاء هذا السؤال", callback_data="admin_draft_dash")])

                prompt = (
                    f"❓ <b>سؤال جديد مكتمل الصياغة!</b>\n\n"
                    f"<b>السؤال:</b> {html.escape(parsed['question'])}\n\n"
                    f"👇 <b>حدد الإجابة الصحيحة لهذا السؤال بنقرة واحدة:</b>"
                )
                await send_clean_message(context, chat_id, prompt, reply_markup=InlineKeyboardMarkup(kb_opts))
                return True

            # If answer is present, add it immediately!
            questions.append(parsed)
            text, kb = build_draft_dashboard(context)
            ack = f"✅ <b>تمت إضافة السؤال رقم {len(questions)} بنجاح!</b> 🎯\n\n" + text
            await send_clean_message(context, chat_id, ack, reply_markup=kb)
            return True

    # 4. Broadcast message input
    if state == "awaiting_broadcast_text":
        context.user_data.pop("admin_quiz_state", None)
        users = db.get_all_users()
        sent_count = 0
        status_msg = await update.message.reply_text(f"⏳ جاري إرسال الإذاعة إلى {len(users)} مستخدم...")
        for u in users:
            uid = u["user_id"]
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"📢 <b>تنبيه من إدارة ذاكرة القدرات:</b>\n\n{html.escape(raw_text)}",
                    parse_mode="HTML"
                )
                sent_count += 1
            except Exception:
                pass
        await status_msg.edit_text(
            f"✅ تم إرسال الإذاعة بنجاح إلى <b>{sent_count}</b> طالب من أصل {len(users)}!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة المشرف", callback_data="admin_panel")]])
        )
        return True

    return False


async def handle_admin_creation_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles native Telegram Quiz Polls sent or forwarded by Admin."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return False

    poll = update.message.poll if update.message else None
    if not poll:
        return False

    chat_id = update.effective_chat.id
    state = context.user_data.get("admin_quiz_state")

    # If Admin is not actively in quiz creation mode, offer to start a new quiz with this poll
    if state != "adding_questions":
        draft = context.user_data.setdefault("admin_quiz_draft", {"name": "كويز تيليجرام الجديد", "questions": []})
        context.user_data["admin_quiz_state"] = "adding_questions"

    draft = context.user_data.setdefault("admin_quiz_draft", {"name": "كويز تيليجرام الجديد", "questions": []})
    questions = draft.setdefault("questions", [])

    q_text = poll.question
    options = [opt.text for opt in poll.options]
    correct_idx = poll.correct_option_id
    explanation = getattr(poll, "explanation", "") or ""

    # If quiz mode with verified answer from Telegram
    if correct_idx is not None and 0 <= correct_idx < len(options):
        correct_answer = options[correct_idx]
        question_entry = {
            "question": q_text,
            "options": options,
            "answer": correct_answer,
            "explanation": explanation
        }
        questions.append(question_entry)
        text, kb = build_draft_dashboard(context)
        ack = f"✅ <b>تم استخراج وإضافة السؤال رقم {len(questions)} بنجاح!</b> 🎯\n\n" + text
        await send_clean_message(context, chat_id, ack, reply_markup=kb)
        return True

    # If poll without pre-set correct answer, ask Admin to pick the correct answer
    pending_q = {
        "question": q_text,
        "options": options,
        "answer": "",
        "explanation": explanation
    }
    context.user_data["admin_pending_q"] = pending_q
    kb_opts = []
    letters = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]
    for i, opt in enumerate(options):
        lbl = f"({letters[i] if i < len(letters) else i+1}) {opt[:25]}"
        kb_opts.append([InlineKeyboardButton(lbl, callback_data=f"admin_pick_correct_{i}")])
    kb_opts.append([InlineKeyboardButton("❌ إلغاء هذا السؤال", callback_data="admin_draft_dash")])

    prompt = (
        f"❓ <b>سؤال استطلاع وارد (بدون إجابة مسبقة):</b>\n\n"
        f"<b>السؤال:</b> {html.escape(q_text)}\n\n"
        f"👇 <b>اضغط على الإجابة الصحيحة لحفظ السؤال فوراً:</b>"
    )
    await send_clean_message(context, chat_id, prompt, reply_markup=InlineKeyboardMarkup(kb_opts))
    return True


# ─── 4. Admin Callbacks Router ────────────────────────────────────────────────

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Central callback router for all admin panel actions."""
    query = update.callback_query
    data = query.data
    user = update.effective_user
    if not user or not is_admin(user.id):
        return False

    # ── A. Main Admin Panel ──
    if data == "admin_panel":
        text, kb = build_admin_dashboard(user.id)
        await safe_edit(query, text, kb)
        return True

    # ── B. Create Quiz Flow ──
    if data == "admin_create_quiz":
        context.user_data["admin_quiz_draft"] = {"questions": []}
        context.user_data["admin_quiz_state"] = "awaiting_name"
        prompt = (
            "➕ <b>إنشاء كويز جديد للمشرف</b> 📝\n\n"
            "يرجى كتابة وإرسال <b>عنوان الكويز</b> في الشات الآن:\n"
            "<i>(مثال: تجميعات النماذج الحديثة - كمي 1446)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_panel")]])
        await safe_edit(query, prompt, kb)
        return True

    if data.startswith("admin_set_cat_"):
        cat_id = int(data.split("_")[3])
        draft = context.user_data.setdefault("admin_quiz_draft", {})
        draft["category_id"] = cat_id
        context.user_data["admin_quiz_state"] = "adding_questions"
        text, kb = build_draft_dashboard(context)
        await safe_edit(query, text, kb)
        return True

    if data == "admin_add_new_cat":
        context.user_data["admin_quiz_state"] = "awaiting_new_cat_name"
        prompt = (
            "📁 <b>إضافة قسم جديد</b>\n\n"
            "أرسل اسم القسم الجديد في الشات الآن:\n"
            "<i>(مثال: مهارات الهندسة والمثلثات)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_create_quiz")]])
        await safe_edit(query, prompt, kb)
        return True

    if data == "admin_draft_dash":
        context.user_data["admin_quiz_state"] = "adding_questions"
        text, kb = build_draft_dashboard(context)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("admin_pick_correct_"):
        opt_idx = int(data.split("_")[3])
        pending_q = context.user_data.pop("admin_pending_q", None)
        if pending_q and 0 <= opt_idx < len(pending_q["options"]):
            pending_q["answer"] = pending_q["options"][opt_idx]
            draft = context.user_data.setdefault("admin_quiz_draft", {})
            questions = draft.setdefault("questions", [])
            questions.append(pending_q)
            context.user_data["admin_quiz_state"] = "adding_questions"
            text, kb = build_draft_dashboard(context)
            ack = f"✅ <b>تم حفظ السؤال رقم {len(questions)} بنجاح!</b> 🎯\n\n" + text
            await safe_edit(query, ack, kb)
        else:
            await query.answer("⚠️ حدث خطأ أو انتهت صلاحية الاختيار.", show_alert=True)
        return True

    if data.startswith("admin_preview_draft_"):
        idx = int(data.split("_")[3])
        draft = context.user_data.get("admin_quiz_draft", {})
        questions = draft.get("questions", [])
        if not questions:
            await query.answer("لا توجد أسئلة في المسودة بعد!", show_alert=True)
            return True
        idx = max(0, min(idx, len(questions) - 1))
        q = questions[idx]
        opts_text = "\n".join([f"• {opt} {'✅' if opt == q['answer'] else ''}" for opt in q["options"]])
        exp_text = f"\n💡 <b>الشرح:</b> {html.escape(q['explanation'])}" if q.get("explanation") else ""
        card = (
            f"👁️ <b>معاينة السؤال {idx + 1} من {len(questions)}</b>\n\n"
            f"<b>{html.escape(q['question'])}</b>\n\n"
            f"<b>الخيارات:</b>\n{opts_text}{exp_text}"
        )
        nav = []
        if idx > 0:
            nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_preview_draft_{idx - 1}"))
        if idx < len(questions) - 1:
            nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"admin_preview_draft_{idx + 1}"))
        kb = [
            nav,
            [InlineKeyboardButton("🗑️ حذف هذا السؤال", callback_data=f"admin_del_draft_q_{idx}")],
            [InlineKeyboardButton("🔙 عودة للوحة الكويز", callback_data="admin_draft_dash")]
        ]
        await safe_edit(query, card, InlineKeyboardMarkup(kb))
        return True

    if data.startswith("admin_del_draft_q_"):
        idx = int(data.split("_")[4])
        draft = context.user_data.get("admin_quiz_draft", {})
        questions = draft.get("questions", [])
        if 0 <= idx < len(questions):
            questions.pop(idx)
            await query.answer("🗑️ تم حذف السؤال بنجاح.")
        text, kb = build_draft_dashboard(context)
        await safe_edit(query, text, kb)
        return True

    if data == "admin_cancel_draft":
        context.user_data.pop("admin_quiz_draft", None)
        context.user_data.pop("admin_quiz_state", None)
        await query.answer("تم إلغاء المسودة.", show_alert=True)
        text, kb = build_admin_dashboard(user.id)
        await safe_edit(query, text, kb)
        return True

    if data == "admin_publish_draft":
        draft = context.user_data.get("admin_quiz_draft", {})
        questions = draft.get("questions", [])
        if not questions:
            await query.answer("⚠️ لا يمكنك نشر كويز بدون أسئلة!", show_alert=True)
            return True

        name = draft.get("name", "كويز جديد")
        cat_id = draft.get("category_id")

        # Save to database
        quiz_id = db.save_quiz_without_review(
            name=name,
            questions=questions,
            category_id=cat_id,
            owner_id=user.id,
            is_public=1
        )

        context.user_data.pop("admin_quiz_draft", None)
        context.user_data.pop("admin_quiz_state", None)

        success_text = (
            f"🎉 <b>تم نشر الكويز بنجاح!</b> 🌟\n\n"
            f"📋 <b>العنوان:</b> {html.escape(name)}\n"
            f"📝 <b>عدد الأسئلة:</b> {len(questions)} سؤال\n\n"
            f"الكويز أصبح الآن متاحاً لجميع الطلاب في قسم تصفح الكويزات ويمكنهم حله فوراً!"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ تجربة الكويز الآن", callback_data=f"start_quiz_{quiz_id}")],
            [InlineKeyboardButton("👑 لوحة تحكم المشرف", callback_data="admin_panel")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ])
        await safe_edit(query, success_text, kb)
        return True

    # ── C. Student Error Reports Viewer & Management ──
    if data == "admin_view_reports":
        reports = db.get_pending_reports()
        if not reports:
            text = (
                "✅ <b>صندوق البلاغات نظيف تماماً!</b> 🎉\n\n"
                "لا توجد أي بلاغات معلقة من الطلاب عن الأسئلة حالياً."
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة المشرف", callback_data="admin_panel")]])
            await safe_edit(query, text, kb)
            return True

        text = f"🚩 <b>بلاغات الطلاب عن الأسئلة ({len(reports)} بلاغ معلق):</b>\n\nاختر البلاغ لفحصه ومعالجته:"
        kb = []
        for r in reports[:15]:
            q_title = (r.get("quiz_name") or "كويز")[:20]
            reason = r.get("reason") or "ملاحظة"
            kb.append([InlineKeyboardButton(f"🚨 {q_title} | {reason}", callback_data=f"admin_rep_detail_{r['id']}")])
        kb.append([InlineKeyboardButton("🔙 لوحة المشرف", callback_data="admin_panel")])
        await safe_edit(query, text, InlineKeyboardMarkup(kb))
        return True

    if data.startswith("admin_rep_detail_"):
        rep_id = int(data.split("_")[3])
        rep = db.get_report_by_id(rep_id)
        if not rep:
            await query.answer("البلاغ غير موجود أو تمت معالجته.", show_alert=True)
            return True

        q_opts = rep.get("question_options") or []
        if isinstance(q_opts, str):
            import json
            try: q_opts = json.loads(q_opts)
            except Exception: q_opts = []

        opts_formatted = "\n".join([f"• {opt} {'✅ (الحل الحالي)' if opt == rep.get('correct_answer') else ''}" for opt in q_opts])

        card = (
            f"🚨 <b>تفاصيل بلاغ الطالب #{rep_id}</b>\n\n"
            f"📚 <b>الكويز:</b> {html.escape(rep.get('quiz_name') or 'غير معروف')}\n"
            f"👤 <b>الطالب:</b> @{html.escape(rep.get('username') or 'طالب')} (ID: <code>{rep.get('user_id')}</code>)\n"
            f"⚠️ <b>نوع البلاغ:</b> <b>{html.escape(rep.get('reason') or '')}</b>\n\n"
            f"❓ <b>نص السؤال المبلغ عنه:</b>\n"
            f"<blockquote>{html.escape(rep.get('question_text') or '')}</blockquote>\n\n"
            f"<b>الخيارات الحالية:</b>\n{opts_formatted}\n\n"
            f"💡 <b>الشرح الحالي:</b> {html.escape(rep.get('explanation') or 'لا يوجد')}\n\n"
            f"اختر الإجراء المطلوب:"
        )

        kb = [
            [InlineKeyboardButton("✅ تم الحل (إغلاق البلاغ)", callback_data=f"admin_rep_resolve_{rep_id}")],
            [InlineKeyboardButton("🗑️ حذف السؤال المبلغ عنه", callback_data=f"admin_rep_del_q_{rep['question_id']}_{rep_id}")],
            [InlineKeyboardButton("❌ تجاهل البلاغ", callback_data=f"admin_rep_dismiss_{rep_id}")],
            [InlineKeyboardButton("🔙 قائمة البلاغات", callback_data="admin_view_reports")],
        ]
        await safe_edit(query, card, InlineKeyboardMarkup(kb))
        return True

    if data.startswith("admin_rep_resolve_"):
        rep_id = int(data.split("_")[3])
        db.update_report_status(rep_id, "resolved")
        await query.answer("✅ تم تحديد البلاغ كـ (تم الحل) بنجاح!", show_alert=True)
        # Re-render reports list
        reports = db.get_pending_reports()
        if not reports:
            text = "✅ <b>تمت معالجة جميع البلاغات!</b> 🎉"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة المشرف", callback_data="admin_panel")]])
            await safe_edit(query, text, kb)
        else:
            query.data = "admin_view_reports"
            await handle_admin_callback(update, context)
        return True

    if data.startswith("admin_rep_dismiss_"):
        rep_id = int(data.split("_")[3])
        db.update_report_status(rep_id, "dismissed")
        await query.answer("تم تجاهل البلاغ.")
        query.data = "admin_view_reports"
        await handle_admin_callback(update, context)
        return True

    if data.startswith("admin_rep_del_q_"):
        parts = data.split("_")
        q_id = int(parts[4])
        rep_id = int(parts[5])
        db.delete_question_by_id(q_id)
        db.update_report_status(rep_id, "resolved")
        await query.answer("🗑️ تم حذف السؤال وإغلاق البلاغ بنجاح!", show_alert=True)
        query.data = "admin_view_reports"
        await handle_admin_callback(update, context)
        return True

    # ── D. Manage Quizzes List ──
    if data.startswith("admin_manage_quizzes_"):
        page = int(data.split("_")[3])
        quizzes = db.get_all_quizzes()
        per_page = 8
        total_pages = max(1, (len(quizzes) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * per_page
        page_quizzes = quizzes[start_idx:start_idx + per_page]

        text = f"📋 <b>إدارة الكويزات المنشورة (صفحة {page} من {total_pages}):</b>\n\nاضغط على أي كويز لتعديله أو حذفه:"
        kb = []
        for q in page_quizzes:
            q_cnt = len(db.get_questions(q["id"]))
            kb.append([InlineKeyboardButton(f"📝 {q['name'][:30]} ({q_cnt}س)", callback_data=f"admin_quiz_detail_{q['id']}")])

        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_manage_quizzes_{page - 1}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"admin_manage_quizzes_{page + 1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 لوحة المشرف", callback_data="admin_panel")])
        await safe_edit(query, text, InlineKeyboardMarkup(kb))
        return True

    if data.startswith("admin_quiz_detail_"):
        quiz_id = int(data.split("_")[3])
        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await query.answer("الكويز غير موجود.", show_alert=True)
            return True
        questions = db.get_questions(quiz_id)
        text = (
            f"📋 <b>تفاصيل الكويز #{quiz_id}</b>\n\n"
            f"<b>الاسم:</b> {html.escape(quiz['name'])}\n"
            f"<b>عدد الأسئلة:</b> {len(questions)} سؤال\n\n"
            f"اختر الإجراء المطلوب:"
        )
        kb = [
            [InlineKeyboardButton("▶️ تجربة الكويز كطالب", callback_data=f"start_quiz_{quiz_id}")],
            [InlineKeyboardButton("🗑️ حذف الكويز نهائياً", callback_data=f"admin_del_quiz_confirm_{quiz_id}")],
            [InlineKeyboardButton("🔙 قائمة الكويزات", callback_data="admin_manage_quizzes_1")],
        ]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))
        return True

    if data.startswith("admin_del_quiz_confirm_"):
        quiz_id = int(data.split("_")[4])
        quiz = db.get_quiz(quiz_id)
        text = (
            f"⚠️ <b>تأكيد حذف الكويز!</b>\n\n"
            f"هل أنت متأكد من رغبتك في حذف الكويز «{html.escape(quiz['name'] if quiz else '')}» نهائياً؟\n"
            f"سيتم حذف جميع أسئلته ومراجعات الطلاب المتعلقة به."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ نعم، احذف الكويز نهائياً", callback_data=f"admin_do_del_quiz_{quiz_id}")],
            [InlineKeyboardButton("❌ تراجع", callback_data=f"admin_quiz_detail_{quiz_id}")],
        ])
        await safe_edit(query, text, kb)
        return True

    if data.startswith("admin_do_del_quiz_"):
        quiz_id = int(data.split("_")[4])
        db.delete_quiz(quiz_id)
        await query.answer("🗑️ تم حذف الكويز بنجاح.", show_alert=True)
        query.data = "admin_manage_quizzes_1"
        await handle_admin_callback(update, context)
        return True

    # ── E. Categories Management ──
    if data == "admin_manage_cats":
        categories = db.get_categories()
        text = f"📁 <b>إدارة الأقسام والتصنيفات ({len(categories)} أقسام):</b>\n\n"
        kb = []
        for c in categories:
            kb.append([InlineKeyboardButton(f"{c.get('icon', '📁')} {c['name']}", callback_data=f"admin_cat_detail_{c['id']}")])
        kb.append([InlineKeyboardButton("➕ إضافة قسم جديد", callback_data="admin_add_new_cat")])
        kb.append([InlineKeyboardButton("🔙 لوحة المشرف", callback_data="admin_panel")])
        await safe_edit(query, text, InlineKeyboardMarkup(kb))
        return True

    if data.startswith("admin_cat_detail_"):
        cat_id = int(data.split("_")[3])
        cat = db.get_category(cat_id)
        if not cat:
            await query.answer("القسم غير موجود.", show_alert=True)
            return True
        quizzes = db.get_quizzes_by_category(cat_id)
        text = (
            f"📁 <b>تفاصيل القسم: {cat.get('icon', '📁')} {html.escape(cat['name'])}</b>\n\n"
            f"<b>عدد الكويزات في هذا القسم:</b> {len(quizzes)}\n\n"
            f"اختر الإجراء:"
        )
        kb = [
            [InlineKeyboardButton("🗑️ حذف هذا القسم", callback_data=f"admin_del_cat_{cat_id}")],
            [InlineKeyboardButton("🔙 قائمة الأقسام", callback_data="admin_manage_cats")],
        ]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))
        return True

    if data.startswith("admin_del_cat_"):
        cat_id = int(data.split("_")[3])
        db.delete_category(cat_id)
        await query.answer("🗑️ تم حذف القسم.")
        query.data = "admin_manage_cats"
        await handle_admin_callback(update, context)
        return True

    # ── F. Broadcast Tool ──
    if data == "admin_broadcast_prompt":
        context.user_data["admin_quiz_state"] = "awaiting_broadcast_text"
        users_cnt = len(db.get_all_users())
        prompt = (
            f"📢 <b>إرسال إذاعة جماعية للطلاب</b> 👥\n\n"
            f"سيتم إرسال رسالتك لجميع الطلاب المسجلين في البوت وعددهم: <b>{users_cnt} طالب</b>.\n\n"
            f"👇 <b>أرسل نص الرسالة التي تريد بثها في الشات الآن:</b>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_panel")]])
        await safe_edit(query, prompt, kb)
        return True

    # ── G. Database Reset / Format Tool ──
    if data == "admin_confirm_format":
        text = (
            "⚠️ <b>تحذير خطير: فرمتة وتطهير البوت من الجذور!</b> 🧹\n\n"
            "هذا الإجراء سيقوم بـ:\n"
            "• مسح جميع الكويزات القديمة والتجارب والمسودات.\n"
            "• تصفير جداول الأسئلة والمراجعات وبنك الأسئلة وبلاغات الأخطاء.\n"
            "• إعادة ضبط الأقسام الأساسية المعتمدة (كمي، لفظي، تجميعات).\n"
            "• <i>الحفاظ على حسابات الطلاب ووقت التنبيه لكل طالب.</i>\n\n"
            "هل أنت متأكد تماماً من رغبتك في الفرمتة الآن؟"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧹 نعم، فرمت البوت من جذوره الآن", callback_data="admin_do_format")],
            [InlineKeyboardButton("❌ تراجع", callback_data="admin_panel")],
        ])
        await safe_edit(query, text, kb)
        return True

    if data == "admin_do_format":
        db.format_database_clean(keep_users=True)
        await query.answer("🧹 تم فرمتة وتطهير قاعدة البيانات بنجاح 100%!", show_alert=True)
        text, kb = build_admin_dashboard(user.id)
        await safe_edit(query, text, kb)
        return True

    return False
