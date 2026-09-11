"""
handlers/weak_handler.py — بنك الأسئلة الضعيفة
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import ADMIN_USER_ID
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 10


def _build_weak_questions(user_id, category_filter=None, page=1):
    all_weak = db.get_all_weak_questions(user_id=user_id)
    due_weak = db.get_due_weak_questions(user_id=user_id)
    if not all_weak:
        return ("❓ <b>الأسئلة الضعيفة</b>\n\nلا توجد أسئلة ضعيفة! أداؤك ممتاز 🌟",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))

    cats = {c["id"]: c["name"] for c in db.get_categories(is_public=1)}

    cat_map = {}
    for w in all_weak:
        cid = w.get("category_id")
        if cid not in cat_map:
            cname = cats.get(cid, "عام / بدون مجلد" if not cid else f"مجلد {cid}")
            cat_map[cid] = {"name": cname, "items": [], "due_count": 0}
        cat_map[cid]["items"].append(w)
    
    for w in due_weak:
        cid = w.get("category_id")
        if cid in cat_map:
            cat_map[cid]["due_count"] += 1

    # If multiple folders and no category filter selected, show folder list
    if len(cat_map) > 1 and category_filter is None:
        text = (f"❓ <b>الأسئلة الضعيفة حسب المجلدات</b>\n\n"
                f"📊 الإجمالي: <b>{len(all_weak)}</b> | مستحق اليوم: <b>{len(due_weak)}</b>\n\n"
                f"لديك أخطاء موزعة على <b>{len(cat_map)}</b> مجلدات.\n"
                f"اختر المجلد لمراجعة أسئلته الضعيفة:")
        kb = []
        if due_weak:
            kb.append([InlineKeyboardButton(f"🔴 راجع جميع المستحق ({len(due_weak)} سؤال)",
                                            callback_data="start_weakall")])
        for cid, info in cat_map.items():
            c_key = cid if cid is not None else 0
            due_lbl = f" 🔴{info['due_count']}" if info['due_count'] > 0 else ""
            kb.append([InlineKeyboardButton(f"📁 {info['name']} ({len(info['items'])}){due_lbl}",
                                            callback_data=f"weak_cat_{c_key}")])
        kb.append([InlineKeyboardButton(f"🌐 عرض كل الكويزات ({len(all_weak)})", callback_data="weak_cat_all")])
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
        return text, InlineKeyboardMarkup(kb)

    if category_filter is not None and category_filter != "all":
        target_cid = None if category_filter == 0 else category_filter
        filtered_weak = [w for w in all_weak if w.get("category_id") == target_cid]
        filtered_due = [w for w in due_weak if w.get("category_id") == target_cid]
        cname = cats.get(target_cid, "عام / بدون مجلد" if not target_cid else f"مجلد {target_cid}")
        folder_title = f"📁 {cname}"
    else:
        filtered_weak = all_weak
        filtered_due = due_weak
        folder_title = "🌐 جميع الأسئلة الضعيفة"

    quiz_map = {}
    for w in filtered_weak:
        qid = w["quiz_id"]
        if qid not in quiz_map:
            quiz_map[qid] = {"name": w.get("quiz_name", "كويز"), "count": 0, "due": 0}
        quiz_map[qid]["count"] += 1
    for w in filtered_due:
        if w["quiz_id"] in quiz_map:
            quiz_map[w["quiz_id"]]["due"] += 1

    quiz_list = list(quiz_map.items())
    total = len(quiz_list)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE

    text = (f"❓ <b>الأسئلة الضعيفة — {folder_title}</b>\n\n"
            f"📊 الإجمالي: <b>{len(filtered_weak)}</b> | مستحق اليوم: <b>{len(filtered_due)}</b>\n\n"
            "اختر كويزاً لمراجعة أسئلته الضعيفة:")

    kb = []
    if filtered_due:
        kb.append([InlineKeyboardButton(f"🔴 راجع المستحق ({len(filtered_due)} سؤال)",
                                        callback_data="start_weakall")])
    for qid, info in quiz_list[start:start + ITEMS_PER_PAGE]:
        name = info["name"][:25] + ("..." if len(info["name"]) > 25 else "")
        due_lbl = f" 🔴{info['due']}" if info["due"] else ""
        kb.append([InlineKeyboardButton(f"❓ {name} ({info['count']}){due_lbl}",
                                        callback_data=f"start_weak_{qid}")])

    nav = []
    c_param = "all" if category_filter == "all" else (category_filter if category_filter is not None else "0")
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"weak_page_{c_param}_{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"weak_page_{c_param}_{page+1}"))
    if nav:
        kb.append(nav)

    if len(cat_map) > 1:
        kb.append([InlineKeyboardButton("📂 تصفية حسب المجلدات", callback_data="weak_questions")])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(kb)


async def weak_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    text, kb = _build_weak_questions(user_id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def handle_weak_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int) -> bool:
    """
    Handles callbacks related to weak questions.
    Returns True if handled, False otherwise.
    """
    query = update.callback_query

    if data.startswith("start_weak_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="weak")
        return True

    if data == "start_weakall":
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, 0, session_type="weakall")
        return True

    if data == "weak_questions":
        text, kb = _build_weak_questions(user_id)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("weak_cat_"):
        cat_val = data.split("_")[-1]
        cat_filter = "all" if cat_val == "all" else int(cat_val)
        text, kb = _build_weak_questions(user_id, category_filter=cat_filter, page=1)
        await safe_edit(query, text, kb)
        return True

    if data.startswith("weak_page_"):
        parts = data.split("_")
        if len(parts) == 4:
            cat_val = parts[2]
            page = int(parts[3])
            cat_filter = "all" if cat_val == "all" else int(cat_val)
        else:
            cat_filter = "all"
            page = int(parts[2])
        text, kb = _build_weak_questions(user_id, category_filter=cat_filter, page=page)
        await safe_edit(query, text, kb)
        return True

    return False
