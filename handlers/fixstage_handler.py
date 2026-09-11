"""
handlers/fixstage_handler.py — ضبط مراحل التكرار المتباعد وإدارة المجلدات وتعديل وتدقيق الأسئلة
"""
import html
import logging
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from spaced_repetition import days_until, stage_label
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 10


def _build_admin_cat_panel(cat_id=0):
    real_cat_id = cat_id if cat_id != 0 else None
    cur_cat = db.get_category(real_cat_id) if real_cat_id else None
    subfolders = db.get_categories(parent_id=real_cat_id, is_public=1)
    text = (f"📁 <b>إدارة المجلد: {html.escape(cur_cat['name'])}</b>\n\nالمجلدات الفرعية:"
            if cur_cat else "📁 <b>إدارة المجلدات الرئيسية</b>\n\nالمجلدات:")
    kb = []
    for sf in subfolders:
        kb.append([InlineKeyboardButton(f"📁 {sf['name']}", callback_data=f"admin_cat_{sf['id']}")])
    kb.append([InlineKeyboardButton("➕ إنشاء مجلد جديد", callback_data=f"admin_new_folder_{cat_id}")])
    if cur_cat:
        kb.append([InlineKeyboardButton("✏️ إعادة تسمية", callback_data=f"admin_rename_cat_{cat_id}"),
                   InlineKeyboardButton("🗑️ حذف", callback_data=f"admin_del_cat_{cat_id}")])
        parent_id = cur_cat.get("parent_id")
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"admin_cat_{parent_id or 0}")])
    else:
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")])
    return text, InlineKeyboardMarkup(kb)


async def fixstage_command(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    user = update.effective_user
    if not user or not is_admin(user.id):
        msg = "❌ هذا الأمر للمشرف فقط."
        if update.message:
            await update.message.reply_text(msg, parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.answer("❌ غير مصرح.", show_alert=True)
        return
    reviews = db.get_all_quiz_reviews()
    if not reviews:
        txt = "لا توجد كويزات مجدولة."
        if update.message:
            await send_clean_message(context, update.effective_chat.id, txt, update=update)
        elif update.callback_query:
            await safe_edit(update.callback_query, txt)
        return
    total = len(reviews)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, total_pages))
    context.user_data["last_fixstage_page"] = page
    start = (page - 1) * ITEMS_PER_PAGE
    kb = []
    for r in reviews[start:start + ITEMS_PER_PAGE]:
        d = days_until(r["next_review_date"])
        timing = "🔴 الآن" if d <= 0 else ("🟡 غداً" if d == 1 else f"⏳ {d} يوم")
        name = r.get("quiz_name", "كويز")
        if len(name) > 25:
            name = name[:22] + "..."
        kb.append([InlineKeyboardButton(f"🔧 {name} — {timing}", callback_data=f"fixstage_menu_{r['quiz_id']}")])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"fixstage_page_{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"fixstage_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    text = f"🛠 <b>ضبط مراحل الكويزات (صفحة {page}/{total_pages})</b>"
    if update.message:
        await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=InlineKeyboardMarkup(kb))
    elif update.callback_query:
        await safe_edit(update.callback_query, text, InlineKeyboardMarkup(kb))


async def handle_fixstage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int, is_adm: bool) -> bool:
    """
    Handles callbacks related to admin categories, fixstage settings, question editing and auditing.
    Returns True if handled, False otherwise.
    """
    query = update.callback_query

    if data.startswith("admin_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        cat_id = int(data.split("_")[-1])
        text, kb = _build_admin_cat_panel(cat_id)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("admin_new_folder_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parent_id = int(data.split("_")[-1])
        context.user_data["waiting_for_new_folder"] = parent_id
        await safe_edit(query, "📁 أرسل اسم المجلد الجديد:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cat_{parent_id}")]]))
        return True

    if data.startswith("admin_rename_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        cat_id = int(data.split("_")[-1])
        context.user_data["waiting_for_cat_rename"] = cat_id
        await safe_edit(query, "✏️ أرسل الاسم الجديد للمجلد:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cat_{cat_id}")]]))
        return True

    if data.startswith("admin_del_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        cat_id = int(data.split("_")[-1])
        cat = db.get_category(cat_id)
        if cat:
            await safe_edit(query, f"🗑️ تأكيد حذف المجلد: <b>{html.escape(cat['name'])}</b>?",
                            InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_del_cat_{cat_id}")],
                                [InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cat_{cat_id}")],
                            ]))
        return True

    if data.startswith("confirm_del_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        cat_id = int(data.split("_")[-1])
        cat = db.get_category(cat_id)
        parent_id = cat.get("parent_id") if cat else None
        db.delete_category(cat_id)
        await safe_edit(query, "✅ تم حذف المجلد.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"admin_cat_{parent_id or 0}")]]))
        return True

    if data.startswith("reupload_excel_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        context.user_data["waiting_for_json_update"] = quiz_id
        await safe_edit(query, f"📊 <b>تحديث كويز: {name}</b>\n\nأرسل الآن ملف <code>.xlsx</code> أو <code>.csv</code> الجديد ليتم تحديث الأسئلة فوراً مع الحفاظ على جدول التكرار المتباعد 🌟:",
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("📥 تحميل قالب Excel", callback_data="download_excel_template")],
                            [InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_detail_{quiz_id}")]
                        ]))
        return True

    if data.startswith("reupload_json_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        context.user_data["waiting_for_json_update"] = quiz_id
        await safe_edit(query, f"🔄 <b>تحديث: {name}</b>\n\nأرسل ملف .json أو الصق النص:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_detail_{quiz_id}")]]))
        return True

    if data.startswith("rename_quiz_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        quiz_id = int(data.split("_")[-1])
        context.user_data["waiting_for_quiz_rename"] = quiz_id
        await safe_edit(query, "✏️ أرسل الاسم الجديد للكويز:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_detail_{quiz_id}")]]))
        return True

    if data.startswith("fixstage_page_"):
        page = int(data.split("_")[-1])
        await fixstage_command(update, context, page)
        return True

    if data.startswith("fixstage_menu_") or data.startswith("fixstage_set_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        if data.startswith("fixstage_set_"):
            parts = data.split("_")
            quiz_id = int(parts[2])
            new_stage = max(0, min(int(parts[3]), 4))
            db.set_quiz_review_stage(quiz_id, user_id, new_stage)
        else:
            quiz_id = int(data.split("_")[-1])

        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await safe_edit(query, "❌ الكويز غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]))
            return True

        review = db.get_quiz_review(quiz_id, user_id)
        q_name = html.escape(quiz.get("name", "كويز"))
        last_fpage = context.user_data.get("last_fixstage_page", 1)

        if not review:
            await safe_edit(query, f"✅ كويز <b>{q_name}</b> اكتملت مراجعاته.",
                            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_page_{last_fpage}")]]))
            return True

        stage = review["stage"]
        next_date = review["next_review_date"]
        d = days_until(next_date)
        status = "🔴 مستحق" if d <= 0 else ("🟡 غداً" if d == 1 else f"⏳ بعد {d} يوم")
        lbl = stage_label(stage)

        text = (f"🛠 <b>ضبط المراجعة</b>\n📚 {q_name}\n\n"
                f"🗓 المرحلة: <b>{lbl}</b>\n"
                f"📅 الموعد: <b>{next_date}</b> — {status}\n\nاختر الإجراء:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➖ المرحلة السابقة", callback_data=f"fixstage_set_{quiz_id}_{stage-1}"),
             InlineKeyboardButton("➕ المرحلة التالية", callback_data=f"fixstage_set_{quiz_id}_{stage+1}")],
            [InlineKeyboardButton("🔴 اليوم", callback_data=f"fixdate_{quiz_id}_0"),
             InlineKeyboardButton("🟡 غداً", callback_data=f"fixdate_{quiz_id}_1"),
             InlineKeyboardButton("🔵 بعد 3", callback_data=f"fixdate_{quiz_id}_3")],
            [InlineKeyboardButton("🔵 بعد 7", callback_data=f"fixdate_{quiz_id}_7"),
             InlineKeyboardButton("🔵 بعد 14", callback_data=f"fixdate_{quiz_id}_14"),
             InlineKeyboardButton("🔵 بعد 30", callback_data=f"fixdate_{quiz_id}_30")],
            [InlineKeyboardButton("📅 تاريخ مخصص", callback_data=f"fixdate_custom_{quiz_id}")],
            [InlineKeyboardButton("✅ إكمال المراجعة", callback_data=f"fixstage_done_{review['id']}_{quiz_id}")],
            [InlineKeyboardButton(f"🔙 صفحة {last_fpage}", callback_data=f"fixstage_page_{last_fpage}")],
        ])
        await safe_edit(query, text, kb)
        return True

    if data.startswith("fixstage_done_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])
        db.advance_quiz_review(review_id)
        last_fpage = context.user_data.get("last_fixstage_page", 1)
        await safe_edit(query, "✅ تم تسجيل المراجعة كمكتملة.",
                        InlineKeyboardMarkup([[InlineKeyboardButton(f"🔙 صفحة {last_fpage}", callback_data=f"fixstage_page_{last_fpage}")]]))
        return True

    if data.startswith("fixdate_custom_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        quiz_id = int(data.split("_")[-1])
        context.user_data["waiting_for_fixdate_custom"] = quiz_id
        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        await safe_edit(query,
                        f"📅 <b>تاريخ مخصص</b> — {q_name}\n\nأرسل كـ <code>2026-09-30</code> أو عدد أيام كـ <code>7</code>",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"fixstage_menu_{quiz_id}")]]))
        return True

    if data.startswith("fixdate_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parts = data.split("_")
        quiz_id = int(parts[1])
        days_offset = int(parts[2])
        if days_offset == 0:
            new_date = (date.today() - timedelta(days=1)).isoformat()
            day_label = "الآن فوراً 🔴"
        else:
            new_date = (date.today() + timedelta(days=days_offset)).isoformat()
            day_label = "غداً 🟡" if days_offset == 1 else f"بعد {days_offset} يوم"
        db.set_quiz_review_next_date(quiz_id, user_id, new_date)
        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        last_fpage = context.user_data.get("last_fixstage_page", 1)
        await safe_edit(query, f"✅ تم تعديل موعد <b>{q_name}</b>\n📅 {new_date} ({day_label})",
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("⚙️ إعدادات الكويز", callback_data=f"fixstage_menu_{quiz_id}")],
                            [InlineKeyboardButton(f"🔙 صفحة {last_fpage}", callback_data=f"fixstage_page_{last_fpage}")],
                        ]))
        return True

    if data.startswith("fixstage_qlist_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parts = data.split("_")
        quiz_id = int(parts[2])
        pg = int(parts[3]) if len(parts) > 3 else 0
        questions = db.get_questions(quiz_id)
        if not questions:
            await safe_edit(query, "❌ لا توجد أسئلة.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_menu_{quiz_id}")]]))
            return True
        PER = 10
        total_p = max(1, (len(questions) + PER - 1) // PER)
        pg = max(0, min(pg, total_p - 1))
        start = pg * PER
        kb_rows = []
        for i, q in enumerate(questions[start:start + PER]):
            q_num = start + i + 1
            clean_q = q["question_text"][:28] + ("..." if len(q["question_text"]) > 28 else "")
            kb_rows.append([InlineKeyboardButton(f"{q_num}: {clean_q}", callback_data=f"fixstage_qedit_{quiz_id}_{q['id']}")])
        nav2 = []
        if pg > 0:
            nav2.append(InlineKeyboardButton("⬅️", callback_data=f"fixstage_qlist_{quiz_id}_{pg-1}"))
        if pg < total_p - 1:
            nav2.append(InlineKeyboardButton("➡️", callback_data=f"fixstage_qlist_{quiz_id}_{pg+1}"))
        if nav2:
            kb_rows.append(nav2)
        kb_rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_menu_{quiz_id}")])
        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        await safe_edit(query, f"🛠 <b>أسئلة: {q_name}</b> (صفحة {pg+1}/{total_p})", InlineKeyboardMarkup(kb_rows))
        return True

    if data.startswith("fixstage_set_ans_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parts = data.split("_")
        quiz_id = int(parts[3])
        q_id = int(parts[4])
        opt_idx = int(parts[5])
        q = db.get_question(q_id)
        if q and q.get("options") and 0 <= opt_idx < len(q["options"]):
            new_ans = q["options"][opt_idx]
            db.update_question_correct_answer(q_id, new_ans)
            await query.answer(f"✅ تم تعيين الإجابة: {new_ans}", show_alert=False)
            data = f"fixstage_qedit_{quiz_id}_{q_id}"
        else:
            await query.answer("❌ تعذر تعيين الإجابة.", show_alert=True)
            return True

    if data.startswith("qedit_text_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        context.user_data["waiting_for_qtext_edit"] = {"quiz_id": quiz_id, "q_id": q_id}
        await safe_edit(query, "✏️ أرسل الآن <b>النص الجديد للسؤال</b> في رسالة نصية:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"fixstage_qedit_{quiz_id}_{q_id}")]]))
        return True

    if data.startswith("qedit_exp_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        context.user_data["waiting_for_qexp_edit"] = {"quiz_id": quiz_id, "q_id": q_id}
        await safe_edit(query, "💡 أرسل الآن <b>الشرح والتوضيح الجديد</b> في رسالة نصية:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"fixstage_qedit_{quiz_id}_{q_id}")]]))
        return True

    if data.startswith("fixstage_qedit_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        q = db.get_question(q_id)
        if not q:
            await safe_edit(query, "❌ السؤال غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_qlist_{quiz_id}_0")]]))
            return True

        options = q.get("options") or []
        cur_ans = (q.get("correct_answer") or "").strip()

        opts_text = "\n".join(f"• {opt}" for opt in options)
        text = (f"✏️ <b>تدقيق وتعديل السؤال</b>\n\n"
                f"❓ <b>نص السؤال:</b>\n{html.escape(q['question_text'])}\n\n"
                f"📋 <b>الخيارات المتاحة:</b>\n{html.escape(opts_text)}\n\n"
                f"✅ <b>الإجابة الصحيحة الحالية:</b> <code>{html.escape(cur_ans)}</code>\n"
                f"💡 <b>الشرح:</b> {html.escape(q.get('explanation', '') or '—')}\n\n"
                f"👇 <i>اضغط على أي خيار بالأسفل لتعيينه كإجابة صحيحة فوراً بنقرة واحدة:</i>")

        kb_rows = []
        ans_btns = []
        labels = ["(أ)", "(ب)", "(ج)", "(د)", "(هـ)", "(و)"]
        for idx, opt in enumerate(options):
            lbl = labels[idx] if idx < len(labels) else f"({idx+1})"
            is_correct = (opt.strip() == cur_ans)
            clean_btn = opt[:12] + ("..." if len(opt) > 12 else "")
            btn_text = f"✅ {lbl} {clean_btn}" if is_correct else f"{lbl} {clean_btn}"
            ans_btns.append(InlineKeyboardButton(btn_text, callback_data=f"fixstage_set_ans_{quiz_id}_{q_id}_{idx}"))

        for i in range(0, len(ans_btns), 2):
            kb_rows.append(ans_btns[i:i+2])

        kb_rows.append([
            InlineKeyboardButton("✏️ تعديل نص السؤال", callback_data=f"qedit_text_{quiz_id}_{q_id}"),
            InlineKeyboardButton("💡 تعديل الشرح", callback_data=f"qedit_exp_{quiz_id}_{q_id}")
        ])
        kb_rows.append([InlineKeyboardButton("🔙 قائمة الأسئلة", callback_data=f"fixstage_qlist_{quiz_id}_0")])
        await safe_edit(query, text, InlineKeyboardMarkup(kb_rows))
        return True

    return False
