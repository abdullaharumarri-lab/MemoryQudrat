"""
handlers/browse_handler.py — تصفح المجلدات والكويزات وتفاصيلها ومعاينتها
"""
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from spaced_repetition import days_until, stage_label
from utils import safe_edit, find_correct_option_index

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 10


def _build_browse_view(cat_id=None, page=1, user_id=None):
    cur_cat = db.get_category(cat_id) if cat_id else None
    subfolders = db.get_categories(parent_id=cat_id, is_public=1)
    quizzes = db.get_quizzes_by_category(category_id=cat_id, is_public=1)

    header = (f"{cur_cat.get('icon', '📁')} <b>{html.escape(cur_cat['name'])}</b>\n"
              if cur_cat else "📚 <b>الكويزات</b>\n")
    kb = []

    for sf in subfolders:
        count = db.get_category_quizzes_count(sf["id"], is_public=1)
        icon = sf.get("icon", "📁")
        kb.append([InlineKeyboardButton(f"{icon} {sf['name']} ({count})",
                                        callback_data=f"browse_cat_{sf['id']}_1")])

    total_q = len(quizzes)
    total_pages = max(1, (total_q + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE) if total_q else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE
    for q in quizzes[start:start + ITEMS_PER_PAGE]:
        kb.append([InlineKeyboardButton(f"📝 {q['name']}", callback_data=f"quiz_detail_{q['id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"browse_cat_{cat_id or 0}_{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"browse_cat_{cat_id or 0}_{page+1}"))
    if nav:
        kb.append(nav)

    if user_id and is_admin(user_id):
        kb.append([InlineKeyboardButton("📁 إدارة المجلد", callback_data=f"admin_cat_{cat_id or 0}")])

    if cur_cat:
        parent_id = cur_cat.get("parent_id")
        kb.append([InlineKeyboardButton("🔙 رجوع",
                                        callback_data=f"browse_cat_{parent_id or 0}_1" if parent_id else "browse_root")])
    else:
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])

    if not subfolders and not quizzes:
        header += "\n📭 لا توجد كويزات هنا حالياً."

    return header, InlineKeyboardMarkup(kb)


def _build_quiz_detail(quiz_id, user_id, back_cb="browse_root"):
    quiz = db.get_quiz(quiz_id)
    if not quiz:
        return "❌ الكويز غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]])

    q_count = len(db.get_questions(quiz_id))
    reviews = db.get_all_quiz_reviews(user_id=user_id)
    user_review = next((r for r in reviews if r["quiz_id"] == quiz_id), None)

    if user_review:
        d = days_until(user_review.get("next_review_date"))
        lbl = stage_label(user_review.get("stage", 0))
        sched_line = (f"📅 مجدول — <b>مستحق الآن!</b> 🔴 ({lbl})" if d <= 0
                      else f"📅 مجدول — <b>غداً</b> 🟡 ({lbl})" if d == 1
                      else f"📅 مجدول — بعد <b>{d}</b> يوم ({lbl})")
    else:
        sched_line = "📅 غير مضاف لجدول مراجعاتك"

    text = (f"📋 <b>{html.escape(quiz['name'])}</b>\n\n"
            f"📝 عدد الأسئلة: <b>{q_count}</b>\n"
            f"{sched_line}\n\nاختر ما تريد:")

    kb = []
    if q_count > 0:
        kb.append([
            InlineKeyboardButton("▶️ ابدأ الكويز", callback_data=f"start_quiz_{quiz_id}"),
            InlineKeyboardButton("👁️ معاينة الأسئلة", callback_data=f"preview_quiz_{quiz_id}_0"),
        ])

    if user_review:
        if days_until(user_review.get("next_review_date")) <= 0:
            kb.append([InlineKeyboardButton("🔁 ابدأ المراجعة المجدولة",
                                            callback_data=f"start_review_{quiz_id}_{user_review['id']}")])
    else:
        if q_count > 0:
            kb.append([InlineKeyboardButton("➕ أضف لجدول مراجعاتي",
                                            callback_data=f"add_to_schedule_{quiz_id}")])

    quiz_weak = [w for w in db.get_due_weak_questions(user_id=user_id) if w["quiz_id"] == quiz_id]
    if quiz_weak:
        kb.append([InlineKeyboardButton(f"❓ راجع الأسئلة الضعيفة ({len(quiz_weak)})",
                                        callback_data=f"start_weak_{quiz_id}")])

    can_manage = is_admin(user_id) or quiz.get("owner_id") == user_id
    if can_manage:
        kb.append([InlineKeyboardButton("📁 نقل إلى مجلد", callback_data=f"move_quiz_{quiz_id}"),
                   InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"rename_quiz_{quiz_id}")])
    if is_admin(user_id):
        kb.append([InlineKeyboardButton("📊 تحديث (Excel)", callback_data=f"reupload_excel_{quiz_id}"),
                   InlineKeyboardButton("🔄 تحديث (JSON)", callback_data=f"reupload_json_{quiz_id}")])
        kb.append([InlineKeyboardButton("⚙️ ضبط المراجعة", callback_data=f"fixstage_menu_{quiz_id}"),
                   InlineKeyboardButton("🛠 تعديل وتدقيق الأسئلة", callback_data=f"fixstage_qlist_{quiz_id}_0")])
        kb.append([InlineKeyboardButton("🗑️ حذف الكويز", callback_data=f"delete_quiz_{quiz_id}")])

    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_cb)])
    return text, InlineKeyboardMarkup(kb)


def _build_quiz_preview(quiz_id: int, q_index: int = 0):
    quiz = db.get_quiz(quiz_id)
    if not quiz:
        return "❌ الكويز غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])

    questions = db.get_questions(quiz_id)
    total_q = len(questions)
    if total_q == 0:
        return f"📭 لا توجد أسئلة في كويز: <b>{html.escape(quiz['name'])}</b>", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_id}")]])

    q_index = max(0, min(q_index, total_q - 1))
    q = questions[q_index]
    q_text = str(q.get("question_text", "")).strip()

    passage_text = None
    clean_q_prompt = q_text

    if "📄" in q_text and "❓" in q_text:
        parts = q_text.split("❓", 1)
        passage_text = parts[0].replace("📄", "").strip()
        clean_q_prompt = parts[1].strip()
    elif "\n\n" in q_text and len(q_text.split("\n\n")[0]) > 25:
        lines = q_text.split("\n\n", 1)
        passage_text = lines[0].strip()
        clean_q_prompt = lines[1].strip()

    raw_options = q.get("options") or []
    correct_ans = str(q.get("correct_answer", "")).strip()
    exp = str(q.get("explanation", "")).strip() if q.get("explanation") else ""

    arabic_letters = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح"]
    correct_idx = find_correct_option_index(raw_options, correct_ans)
    options_lines = []
    for idx, opt in enumerate(raw_options):
        letter = arabic_letters[idx] if idx < len(arabic_letters) else str(idx + 1)
        opt_str = str(opt).strip()
        is_correct = (idx == correct_idx)
        if is_correct:
            options_lines.append(f"  <b>({letter})</b> {html.escape(opt_str)} ✅ <b>(الإجابة الصحيحة)</b>")
        else:
            options_lines.append(f"  ({letter}) {html.escape(opt_str)}")

    msg_lines = [
        f"📋 <b>معاينة: {html.escape(quiz['name'])}</b>",
        f"📝 <b>السؤال {q_index + 1} من {total_q}</b>\n",
    ]

    if q.get("passage_image"):
        msg_lines.append("🖼️ <b>صورة القطعة:</b> محفوظة بجودة عالية وجاهزة للعرض كصورة 📷\n")
    elif passage_text:
        msg_lines.append(f"📄 <b>القطعة / النص:</b>\n<blockquote>{html.escape(passage_text)}</blockquote>\n")

    msg_lines.append(f"❓ <b>{html.escape(clean_q_prompt)}</b>\n")
    msg_lines.append("<b>الخيارات:</b>\n" + "\n".join(options_lines))

    if exp:
        msg_lines.append(f"\n💡 <b>الشرح والتوضيح:</b>\n<i>{html.escape(exp)}</i>")

    nav_row = []
    if q_index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"preview_quiz_{quiz_id}_{q_index - 1}"))
    nav_row.append(InlineKeyboardButton(f"{q_index + 1}/{total_q}", callback_data="noop"))
    if q_index < total_q - 1:
        nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"preview_quiz_{quiz_id}_{q_index + 1}"))

    kb = [nav_row]
    kb.append([
        InlineKeyboardButton("🛠 تعديل هذا السؤال", callback_data=f"fixstage_qedit_{quiz_id}_{q['id']}"),
        InlineKeyboardButton("📋 قائمة الأسئلة", callback_data=f"fixstage_qlist_{quiz_id}_{q_index // 10}")
    ])
    kb.append([
        InlineKeyboardButton("▶️ ابدأ الكويز", callback_data=f"start_quiz_{quiz_id}"),
        InlineKeyboardButton("🔙 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_id}")
    ])

    return "\n".join(msg_lines), InlineKeyboardMarkup(kb)


async def handle_browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int, is_adm: bool) -> bool:
    """
    Handles callbacks related to browsing folders, quizzes, details, preview, and moving quizzes.
    Returns True if handled, False otherwise.
    """
    query = update.callback_query

    if data in ("browse_root", "public_bank_root", "my_quizzes"):
        context.user_data["last_browse_cb"] = "browse_root"
        text, kb = _build_browse_view(cat_id=None, page=1, user_id=user_id)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("browse_cat_") or data.startswith("my_cat_"):
        parts = data.split("_")
        raw_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 1
        cat_id = raw_id if raw_id != 0 else None
        context.user_data["last_browse_cb"] = data
        text, kb = _build_browse_view(cat_id=cat_id, page=page, user_id=user_id)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("quiz_detail_"):
        quiz_id = int(data.split("_")[-1])
        back_cb = context.user_data.get("last_browse_cb", "browse_root")
        text, kb = _build_quiz_detail(quiz_id, user_id, back_cb=back_cb)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("start_quiz_") or data.startswith("start_practice_"):
        quiz_id = int(data.split("_")[-1])
        s_type = "practice" if data.startswith("start_practice_") else "quiz"
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type=s_type)
        return True

    if data.startswith("move_quiz_"):
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await query.answer("❌ الكويز غير موجود.", show_alert=True)
            return True
        if not (is_adm or quiz.get("owner_id") == user_id):
            await query.answer("❌ ليس لديك صلاحية نقل هذا الكويز.", show_alert=True)
            return True
        categories = db.get_categories(is_public=1)
        kb = []
        for c in categories:
            icon = c.get("icon", "📁")
            kb.append([InlineKeyboardButton(f"{icon} {c['name']}", callback_data=f"set_quiz_cat_{quiz_id}_{c['id']}")])
        kb.append([InlineKeyboardButton("📁 في الرئيسية (بدون مجلد)", callback_data=f"set_quiz_cat_{quiz_id}_0")])
        kb.append([InlineKeyboardButton("🔙 إلغاء", callback_data=f"quiz_detail_{quiz_id}")])
        name = html.escape(quiz.get("name", "كويز"))
        await safe_edit(query, f"📁 <b>نقل الكويز إلى مجلد</b>\n\nاختر المجلد الذي تريد نقل كويز <b>{name}</b> إليه:", InlineKeyboardMarkup(kb))
        return True

    if data.startswith("set_quiz_cat_"):
        parts = data.split("_")
        quiz_id = int(parts[3])
        cat_id = int(parts[4])
        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await query.answer("❌ الكويز غير موجود.", show_alert=True)
            return True
        if not (is_adm or quiz.get("owner_id") == user_id):
            await query.answer("❌ ليس لديك صلاحية نقل هذا الكويز.", show_alert=True)
            return True
        real_cat_id = cat_id if cat_id != 0 else None
        db.move_quiz_to_category(quiz_id, real_cat_id)
        name = html.escape(quiz.get("name", "كويز"))
        cat = db.get_category(real_cat_id) if real_cat_id else None
        cat_name = cat.get("name", "الرئيسية") if cat else "الرئيسية (بدون مجلد)"
        await safe_edit(query, f"✅ تم نقل كويز <b>{name}</b> إلى: <b>{html.escape(cat_name)}</b> بنجاح!",
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("📋 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_id}")],
                            [InlineKeyboardButton("📚 تصفح الكويزات", callback_data=f"browse_cat_{cat_id}_1" if cat_id != 0 else "browse_root")],
                            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
                        ]))
        return True

    if data == "resume_quiz":
        from handlers.quiz_handler import show_next_question
        await show_next_question(update, context)
        return True

    if data.startswith("preview_quiz_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_idx = int(parts[3]) if len(parts) > 3 else 0
        text, kb = _build_quiz_preview(quiz_id, q_idx)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("delete_quiz_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        if quiz:
            await safe_edit(query, f"🗑️ تأكيد حذف: <b>{html.escape(quiz['name'])}</b>?",
                            InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_del_quiz_{quiz_id}")],
                                [InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_detail_{quiz_id}")],
                            ]))
        return True

    if data.startswith("confirm_del_quiz_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return True
        quiz_id = int(data.split("_")[-1])
        db.delete_quiz(quiz_id)
        await safe_edit(query, "✅ تم حذف الكويز.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الكويزات", callback_data="browse_root")]]))
        return True

    return False
