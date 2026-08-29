"""
main_menu.py — ذاكرة القدرات (نسخة مُعاد بناؤها)
بسيط | عملي | محفز
"""
import html
import json
import logging
import re
from datetime import date, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin, ADMIN_USER_ID
from utils import safe_edit, send_clean_message, normalize_arabic_digits
from spaced_repetition import days_until, stage_label

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 10


# ═══════════════════════════════════════════════════════════════
#  الصفحة الرئيسية
# ═══════════════════════════════════════════════════════════════

def main_menu_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("📚 الكويزات", callback_data="browse_root")],
        [
            InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule"),
        ],
        [
            InlineKeyboardButton("❓ الأسئلة الضعيفة", callback_data="weak_questions"),
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu"),
        ],
    ]
    if user_id and is_admin(user_id):
        kb.append([InlineKeyboardButton("➕ إنشاء / رفع كويز", callback_data="create_upload_menu")])
    return InlineKeyboardMarkup(kb)


def main_menu_text(user_id: int = None) -> str:
    due = db.get_due_quiz_reviews(user_id=user_id) if user_id else []
    weak = db.get_due_weak_questions(user_id=user_id) if user_id else []
    lines = ["🧠 <b>ذاكرة القدرات</b>\n"]
    if due:
        lines.append(f"🔔 لديك <b>{len(due)}</b> مراجعة مستحقة اليوم")
    if weak:
        lines.append(f"❓ لديك <b>{len(weak)}</b> سؤال ضعيف مستحق")
    if not due and not weak:
        lines.append("✅ لا توجد مراجعات مستحقة اليوم — أحسنت!")
    lines.append("\nاختر ما تريد 👇")
    return "\n".join(lines)


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return
    text = main_menu_text(user_id)
    kb = main_menu_keyboard(user_id)
    if update.message:
        from utils import clean_entire_chat
        extra = [update.message.message_id]
        await clean_entire_chat(context, chat_id, extra_ids=extra)
        await send_clean_message(context, chat_id, text, reply_markup=kb)
    elif update.callback_query:
        await safe_edit(update.callback_query, text, kb)


# ═══════════════════════════════════════════════════════════════
#  تصفح المجلدات والكويزات
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
#  تفاصيل الكويز
# ═══════════════════════════════════════════════════════════════

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
        kb.append([InlineKeyboardButton("▶️ ابدأ الكويز", callback_data=f"start_quiz_{quiz_id}")])

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

    if is_admin(user_id):
        kb.append([InlineKeyboardButton("🔄 تحديث (JSON)", callback_data=f"reupload_json_{quiz_id}"),
                   InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"rename_quiz_{quiz_id}")])
        kb.append([InlineKeyboardButton("⚙️ ضبط المراجعة", callback_data=f"fixstage_menu_{quiz_id}"),
                   InlineKeyboardButton("🗑️ حذف", callback_data=f"delete_quiz_{quiz_id}")])

    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_cb)])
    return text, InlineKeyboardMarkup(kb)


# ═══════════════════════════════════════════════════════════════
#  مراجعات اليوم
# ═══════════════════════════════════════════════════════════════

def _build_due_reviews(user_id):
    reviews = db.get_due_quiz_reviews(user_id=user_id)
    if not reviews:
        return ("✅ <b>لا توجد مراجعات مستحقة اليوم</b>\n\nأحسنت! جدولك نظيف 🌟",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))
    text = f"🔔 <b>مراجعات اليوم</b> — {len(reviews)} مراجعة مستحقة\n\n"
    kb = []
    for r in reviews:
        name = r.get("quiz_name", "كويز")[:30] + ("..." if len(r.get("quiz_name", "")) > 30 else "")
        d = days_until(r["next_review_date"])
        lbl = stage_label(r.get("stage", 0))
        timing = "🔴 الآن" if d <= 0 else f"🟡 بعد {d} يوم"
        kb.append([InlineKeyboardButton(f"▶️ {name} ({lbl}) — {timing}",
                                        callback_data=f"start_review_{r['quiz_id']}_{r['id']}")])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(kb)


# ═══════════════════════════════════════════════════════════════
#  جدول المراجعة
# ═══════════════════════════════════════════════════════════════

def _build_review_schedule(user_id, page=1):
    all_reviews = db.get_all_quiz_reviews(user_id=user_id)
    if not all_reviews:
        return ("📅 <b>جدول المراجعة</b>\n\nلم تضف أي كويز لجدول مراجعاتك بعد.",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))

    all_reviews.sort(key=lambda r: r.get("next_review_date", "9999"))
    total = len(all_reviews)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE

    text = f"📅 <b>جدول المراجعة</b> — {total} كويز مجدول\n\n"
    kb = []
    for r in all_reviews[start:start + ITEMS_PER_PAGE]:
        name = r.get("quiz_name", "كويز")
        if len(name) > 28:
            name = name[:25] + "..."
        d = days_until(r["next_review_date"])
        lbl = stage_label(r.get("stage", 0))
        timing = "🔴 مستحق" if d <= 0 else "🟡 غداً" if d == 1 else f"⏳ بعد {d} يوم"
        kb.append([InlineKeyboardButton(f"{timing} | {name} ({lbl})",
                                        callback_data=f"quiz_detail_{r['quiz_id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"schedule_page_{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"schedule_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(kb)


# ═══════════════════════════════════════════════════════════════
#  الأسئلة الضعيفة
# ═══════════════════════════════════════════════════════════════

def _build_weak_questions(user_id, page=1):
    all_weak = db.get_all_weak_questions(user_id=user_id)
    due_weak = db.get_due_weak_questions(user_id=user_id)
    if not all_weak:
        return ("❓ <b>الأسئلة الضعيفة</b>\n\nلا توجد أسئلة ضعيفة! أداؤك ممتاز 🌟",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))

    text = (f"❓ <b>الأسئلة الضعيفة</b>\n\n"
            f"📊 الإجمالي: <b>{len(all_weak)}</b> | مستحق اليوم: <b>{len(due_weak)}</b>\n\n"
            "اختر كويزاً لمراجعة أسئلته الضعيفة:")

    quiz_map = {}
    for w in all_weak:
        qid = w["quiz_id"]
        if qid not in quiz_map:
            quiz_map[qid] = {"name": w.get("quiz_name", "كويز"), "count": 0, "due": 0}
        quiz_map[qid]["count"] += 1
    for w in due_weak:
        if w["quiz_id"] in quiz_map:
            quiz_map[w["quiz_id"]]["due"] += 1

    quiz_list = list(quiz_map.items())
    total = len(quiz_list)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE

    kb = []
    if due_weak:
        kb.append([InlineKeyboardButton(f"🔴 راجع جميع المستحق ({len(due_weak)} سؤال)",
                                        callback_data="start_weakall")])
    for qid, info in quiz_list[start:start + ITEMS_PER_PAGE]:
        name = info["name"][:25] + ("..." if len(info["name"]) > 25 else "")
        due_lbl = f" 🔴{info['due']}" if info["due"] else ""
        kb.append([InlineKeyboardButton(f"❓ {name} ({info['count']}){due_lbl}",
                                        callback_data=f"start_weak_{qid}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"weak_page_{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"weak_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(kb)


# ═══════════════════════════════════════════════════════════════
#  الإعدادات
# ═══════════════════════════════════════════════════════════════

def _build_settings(user_id):
    user_row = db.get_user(user_id)
    h = user_row.get("reminder_hour", 4) if user_row else 4
    m = user_row.get("reminder_minute", 30) if user_row else 30
    period = "ص" if h < 12 else "م"
    dh = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    text = (f"⚙️ <b>الإعدادات</b>\n\n"
            f"⏰ وقت التذكير اليومي: <b>{dh}:{m:02d} {period}</b>\n\n"
            "اختر وقتاً جديداً:")
    kb = [
        [InlineKeyboardButton("6:00 ص", callback_data="set_reminder_6_0"),
         InlineKeyboardButton("7:00 ص", callback_data="set_reminder_7_0"),
         InlineKeyboardButton("8:00 ص", callback_data="set_reminder_8_0")],
        [InlineKeyboardButton("9:00 م", callback_data="set_reminder_21_0"),
         InlineKeyboardButton("10:00 م", callback_data="set_reminder_22_0"),
         InlineKeyboardButton("11:00 م", callback_data="set_reminder_23_0")],
        [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
    ]
    return text, InlineKeyboardMarkup(kb)


# ═══════════════════════════════════════════════════════════════
#  لوحة الأدمن
# ═══════════════════════════════════════════════════════════════

def _build_create_menu():
    text = "➕ <b>إنشاء / رفع كويز</b>\n\nاختر طريقة الإضافة:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 رفع ملف JSON", callback_data="upload_json")],
        [InlineKeyboardButton("🔗 إضافة رابط", callback_data="upload_url")],
        [InlineKeyboardButton("📁 إدارة المجلدات", callback_data="admin_cat_0")],
        [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
    ])
    return text, kb


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


# ═══════════════════════════════════════════════════════════════
#  Fixstage
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
#  أوامر نصية
# ═══════════════════════════════════════════════════════════════

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    text, kb = _build_due_reviews(user_id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def weak_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    text, kb = _build_weak_questions(user_id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    text, kb = _build_review_schedule(user_id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    all_reviews = db.get_all_quiz_reviews(user_id=user_id)
    all_weak = db.get_all_weak_questions(user_id=user_id)
    due_reviews = db.get_due_quiz_reviews(user_id=user_id)
    due_weak = db.get_due_weak_questions(user_id=user_id)
    text = (f"📊 <b>إحصائياتك</b>\n\n"
            f"📅 الكويزات المجدولة: <b>{len(all_reviews)}</b>\n"
            f"🔔 مراجعات مستحقة اليوم: <b>{len(due_reviews)}</b>\n"
            f"❓ أسئلة ضعيفة (الإجمالي): <b>{len(all_weak)}</b>\n"
            f"⚠️ أسئلة ضعيفة مستحقة: <b>{len(due_weak)}</b>\n")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💡 <b>شرح نظام التكرار المتباعد</b>\n\n"
        "بعد حل أي كويز، اضغط <b>«أضف لمراجعاتي»</b> فيُذكّرك البوت في مواعيد:\n"
        "• بعد يوم → بعد 3 أيام → بعد 7 أيام → بعد 14 يوم → بعد 30 يوم\n\n"
        "<b>الأسئلة الضعيفة:</b> كل سؤال تخطئ فيه يُحفظ تلقائياً.\n\n"
        "<b>النصيحة:</b> راجع يومياً في نفس الوقت 🧠"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    args = context.args or []
    query_text = " ".join(args).strip()
    if not query_text:
        text = "🔍 <b>البحث</b>\n\nأرسل: <code>/find اسم الكويز</code>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)
        return
    all_quizzes = db.get_all_public_quizzes()
    norm = normalize_arabic_digits(query_text.lower())
    results = [q for q in all_quizzes if norm in normalize_arabic_digits(q.get("name", "").lower())]
    if not results:
        text = f"🔍 لا توجد نتائج لـ «{html.escape(query_text)}»"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
    else:
        text = f"🔍 «{html.escape(query_text)}» — {len(results)} كويز:"
        kb_rows = [[InlineKeyboardButton(f"📝 {q['name']}", callback_data=f"quiz_detail_{q['id']}")] for q in results[:15]]
        kb_rows.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
        kb = InlineKeyboardMarkup(kb_rows)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


# ═══════════════════════════════════════════════════════════════
#  معالج النصوص
# ═══════════════════════════════════════════════════════════════

async def url_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    msg = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    is_adm = is_admin(user_id)

    if is_adm and context.user_data.get("waiting_for_json_update"):
        quiz_update_id = context.user_data.pop("waiting_for_json_update")
        try:
            data = json.loads(msg)
            from handlers.pdf_handler import _validate_json_upload, process_json_quiz_data
            _validate_json_upload(None, data)
            await process_json_quiz_data(data, user, context, chat_id, update, quiz_update_id=quiz_update_id)
        except Exception as e:
            await send_clean_message(context, chat_id, f"❌ خطأ: {html.escape(str(e))}", update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]))
        return

    if is_adm and context.user_data.get("waiting_for_json_new"):
        context.user_data.pop("waiting_for_json_new", None)
        try:
            data = json.loads(msg)
            from handlers.pdf_handler import _validate_json_upload, process_json_quiz_data
            _validate_json_upload(None, data)
            await process_json_quiz_data(data, user, context, chat_id, update)
        except Exception as e:
            await send_clean_message(context, chat_id, f"❌ خطأ: {html.escape(str(e))}", update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]))
        return

    if is_adm and context.user_data.get("waiting_for_url_quiz"):
        context.user_data.pop("waiting_for_url_quiz", None)
        if not msg.startswith("http"):
            await send_clean_message(context, chat_id, "❌ الرابط غير صحيح.", update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")]]))
            return
        quiz_id = db.save_quiz_without_review("كويز رابط", [], owner_id=user_id, is_public=1, url=msg)
        db.schedule_first_review(quiz_id, user_id=user_id, start_today=True)
        await send_clean_message(context, chat_id, f"✅ تم حفظ الرابط!\n🔗 <code>{html.escape(msg)}</code>",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))
        return

    if is_adm and context.user_data.get("waiting_for_quiz_rename"):
        quiz_id = context.user_data.pop("waiting_for_quiz_rename")
        db.rename_quiz(quiz_id, msg)
        await send_clean_message(context, chat_id, f"✅ تم تغيير الاسم إلى: <b>{html.escape(msg)}</b>",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 الكويز", callback_data=f"quiz_detail_{quiz_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_new_folder") is not None:
        parent_id = context.user_data.pop("waiting_for_new_folder")
        real_parent = parent_id if parent_id != 0 else None
        db.create_category(msg, parent_id=real_parent, is_public=1, owner_id=user_id)
        await send_clean_message(context, chat_id, f"✅ تم إنشاء مجلد: <b>{html.escape(msg)}</b>",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📁 إدارة المجلدات", callback_data=f"admin_cat_{parent_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_cat_rename"):
        cat_id = context.user_data.pop("waiting_for_cat_rename")
        db.rename_category(cat_id, msg)
        await send_clean_message(context, chat_id, f"✅ تم تغيير اسم المجلد إلى: <b>{html.escape(msg)}</b>",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📁 المجلد", callback_data=f"admin_cat_{cat_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_fixdate_custom") is not None:
        quiz_id = context.user_data.pop("waiting_for_fixdate_custom")
        raw = normalize_arabic_digits(msg.strip())
        parsed_date = None
        day_label = ""
        if re.match(r"^[+-]?\d+$", raw):
            days_offset = int(raw)
            if days_offset <= 0:
                parsed_date = (date.today() - timedelta(days=1)).isoformat()
                day_label = "الآن فوراً 🔴"
            else:
                parsed_date = (date.today() + timedelta(days=days_offset)).isoformat()
                day_label = f"بعد {days_offset} يوم"
        else:
            for pattern, order in [(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", "ymd"),
                                    (r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", "dmy")]:
                m = re.match(pattern, raw)
                if m:
                    try:
                        td = (date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if order == "ymd"
                              else date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
                        parsed_date = td.isoformat()
                        d = (td - date.today()).days
                        day_label = "الآن فوراً 🔴" if d <= 0 else ("غداً 🟡" if d == 1 else f"بعد {d} يوم")
                    except ValueError:
                        pass
                    break

        last_fpage = context.user_data.get("last_fixstage_page", 1)
        if parsed_date:
            conn = db.get_connection()
            conn.execute("UPDATE quiz_reviews SET next_review_date = ? WHERE quiz_id = ?", (parsed_date, quiz_id))
            conn.commit()
            conn.close()
            quiz = db.get_quiz(quiz_id)
            q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
            await send_clean_message(context, chat_id,
                                     f"✅ تم تحديد موعد مراجعة <b>{q_name}</b> إلى {parsed_date} ({day_label})",
                                     update=update,
                                     reply_markup=InlineKeyboardMarkup([
                                         [InlineKeyboardButton(f"🔙 صفحة {last_fpage}", callback_data=f"fixstage_page_{last_fpage}")],
                                         [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
                                     ]))
        else:
            context.user_data["waiting_for_fixdate_custom"] = quiz_id
            await send_clean_message(context, chat_id,
                                     "❌ صيغة غير صحيحة! أرسل كـ <code>2026-09-15</code> أو عدد أيام كـ <code>7</code>",
                                     update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"fixstage_menu_{quiz_id}")]]))
        return

    from handlers.admin_handler import handle_broadcast_input
    await handle_broadcast_input(update, context)


# ═══════════════════════════════════════════════════════════════
#  معالج الأزرار الرئيسي
# ═══════════════════════════════════════════════════════════════

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    is_adm = is_admin(user_id)

    if data == "noop":
        return

    if data == "main_menu":
        await safe_edit(query, main_menu_text(user_id), main_menu_keyboard(user_id))
        return

    if data == "browse_root":
        context.user_data["last_browse_cb"] = "browse_root"
        text, kb = _build_browse_view(cat_id=None, page=1, user_id=user_id)
        await safe_edit(query, text, kb)
        return

    if data.startswith("browse_cat_"):
        parts = data.split("_")
        raw_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 1
        cat_id = raw_id if raw_id != 0 else None
        context.user_data["last_browse_cb"] = data
        text, kb = _build_browse_view(cat_id=cat_id, page=page, user_id=user_id)
        await safe_edit(query, text, kb)
        return

    if data.startswith("quiz_detail_"):
        quiz_id = int(data.split("_")[-1])
        back_cb = context.user_data.get("last_browse_cb", "browse_root")
        text, kb = _build_quiz_detail(quiz_id, user_id, back_cb=back_cb)
        await safe_edit(query, text, kb)
        return

    if data.startswith("start_quiz_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="quiz")
        return

    if data.startswith("start_review_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        review_id = int(parts[3])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="review", review_id=review_id)
        return

    if data.startswith("start_weak_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="weak")
        return

    if data == "start_weakall":
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, 0, session_type="weakall")
        return

    if data == "resume_quiz":
        from handlers.quiz_handler import show_next_question
        await show_next_question(update, context)
        return

    if data.startswith("add_to_schedule_"):
        quiz_id = int(data.split("_")[-1])
        db.schedule_first_review(quiz_id, user_id=user_id, start_today=False)
        quiz = db.get_quiz(quiz_id)
        name = html.escape(quiz["name"]) if quiz else "الكويز"
        await safe_edit(query,
                        f"✅ تم إضافة <b>{name}</b> لجدول مراجعاتك!\n\n📅 ستظهر أول مراجعة غداً.",
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")],
                            [InlineKeyboardButton("🔙 رجوع للكويز", callback_data=f"quiz_detail_{quiz_id}")],
                        ]))
        return

    if data == "due_reviews":
        text, kb = _build_due_reviews(user_id)
        await safe_edit(query, text, kb)
        return

    if data == "review_schedule":
        text, kb = _build_review_schedule(user_id)
        await safe_edit(query, text, kb)
        return

    if data.startswith("schedule_page_"):
        page = int(data.split("_")[-1])
        text, kb = _build_review_schedule(user_id, page=page)
        await safe_edit(query, text, kb)
        return

    if data == "weak_questions" or data.startswith("weak_page_"):
        page = int(data.split("_")[-1]) if data.startswith("weak_page_") else 1
        text, kb = _build_weak_questions(user_id, page=page)
        await safe_edit(query, text, kb)
        return

    if data == "settings_menu":
        text, kb = _build_settings(user_id)
        await safe_edit(query, text, kb)
        return

    if data.startswith("set_reminder_"):
        parts = data.split("_")
        h, m = int(parts[2]), int(parts[3])
        db.update_user_reminder(user_id, h, m)
        chat_id = update.effective_chat.id
        try:
            from bot import schedule_reminder
            schedule_reminder(context.job_queue, chat_id, hour=h, minute=m)
        except Exception:
            pass
        period = "ص" if h < 12 else "م"
        dh = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
        await safe_edit(query, f"✅ تم تعيين وقت التذكير: <b>{dh}:00 {period}</b>",
                        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))
        return

    if data == "create_upload_menu":
        if not is_adm:
            await query.answer("❌ للمشرف فقط.", show_alert=True)
            return
        text, kb = _build_create_menu()
        await safe_edit(query, text, kb)
        return

    if data == "upload_json":
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        context.user_data["waiting_for_json_new"] = True
        await safe_edit(query, "📋 أرسل ملف .json أو الصق نص الـ JSON:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="create_upload_menu")]]))
        return

    if data == "upload_url":
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        context.user_data["waiting_for_url_quiz"] = True
        await safe_edit(query, "🔗 أرسل الآن رابط الكويز:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="create_upload_menu")]]))
        return

    if data.startswith("admin_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        cat_id = int(data.split("_")[-1])
        text, kb = _build_admin_cat_panel(cat_id)
        await safe_edit(query, text, kb)
        return

    if data.startswith("admin_new_folder_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        parent_id = int(data.split("_")[-1])
        context.user_data["waiting_for_new_folder"] = parent_id
        await safe_edit(query, "📁 أرسل اسم المجلد الجديد:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cat_{parent_id}")]]))
        return

    if data.startswith("admin_rename_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        cat_id = int(data.split("_")[-1])
        context.user_data["waiting_for_cat_rename"] = cat_id
        await safe_edit(query, "✏️ أرسل الاسم الجديد للمجلد:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cat_{cat_id}")]]))
        return

    if data.startswith("admin_del_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        cat_id = int(data.split("_")[-1])
        cat = db.get_category(cat_id)
        if cat:
            await safe_edit(query, f"🗑️ تأكيد حذف المجلد: <b>{html.escape(cat['name'])}</b>?",
                            InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_del_cat_{cat_id}")],
                                [InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cat_{cat_id}")],
                            ]))
        return

    if data.startswith("confirm_del_cat_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        cat_id = int(data.split("_")[-1])
        cat = db.get_category(cat_id)
        parent_id = cat.get("parent_id") if cat else None
        db.delete_category(cat_id)
        await safe_edit(query, "✅ تم حذف المجلد.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"admin_cat_{parent_id or 0}")]]))
        return

    if data.startswith("reupload_json_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        context.user_data["waiting_for_json_update"] = quiz_id
        await safe_edit(query, f"🔄 <b>تحديث: {name}</b>\n\nأرسل ملف .json أو الصق النص:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_detail_{quiz_id}")]]))
        return

    if data.startswith("rename_quiz_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        quiz_id = int(data.split("_")[-1])
        context.user_data["waiting_for_quiz_rename"] = quiz_id
        await safe_edit(query, "✏️ أرسل الاسم الجديد للكويز:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_detail_{quiz_id}")]]))
        return

    if data.startswith("delete_quiz_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        if quiz:
            await safe_edit(query, f"🗑️ تأكيد حذف: <b>{html.escape(quiz['name'])}</b>?",
                            InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_del_quiz_{quiz_id}")],
                                [InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_detail_{quiz_id}")],
                            ]))
        return

    if data.startswith("confirm_del_quiz_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        quiz_id = int(data.split("_")[-1])
        db.delete_quiz(quiz_id)
        await safe_edit(query, "✅ تم حذف الكويز.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الكويزات", callback_data="browse_root")]]))
        return

    if data.startswith("admin_"):
        from handlers.admin_handler import admin_button_handler
        await admin_button_handler(update, context)
        return

    if data.startswith("fixstage_page_"):
        page = int(data.split("_")[-1])
        await fixstage_command(update, context, page)
        return

    if data.startswith("fixstage_menu_") or data.startswith("fixstage_set_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        if data.startswith("fixstage_set_"):
            parts = data.split("_")
            quiz_id = int(parts[2])
            new_stage = max(0, min(int(parts[3]), 4))
            conn = db.get_connection()
            conn.execute("UPDATE quiz_reviews SET stage = ? WHERE quiz_id = ?", (new_stage, quiz_id))
            conn.commit()
            conn.close()
        else:
            quiz_id = int(data.split("_")[-1])

        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await safe_edit(query, "❌ الكويز غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]))
            return

        conn = db.get_connection()
        review = conn.execute("SELECT * FROM quiz_reviews WHERE quiz_id = ?", (quiz_id,)).fetchone()
        conn.close()

        q_name = html.escape(quiz.get("name", "كويز"))
        last_fpage = context.user_data.get("last_fixstage_page", 1)

        if not review:
            await safe_edit(query, f"✅ كويز <b>{q_name}</b> اكتملت مراجعاته.",
                            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_page_{last_fpage}")]]))
            return

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
        return

    if data.startswith("fixstage_done_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])
        db.advance_quiz_review(review_id)
        last_fpage = context.user_data.get("last_fixstage_page", 1)
        await safe_edit(query, "✅ تم تسجيل المراجعة كمكتملة.",
                        InlineKeyboardMarkup([[InlineKeyboardButton(f"🔙 صفحة {last_fpage}", callback_data=f"fixstage_page_{last_fpage}")]]))
        return

    if data.startswith("fixdate_custom_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        quiz_id = int(data.split("_")[-1])
        context.user_data["waiting_for_fixdate_custom"] = quiz_id
        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        await safe_edit(query,
                        f"📅 <b>تاريخ مخصص</b> — {q_name}\n\nأرسل كـ <code>2026-09-30</code> أو عدد أيام كـ <code>7</code>",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"fixstage_menu_{quiz_id}")]]))
        return

    if data.startswith("fixdate_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        parts = data.split("_")
        quiz_id = int(parts[1])
        days_offset = int(parts[2])
        if days_offset == 0:
            new_date = (date.today() - timedelta(days=1)).isoformat()
            day_label = "الآن فوراً 🔴"
        else:
            new_date = (date.today() + timedelta(days=days_offset)).isoformat()
            day_label = "غداً 🟡" if days_offset == 1 else f"بعد {days_offset} يوم"
        conn = db.get_connection()
        conn.execute("UPDATE quiz_reviews SET next_review_date = ? WHERE quiz_id = ?", (new_date, quiz_id))
        conn.commit()
        conn.close()
        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        last_fpage = context.user_data.get("last_fixstage_page", 1)
        await safe_edit(query, f"✅ تم تعديل موعد <b>{q_name}</b>\n📅 {new_date} ({day_label})",
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("⚙️ إعدادات الكويز", callback_data=f"fixstage_menu_{quiz_id}")],
                            [InlineKeyboardButton(f"🔙 صفحة {last_fpage}", callback_data=f"fixstage_page_{last_fpage}")],
                        ]))
        return

    if data.startswith("fixstage_qlist_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        parts = data.split("_")
        quiz_id = int(parts[2])
        pg = int(parts[3]) if len(parts) > 3 else 0
        questions = db.get_questions(quiz_id)
        if not questions:
            await safe_edit(query, "❌ لا توجد أسئلة.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_menu_{quiz_id}")]]))
            return
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
        return

    if data.startswith("fixstage_qedit_"):
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        q = db.get_question(q_id)
        if not q:
            await safe_edit(query, "❌ السؤال غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_qlist_{quiz_id}_0")]]))
            return
        options = q.get("options") or []
        opts_text = "\n".join(f"• {opt}" for opt in options)
        text = (f"✏️ <b>تفاصيل السؤال</b>\n\n❓ {html.escape(q['question_text'])}\n\n"
                f"الخيارات:\n{html.escape(opts_text)}\n\n"
                f"✅ الإجابة: <b>{html.escape(q['correct_answer'])}</b>\n"
                f"💡 الشرح: {html.escape(q.get('explanation', '') or '—')}")
        await safe_edit(query, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 قائمة الأسئلة", callback_data=f"fixstage_qlist_{quiz_id}_0")]]))
        return

    logger.warning("Unhandled callback: %s from user %s", data, user_id)
