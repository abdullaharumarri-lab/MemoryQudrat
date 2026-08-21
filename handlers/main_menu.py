import html
import pytz
import logging
import re
import time as _time
import collections
import httpx
from datetime import datetime, date, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin
from spaced_repetition import days_until, stage_label, DEFAULT_REVIEW_INTERVALS
from utils import send_clean_message, safe_edit, strip_html_tags

logger = logging.getLogger(__name__)

# ─── Rate Limiter ─────────────────────────────────────────────────────────────
# Tracks the last N timestamps per user_id to detect flood/spam
_user_request_times: dict = collections.defaultdict(list)
_RATE_LIMIT_MAX = 12        # max requests
_RATE_LIMIT_WINDOW = 6.0    # within this many seconds

def _is_rate_limited(user_id: int) -> bool:
    """Return True if the user has exceeded the rate limit."""
    now = _time.monotonic()
    times = _user_request_times[user_id]
    # Remove timestamps outside the window
    _user_request_times[user_id] = [t for t in times if now - t < _RATE_LIMIT_WINDOW]
    _user_request_times[user_id].append(now)
    return len(_user_request_times[user_id]) > _RATE_LIMIT_MAX


# ─── URL Text Handler ────────────────────────────────────────────────────────

async def get_page_title(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
            resp = await client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200:
                match = re.search(r'<title>(.*?)</title>', resp.text, re.IGNORECASE | re.DOTALL)
                if match:
                    title = match.group(1).strip()
                    title = html.unescape(title)
                    title = title.replace(" - Google Forms", "").replace(" - نماذج Google", "").strip()
                    return title
    except Exception as e:
        logging.debug("get_page_title failed for %s: %s", url, e)
    return ""

async def url_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text or update.message.caption or ""
    msg = msg.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user

    # ── Rate limiting guard ───────────────────────────────────────────────────
    if user and _is_rate_limited(user.id):
        return  # Silently ignore flood

    # ── Admin Broadcast Handler ───────────────────────────────────────────────
    from handlers.admin_handler import handle_broadcast_input
    if await handle_broadcast_input(update, context):
        return

    # ── Creation & Upload Text Handler (Manual Quiz & Media Notes) ───────────
    from handlers.creation_handler import handle_creation_text_input
    if await handle_creation_text_input(update, context):
        return

    # ── Admin Folder Handlers ─────────────────────────────────────────────────
    if context.user_data.get("waiting_for_new_folder") is not None:
        parent_id = context.user_data.pop("waiting_for_new_folder")
        if user and is_admin(user.id):
            cat_name = msg.strip()
            cat_id = db.create_category(name=cat_name, parent_id=parent_id if parent_id != 0 else None)
            await send_clean_message(
                context, chat_id,
                f"✅ تم إنشاء المجلد <b>{html.escape(cat_name)}</b> بنجاح!",
                update=update,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 فتح المجلد", callback_data=f"bank_cat_{cat_id}_1")]])
            )
            return

    if context.user_data.get("waiting_for_rename_folder") is not None:
        cat_id = context.user_data.pop("waiting_for_rename_folder")
        if user and is_admin(user.id):
            new_name = msg.strip()
            db.update_category_name(cat_id, new_name)
            await send_clean_message(
                context, chat_id,
                f"✅ تم تغيير اسم المجلد إلى <b>{html.escape(new_name)}</b> بنجاح!",
                update=update,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 العودة للمجلد", callback_data=f"bank_cat_{cat_id}_1")]])
            )
            return

    # ── User Private Folder Rename ────────────────────────────────────────────
    if context.user_data.get("waiting_for_rename_user_folder") is not None:
        cat_id = context.user_data.pop("waiting_for_rename_user_folder")
        new_name = msg.strip()
        db.update_category_name(cat_id, new_name)
        text = f"✅ تم تغيير اسم المجلد إلى <b>{html.escape(new_name)}</b> بنجاح!"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 العودة للمجلد", callback_data=f"my_cat_{cat_id}_1")],
            [InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes")],
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return

    # Check if waiting for custom reminder time
    if context.user_data.get("waiting_for_custom_reminder"):
        context.user_data.pop("waiting_for_custom_reminder", None)
        user = update.effective_user
        u_id = user.id if user else chat_id
        
        # Match HH:MM format
        m = re.match(r"^(\d{1,2})[:.](\d{2})$", msg.strip())
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                db.update_user_reminder(u_id, hour, minute)
                from bot import schedule_reminder
                schedule_reminder(context.job_queue, u_id, hour=hour, minute=minute)
                
                period = "ص" if hour < 12 else "م"
                disp_h = hour if 1 <= hour <= 12 else (hour - 12 if hour > 12 else 12)
                time_str = f"{disp_h}:{minute:02d} {period}"
                
                text = (
                    f"✅ <b>تم تحديث وقت التذكير اليومي بنجاح!</b>\n\n"
                    f"⏰ الموعد الجديد: <b>{time_str}</b> (بتوقيت الرياض 🇸🇦)\n"
                    f"سيصلك تذكيرك اليومي بمراجعاتك في هذا الموعد يومياً."
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu")],
                    [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
                ])
                await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
                return
        
        # Invalid format
        text = (
            "❌ <b>صيغة الوقت غير صحيحة!</b>\n\n"
            "يرجى إرسال الوقت بصيغة <code>HH:MM</code> بنظام 24 ساعة.\n"
            "أمثلة:\n"
            "• <code>06:00</code> (الساعة 6 صباحاً)\n"
            "• <code>18:30</code> (الساعة 6:30 مساءً)\n"
            "• <code>21:00</code> (الساعة 9 مساءً)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ إعادة المحاولة", callback_data="set_rem_custom")],
            [InlineKeyboardButton("🔙 الإعدادات", callback_data="settings_menu")],
        ])
        await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return

    # Check if we are editing a question text
    q_id_edit = context.user_data.get("editing_q_text")
    if q_id_edit:
        conn = db.get_connection()
        conn.execute("UPDATE questions SET question_text = ? WHERE id = ?", (msg, q_id_edit))
        conn.commit()
        conn.close()
        
        quiz_id = context.user_data.get("editing_q_quiz")
        context.user_data.pop("editing_q_text", None)
        context.user_data.pop("editing_q_quiz", None)
        
        kb = [[InlineKeyboardButton("🔙 العودة للسؤال", callback_data=f"fixstage_qedit_{quiz_id}_{q_id_edit}")]]
        await context.bot.send_message(chat_id, "✅ تم تحديث نص السؤال بنجاح!", reply_markup=InlineKeyboardMarkup(kb))
        return

    # If it's a URL, process it regardless of state
    is_url = msg and (msg.startswith("http://") or msg.startswith("https://"))
    
    if context.user_data.get("waiting_for_url") or is_url:
        try:
            context.user_data["waiting_for_url"] = False
            url = msg
            
            # Try to extract title asynchronously
            extracted_title = await get_page_title(url)
            
            if extracted_title:
                u_id = user.id if user else 6099429826
                is_pub = 1 if is_admin(u_id) else 0
                quiz_id = db.save_quiz_url(extracted_title, url, user_id=u_id, is_public=is_pub)
                text = (
                    f"✅ <b>تم استخراج الاسم وإضافة الكويز بنجاح!</b>\n\n"
                    f"📚 الكويز: <b>{html.escape(extracted_title)}</b>\n"
                    f"تمت جدولة المراجعة في نظام <b>التكرار المتباعد</b> 🧠.\n\n"
                    f"<i>سيتم تذكيرك بالرابط عندما يحين وقت المراجعة.</i>"
                )
                await send_clean_message(context, chat_id, text, update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))
                return
            else:
                context.user_data["temp_url"] = url
                context.user_data["waiting_for_url_name"] = True
                await send_clean_message(context, chat_id, "🔗 <b>تم استلام الرابط.</b>\n\nلم أتمكن من استخراج الاسم تلقائياً. يرجى إرسال اسم لهذا الكويز:", update=update)
                return
        except Exception as e:
            await send_clean_message(context, chat_id, f"❌ حدث خطأ أثناء معالجة الرابط: {e}", update=update)
            return
            
    if context.user_data.get("waiting_for_url_name"):
        try:
            context.user_data["waiting_for_url_name"] = False
            url = context.user_data.pop("temp_url", "")
            name = msg
            
            u_id = user.id if user else 6099429826
            is_pub = 1 if is_admin(u_id) else 0
            quiz_id = db.save_quiz_url(name, url, user_id=u_id, is_public=is_pub)
            
            text = (
                f"✅ <b>تمت الإضافة بنجاح!</b>\n\n"
                f"📚 الكويز: <b>{html.escape(name)}</b>\n"
                f"تمت جدولة المراجعة في نظام <b>التكرار المتباعد</b> 🧠.\n\n"
                f"<i>سيتم تذكيرك بالرابط عندما يحين وقت المراجعة.</i>"
            )
            await send_clean_message(context, chat_id, text, update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))
        except Exception as e:
            await send_clean_message(context, chat_id, f"❌ حدث خطأ أثناء الحفظ: {e}", update=update)
        return


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard(user_id: int = None):
    session = db.get_session(user_id=user_id) if user_id else db.get_session()
    rows = []
    if session and session.get("question_ids") and session.get("current_index", 0) < len(session["question_ids"]):
        cur_q = session.get("current_index", 0) + 1
        tot_q = len(session["question_ids"])
        rows.append([InlineKeyboardButton(f"▶️ استكمال الكويز ({cur_q}/{tot_q})", callback_data="resume_quiz")])

    rows.extend([
        [
            InlineKeyboardButton("📚 بنك الكويزات (العام)", callback_data="public_bank_root"),
            InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes"),
        ],
        [
            InlineKeyboardButton("🔁 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("❌ الأسئلة الضعيفة", callback_data="weak_questions"),
        ],
        [
            InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule"),
            InlineKeyboardButton("📊 إحصائياتي", callback_data="weekly_stats"),
        ],
        [
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu"),
            InlineKeyboardButton("➕ إنشاء ورفع كويز", callback_data="create_upload_menu"),
        ],
        [
            InlineKeyboardButton("📢 قناة التحديثات والشروحات", url="https://t.me/MemoryQudrat"),
        ],
    ])
    return InlineKeyboardMarkup(rows)


def public_bank_view(cat_id: int = None, page: int = 1, user_id: int = None):
    """
    Builds the message text and inline keyboard for browsing the public bank by category/folder.
    """
    subcats = db.get_categories(parent_id=cat_id)
    cur_cat = db.get_category(cat_id) if cat_id else None
    
    if cur_cat:
        title_icon = cur_cat.get("icon", "📁")
        title_name = cur_cat["name"]
        header_text = f"📂 <b>{title_icon} {html.escape(title_name)}</b>\n\nاختر كويزاً للبدء أو تصفح المجلدات الفرعية:\n"
        quizzes = db.get_quizzes_by_category(category_id=cat_id, is_public=1)
    else:
        header_text = "📚 <b>بنك كويزات القدرات العام</b>\n\nاختر القسم الذي تريد التدرب عليه 🧠:\n"
        quizzes = []  # Root screen shows ONLY folders!

    kb = []

    # 1. Subfolders (if any)
    for sc in subcats:
        count = db.get_category_quizzes_count(sc["id"])
        icon = sc.get("icon", "📁")
        kb.append([InlineKeyboardButton(f"{icon} {sc['name']} ({count} كويز)", callback_data=f"bank_cat_{sc['id']}_1")])

    # 2. Quizzes inside this category with pagination
    ITEMS_PER_PAGE = 10
    total_q = len(quizzes)
    if total_q > 0:
        total_pages = (total_q + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        page = max(1, min(page, total_pages))
        start = (page - 1) * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        page_quizzes = quizzes[start:end]

        for q in page_quizzes:
            kb.append([InlineKeyboardButton(f"📝 {q['name']}", callback_data=f"bank_quiz_{q['id']}")])

        # Pagination controls
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"bank_cat_{cat_id or 0}_{page - 1}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"bank_cat_{cat_id or 0}_{page + 1}"))
        if nav_row:
            kb.append(nav_row)

    if not subcats and not quizzes:
        header_text += "\n📭 هذا المجلد لا يحتوي على كويزات حالياً."

    # 3. Admin Tools (if user is admin)
    if user_id and is_admin(user_id):
        admin_row = [InlineKeyboardButton("➕ إضافة مجلد هنا", callback_data=f"admin_add_cat_{cat_id or 0}")]
        if cur_cat:
            admin_row.append(InlineKeyboardButton("✏️ إدارة المجلد", callback_data=f"admin_edit_cat_{cat_id}"))
        kb.append(admin_row)

    # 4. Back navigation
    if cur_cat:
        parent_id = cur_cat.get("parent_id")
        kb.append([InlineKeyboardButton("🔙 رجوع للأقسام", callback_data=f"bank_cat_{parent_id}_1" if parent_id else "public_bank_root")])
    else:
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])

    return header_text, InlineKeyboardMarkup(kb)


def my_quizzes_view(folder_id: int = None, page: int = 1, user_id: int = None, show_all: bool = False):
    """
    Builds the message text and keyboard for browsing personal private quizzes and personal folders.
    """
    if show_all:
        quizzes = db.get_user_private_quizzes(user_id) if user_id else []
        subfolders = []
        cur_folder = None
        header_text = f"📚 <b>جميع كويزاتي الخاصة</b> — ({len(quizzes)} كويز)\n\nاختر أي كويز للبدء أو نقله لأحد مجلداتك:\n"
    else:
        subfolders = db.get_categories(parent_id=folder_id, user_id=user_id, is_public=0)
        cur_folder = db.get_category(folder_id) if folder_id else None
        
        if cur_folder:
            title_name = cur_folder.get("name", "مجلد")
            header_text = f"📂 <b>مجلد: {html.escape(title_name)}</b>\n\nاختر كويزاً للبدء أو تصفح المجلدات الفرعية:\n"
            quizzes = db.get_quizzes_by_category(category_id=folder_id, user_id=user_id, is_public=0)
        else:
            header_text = "📁 <b>كويزاتي ومجلداتي الخاصة</b> 🧠\n\nتصفح مجلداتك وكويزاتك الخاصة، أو أنشئ مجلداً جديداً:\n"
            quizzes = db.get_quizzes_by_category(category_id=None, user_id=user_id, is_public=0)

    kb = []

    # 1. Top Button for All Quizzes (in Root)
    if not show_all and folder_id is None and user_id:
        all_private = db.get_user_private_quizzes(user_id)
        if all_private:
            kb.append([InlineKeyboardButton(f"📚 كل كويزاتي الخاصة ({len(all_private)} كويز)", callback_data="my_all_quizzes_1")])

    # 2. Subfolders
    for sf in subfolders:
        count = db.get_category_quizzes_count(sf["id"], user_id=user_id)
        icon = sf.get("icon", "📁")
        kb.append([InlineKeyboardButton(f"{icon} {sf['name']} ({count} كويز)", callback_data=f"my_cat_{sf['id']}_1")])

    # 3. Quizzes inside this view with pagination
    ITEMS_PER_PAGE = 8
    total_q = len(quizzes)
    if total_q > 0:
        total_pages = (total_q + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        page = max(1, min(page, total_pages))
        start = (page - 1) * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        page_quizzes = quizzes[start:end]

        for q in page_quizzes:
            kb.append([InlineKeyboardButton(f"📝 {q['name']}", callback_data=f"bank_quiz_{q['id']}")])

        nav_row = []
        page_prefix = "my_all_quizzes_" if show_all else f"my_cat_{folder_id or 0}_"
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"{page_prefix}{page - 1}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"{page_prefix}{page + 1}"))
        if nav_row:
            kb.append(nav_row)

    if not subfolders and not quizzes:
        header_text += "\n📭 لا توجد كويزات هنا حالياً.\n• اضغط (➕ إنشاء مجلد خاص) لإنشاء تصنيف جديد.\n• أو (➕ إنشاء / رفع كويز) لإضافة كويز جديد."

    # 4. Action Buttons
    if not show_all:
        action_row = [
            InlineKeyboardButton("➕ إنشاء مجلد خاص", callback_data=f"my_new_folder_{folder_id or 0}"),
            InlineKeyboardButton("➕ إنشاء / رفع كويز", callback_data="create_upload_menu"),
        ]
        kb.append(action_row)

        if cur_folder:
            kb.append([
                InlineKeyboardButton("✏️ إعادة تسمية المجلد", callback_data=f"my_rename_folder_{folder_id}"),
                InlineKeyboardButton("🗑️ حذف هذا المجلد", callback_data=f"my_del_folder_{folder_id}"),
            ])

    # 5. Back navigation
    if show_all:
        kb.append([InlineKeyboardButton("🔙 رجوع للمجلدات", callback_data="my_quizzes")])
    elif cur_folder:
        parent_id = cur_folder.get("parent_id")
        kb.append([InlineKeyboardButton("🔙 رجوع للمجلد السابق", callback_data=f"my_cat_{parent_id}_1" if parent_id else "my_quizzes")])
    else:
        kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])

    return header_text, InlineKeyboardMarkup(kb)


def quizzes_keyboard(quizzes: list, page: int = 1):
    ITEMS_PER_PAGE = 20
    total = len(quizzes)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(1, min(page, total_pages))

    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_quizzes = quizzes[start:end]

    kb = []
    for q in page_quizzes:
        kb.append([InlineKeyboardButton(f"📋 {q['name']}", callback_data=f"quiz_menu_{q['id']}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"my_quizzes_page_{page-1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"my_quizzes_page_{page+1}"))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb), page, total_pages


def quiz_menu_keyboard(quiz_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ ابدأ الكويز (تجربة)", callback_data=f"start_practice_{quiz_id}")],
        [InlineKeyboardButton("❌ حذف الكويز", callback_data=f"delete_quiz_{quiz_id}")],
        [InlineKeyboardButton("🔙 كويزاتي", callback_data="my_quizzes")],
    ])


def due_reviews_keyboard(reviews: list):
    kb = []
    for r in reviews:
        label = stage_label(r["stage"])
        kb.append([InlineKeyboardButton(
            f"🔁 {r['quiz_name']} — {label}",
            callback_data=f"start_review_{r['id']}_{r['quiz_id']}"
        )])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)


def weak_quizzes_keyboard(quizzes_with_weak: list):
    kb = []
    for item in quizzes_with_weak:
        kb.append([InlineKeyboardButton(
            f"❌ {item['quiz_name']} ({item['count']} سؤال ضعيف)",
            callback_data=f"weak_menu_{item['quiz_id']}"
        )])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else (user.id if user else None)
    user_id = user.id if user else None
    if user:
        db.save_or_update_user(user.id, user.username, user.full_name)

    text = (
        "👋 أهلاً بك في بوت <b>ذاكرة القدرات</b>!\n\n"
        "نظام مراجعة ذكي باستخدام التكرار المتباعد 📚\n"
        "اختر ما تريد:"
    )
    kb = main_menu_keyboard(user_id=user_id)
    
    from utils import clean_entire_chat

    if update.message:
        # Delete user command message immediately & wipe all other messages
        clean_entire_chat(context, chat_id)
        await send_clean_message(
            context, chat_id, text,
            update=update, reply_markup=kb
        )
    elif update.callback_query:
        cb_msg_id = update.callback_query.message.message_id if update.callback_query.message else None
        # Clean all other messages in chat history leaving ONLY this edited Main Menu
        if chat_id:
            clean_entire_chat(context, chat_id, keep_message_id=cb_msg_id)
        await safe_edit(update.callback_query, text, kb)


async def fixstage_command(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    # ── Admin-only command ────────────────────────────────────────────────────
    user = update.effective_user
    if not user or not is_admin(user.id):
        err_msg = f"❌ هذا الأمر متاح للمشرف فقط.\n(معرّفك: <code>{user.id if user else 'غير معروف'}</code>)"
        if update.message:
            await update.message.reply_text(err_msg, parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.answer("❌ غير مصرح.", show_alert=True)
        logger.warning("Unauthorized fixstage attempt by user_id=%s", user.id if user else "unknown")
        return

    reviews = db.get_all_quiz_reviews()
    if not reviews:
        text = "لا توجد كويزات مجدولة."
        if update.message:
            await send_clean_message(context, update.effective_chat.id, text, update=update)
        elif update.callback_query:
            await safe_edit(update.callback_query, text)
        return

    ITEMS_PER_PAGE = 30
    total_items = len(reviews)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_reviews = reviews[start_idx:end_idx]


    kb = []
    for r in page_reviews:
        days = days_until(r["next_review_date"])
        if days <= 0:
            timing = "🔴 مستحق الآن"
        elif days == 1:
            timing = "🟡 غداً"
        else:
            timing = f"⏳ بعد {days} يوم"
        kb.append([InlineKeyboardButton(
            f"🔧 {r['quiz_name']} — {timing}",
            callback_data=f"fixstage_menu_{r['quiz_id']}"
        )])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"fixstage_page_{page-1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"fixstage_page_{page+1}"))
    
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])

    text = f"🛠 <b>تعديل موعد المراجعة (صفحة {page}/{total_pages})</b>\n\nاختر الكويز الذي تريد تعديل موعده:"
    if update.message:
        await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=InlineKeyboardMarkup(kb))
    elif update.callback_query:
        await safe_edit(update.callback_query, text, InlineKeyboardMarkup(kb))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    # ── Rate limiting guard ───────────────────────────────────────────────────
    if user and _is_rate_limited(user.id):
        try:
            await query.answer("⏳ أرسلت طلبات كثيرة. انتظر لحظة ثم حاول مرة أخرى.", show_alert=False)
        except Exception:
            pass
        return

    try:
        import asyncio
        asyncio.create_task(query.answer())
    except Exception:
        pass
    data = query.data

    try:
        await _handle_button_click(update, context, query, data)
    except Exception as e:
        logger.exception("Error in button_handler for data=%s: %s", data, e)
        back_btn = [[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]
        await safe_edit(
            query,
            "❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.",
            InlineKeyboardMarkup(back_btn)
        )


async def _handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE, query, data):
    back_btn = [[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]

    # ── Main menu ──
    if data == "main_menu":
        from handlers.quiz_handler import cleanup_quiz_messages
        await cleanup_quiz_messages(update.effective_chat.id, context)
        await main_menu_handler(update, context)

    # ── Resume Quiz ──
    elif data == "resume_quiz":
        from handlers.quiz_handler import show_next_question
        await show_next_question(update, context)

    # ── Public Bank Navigation ──
    elif data == "public_bank_root" or data.startswith("bank_cat_"):
        parts = data.split("_")
        if data == "public_bank_root":
            cat_id = None
            page = 1
        else:
            raw_cat_id = int(parts[2])
            cat_id = raw_cat_id if raw_cat_id != 0 else None
            page = int(parts[3]) if len(parts) > 3 else 1

        user = update.effective_user
        text, kb = public_bank_view(cat_id=cat_id, page=page, user_id=user.id if user else None)
        await safe_edit(query, text, kb)

    # ── Public Bank Quiz Details ──
    elif data.startswith("bank_quiz_"):
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await safe_edit(query, "❌ الكويز غير موجود.", InlineKeyboardMarkup(back_btn))
            return

        questions = db.get_questions(quiz_id)
        cat = db.get_category(quiz.get("category_id")) if quiz.get("category_id") else None
        cat_name = cat["name"] if cat else "عام"
        user = update.effective_user

        # Check if user already scheduled this quiz
        reviews = db.get_all_quiz_reviews(user_id=user.id if user else 6099429826)
        user_review = next((r for r in reviews if r["quiz_id"] == quiz_id), None)

        status_text = "غير مضاف لجدول المراجعة اليومية ⚪"
        if user_review:
            days = days_until(user_review.get("next_review_date"))
            lbl = stage_label(user_review.get("stage", 0))
            status_text = f"مجدول ({lbl}) — موعده: {'اليوم!' if days <= 0 else f'بعد {days} يوم'} 🟢"

        text = (
            f"📋 <b>{html.escape(quiz['name'])}</b>\n\n"
            f"📁 القسم: <b>{html.escape(cat_name)}</b>\n"
            f"📝 عدد الأسئلة: <b>{len(questions)} سؤال</b>\n"
            f"🧠 حالة التكرار المتباعد: <b>{status_text}</b>\n\n"
            f"اختر ما تريد:"
        )

        kb = [
            [InlineKeyboardButton("▶️ ابدأ الكويز (تجربة فورية)", callback_data=f"start_practice_{quiz_id}")],
        ]
        if not user_review:
            kb.append([InlineKeyboardButton("🔁 إضافة لجدول مراجعاتي اليومية", callback_data=f"sched_pub_quiz_{quiz_id}")])
        else:
            kb.append([InlineKeyboardButton("▶️ بدء مراجعة مجدولة", callback_data=f"start_quiz_{quiz_id}")])

        if quiz.get("is_public") == 1:
            kb.append([InlineKeyboardButton("📥 إضافة إلى كويزاتي الخاصة", callback_data=f"copy_to_my_quizzes_{quiz_id}")])

        is_owner = (user and quiz.get("owner_id") == user.id and not quiz.get("is_public"))
        if is_owner:
            kb.append([
                InlineKeyboardButton("📁 نقل لمجلد خاص", callback_data=f"my_move_quiz_{quiz_id}"),
                InlineKeyboardButton("🗑️ حذف الكويز", callback_data=f"delete_quiz_{quiz_id}")
            ])
        elif user and is_admin(user.id):
            kb.append([
                InlineKeyboardButton("📂 نقل لمجلد", callback_data=f"admin_move_quiz_{quiz_id}"),
                InlineKeyboardButton("🗑️ حذف من البنك", callback_data=f"delete_quiz_{quiz_id}")
            ])

        parent_cat_id = quiz.get("category_id") or 0
        if is_owner:
            kb.append([InlineKeyboardButton("🔙 رجوع لكويزاتي", callback_data=f"my_cat_{parent_cat_id}_1" if parent_cat_id else "my_quizzes")])
        else:
            kb.append([InlineKeyboardButton("🔙 رجوع للمجلد", callback_data=f"bank_cat_{parent_cat_id}_1")])

        await safe_edit(query, text, InlineKeyboardMarkup(kb))

    # ── Schedule Public Quiz for User ──
    elif data.startswith("sched_pub_quiz_"):
        quiz_id = int(data.split("_")[-1])
        user = update.effective_user
        u_id = user.id if user else 6099429826
        riyadh_tz = pytz.timezone("Asia/Riyadh")
        now_riyadh = datetime.now(riyadh_tz)
        start_today = now_riyadh.hour < 4 or (now_riyadh.hour == 4 and now_riyadh.minute < 30)
        db.schedule_first_review(quiz_id, user_id=u_id, start_today=start_today)

        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        timing_str = "اليوم الساعة 4:30 الفجر" if start_today else "غداً الساعة 4:30 الفجر"
        await safe_edit(
            query,
            f"✅ <b>تمت إضافة الكويز لجدول مراجعاتك بنجاح!</b>\n\n"
            f"📋 <b>{name_safe}</b>\n"
            f"🔔 أول مراجعة ستصلك: <b>{timing_str}</b>\n\n"
            f"يمكنك البدء بحله الآن كتجربة أولى أو العودة للقائمة.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ ابدأ الكويز الآن", callback_data=f"start_practice_{quiz_id}")],
                [InlineKeyboardButton("🔙 العودة للمجلد", callback_data=f"bank_cat_{quiz.get('category_id') or 0}_1")],
            ])
        )

    # ── Admin Folder Actions ──
    elif data.startswith("admin_add_cat_"):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await query.answer("❌ غير مصرح.", show_alert=True)
            return
        parent_id = int(data.split("_")[-1])
        context.user_data["waiting_for_new_folder"] = parent_id
        await safe_edit(
            query,
            "✍️ <b>إنشاء مجلد جديد</b>\n\nأرسل اسم المجلد الجديد الآن في رسالة نصية:",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"bank_cat_{parent_id}_1")]])
        )

    elif data.startswith("admin_edit_cat_"):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await query.answer("❌ غير مصرح.", show_alert=True)
            return
        cat_id = int(data.split("_")[-1])
        cat = db.get_category(cat_id)
        cat_name = cat["name"] if cat else "المجلد"
        await safe_edit(
            query,
            f"⚙️ <b>إدارة المجلد: {html.escape(cat_name)}</b>\n\nاختر الإجراء المطلوب:",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ تغيير اسم المجلد", callback_data=f"admin_rename_cat_{cat_id}")],
                [InlineKeyboardButton("🗑️ حذف المجلد", callback_data=f"admin_del_cat_{cat_id}")],
                [InlineKeyboardButton("🔙 رجوع للمجلد", callback_data=f"bank_cat_{cat_id}_1")],
            ])
        )

    elif data.startswith("admin_rename_cat_"):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await query.answer("❌ غير مصرح.", show_alert=True)
            return
        cat_id = int(data.split("_")[-1])
        context.user_data["waiting_for_rename_folder"] = cat_id
        await safe_edit(
            query,
            "✍️ <b>تغيير اسم المجلد</b>\n\nأرسل الاسم الجديد للمجلد في رسالة نصية:",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"bank_cat_{cat_id}_1")]])
        )

    elif data.startswith("admin_del_cat_"):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await query.answer("❌ غير مصرح.", show_alert=True)
            return
        cat_id = int(data.split("_")[-1])
        db.delete_category(cat_id)
        await safe_edit(
            query,
            "🗑️ تم حذف المجلد بنجاح.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 البنك العام", callback_data="public_bank_root")]])
        )

    elif data.startswith("admin_move_quiz_"):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await query.answer("❌ غير مصرح.", show_alert=True)
            return
        quiz_id = int(data.split("_")[-1])
        cats = db.get_categories()
        kb = []
        for c in cats:
            kb.append([InlineKeyboardButton(f"{c.get('icon', '📁')} {c['name']}", callback_data=f"admin_domove_{quiz_id}_{c['id']}")])
        kb.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"bank_quiz_{quiz_id}")])
        await safe_edit(query, "📂 <b>اختر المجلد الذي تريد نقل هذا الكويز إليه:</b>", InlineKeyboardMarkup(kb))

    elif data.startswith("admin_domove_"):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await query.answer("❌ غير مصرح.", show_alert=True)
            return
        parts = data.split("_")
        quiz_id = int(parts[2])
        cat_id = int(parts[3])
        db.move_quiz_to_category(quiz_id, cat_id)
        await safe_edit(
            query,
            "✅ تم نقل الكويز للمجلد بنجاح!",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عرض الكويز", callback_data=f"bank_quiz_{quiz_id}")]])
        )

    # ── Admin Dashboard Callbacks ──
    elif data.startswith("admin_refresh_stats") or data.startswith("admin_broadcast_") or data.startswith("admin_confirm_") or data.startswith("admin_cancel_"):
        from handlers.admin_handler import admin_button_handler
        await admin_button_handler(update, context)

    # ── Create & Upload Main Menu ──
    elif data == "create_upload_menu":
        from handlers.creation_handler import build_create_upload_menu
        text, kb = build_create_upload_menu()
        await safe_edit(query, text, kb)

    # ── Manual Quiz Builder Callbacks ──
    elif data in ("create_manual_quiz", "manual_add_q", "manual_dashboard", "manual_save_quiz") or data.startswith("manual_set_correct_"):
        from handlers.creation_handler import handle_manual_quiz_callback
        await handle_manual_quiz_callback(update, context)

    # ── Upload JSON ──
    elif data == "upload_json":
        await safe_edit(
            query,
            "📋 <b>2- رفع كويز عبر ملف JSON</b>\n\n"
            "أرسل ملف JSON الخاص بالكويز الآن 📄.\n\n"
            "💡 <i>للحصول على قالب JSON الجاهز، يمكنك كتابة أمر /template</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")]]),
        )

    # ── Upload URL ──
    elif data == "upload_url":
        context.user_data["waiting_for_url"] = True
        await safe_edit(
            query,
            "🔗 <b>3- إضافة كويز كرابط (Forms / Quizizz)</b>\n\n"
            "أرسل رابط الكويز الآن في رسالة نصية:\n"
            "<i>(مثال: رابط Google Forms أو منصة اختبارات)</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")]]),
        )

    # ── Upload Media & Notes ──
    elif data == "upload_media_note":
        context.user_data["waiting_for_media_note"] = True
        await safe_edit(
            query,
            "📁 <b>4- إدراج صورة / ملف / ملخص في التكرار المتباعد</b> 🧠\n\n"
            "أرسل الآن إلى المحادثة:\n"
            "• 📸 <b>صورة</b> (قانون، خريطة مفاهيم، جدول، تجميعة مصورة)\n"
            "• 📄 <b>ملف PDF أو مستند</b>\n"
            "• 📝 <b>ملاحظة أو نص مكتوب</b>\n\n"
            "سيقوم البوت بحفظها وتذكيرك بمراجعتها في المواعيد الذكية (بعد 1، 3، 7، 14، 30 يوم) 💪.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")]]),
        )

    # ── My Quizzes & Private Folders ──
    elif data == "my_quizzes" or data.startswith("my_cat_"):
        user = update.effective_user
        u_id = user.id if user else 6099429826
        
        folder_id = None
        page = 1
        if data.startswith("my_cat_"):
            parts = data.split("_")
            fid = int(parts[2])
            folder_id = fid if fid != 0 else None
            page = int(parts[3]) if len(parts) > 3 else 1

        text, kb = my_quizzes_view(folder_id=folder_id, page=page, user_id=u_id)
        await safe_edit(query, text, kb)

    # ── User Private Folder Creation, Rename, Deletion ──
    elif data.startswith("my_new_folder_"):
        parent_id = int(data.split("_")[-1])
        context.user_data["waiting_for_user_folder"] = parent_id
        text = (
            "📁 <b>إنشاء مجلد خاص جديد</b>\n\n"
            "أرسل الآن <b>اسم المجلد الجديد</b> في رسالة نصية:\n"
            "<i>(مثال: تجميعات القسم الكمي، قوانين الهندسة، تجميعات 1445)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"my_cat_{parent_id}_1" if parent_id else "my_quizzes")]])
        await safe_edit(query, text, kb)

    elif data.startswith("my_rename_folder_"):
        cat_id = int(data.split("_")[-1])
        context.user_data["waiting_for_rename_user_folder"] = cat_id
        text = (
            "✏️ <b>إعادة تسمية المجلد الخاص</b>\n\n"
            "أرسل الآن <b>الاسم الجديد للمجلد</b> في رسالة نصية:"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"my_cat_{cat_id}_1")]])
        await safe_edit(query, text, kb)

    elif data.startswith("my_del_folder_"):
        cat_id = int(data.split("_")[-1])
        user = update.effective_user
        u_id = user.id if user else 6099429826
        db.delete_category(cat_id, user_id=u_id)
        await safe_edit(
            query,
            "✅ <b>تم حذف المجلد بنجاح!</b>\n<i>(الكويزات التي كانت بداخله تم نقلها للمجلد الرئيسي)</i>",
            InlineKeyboardMarkup([[InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes")]])
        )

    # ── Move Quiz to Private Folder ──
    elif data.startswith("my_move_quiz_"):
        quiz_id = int(data.split("_")[-1])
        user = update.effective_user
        u_id = user.id if user else 6099429826
        user_cats = db.get_categories(user_id=u_id, is_public=0)
        
        kb = [
            [InlineKeyboardButton("📁 المجلد الرئيسي (بدون مجلد)", callback_data=f"my_set_quiz_cat_{quiz_id}_0")]
        ]
        for c in user_cats:
            kb.append([InlineKeyboardButton(f"📁 {c['name']}", callback_data=f"my_set_quiz_cat_{quiz_id}_{c['id']}")])
        kb.append([InlineKeyboardButton("🔙 رجوع للكويز", callback_data=f"bank_quiz_{quiz_id}")])

        await safe_edit(
            query,
            "📁 <b>اختر المجلد الذي ترغب بنقل الكويز إليه:</b>",
            InlineKeyboardMarkup(kb)
        )

    elif data.startswith("my_set_quiz_cat_"):
        parts = data.split("_")
        quiz_id = int(parts[4])
        cat_id = int(parts[5]) if len(parts) > 5 else 0
        db.move_quiz_to_category(quiz_id, cat_id if cat_id != 0 else None)
        await safe_edit(
            query,
            "✅ <b>تم نقل الكويز بنجاح!</b>",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 الذهاب للكويز", callback_data=f"bank_quiz_{quiz_id}")],
                [InlineKeyboardButton("📁 كويزاتي الخاصة", callback_data="my_quizzes")],
                [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
            ])
        )

    # ── Browse All Private Quizzes ──
    elif data.startswith("my_all_quizzes_"):
        page = int(data.split("_")[-1])
        user = update.effective_user
        u_id = user.id if user else 6099429826
        text, kb = my_quizzes_view(folder_id=None, page=page, user_id=u_id, show_all=True)
        await safe_edit(query, text, kb)

    # ── Copy Public Quiz to Private Library ──
    elif data.startswith("copy_to_my_quizzes_"):
        quiz_id = int(data.split("_")[-1])
        user = update.effective_user
        u_id = user.id if user else 6099429826
        new_quiz_id = db.copy_quiz_to_user(quiz_id, user_id=u_id)
        
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        text = (
            f"✅ <b>تمت إضافة الكويز إلى كويزاتك الخاصة بنجاح!</b>\n\n"
            f"📋 <b>{name_safe}</b>\n\n"
            f"تم نسخه إلى مكتبتك الخاصة وجدولته في مراجعاتك اليومية 🧠.\n"
            f"يمكنك الآن نقله إلى أي من مجلداتك الخاصة أو البدء بحله فوراً."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 نقل لمجلد خاص", callback_data=f"my_move_quiz_{new_quiz_id}")],
            [InlineKeyboardButton("▶️ ابدأ الكويز الآن", callback_data=f"start_practice_{new_quiz_id}")],
            [InlineKeyboardButton("📁 فتح كويزاتي الخاصة", callback_data="my_quizzes")],
            [InlineKeyboardButton("🔙 العودة للمجلد العام", callback_data=f"bank_cat_{quiz.get('category_id') or 0}_1")],
        ])
        await safe_edit(query, text, kb)

    # ── Settings Menu ──
    elif data == "settings_menu":
        user = update.effective_user
        u_id = user.id if user else 6099429826
        user_row = db.get_user(u_id)
        h = user_row.get("reminder_hour", 4) if user_row else 4
        m = user_row.get("reminder_minute", 30) if user_row else 30
        
        period = "ص" if h < 12 else "م"
        disp_h = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
        time_str = f"{disp_h}:{m:02d} {period}"
        name_safe = html.escape(user.full_name if user else "صديقنا")
        
        text = (
            f"⚙️ <b>إعدادات الحساب والتذكير</b>\n\n"
            f"👤 الطالب: <b>{name_safe}</b>\n"
            f"🆔 المعرّف: <code>{u_id}</code>\n"
            f"⏰ موعد التذكير اليومي: <b>{time_str}</b> (بتوقيت الرياض 🇸🇦)\n\n"
            f"اختر الإجراء المطلوب:"
        )
        kb = [
            [InlineKeyboardButton("⏰ تغيير وقت التذكير اليومي", callback_data="settings_reminder_time")],
            [InlineKeyboardButton("🧠 كيف يعمل التكرار المتباعد؟", callback_data="settings_how_it_works")],
            [InlineKeyboardButton("📢 قناة التحديثات والشروحات", url="https://t.me/MemoryQudrat")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))

    # ── Settings: Choose Reminder Time ──
    elif data == "settings_reminder_time":
        text = (
            "⏰ <b>تخصيص وقت التذكير اليومي</b>\n\n"
            "اختر أحد المواعيد المقترحة أو حدد وقتاً مخصصاً يناسب جدولك اليومي:\n"
            "<i>(جميع المواعيد محسوبة بتوقيت الرياض 🇸🇦)</i>"
        )
        kb = [
            [
                InlineKeyboardButton("🌅 الفجر (4:30 ص)", callback_data="set_rem_4_30"),
                InlineKeyboardButton("☀️ الظهر (2:00 م)", callback_data="set_rem_14_0"),
            ],
            [
                InlineKeyboardButton("🌇 العصر (5:00 م)", callback_data="set_rem_17_0"),
                InlineKeyboardButton("🌙 المساء (9:00 م)", callback_data="set_rem_21_0"),
            ],
            [
                InlineKeyboardButton("✍️ تحديد وقت مخصص آخر", callback_data="set_rem_custom"),
            ],
            [
                InlineKeyboardButton("🔙 الإعدادات", callback_data="settings_menu"),
            ]
        ]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))

    # ── Settings: Set Preset Reminder Time ──
    elif data.startswith("set_rem_") and data != "set_rem_custom":
        parts = data.split("_")
        hour = int(parts[2])
        minute = int(parts[3])
        user = update.effective_user
        u_id = user.id if user else 6099429826
        
        db.update_user_reminder(u_id, hour, minute)
        from bot import schedule_reminder
        schedule_reminder(context.job_queue, u_id, hour=hour, minute=minute)
        
        period = "ص" if hour < 12 else "م"
        disp_h = hour if 1 <= hour <= 12 else (hour - 12 if hour > 12 else 12)
        time_str = f"{disp_h}:{minute:02d} {period}"
        
        text = (
            f"✅ <b>تم ضبط وقت التذكير اليومي بنجاح!</b>\n\n"
            f"⏰ موعدك الجديد: <b>{time_str}</b> (بتوقيت الرياض 🇸🇦)\n"
            f"سنقوم بتذكيرك يومياً بمراجعاتك وأسئلتك الضعيفة في هذا الموعد لتثبيت حفظك 💪"
        )
        kb = [
            [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))

    # ── Settings: Custom Time Prompt ──
    elif data == "set_rem_custom":
        context.user_data["waiting_for_custom_reminder"] = True
        text = (
            "✍️ <b>تحديد وقت تذكير مخصص</b>\n\n"
            "أرسل الوقت الذي يناسبك بنظام 24 ساعة في رسالة نصية (مثال: <code>06:00</code> أو <code>16:45</code> أو <code>22:30</code>):\n\n"
            "<i>(بتوقيت الرياض 🇸🇦)</i>"
        )
        kb = [[InlineKeyboardButton("❌ إلغاء", callback_data="settings_reminder_time")]]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))

    # ── Settings: How Spaced Repetition Works ──
    elif data == "settings_how_it_works":
        text = (
            "🧠 <b>كيف يعمل نظام التكرار المتباعد في ذاكرة القدرات؟</b>\n\n"
            "يعتمد البوت على <b>منحنى النسيان العلمي (Ebbinghaus Forgetting Curve)</b>:\n\n"
            "📅 <b>مراحل تكرار الكويزات:</b>\n"
            "• <b>المرحلة 1:</b> بعد يوم واحد (1 day)\n"
            "• <b>المرحلة 2:</b> بعد 3 أيام (3 days)\n"
            "• <b>المرحلة 3:</b> بعد أسبوع (7 days)\n"
            "• <b>المرحلة 4:</b> بعد أسبوعين (14 days)\n"
            "• <b>المرحلة 5:</b> بعد شهر (30 days)\n\n"
            "❌ <b>نظام معالجة نقاط الضعف:</b>\n"
            "كل سؤال تخطئ فيه يتم إدراجه فوراً في <b>«❌ الأسئلة الضعيفة»</b> ويتكرر تلقائياً حتى تجيبه بشكل صحيح ويتقدم في المراحل ليثبت تماماً في ذاكرتك.\n\n"
            "🎯 <b>سر الحصول على 95+ :</b>\n"
            "التزم بحل «🔁 مراجعات اليوم» أولاً بأول ولا تدع المراجعات تتراكم!"
        )
        kb = [
            [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))

    # ── Quiz menu ──
    elif data.startswith("quiz_menu_"):
        try:
            quiz_id = int(data.split("_")[-1])
        except (ValueError, TypeError):
            await safe_edit(query, "❌ الكويز غير موجود.", InlineKeyboardMarkup(back_btn))
            return

        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await safe_edit(query, "❌ الكويز غير موجود.", InlineKeyboardMarkup(back_btn))
            return
        questions = db.get_questions(quiz_id) or []
        weak = db.get_weak_questions_by_quiz(quiz_id) or []

        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quiz_reviews WHERE quiz_id = ?", (quiz_id,))
        review = cursor.fetchone()
        conn.close()

        review_text = ""
        if review:
            d = dict(review)
            days = days_until(d.get("next_review_date"))
            lbl = stage_label(d.get("stage", 0))
            review_text = f"\n🔁 {lbl} — بعد {days} يوم" if days > 0 else f"\n🔁 {lbl} — <b>اليوم!</b>"

        name_safe = html.escape(quiz.get("name", "كويز"))
        
        if quiz.get("url"):
            # URL Quiz Menu
            await safe_edit(
                query,
                f"🔗 <b>{name_safe}</b> (رابط)\n"
                f"{review_text}",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶️ تجربة الكويز", url=quiz["url"])],
                    [InlineKeyboardButton("🔄 تحويل إلى JSON", callback_data=f"upgrade_json_{quiz_id}")],
                    [InlineKeyboardButton("❌ حذف الكويز", callback_data=f"delete_quiz_{quiz_id}")],
                    [InlineKeyboardButton("🔙 كويزاتي", callback_data="my_quizzes")],
                ])
            )
        else:
            await safe_edit(
                query,
                f"📋 <b>{name_safe}</b>\n"
                f"📝 {len(questions)} سؤال | ❌ {len(weak)} سؤال ضعيف"
                f"{review_text}",
                quiz_menu_keyboard(quiz_id)
            )

    # ── Set Category ──
    elif data.startswith("setcat_"):
        parts = data.split("_")
        quiz_id = int(parts[1])
        cat_id = int(parts[2])
        
        conn = db.get_connection()
        conn.execute("UPDATE quizzes SET category_id = ? WHERE id = ?", (cat_id, quiz_id))
        conn.commit()
        conn.close()
        
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz.get('name', 'كويز')) if quiz else "كويز"
        
        text = (
            f"✅ تم تعيين القسم بنجاح!\n\n"
            f"📋 <b>{name_safe}</b>\n\n"
            f"متى تريد أن تبدأ أول مراجعة لهذا الكويز؟"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 اليوم (الساعة 4:30 فجراً)", callback_data=f"sched_today_{quiz_id}")],
            [InlineKeyboardButton("📅 غداً (الساعة 4:30 فجراً)", callback_data=f"sched_tomorrow_{quiz_id}")],
        ])
        await safe_edit(query, text, keyboard)

    # ── Schedule review ──
    elif data.startswith("sched_today_"):
        quiz_id = int(data.split("_")[-1])
        db.schedule_first_review(quiz_id, start_today=True)
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        await safe_edit(
            query,
            f"✅ <b>تم!</b> ستصلك مراجعة <b>{name_safe}</b> اليوم الساعة 4:30 الفجر. 🔔",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ ابدأ الكويز (تجربة)", callback_data=f"start_practice_{quiz_id}")],
                [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
            ])
        )

    elif data.startswith("sched_tomorrow_"):
        quiz_id = int(data.split("_")[-1])
        db.schedule_first_review(quiz_id, start_today=False)
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        await safe_edit(
            query,
            f"✅ <b>تم!</b> ستصلك مراجعة <b>{name_safe}</b> غداً الساعة 4:30 الفجر. 🔔",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ ابدأ الكويز (تجربة)", callback_data=f"start_practice_{quiz_id}")],
                [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
            ])
        )

    # ── Start quiz ──
    elif data.startswith("start_quiz_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="quiz")

    # ── Start practice ──
    elif data.startswith("start_practice_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="practice")

    # ── Delete quiz ──
    elif data.startswith("delete_quiz_"):
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        await safe_edit(
            query,
            f"⚠️ هل أنت متأكد من حذف <b>{name_safe}</b>؟",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ نعم، احذف", callback_data=f"confirm_delete_{quiz_id}"),
                InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_menu_{quiz_id}"),
            ]])
        )

    elif data.startswith("confirm_delete_"):
        quiz_id = int(data.split("_")[-1])
        db.delete_quiz(quiz_id)
        await safe_edit(query, "🗑️ تم حذف الكويز بنجاح.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 كويزاتي", callback_data="my_quizzes")]]))

    # ── Due reviews ──
    elif data == "due_reviews":
        riyadh_tz = pytz.timezone("Asia/Riyadh")
        now = datetime.now(riyadh_tz)
        today_date = now.date().isoformat()
        
        user = update.effective_user
        u_id = user.id if user else 6099429826
        reviews = db.get_due_quiz_reviews(user_id=u_id)
        if not reviews:
            await safe_edit(query,
                "✅ لا توجد مراجعات مستحقة اليوم في حسابك!\n\nاستمر بالعمل الجيد 💪",
                InlineKeyboardMarkup(back_btn)
            )
            return

        overdue_reviews = [r for r in reviews if r["next_review_date"] < today_date]
        due_today_reviews = [r for r in reviews if r["next_review_date"] == today_date]

        if now.hour > 4 or (now.hour == 4 and now.minute >= 30):
            open_reviews = overdue_reviews + due_today_reviews
            locked_reviews = []
        else:
            open_reviews = overdue_reviews
            locked_reviews = due_today_reviews

        text = ""
        kb = []

        if open_reviews:
            text += f"🔁 <b>المراجعات المتاحة الآن</b> — {len(open_reviews)} مراجعة\n\n"
            kb = due_reviews_keyboard(open_reviews).inline_keyboard
            
        if locked_reviews:
            if open_reviews:
                text += "──────────────\n\n"
            text += f"🔒 <b>مراجعات مجدولة لليوم</b> — ({len(locked_reviews)} مراجعة)\n"
            text += "ستتاح لك الساعة 4:30 فجراً بتوقيت الرياض."

        if not kb:
            kb = back_btn
        elif not open_reviews:
            kb.append(back_btn[0])

        await safe_edit(
            query,
            text,
            InlineKeyboardMarkup(kb)
        )

    # ── Start review ──
    elif data.startswith("start_review_"):
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])
        
        quiz = db.get_quiz(quiz_id)
        if quiz and quiz.get("url"):
            raw_url = quiz["url"]
            q_name = html.escape(quiz.get("name", "مادة مراجعة"))
            chat_id = update.effective_chat.id

            if raw_url.startswith("media:photo:"):
                file_id = raw_url.replace("media:photo:", "", 1)
                kb = [
                    [InlineKeyboardButton("✅ تمت المراجعة وتثبيت الحفظ", callback_data=f"done_url_review_{review_id}")],
                    [InlineKeyboardButton("🔙 رجوع للمراجعات", callback_data="due_reviews")],
                ]
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=f"📸 <b>مراجعة: {q_name}</b>\n\nتأمل الصورة وراجع القوانين/المفاهيم جيداً 🧠.\nعند الانتهاء اضغط على الزر أدناه لجدولة التكرار القادم.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            elif raw_url.startswith("media:doc:"):
                file_id = raw_url.replace("media:doc:", "", 1)
                kb = [
                    [InlineKeyboardButton("✅ تمت المراجعة وتثبيت الحفظ", callback_data=f"done_url_review_{review_id}")],
                    [InlineKeyboardButton("🔙 رجوع للمراجعات", callback_data="due_reviews")],
                ]
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=f"📄 <b>مراجعة: {q_name}</b>\n\nافتح الملف وراجع محتواه جيداً 🧠.\nعند الانتهاء اضغط على الزر أدناه لجدولة التكرار القادم.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            elif raw_url.startswith("media:text:"):
                content = raw_url.replace("media:text:", "", 1)
                kb = [
                    [InlineKeyboardButton("✅ تمت المراجعة وتثبيت الحفظ", callback_data=f"done_url_review_{review_id}")],
                    [InlineKeyboardButton("🔙 رجوع للمراجعات", callback_data="due_reviews")],
                ]
                await safe_edit(
                    query,
                    f"📝 <b>مراجعة ملخص: {q_name}</b>\n\n"
                    f"{html.escape(content)}\n\n"
                    f"────────────────────\n"
                    f"<i>بعد مراجعة الملاحظة، اضغط على (تمت المراجعة) لجدولة التكرار التالي.</i>",
                    InlineKeyboardMarkup(kb)
                )
            else:
                # Regular URL (Google Forms etc.)
                kb = [
                    [InlineKeyboardButton("🌐 افتح الرابط", url=quiz["url"])],
                    [InlineKeyboardButton("✅ تم الحل", callback_data=f"done_url_review_{review_id}")],
                    [InlineKeyboardButton("🔄 تحويل إلى JSON", callback_data=f"upgrade_json_{quiz_id}")],
                    [InlineKeyboardButton("🔙 رجوع", callback_data="due_reviews")],
                ]
                await safe_edit(
                    query,
                    f"🔗 <b>{q_name}</b>\n\n"
                    f"للبدء في المراجعة، افتح الرابط وحل الكويز في المتصفح.\n\n"
                    f"⚠️ <i>بعد الانتهاء، اضغط على (تم الحل) لجدولة المراجعة القادمة.</i>",
                    InlineKeyboardMarkup(kb)
                )
        else:
            from handlers.quiz_handler import start_quiz_session
            await start_quiz_session(update, context, quiz_id, session_type="review", review_id=review_id)

    # ── Done URL review ──
    elif data.startswith("done_url_review_"):
        review_id = int(data.split("_")[-1])
        user = update.effective_user
        db.advance_quiz_review(review_id, user_id=user.id if user else 6099429826)
        await safe_edit(
            query,
            "✅ <b>ممتاز!</b> تم تسجيل حلك وجدولة الموعد القادم بنجاح.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        )
        
    # ── Upgrade URL to JSON ──
    elif data.startswith("upgrade_json_"):
        quiz_id = int(data.split("_")[-1])
        context.user_data["waiting_for_json_upgrade"] = quiz_id
        await safe_edit(
            query,
            "🔄 <b>تحويل إلى JSON</b>\n\n"
            "الرجاء إرسال ملف الـ JSON الخاص بهذا الكويز ليتم دمجه والبدء بتتبع الأسئلة الضعيفة.\n\n"
            "<i>(لن تفقد تقدمك الحالي في التكرار المتباعد)</i>",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="due_reviews")]])
        )

    # ── Weak questions ──
    elif data == "weak_questions":
        user = update.effective_user
        u_id = user.id if user else 6099429826
        all_weak = db.get_all_weak_questions(user_id=u_id)
        if not all_weak:
            await safe_edit(query,
                "✅ لا توجد أسئلة ضعيفة مسجلة في حسابك!\n\nأداؤك ممتاز 🌟",
                InlineKeyboardMarkup(back_btn)
            )
            return
        
        all_weak_count = len(all_weak)
        all_due_weak = db.get_due_all_weak_questions_sorted(user_id=u_id)
        due_count = len(all_due_weak)
        
        quiz_map = {}
        for wq in all_weak:
            qid = wq["quiz_id"]
            if qid not in quiz_map:
                quiz_map[qid] = {"quiz_id": qid, "quiz_name": wq["quiz_name"], "count": 0}
            quiz_map[qid]["count"] += 1

        kb = []
        if all_weak_count > 0:
            kb.append([InlineKeyboardButton(
                f"📚 مراجعة الكل — {all_weak_count} سؤال (الأحدث أولاً)",
                callback_data="start_weakall"
            )])
        for item in quiz_map.values():
            kb.append([InlineKeyboardButton(
                f"❌ {item['quiz_name']} ({item['count']} سؤال ضعيف)",
                callback_data=f"weak_menu_{item['quiz_id']}"
            )])
        kb.append(back_btn[0])

        await safe_edit(query,
            f"❌ <b>الأسئلة الضعيفة</b> — {all_weak_count} سؤال كلي\n"
            f"🔴 مستحق اليوم: <b>{due_count}</b>",
            InlineKeyboardMarkup(kb)
        )

    # ── Weak Menu ──
    elif data.startswith("weak_menu_"):
        quiz_id = int(data.split("_")[-1])
        user = update.effective_user
        u_id = user.id if user else 6099429826
        all_weak = db.get_all_weak_questions(user_id=u_id)
        
        quiz_weak = [wq for wq in all_weak if wq["quiz_id"] == quiz_id]
        if not quiz_weak:
            await safe_edit(query, "✅ لا توجد أسئلة ضعيفة هنا.", InlineKeyboardMarkup(back_btn))
            return
            
        quiz_name = quiz_weak[0]["quiz_name"]
        
        riyadh_tz = pytz.timezone("Asia/Riyadh")
        now = datetime.now(riyadh_tz)
        today_date = now.date().isoformat()
        
        # Calculate how many are due today/overdue
        due_weak = [wq for wq in quiz_weak if wq["next_review_date"] <= today_date]
        
        kb = [
            [InlineKeyboardButton("📚 تدريب على جميع الأخطاء", callback_data=f"start_weakpractice_{quiz_id}")]
        ]
        if due_weak:
            kb.insert(0, [InlineKeyboardButton(f"🔁 مراجعة المستحق ({len(due_weak)})", callback_data=f"start_weak_{quiz_id}")])
            
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="weak_questions")])
        
        await safe_edit(
            query,
            f"📋 <b>{html.escape(quiz_name)}</b>\n\n"
            f"مجموع الأخطاء: {len(quiz_weak)}\n"
            f"المستحق للمراجعة اليوم: {len(due_weak)}\n\n"
            f"اختر كيف تريد المراجعة:",
            InlineKeyboardMarkup(kb)
        )

    # ── Start weak all ──
    elif data == "start_weakall":
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id=0, session_type="weakall")

    # ── My stats (إحصائياتي) ──
    elif data == "weekly_stats":
        MONTH_AR = {
            "01": "يناير", "02": "فبراير", "03": "مارس", "04": "أبريل",
            "05": "مايو", "06": "يونيو", "07": "يوليو", "08": "أغسطس",
            "09": "سبتمبر", "10": "أكتوبر", "11": "نوفمبر", "12": "ديسمبر"
        }
        user = update.effective_user
        u_id = user.id if user else 6099429826
        stats = db.get_my_stats(user_id=u_id)
        t = stats["total"]
        c = stats["correct"]
        w = stats["wrong"]
        s = stats["sessions"]
        pct = int((c / t) * 100) if t > 0 else 0

        if pct >= 80: medal = "🏆"
        elif pct >= 60: medal = "👍"
        elif pct >= 40: medal = "📚"
        else: medal = "💪"

        lines = [f"📊 <b>إحصائياتي الشخصية</b>\n"]

        if t == 0:
            lines.append("لا توجد بيانات بعد. ابدأ بحل الكويزات لتظهر إحصائياتك هنا! 💪")
        else:
            lines += [
                f"────── جملة عامة ──────",
                f"📖 عدد الجلسات: <b>{s}</b>",
                f"📝 إجمالي الأسئلة: <b>{t}</b>",
                f"✅ صحيح: <b>{c}</b>  |  ❌ خطأ: <b>{w}</b>",
                f"{medal} النسبة العامة: <b>{pct}%</b>",
                f"❌ الأسئلة الضعيفة الحالية: <b>{stats['total_weak']}</b>",
            ]

            if stats["best_month"]:
                bm = stats["best_month"]["month_key"]
                yr, mo = bm.split("-")
                bm_ar = f"{MONTH_AR.get(mo, mo)} {yr}"
                lines.append(f"🌟 أفضل شهر: <b>{bm_ar}</b> ({stats['best_month']['pct']}%)")

            if stats["monthly"]:
                lines.append("")
                lines.append("────── تفصيل شهري ──────")
                for m in stats["monthly"]:
                    yr, mo = m["month_key"].split("-")
                    m_ar = f"{MONTH_AR.get(mo, mo)} {yr}"
                    m_pct = int((m["correct"] / m["total"]) * 100) if m["total"] else 0
                    lines.append(
                        f"🗓 <b>{m_ar}</b>: {m['sessions']} جلسة | "
                        f"✅{m['correct']} ❌{m['wrong']} | {m_pct}%"
                    )

        await safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(back_btn))

    # ── Start weak (spaced repetition) ──
    elif data.startswith("start_weak_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="weak")

    # ── Start weak (practice all) ──
    elif data.startswith("start_weakpractice_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="weakpractice")

    # ── Review schedule ──
    elif data == "review_schedule" or data.startswith("review_schedule_"):
        try:
            page = int(data.split("_")[-1])
        except (ValueError, TypeError):
            page = 1

        user = update.effective_user
        u_id = user.id if user else 6099429826
        
        user_reviews = db.get_all_quiz_reviews(user_id=u_id)
        if not user_reviews:
            await safe_edit(
                query,
                "📅 <b>جدول المراجعات فارغ حالياً!</b>\n\n"
                "تصفح <b>بنك الكويزات العام</b> واختر أي كويز ترغب بجدولته في نظام التكرار المتباعد 🧠.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("📚 تصفح بنك الكويزات العام", callback_data="public_bank_root")],
                    back_btn[0]
                ])
            )
            return

        ITEMS_PER_PAGE = 8
        total_pages = (len(user_reviews) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        page_reviews = user_reviews[start_idx:end_idx]

        lines = [
            f"📅 <b>جدول مراجعاتي</b> (صفحة {page} من {total_pages})",
            f"إجمالي الكويزات المجدولة: <b>{len(user_reviews)}</b> كويز",
            "────────────────────"
        ]

        for item in page_reviews:
            name_safe = html.escape(item.get("quiz_name", "كويز"))
            stage = item.get("stage", 0) or 0
            bar = "✅" * stage + "◻️" * (5 - stage)
            
            lines.append(f"📚 <b>{name_safe}</b>")
            lines.append(f"📊 التقدم: {bar} ({stage}/5)")

            if item.get("next_review_date"):
                days = days_until(item["next_review_date"])
                lbl = stage_label(stage)
                if days <= 0:
                    lines.append(f"🔁 <b>{lbl}</b> — 🔴 <b>مستحقة اليوم!</b> ⚠️")
                elif days == 1:
                    lines.append(f"🔁 <b>{lbl}</b> — 🟡 غداً 🔔")
                else:
                    lines.append(f"🔁 <b>{lbl}</b> — ⏳ بعد {days} يوم")
            else:
                lines.append("✅ <b>اكتملت جميع مراحل المراجعة</b> 🎉")

            lines.append("────────────────────")

        kb = []
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"review_schedule_page_{page-1}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"review_schedule_page_{page+1}"))
        if nav_row:
            kb.append(nav_row)

        kb.append(back_btn[0])
        await safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(kb))

    # ── Fixstage pagination ──
    elif data.startswith("fixstage_page_"):
        page = int(data.split("_")[-1])
        await fixstage_command(update, context, page)

    # ── Fix Stage Menu ──
    elif data.startswith("fixstage_menu_") or data.startswith("fixstage_set_"):
        if data.startswith("fixstage_menu_"):
            quiz_id = int(data.split("_")[-1])
        else:
            parts = data.split("_")
            quiz_id = int(parts[2])
            new_stage = int(parts[3])
            
            # Ensure boundaries
            if new_stage < 0: new_stage = 0
            if new_stage > 4: new_stage = 4
            
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE quiz_reviews SET stage = ? WHERE quiz_id = ?", (new_stage, quiz_id))
            conn.commit()
            conn.close()

        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await safe_edit(query, "الكويز غير موجود.", InlineKeyboardMarkup(back_btn))
            return

        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quiz_reviews WHERE quiz_id = ?", (quiz_id,))
        review = cursor.fetchone()
        conn.close()

        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        if not review:
            await safe_edit(query, f"✅ كويز <b>{q_name}</b> اكتملت مراجعاته كلها.", InlineKeyboardMarkup(back_btn))
            return



        stage = review["stage"]
        next_date = review["next_review_date"]
        days = days_until(next_date)

        if days <= 0:
            status = f"🔴 <b>متأخر {abs(days)} يوم</b> (can review now)"
        elif days == 1:
            status = "🟡 غداً"
        else:
            status = f"⏳ بعد <b>{days} يوم</b>"

        stage_labels = ["أولى", "ثانية", "ثالثة", "رابعة", "خامسة"]
        current_stage_label = stage_labels[stage] if stage < len(stage_labels) else str(stage)

        # Compute the next date string after completing this stage
        # To get the custom category intervals, we should fetch from db, or fallback to default
        intervals = DEFAULT_REVIEW_INTERVALS
        if quiz.get("category_id"):
            conn = db.get_connection()
            c = conn.execute("SELECT intervals_json FROM categories WHERE id = ?", (quiz["category_id"],))
            row = c.fetchone()
            if row:
                try: intervals = json.loads(row[0])
                except: pass
            conn.close()

        next_interval = intervals[stage + 1] if stage + 1 < len(intervals) else None

        text = (
            f"🛠 <b>تعديل موعد المراجعة</b>\n"
            f"📚 {q_name}\n\n"
            f"🗓 المرحلة: <b>ال{current_stage_label}</b>\n"
            f"📅 موعدها: <b>{next_date}</b> — {status}\n"
        )
        if next_interval is not None:
            after_complete = (date.today() + timedelta(days=next_interval)).isoformat()
            text += f"ℹ️ بعد إتمامها ستكون التالية: <b>{after_complete}</b>\n"
        text += f"\nاختر متى تريد مراجعتها:"

        kb = [
            [InlineKeyboardButton("➖ المرحلة السابقة", callback_data=f"fixstage_set_{quiz_id}_{stage-1}"),
             InlineKeyboardButton("➕ المرحلة التالية", callback_data=f"fixstage_set_{quiz_id}_{stage+1}")],
            [InlineKeyboardButton("🔴 اليوم", callback_data=f"fixdate_{quiz_id}_0"),
             InlineKeyboardButton("🟡 غداً", callback_data=f"fixdate_{quiz_id}_1")],
            [InlineKeyboardButton("🔵 بعد يومين", callback_data=f"fixdate_{quiz_id}_2"),
             InlineKeyboardButton("🔵 بعد 3 أيام", callback_data=f"fixdate_{quiz_id}_3")],
            [InlineKeyboardButton("🔵 بعد 7 أيام", callback_data=f"fixdate_{quiz_id}_7"),
             InlineKeyboardButton("🔵 بعد 14 يوم", callback_data=f"fixdate_{quiz_id}_14")],
            [InlineKeyboardButton("✅ تم الحل (إكمال المراجعة)", callback_data=f"fixstage_done_{review['id']}_{quiz_id}")],
            [InlineKeyboardButton("🛠 تعديل أسئلة الكويز", callback_data=f"fixstage_qlist_{quiz_id}_0")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="fixstage_page_1")]
        ]
        await safe_edit(query, text, InlineKeyboardMarkup(kb))

    # ── Fixstage done (mark review as completed) ──
    elif data.startswith("fixstage_done_"):
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])

        db.advance_quiz_review(review_id)
        await query.answer("✅ تم تحديث المراجعة وتسجيلها كمكتملة!")
        # reload menu
        query.data = f"fixstage_menu_{quiz_id}"
        await main_menu_handler(update, context)

    # ── Fixstage List Questions ──
    elif data.startswith("fixstage_qlist_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        page = int(parts[3])
        
        questions = db.get_questions(quiz_id)
        if not questions:
            await safe_edit(query, "❌ لا توجد أسئلة.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_menu_{quiz_id}")]]))
            return
            
        ITEMS_PER_PAGE = 10
        total_pages = (len(questions) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        if page >= total_pages: page = total_pages - 1
        if page < 0: page = 0
        
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        page_qs = questions[start_idx:end_idx]
        
        kb = []
        for idx, q in enumerate(page_qs):
            q_num = start_idx + idx + 1
            q_text = str(q.get("question_text", ""))
            if len(q_text) > 40:
                q_text = q_text[:37] + "..."
            kb.append([InlineKeyboardButton(f"سؤال {q_num}: {q_text}", callback_data=f"fixstage_qedit_{quiz_id}_{q['id']}")])
            
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"fixstage_qlist_{quiz_id}_{page-1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"fixstage_qlist_{quiz_id}_{page+1}"))
        if nav_row:
            kb.append(nav_row)
            
        kb.append([InlineKeyboardButton("🔙 رجوع لإعدادات الكويز", callback_data=f"fixstage_menu_{quiz_id}")])
        
        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        
        text = f"🛠 <b>تعديل أسئلة الكويز</b>\n📚 {q_name}\n\nاختر السؤال المراد تعديله:"
        await safe_edit(query, text, InlineKeyboardMarkup(kb), context=context)

    # ── Edit Question Menu ──
    elif data.startswith("fixstage_qedit_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        
        question = db.get_question(q_id)
        if not question:
            await safe_edit(query, "❌ السؤال غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"fixstage_qlist_{quiz_id}_0")]]))
            return
            
        q_text = html.escape(str(question.get("question_text", "")))
        options = question.get("options", [])
        correct = html.escape(str(question.get("correct_answer", "")))
        
        text = f"📝 <b>السؤال:</b>\n{q_text}\n\n<b>الخيارات:</b>\n"
        for i, opt in enumerate(options):
            opt_safe = html.escape(str(opt))
            if str(opt).strip() == correct.strip():
                text += f"✅ {opt_safe}\n"
            else:
                text += f"🔘 {opt_safe}\n"
                
        text += "\nاختر ماذا تريد أن تفعل:"
        
        kb = []
        kb.append([InlineKeyboardButton("✏️ تعديل نص السؤال", callback_data=f"fixstg_edittext_{quiz_id}_{q_id}")])
        for i, opt in enumerate(options):
            kb.append([InlineKeyboardButton(f"✅ جعل الخيار '{str(opt)[:20]}' الإجابة الصحيحة", callback_data=f"fixstg_setans_{quiz_id}_{q_id}_{i}")])
            
        kb.append([InlineKeyboardButton("🗑 حذف السؤال نهائياً", callback_data=f"fixstg_qdel_{quiz_id}_{q_id}")])
        kb.append([InlineKeyboardButton("🔙 رجوع لقائمة الأسئلة", callback_data=f"fixstage_qlist_{quiz_id}_0")])
        
        await safe_edit(query, text, InlineKeyboardMarkup(kb), context=context)

    # ── Edit Text Mode ──
    elif data.startswith("fixstg_edittext_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        context.user_data["editing_q_text"] = q_id
        context.user_data["editing_q_quiz"] = quiz_id
        await query.answer()
        await context.bot.send_message(
            update.effective_chat.id, 
            "✍️ الرجاء إرسال النص الجديد للسؤال الآن:\n(لإلغاء العملية، اضغط /menu)"
        )

    # ── Set Correct Answer ──
    elif data.startswith("fixstg_setans_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        opt_idx = int(parts[4])
        
        question = db.get_question(q_id)
        if question:
            options = question.get("options", [])
            if 0 <= opt_idx < len(options):
                new_correct = options[opt_idx]
                conn = db.get_connection()
                conn.execute("UPDATE questions SET correct_answer = ? WHERE id = ?", (new_correct, q_id))
                conn.commit()
                conn.close()
                await query.answer("✅ تم تحديث الإجابة الصحيحة!")
                
        query.data = f"fixstage_qedit_{quiz_id}_{q_id}"
        await main_menu_handler(update, context)

    # ── Delete Question ──
    elif data.startswith("fixstg_qdel_"):
        parts = data.split("_")
        quiz_id = int(parts[2])
        q_id = int(parts[3])
        
        conn = db.get_connection()
        conn.execute("DELETE FROM questions WHERE id = ?", (q_id,))
        conn.commit()
        conn.close()
        
        await query.answer("🗑 تم حذف السؤال!")
        query.data = f"fixstage_qlist_{quiz_id}_0"
        await main_menu_handler(update, context)
    elif data.startswith("fixstage_done_"):
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])
        db.advance_quiz_review(review_id)
        
        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
        
        await safe_edit(
            query,
            f"✅ <b>تمت المراجعة!</b>\n\n"
            f"تم تحديث موعد كويز <b>{q_name}</b> للمرحلة التالية بنجاح.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ تعديل الكويز مجدداً", callback_data=f"fixstage_menu_{quiz_id}")],
                [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="fixstage_page_1")]
            ])
        )

    # ── Fix date (set next_review_date directly, keep stage) ──
    elif data.startswith("fixdate_"):
        parts = data.split("_")
        quiz_id = int(parts[1])
        days_offset = int(parts[2])


        # If "today" → set yesterday so it's immediately available (overdue), bypass 6PM rule
        if days_offset == 0:
            new_date = (date.today() - timedelta(days=1)).isoformat()
            day_label = "الآن فوراً 🔴"
        else:
            new_date = (date.today() + timedelta(days=days_offset)).isoformat()
            day_label = "غداً" if days_offset == 1 else f"بعد {days_offset} يوم"

        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE quiz_reviews SET next_review_date = ? WHERE quiz_id = ?",
            (new_date, quiz_id)
        )
        conn.commit()
        conn.close()

        quiz = db.get_quiz(quiz_id)
        q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"

        await safe_edit(
            query,
            f"✅ تم التعديل!\n\n"
            f"📚 <b>{q_name}</b>\n"
            f"📅 ستظهر للمراجعة: <b>{new_date}</b> ({day_label})\n\n"
            f"<i>المرحلة لم تتغير، فقط التاريخ تغير.</i>",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="fixstage_page_1")]])
        )
