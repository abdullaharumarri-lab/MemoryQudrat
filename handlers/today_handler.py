import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from spaced_repetition import stage_label
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def build_today_tasks_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the single-screen today view with interactive checkoff buttons."""
    tasks = db.get_today_tasks(user_id)
    new_lessons = tasks["new_lessons"]
    due_reviews = tasks["due_reviews"]

    if not new_lessons and not due_reviews:
        text = (
            "🎉 <b>كفووو يا وحش! جدولك اليوم نظيف 100%!</b> 🌟\n\n"
            "أنهيت كل مهام المذاكرة ومراجعات التكرار المتباعد المقررة لليوم.\n"
            "راحة مستحقة، استمتع بوقتك ونلتقي بكرة بإذن الله مع إنجاز جديد! ☕💪"
        )
        kb = [
            [InlineKeyboardButton("📚 موادي وجداولي", callback_data="subjects_menu")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ]
        return text, InlineKeyboardMarkup(kb)

    text_lines = [
        "📅 <b>مهام إنجازك لليوم:</b> 🎯\n",
    ]

    kb = []

    # 1. New Lessons
    if new_lessons:
        text_lines.append(f"📖 <b>دروس جديدة للمذاكرة ({len(new_lessons)}):</b>")
        for les in new_lessons:
            s_name = les.get("subject_name", "")
            title = les.get("title", f"الدرس {les.get('lesson_order')}")
            text_lines.append(f"• <b>{html.escape(s_name)}:</b> {html.escape(title)}")
            # Add one-tap complete button
            kb.append([
                InlineKeyboardButton(
                    f"✅ تم مذاكرة: {title[:25]}",
                    callback_data=f"done_les_{les['id']}",
                )
            ])
        text_lines.append("")

    # 2. Due Reviews
    if due_reviews:
        text_lines.append(f"🔄 <b>مراجعات التثبيت الذكية ({len(due_reviews)}):</b>")
        for rev in due_reviews:
            s_name = rev.get("subject_name", "")
            title = rev.get("lesson_title", "")
            stage = rev.get("stage", 1)
            text_lines.append(f"• <b>{html.escape(s_name)}:</b> {html.escape(title)} <i>({stage_label(stage)})</i>")
            # Add one-tap complete button
            kb.append([
                InlineKeyboardButton(
                    f"🧠 تم مراجعة: {title[:25]}",
                    callback_data=f"done_rev_{rev['id']}",
                )
            ])
        text_lines.append("")

    text_lines.append("💡 <i>اضغط على الزر بالأسفل فور انتهائك من أي درس ليثبت البوت موعد مراجعته تلقائياً!</i>")

    kb.append([InlineKeyboardButton("🔄 تدارك وتعديل الجدول", callback_data="catchup_select")])
    kb.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


async def show_today_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /today or callback 'today_tasks'."""
    user = update.effective_user
    if not user:
        return
    text, kb = build_today_tasks_view(user.id)
    if update.callback_query:
        await safe_edit(update.callback_query, text, kb)
    else:
        await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def handle_today_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles check-off buttons and catch-up triggers."""
    query = update.callback_query
    if not query:
        return False
    data = query.data
    user = update.effective_user
    if not user:
        return False
    uid = user.id

    if data == "today_tasks":
        await show_today_tasks(update, context)
        return True

    # Complete a new lesson
    if data.startswith("done_les_"):
        try:
            lesson_id = int(data.split("_")[2])
        except (IndexError, ValueError):
            return False

        res = db.complete_lesson(lesson_id, uid)
        if res:
            await query.answer("كفو يا بطل! تم إنجاز الدرس وجدولة مراجعته الذكية 💪", show_alert=False)
        else:
            await query.answer("تم تسجيل الإنجاز سابقاً أو غير موجود.", show_alert=False)

        text, kb = build_today_tasks_view(uid)
        await safe_edit(query, text, kb)
        return True

    # Complete an active recall review
    if data.startswith("done_rev_"):
        try:
            review_id = int(data.split("_")[2])
        except (IndexError, ValueError):
            return False

        res = db.complete_review(review_id, uid)
        if res:
            if res.get("is_mastered"):
                await query.answer("🔥 أسطوري! تم تثبيت هذا الدرس في الذاكرة الدائمة 100%!", show_alert=False)
            else:
                await query.answer("أحسنت! تم تأكيد المراجعة وحساب موعد التكرار القادم 🎯", show_alert=False)
        else:
            await query.answer("تم تسجيل المراجعة سابقاً أو غير موجودة.", show_alert=False)

        text, kb = build_today_tasks_view(uid)
        await safe_edit(query, text, kb)
        return True

    # Catch-up Subject Selection
    if data == "catchup_select":
        subjects = db.get_user_subjects(uid)
        if not subjects:
            await query.answer("ما عندك أي مواد حالياً!", show_alert=True)
            return True

        text = (
            "🔄 <b>تدارك وتعديل الجدول</b> ⏱️\n\n"
            "تراكمت عليك دروس أو تأخرت يوم؟ ولا تشيل هم أبداً!\n"
            "اختر المادة اللي ودك نعيد جدولة دروسها المتبقية ابتداءً من اليوم بالتساوي وبدون أي ضغط:"
        )
        kb = []
        for s in subjects:
            rem = s["total_lessons"] - s["completed_count"]
            kb.append([
                InlineKeyboardButton(
                    f"{s['icon']} {s['name']} (متبقي {rem} درس)",
                    callback_data=f"catchup_do_{s['id']}",
                )
            ])
        kb.append([InlineKeyboardButton("🔙 العودة لمهام اليوم", callback_data="today_tasks")])
        await safe_edit(query, text, InlineKeyboardMarkup(kb))
        return True

    # Execute Catch-up for specific subject
    if data.startswith("catchup_do_"):
        try:
            sub_id = int(data.split("_")[2])
        except (IndexError, ValueError):
            return False

        res = db.reschedule_subject(sub_id, uid)
        msg = res.get("message", "تم التعديل بنجاح!")
        await query.answer(msg, show_alert=True)

        # Return to today view
        text, kb = build_today_tasks_view(uid)
        await safe_edit(query, text, kb)
        return True

    return False
