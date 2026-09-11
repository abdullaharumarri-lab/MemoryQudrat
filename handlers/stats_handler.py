"""
handlers/stats_handler.py — إحصائيات الطالب ولوحة الأداء الشخصية
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import ADMIN_USER_ID
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def _build_my_stats(user_id: int):
    stats = db.get_my_stats(user_id)
    total_q = stats["total"]
    correct_q = stats["correct"]
    acc = stats["accuracy"]
    sessions = stats["sessions"]
    mastered = stats["mastered_count"]
    active_rev = stats["active_reviews"]
    due_rev = stats["due_reviews"]
    total_weak = stats["total_weak"]
    due_weak = stats["due_weak"]
    weekly = stats.get("weekly", [])
    stages = stats.get("stage_breakdown", {})

    if total_q == 0:
        badge = "🌱 بداية موفقة"
        advice = "لم تبدأ حل الكويزات بعد! اختر كويزاً من بنك الكويزات وابدأ أولى خطواتك 🚀."
    elif acc >= 90:
        badge = "🏆 مستوى أسطوري"
        advice = "أداؤك استثنائي وثابت! حافظ على المراجعات المجدولة لضمان ترسيخ الذاكرة 🌟."
    elif acc >= 80:
        badge = "🌟 أداء ممتاز"
        advice = "دقتك عالية جداً! ركز على الأسئلة الضعيفة لتصل إلى 100% بإذن الله 💪."
    elif acc >= 65:
        badge = "👍 أداء جيد"
        advice = "أنت في مسار تصاعدي سليم، واظب على حل الكويزات وسيرتفع مستواك بسرعة 🎯."
    else:
        badge = "💪 يحتاج تركيز ومثابرة"
        advice = "التدريب المستمر هو سر النجاح، راجع أخطاءك في بنك الأسئلة الضعيفة أولاً بأول ✨."

    text_parts = [
        "📊 <b>لوحة إحصائياتك وأدائك الشخصي</b>\n",
        f"🎖️ <b>المستوى الحالي:</b> {badge}",
        f"🎯 <b>نسبة الصحة الإجمالية:</b> <b>{acc}%</b>",
        f"📝 <b>الأسئلة المحلولة:</b> <b>{total_q}</b> سؤال (عبر <b>{sessions}</b> جلسة)",
        f"✅ <b>الإجابات الصحيحة:</b> <b>{correct_q}</b> | ❌ <b>الخاطئة:</b> <b>{stats['wrong']}</b>",
        f"🏆 <b>الكويزات المتقنة:</b> <b>{mastered}</b> كويز (علامة كاملة 5 مرات متتالية)\n",
        "🧠 <b>التكرار المتباعد وجدولة الذاكرة:</b>",
        f"• 🔁 كويزات قيد المراجعة: <b>{active_rev}</b> (🔴 مستحق اليوم: <b>{due_rev}</b>)",
        f"• ❓ أسئلة ضعيفة تحت التدريب: <b>{total_weak}</b> (🔴 مستحق اليوم: <b>{due_weak}</b>)",
    ]

    if active_rev > 0:
        stage_strs = []
        labels = ["م1", "م2", "م3", "م4", "م5"]
        for stg_idx in range(5):
            c = stages.get(stg_idx, 0)
            if c > 0:
                stage_strs.append(f"{labels[stg_idx]}: {c}")
        if stage_strs:
            text_parts.append("• مراحل الحفظ: " + " | ".join(stage_strs))

    chart_lines = ["\n📈 <b>نشاطك في آخر 7 أيام:</b>"]
    any_weekly_activity = False
    for day in weekly:
        d_tot = day["total"]
        d_acc = day["accuracy"]
        d_name = day["day_name"]
        if d_tot > 0:
            any_weekly_activity = True
            filled = min(10, max(1, int(d_acc / 10)))
            empty = 10 - filled
            bar = "█" * filled + "░" * empty
            chart_lines.append(f"• {d_name:<7}: <code>{bar}</code> {d_acc}% ({d_tot} سؤال)")
        else:
            chart_lines.append(f"• {d_name:<7}: <i>استراحة</i> ☕")

    if any_weekly_activity:
        text_parts.extend(chart_lines)

    text_parts.append(f"\n💡 <i>{advice}</i>")

    kb = [
        [
            InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("❓ الأسئلة الضعيفة", callback_data="weak_questions"),
        ],
        [
            InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="my_stats"),
            InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu"),
        ]
    ]

    return "\n".join(text_parts), InlineKeyboardMarkup(kb)


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


async def handle_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int) -> bool:
    """
    Handles callbacks related to student statistics.
    Returns True if handled, False otherwise.
    """
    query = update.callback_query

    if data == "my_stats":
        text, kb = _build_my_stats(user_id)
        await safe_edit(query, text, kb)
        return True

    return False
