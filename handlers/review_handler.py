import os
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_authorized
from spaced_repetition import stage_label
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


# ─── Review Session Engine ───────────────────────────────────────────────────

async def start_review_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE, folder_id: int = None
):
    """Initiates an active recall review session (either global or folder-specific)."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    query = update.callback_query

    due_items = db.get_due_items(folder_id)
    folder_name = "جميع المجلدات"
    if folder_id:
        f = db.get_folder(folder_id)
        if f:
            folder_name = f"{f['icon']} {f['name']}"

    if not due_items:
        clean_text = (
            f"🎉 <b>أحسنت يا بطل! جدولك نظيف تماماً</b> 🌟\n\n"
            f"لا توجد أي مراجعات مستحقة اليوم في: <b>{html.escape(folder_name)}</b>.\n\n"
            f"💪 <i>ذاكرتك في أعلى مستويات الأمان. استغل وقتك في إضافة مسائل جديدة من تجميعاتك!</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 استعراض المجلدات", callback_data="folders_menu")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ])
        if query:
            await safe_edit(query, clean_text, kb)
        else:
            await send_clean_message(context, chat_id, clean_text, update=update, reply_markup=kb)
        return

    # Initialize review session state
    item_ids = [item["id"] for item in due_items]
    context.user_data["active_review_session"] = {
        "item_ids": item_ids,
        "index": 0,
        "folder_id": folder_id,
        "session_message_ids": [],
    }

    # Clean prior menu message
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    await show_current_review_item(update, context)


async def show_current_review_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renders the current item in the active review session."""
    session = context.user_data.get("active_review_session")
    if not session:
        return

    chat_id = update.effective_chat.id
    idx = session["index"]
    item_ids = session["item_ids"]

    # Check if session is finished
    if idx >= len(item_ids):
        await finish_review_session(update, context)
        return

    item_id = item_ids[idx]
    item = db.get_item(item_id)
    if not item:
        # Item deleted, advance safely
        session["index"] += 1
        await show_current_review_item(update, context)
        return

    total = len(item_ids)
    f_icon = item.get("folder_icon") or "📁"
    f_name = item.get("folder_name") or "عام"
    stg_lbl = stage_label(item["stage"], bool(item["is_mastered"]))

    header = (
        f"📝 <b>مراجعة {idx + 1} من {total}</b> | {f_icon} <b>{html.escape(f_name)}</b>\n"
        f"📅 <i>{stg_lbl}</i>\n"
        f"────────────────────\n"
    )

    review_mode = item.get("review_mode", "qa")
    item_type = item.get("item_type", "photo")
    content = item.get("content", "")

    # Clean old session messages before showing next item
    old_ids = session.get("session_message_ids", [])
    if old_ids:
        from utils import delete_messages_bulk
        await delete_messages_bulk(context, chat_id, old_ids)
        session["session_message_ids"] = []

    # ── 1. Photo Item ──
    if item_type == "photo":
        full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), content)
        if not os.path.exists(full_path) and os.path.exists(content):
            full_path = content

        if review_mode == "direct":
            # Direct review mode: photo + direct 3 rating buttons underneath!
            caption = (
                f"{header}\n"
                f"📖 <b>مراجعة مباشرة وتثبيت:</b>\n"
                f"<i>اقرأ وراجع المحتوى أعلاه ثم قيّم استقرار المعلومة في ذاكرتك:</i>"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔴 أحتاج تكرارها", callback_data=f"rev_rate_{item_id}_hard"),
                    InlineKeyboardButton("🟡 مألوفة لدي", callback_data=f"rev_rate_{item_id}_good"),
                    InlineKeyboardButton("🟢 متقنة ومحفوظة", callback_data=f"rev_rate_{item_id}_easy"),
                ],
                [InlineKeyboardButton("⏹️ إنهاء الجلسة مؤقتاً", callback_data="exit_review_session")],
            ])
            with open(full_path, "rb") as pf:
                msg = await context.bot.send_photo(chat_id=chat_id, photo=pf, caption=caption, parse_mode="HTML", reply_markup=kb)
            session["session_message_ids"].append(msg.message_id)
            db.track_chat_message(chat_id, msg.message_id)
            return

        else:
            # QA mode: photo + Reveal Button first
            caption = (
                f"{header}\n"
                f"👇 <b>فكر في طريقة الحل أولاً، ثم اضغط لإظهار الشرح والتأكد:</b>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💡 إظهار الحل / الشرح", callback_data=f"rev_reveal_{item_id}")],
                [InlineKeyboardButton("⏹️ إنهاء الجلسة مؤقتاً", callback_data="exit_review_session")],
            ])
            with open(full_path, "rb") as pf:
                msg = await context.bot.send_photo(chat_id=chat_id, photo=pf, caption=caption, parse_mode="HTML", reply_markup=kb)
            session["session_message_ids"].append(msg.message_id)
            db.track_chat_message(chat_id, msg.message_id)
            return

    # ── 2. Link Item (Forms / Quiz URLs) ──
    if item_type == "link":
        text = (
            f"{header}\n"
            f"🔗 <b>اختبار فورمز: {html.escape(item.get('title') or 'رابط كويز')}</b>\n\n"
            f"اضغط على الزر بالأسفل لفتح وحل الكويز في المتصفح:\n"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 افتح وحل الكويز في المتصفح", url=content)],
            [InlineKeyboardButton("✅ أتممت الحل بنجاح اليوم", callback_data=f"rev_rate_{item_id}_good")],
            [InlineKeyboardButton("🔴 واجهت صعوبة / لم أتقنه", callback_data=f"rev_rate_{item_id}_hard")],
            [InlineKeyboardButton("⏹️ إنهاء الجلسة", callback_data="exit_review_session")],
        ])
        msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=kb)
        session["session_message_ids"].append(msg.message_id)
        db.track_chat_message(chat_id, msg.message_id)
        return

    # ── 3. Text Item ──
    if item_type == "text":
        if review_mode == "direct":
            text = (
                f"{header}\n"
                f"<b>{html.escape(content)}</b>\n\n"
                f"👇 <i>قيّم استقرار هذه المعلومة في ذاكرتك:</i>"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔴 أحتاج تكرارها", callback_data=f"rev_rate_{item_id}_hard"),
                    InlineKeyboardButton("🟡 مألوفة لدي", callback_data=f"rev_rate_{item_id}_good"),
                    InlineKeyboardButton("🟢 متقنة ومحفوظة", callback_data=f"rev_rate_{item_id}_easy"),
                ],
                [InlineKeyboardButton("⏹️ إنهاء", callback_data="exit_review_session")],
            ])
        else:
            text = (
                f"{header}\n"
                f"❓ <b>{html.escape(content)}</b>\n\n"
                f"👇 <i>فكر في الإجابة ثم اضغط لإظهار الحل:</i>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💡 إظهار الحل / الشرح", callback_data=f"rev_reveal_{item_id}")],
                [InlineKeyboardButton("⏹️ إنهاء", callback_data="exit_review_session")],
            ])
        msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=kb)
        session["session_message_ids"].append(msg.message_id)
        db.track_chat_message(chat_id, msg.message_id)
        return

    # ── 4. Document / PDF Item ──
    if item_type == "pdf":
        text = (
            f"{header}\n"
            f"📑 <b>مستند: {html.escape(item.get('title') or 'ملف PDF')}</b>\n"
            f"مسار التخزين: <code>{html.escape(content)}</code>\n\n"
            f"👇 <i>راجع المستند ثم قيّم إنجازك للمراجعة:</i>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔴 لم أراجعه / صعب", callback_data=f"rev_rate_{item_id}_hard"),
                InlineKeyboardButton("🟢 أتممت مراجعته بنجاح", callback_data=f"rev_rate_{item_id}_good"),
            ],
            [InlineKeyboardButton("⏹️ إنهاء", callback_data="exit_review_session")],
        ])
        msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=kb)
        session["session_message_ids"].append(msg.message_id)
        db.track_chat_message(chat_id, msg.message_id)


async def finish_review_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finishes the review session, cleans up, and displays a summary."""
    session = context.user_data.pop("active_review_session", None)
    chat_id = update.effective_chat.id

    if session:
        old_ids = session.get("session_message_ids", [])
        if old_ids:
            from utils import delete_messages_bulk
            await delete_messages_bulk(context, chat_id, old_ids)

    stats = db.get_dashboard_stats()
    text = (
        "🎉 <b>اكتملت جلسة المراجعة بنجاح استثنائي!</b> 🌟\n\n"
        "أحسنت العمل يا بطل! تم ترقية وتثبيت استقرار جميع المسائل المنجزة في منحنى الذاكرة طويلة المدى.\n\n"
        f"📊 <b>نبض اليوم:</b>\n"
        f"• إجمالي بنك الذاكرة: <b>{stats['total_items']}</b> مادة\n"
        f"• المتقن في الذاكرة الدائمة: <b>{stats['mastered_count']}</b> 🏆\n"
        f"• المتبقي اليوم: <b>{stats['due_count']}</b> مادة\n\n"
        "استمر على هذا الانضباط، والدرجة الكاملة 100% في متناول يدك بإذن الله 💪"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 استعراض مجلداتي", callback_data="folders_menu")],
        [InlineKeyboardButton("🏠 العودة للقائمة الرئيسية", callback_data="main_menu")],
    ])
    await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)


# ─── Review Callbacks Router ──────────────────────────────────────────────────

async def handle_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data

    # Exit session
    if data == "exit_review_session":
        await query.answer("تم إنهاء جلسة المراجعة مؤقتاً.")
        await finish_review_session(update, context)
        return True

    # Reveal Solution in QA mode
    if data.startswith("rev_reveal_"):
        item_id = int(data.split("_")[-1])
        item = db.get_item(item_id)
        if not item:
            await query.answer("المسألة غير موجودة.")
            return True

        sol_text = item.get("solution") or "💡 قارن إجابتك بالحل النموذجي واستراتيجيات القدرات السريعة."
        reveal_msg = (
            f"💡 <b>الحل والشرح النموذجي:</b>\n\n"
            f"<blockquote>{html.escape(sol_text)}</blockquote>\n\n"
            f"👇 <b>كيف كان استرجاعك للحل؟</b>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔴 نسيت/أخطأت", callback_data=f"rev_rate_{item_id}_hard"),
                InlineKeyboardButton("🟡 بجهد/متوسط", callback_data=f"rev_rate_{item_id}_good"),
                InlineKeyboardButton("🟢 سهل ومتقن", callback_data=f"rev_rate_{item_id}_easy"),
            ]
        ])

        # If caption exists on photo, edit caption, else edit text
        if query.message and query.message.photo:
            try:
                caption = (query.message.caption or "") + "\n\n" + reveal_msg
                await query.edit_message_caption(caption=caption[:1024], parse_mode="HTML", reply_markup=kb)
            except Exception:
                await query.message.reply_text(reveal_msg, parse_mode="HTML", reply_markup=kb)
        else:
            await safe_edit(query, (query.message.text or "") + "\n\n" + reveal_msg, kb)
        return True

    # Rating button clicked
    if data.startswith("rev_rate_"):
        parts = data.split("_")
        item_id = int(parts[2])
        rating = parts[3]  # 'easy', 'good', 'hard'

        # Record rating in database
        _ = db.record_item_review(item_id, rating)

        # Immediate feedback toast
        labels = {"easy": "🟢 ممتاز! قفزت مرحلتين للأمام 🌟", "good": "🟡 أحسنت! تقدمت مرحلة ⚡", "hard": "🔴 سيعاد تكرارها غداً للترسيخ 💪"}
        await query.answer(labels.get(rating, "تم الحفظ!"))

        # Advance session
        session = context.user_data.get("active_review_session")
        if session:
            session["index"] += 1
            await show_current_review_item(update, context)
        return True

    return False
