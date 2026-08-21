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
                quiz_id = db.save_quiz_url(extracted_title, url)
                text = (
                    f"✅ <b>تم استخراج الاسم وإضافة الكويز بنجاح!</b>\n\n"
                    f"📚 الكويز: <b>{html.escape(extracted_title)}</b>\n"
                    f"تم جدولة المراجعة في نظام التكرار المتباعد.\n\n"
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
            
            quiz_id = db.save_quiz_url(name, url)
            
            text = (
                f"✅ <b>تمت الإضافة بنجاح!</b>\n\n"
                f"📚 الكويز: <b>{html.escape(name)}</b>\n"
                f"تم جدولة المراجعة في نظام التكرار المتباعد.\n\n"
                f"<i>سيتم تذكيرك بالرابط عندما يحين وقت المراجعة.</i>"
            )
            await send_clean_message(context, chat_id, text, update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))
        except Exception as e:
            await send_clean_message(context, chat_id, f"❌ حدث خطأ أثناء الحفظ: {e}", update=update)
        return


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    session = db.get_session()
    rows = []
    if session and session.get("question_ids") and session.get("current_index", 0) < len(session["question_ids"]):
        cur_q = session.get("current_index", 0) + 1
        tot_q = len(session["question_ids"])
        rows.append([InlineKeyboardButton(f"▶️ استكمال الكويز ({cur_q}/{tot_q})", callback_data="resume_quiz")])

    rows.extend([
        [InlineKeyboardButton("📋 رفع كويز JSON", callback_data="upload_json"),
         InlineKeyboardButton("🔗 إضافة كويز كرابط", callback_data="upload_url")],
        [InlineKeyboardButton("📚 كويزاتي", callback_data="my_quizzes")],
        [
            InlineKeyboardButton("🔁 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("❌ الأسئلة الضعيفة", callback_data="weak_questions"),
        ],
        [InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule"),
         InlineKeyboardButton("📊 إحصائياتي", callback_data="weekly_stats")],
    ])
    return InlineKeyboardMarkup(rows)


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
    text = (
        "👋 أهلاً بك في <b>MemoryQudrat</b>!\n\n"
        "نظام مراجعة ذكي باستخدام التكرار المتباعد 🧠\n"
        "اختر ما تريد:"
    )
    if update.message:
        await send_clean_message(
            context, update.effective_chat.id, text,
            update=update, reply_markup=main_menu_keyboard()
        )
    elif update.callback_query:
        await safe_edit(update.callback_query, text, main_menu_keyboard())


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

    # ── Upload JSON ──
    elif data == "upload_json":
        await safe_edit(
            query,
            "📋 <b>رفع كويز JSON</b>\n\n"
            "أرسل ملف JSON بالكويز. أرسل /template لتحميل نموذج 📥",
            reply_markup=InlineKeyboardMarkup(back_btn),
        )

    # ── Upload URL ──
    elif data == "upload_url":
        await safe_edit(
            query,
            "🔗 <b>إضافة كويز كرابط</b>\n\n"
            "أرسل رابط الكويز (مثلاً رابط Google Forms).",
            reply_markup=InlineKeyboardMarkup(back_btn),
        )
        context.user_data["waiting_for_url"] = True

    # ── My Quizzes ──
    elif data == "my_quizzes" or data.startswith("my_quizzes_page_"):
        page = 1
        if data.startswith("my_quizzes_page_"):
            page = int(data.split("_")[-1])

        quizzes = db.get_all_quizzes()
        if not quizzes:
            await safe_edit(query,
                "📭 لا يوجد كويزات بعد!\n\nارفع ملف JSON لإنشاء أول كويز.",
                InlineKeyboardMarkup(back_btn)
            )
        else:
            keyboard, cur_page, total_pages = quizzes_keyboard(quizzes, page)
            await safe_edit(query,
                f"📚 <b>كويزاتي</b> — {len(quizzes)} كويز (صفحة {cur_page}/{total_pages})",
                keyboard
            )

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
        
        reviews = db.get_due_quiz_reviews()
        if not reviews:
            await safe_edit(query,
                "✅ لا توجد مراجعات اليوم!\n\nاستمر بالعمل الجيد 💪",
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
            # If we didn't add due_reviews_keyboard, we need to add the back button
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
            # It's a link quiz
            kb = [
                [InlineKeyboardButton("🌐 افتح الرابط", url=quiz["url"])],
                [InlineKeyboardButton("✅ تم الحل", callback_data=f"done_url_review_{review_id}")],
                [InlineKeyboardButton("🔄 تحويل إلى JSON", callback_data=f"upgrade_json_{quiz_id}")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="due_reviews")],
            ]
            q_name = html.escape(quiz.get("name", "كويز"))
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
        db.advance_quiz_review(review_id)
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
        all_weak = db.get_all_weak_questions()
        if not all_weak:
            await safe_edit(query,
                "✅ لا توجد أسئلة ضعيفة مسجلة!\n\nأداؤك ممتاز 🌟",
                InlineKeyboardMarkup(back_btn)
            )
            return
        
        # Count ALL weak questions (not just due)
        all_weak_count = len(all_weak)
        all_due_weak = db.get_due_all_weak_questions_sorted()
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
        all_weak = db.get_all_weak_questions()
        
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
        stats = db.get_my_stats()
        t = stats["total"]
        c = stats["correct"]
        w = stats["wrong"]
        s = stats["sessions"]
        pct = int((c / t) * 100) if t > 0 else 0

        if pct >= 80: medal = "🏆"
        elif pct >= 60: medal = "👍"
        elif pct >= 40: medal = "📚"
        else: medal = "💪"

        lines = [f"📊 <b>إحصائياتي</b>\n"]

        if t == 0:
            lines.append("لا توجد بيانات بعد. ابدأ بالمراجعة لتظهر إحصائياتك! 💪")
        else:
            lines += [
                f"────── جملة عامة ──────",
                f"📖 عدد الجلسات: <b>{s}</b>",
                f"📝 إجمالي الأسئلة: <b>{t}</b>",
                f"✅ صحيح: <b>{c}</b>  |  ❌ خطأ: <b>{w}</b>",
                f"{medal} النسبة العامة: <b>{pct}%</b>",
                f"🖥 الكويزات المجدولة: <b>{stats['total_quizzes']}</b>",
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
    elif data.startswith("review_schedule"):
        parts = data.split("_")
        page = int(parts[2]) if len(parts) > 2 else 1
        
        quizzes = db.get_all_quizzes()
        if not quizzes:
            await safe_edit(query, "📅 لا توجد كويزات بعد!", InlineKeyboardMarkup(back_btn))
            return

        ITEMS_PER_PAGE = 10
        total_pages = (len(quizzes) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        if page > total_pages: page = total_pages
        if page < 1: page = 1
        
        start_idx = (page - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        page_quizzes = quizzes[start_idx:end_idx]

        conn = db.get_connection()
        cursor = conn.cursor()
        lines = [f"📅 <b>جدول المراجعة</b> (صفحة {page} من {total_pages})", ""]

        quiz_ids = [str(q["id"]) for q in page_quizzes]
        placeholders = ",".join(quiz_ids)
        cursor.execute(f"""
            SELECT 
                q.id, q.name,
                (SELECT COUNT(*) FROM questions WHERE quiz_id = q.id) as questions_count,
                qr.stage, qr.next_review_date,
                (SELECT COUNT(*) FROM weak_questions WHERE quiz_id = q.id) as weak_count,
                (SELECT COUNT(*) FROM weak_questions WHERE quiz_id = q.id AND next_review_date <= date('now')) as weak_due
            FROM quizzes q
            LEFT JOIN quiz_reviews qr ON qr.quiz_id = q.id
            WHERE q.id IN ({placeholders})
            ORDER BY q.id DESC
        """)
        results = [dict(r) for r in cursor.fetchall()]
        conn.close()

        for item in results:
            name_safe = html.escape(item.get("name", "كويز"))
            lines.append("──────────────────")
            lines.append(f"📚 <b>{name_safe}</b>")
            lines.append(f"📝 {item['questions_count']} سؤال")

            if item["next_review_date"]:
                days = days_until(item["next_review_date"])
                lbl = stage_label(item["stage"] or 0)
                if days <= 0:
                    lines.append(f"🔁 {lbl}: <b>مستحقة!</b> ⚠️")
                elif days == 1:
                    lines.append(f"🔁 {lbl}: غداً")
                else:
                    lines.append(f"🔁 {lbl}: بعد {days} يوم")
                stages_done = item["stage"] or 0
                bar = "✅" * stages_done + "◻️" * (5 - stages_done)
                lines.append(f"📊 التقدم: {bar} ({stages_done}/5)")
            else:
                lines.append("✅ اكتملت جميع مراجعات الكويز")

            weak_count = item["weak_count"]
            weak_due = item["weak_due"]
            if weak_count > 0:
                if weak_due > 0:
                    lines.append(f"❌ {weak_count} سؤال ضعيف — <b>{weak_due} مستحقة اليوم</b> ⚠️")
                else:
                    lines.append(f"❌ {weak_count} سؤال ضعيف — لا يوجد مستحق اليوم")
            else:
                lines.append("🌟 لا توجد أسئلة ضعيفة")
            lines.append("")
        
        # Pagination buttons
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"review_schedule_{page-1}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"review_schedule_{page+1}"))
            
        kb = []
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
