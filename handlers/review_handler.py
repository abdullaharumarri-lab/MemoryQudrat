"""
handlers/review_handler.py — مراجعات اليوم وجدول التكرار المتباعد
"""
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import ADMIN_USER_ID
from spaced_repetition import days_until, stage_label
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 10


def _build_due_reviews(user_id, category_filter=None, page=1):
    reviews = db.get_due_quiz_reviews(user_id=user_id)
    if not reviews:
        return ("✅ <b>لا توجد مراجعات مستحقة اليوم</b>\n\nأحسنت! جدولك نظيف 🌟",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))

    cats = {c["id"]: c["name"] for c in db.get_categories(is_public=1)}

    cat_map = {}
    for r in reviews:
        cid = r.get("category_id")
        if cid not in cat_map:
            cname = cats.get(cid, "عام / بدون مجلد" if not cid else f"مجلد {cid}")
            cat_map[cid] = {"name": cname, "reviews": []}
        cat_map[cid]["reviews"].append(r)

    # If there are multiple folders and user didn't pick one yet, show folder selection
    if len(cat_map) > 1 and category_filter is None:
        text = (f"🔔 <b>مراجعات اليوم</b> — <b>{len(reviews)}</b> مراجعة مستحقة\n\n"
                f"الكويزات المستحقة موزعة على <b>{len(cat_map)}</b> مجلدات.\n"
                f"اختر المجلد الذي ترغب بمراجعته:")
        kb = []
        for cid, info in cat_map.items():
            c_key = cid if cid is not None else 0
            kb.append([InlineKeyboardButton(f"📁 {info['name']} ({len(info['reviews'])})",
                                            callback_data=f"due_cat_{c_key}")])
        kb.append([InlineKeyboardButton(f"🌐 عرض الكل ({len(reviews)})", callback_data="due_cat_all")])
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
        return text, InlineKeyboardMarkup(kb)

    # If category_filter is selected or only 1 folder exists
    if category_filter is not None and category_filter != "all":
        target_cid = None if category_filter == 0 else category_filter
        folder_info = cat_map.get(target_cid)
        filtered_reviews = folder_info["reviews"] if folder_info else []
        folder_title = f"📁 {folder_info['name']}" if folder_info else "المجلد"
    else:
        filtered_reviews = reviews
        folder_title = "🌐 جميع المراجعات المستحقة"

    if not filtered_reviews:
        text = "✅ <b>لا توجد مراجعات مستحقة في هذا المجلد اليوم!</b>"
        kb = []
        if len(cat_map) > 1:
            kb.append([InlineKeyboardButton("📂 تصفية حسب المجلدات", callback_data="due_reviews")])
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
        return text, InlineKeyboardMarkup(kb)

    total = len(filtered_reviews)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE

    text = f"🔔 <b>مراجعات اليوم — {folder_title}</b>\n📊 <b>{total}</b> مراجعة مستحقة\n\n"
    kb = []
    for r in filtered_reviews[start:start + ITEMS_PER_PAGE]:
        name = r.get("quiz_name", "كويز")
        if len(name) > 28:
            name = name[:25] + "..."
        lbl = stage_label(r.get("stage", 0))
        is_topic = (r.get("item_type") == "topic")
        is_quizbot = (r.get("item_type") == "quiz_bot" or ("t.me/QuizBot" in str(r.get("url") or "")))
        if is_topic:
            icon = "📖"
            callback = f"view_topic_review_{r['quiz_id']}_{r['id']}"
        elif is_quizbot:
            icon = "🎲"
            callback = f"view_quizbot_review_{r['quiz_id']}_{r['id']}"
        else:
            icon = "📝"
            callback = f"start_review_{r['quiz_id']}_{r['id']}"
        kb.append([InlineKeyboardButton(f"{icon} {name} ({lbl})", callback_data=callback)])

    nav = []
    c_param = "all" if category_filter == "all" else (category_filter if category_filter is not None else "0")
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"due_page_{c_param}_{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"due_page_{c_param}_{page+1}"))
    if nav:
        kb.append(nav)

    if len(cat_map) > 1:
        kb.append([InlineKeyboardButton("📂 تصفية حسب المجلدات", callback_data="due_reviews")])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(kb)


def _build_review_schedule(user_id, category_filter=None, page=1):
    all_reviews = db.get_all_quiz_reviews(user_id=user_id)
    if not all_reviews:
        return ("📅 <b>جدول المراجعة</b>\n\nلم تضف أي كويز لجدول مراجعاتك بعد.",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))

    cats = {c["id"]: c["name"] for c in db.get_categories(is_public=1)}

    cat_map = {}
    for r in all_reviews:
        cid = r.get("category_id")
        if cid not in cat_map:
            cname = cats.get(cid, "عام / بدون مجلد" if not cid else f"مجلد {cid}")
            cat_map[cid] = {"name": cname, "reviews": []}
        cat_map[cid]["reviews"].append(r)

    # If multiple folders and no category filter selected, show folder list
    if len(cat_map) > 1 and category_filter is None:
        text = (f"📅 <b>جدول المراجعة</b> — <b>{len(all_reviews)}</b> كويز مجدول\n\n"
                f"الكويزات موزعة على <b>{len(cat_map)}</b> مجلدات.\n"
                f"اختر المجلد لعرض جدوله الزمني:")
        kb = []
        for cid, info in cat_map.items():
            c_key = cid if cid is not None else 0
            kb.append([InlineKeyboardButton(f"📁 {info['name']} ({len(info['reviews'])})",
                                            callback_data=f"sched_cat_{c_key}")])
        kb.append([InlineKeyboardButton(f"🌐 عرض الكل ({len(all_reviews)})", callback_data="sched_cat_all")])
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
        return text, InlineKeyboardMarkup(kb)

    if category_filter is not None and category_filter != "all":
        target_cid = None if category_filter == 0 else category_filter
        folder_info = cat_map.get(target_cid)
        filtered_reviews = folder_info["reviews"] if folder_info else []
        folder_title = f"📁 {folder_info['name']}" if folder_info else "المجلد"
    else:
        filtered_reviews = list(all_reviews)
        folder_title = "🌐 جميع الكويزات المجدولة"

    filtered_reviews.sort(key=lambda r: r.get("next_review_date", "9999"))
    total = len(filtered_reviews)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE

    text = f"📅 <b>جدول المراجعة — {folder_title}</b>\n📊 <b>{total}</b> كويز مجدول\n\n"
    kb = []
    for r in filtered_reviews[start:start + ITEMS_PER_PAGE]:
        name = r.get("quiz_name", "كويز")
        if len(name) > 28:
            name = name[:25] + "..."
        d = days_until(r["next_review_date"])
        lbl = stage_label(r.get("stage", 0))
        timing = "🔴 مستحق" if d <= 0 else "🟡 غداً" if d == 1 else f"⏳ بعد {d} يوم"
        is_topic = (r.get("item_type") == "topic")
        is_quizbot = (r.get("item_type") == "quiz_bot" or ("t.me/QuizBot" in str(r.get("url") or "")))
        if is_topic:
            icon = "📖"
            callback = f"view_topic_review_{r['quiz_id']}_{r['id']}"
        elif is_quizbot:
            icon = "🎲"
            callback = f"view_quizbot_review_{r['quiz_id']}_{r['id']}"
        else:
            icon = "📝"
            callback = f"quiz_detail_{r['quiz_id']}"
        kb.append([InlineKeyboardButton(f"{timing} | {icon} {name} ({lbl})", callback_data=callback)])

    nav = []
    c_param = "all" if category_filter == "all" else (category_filter if category_filter is not None else "0")
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"sched_page_{c_param}_{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"sched_page_{c_param}_{page+1}"))
    if nav:
        kb.append(nav)

    if len(cat_map) > 1:
        kb.append([InlineKeyboardButton("📂 تصفية حسب المجلدات", callback_data="review_schedule")])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(kb)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    text, kb = _build_due_reviews(user_id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    text, kb = _build_review_schedule(user_id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def handle_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int) -> bool:
    """
    Handles callbacks related to due reviews and schedule views.
    Returns True if handled, False otherwise.
    """
    query = update.callback_query

    if data.startswith("start_review_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        review_id = int(parts[3])
        quiz = db.get_quiz(quiz_id)
        if quiz and (quiz.get("item_type") == "quiz_bot" or (quiz.get("url") and not db.get_questions(quiz_id))):
            from handlers.quizbot_handler import handle_quizbot_callback
            query.data = f"view_quizbot_review_{quiz_id}_{review_id}"
            await handle_quizbot_callback(update, context)
            return True
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="review", review_id=review_id)
        return True

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
        return True

    if data == "due_reviews":
        text, kb = _build_due_reviews(user_id)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("due_cat_"):
        cat_val = data.split("_")[-1]
        cat_filter = "all" if cat_val == "all" else int(cat_val)
        text, kb = _build_due_reviews(user_id, category_filter=cat_filter, page=1)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("due_page_"):
        parts = data.split("_")
        cat_val = parts[2]
        page = int(parts[3])
        cat_filter = "all" if cat_val == "all" else int(cat_val)
        text, kb = _build_due_reviews(user_id, category_filter=cat_filter, page=page)
        await safe_edit(query, text, kb)
        return True

    if data == "review_schedule":
        text, kb = _build_review_schedule(user_id)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("sched_cat_"):
        cat_val = data.split("_")[-1]
        cat_filter = "all" if cat_val == "all" else int(cat_val)
        text, kb = _build_review_schedule(user_id, category_filter=cat_filter, page=1)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("sched_page_"):
        parts = data.split("_")
        cat_val = parts[2]
        page = int(parts[3])
        cat_filter = "all" if cat_val == "all" else int(cat_val)
        text, kb = _build_review_schedule(user_id, category_filter=cat_filter, page=page)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("schedule_page_"):
        page = int(data.split("_")[-1])
        text, kb = _build_review_schedule(user_id, category_filter="all", page=page)
        await safe_edit(query, text, kb)
        return True

    return False
