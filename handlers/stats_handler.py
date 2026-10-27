import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from utils import safe_edit

logger = logging.getLogger(__name__)


def build_stats_view() -> tuple[str, InlineKeyboardMarkup]:
    """Generates the personal analytics dashboard and activity report."""
    stats = db.get_dashboard_stats()
    total = stats["total_items"]
    active = stats["active_count"]
    mastered = stats["mastered_count"]
    due = stats["due_count"]
    by_stage = stats["by_stage"]
    by_type = stats["by_type"]
    recent_daily = stats["recent_daily"]

    # Calculate overall retention rate
    total_rev_all = sum(d["total_reviewed"] for d in recent_daily)
    easy_good_all = sum(d["easy_count"] + d["good_count"] for d in recent_daily)
    retention_pct = int((easy_good_all / total_rev_all) * 100) if total_rev_all > 0 else 100

    badge = "🏆 مستوى أسطوري" if retention_pct >= 90 else ("🌟 أداء متميز" if retention_pct >= 75 else "💪 في طور البناء")

    # Reminder time
    r_h = int(db.get_setting("reminder_hour", "5"))
    r_m = int(db.get_setting("reminder_minute", "0"))
    period = "ص" if r_h < 12 else "م"
    dh = r_h if 1 <= r_h <= 12 else (r_h - 12 if r_h > 12 else 12)

    text_lines = [
        "📊 <b>تقريرك الإحصائي ونبض الذاكرة</b> 🧠\n",
        f"🎖️ <b>المستوى التقييمي:</b> {badge}",
        f"🎯 <b>نسبة ثبات الذاكرة (Retention):</b> <b>{retention_pct}%</b>",
        f"📚 <b>إجمالي بنك المذاكرة:</b> <b>{total}</b> مادة دراسية",
        f"• 🔄 قيد التكرار المرحلي: <b>{active}</b> | 🔴 مستحق اليوم: <b>{due}</b>",
        f"• 🏆 مثبت في الذاكرة الدائمة: <b>{mastered}</b> مادة\n",
        "🧠 <b>توزيع المواد على مراحل التكرار (منحنى النسيان):</b>",
    ]

    labels = ["م1 (غداً)", "م2 (3 أيام)", "م3 (أسبوع)", "م4 (أسبوعين)", "م5 (شهر)"]
    stage_strs = []
    for s_idx in range(5):
        cnt = by_stage.get(s_idx, 0)
        stage_strs.append(f"{labels[s_idx]}: <b>{cnt}</b>")
    text_lines.append(" • " + " | ".join(stage_strs[:3]))
    text_lines.append(" • " + " | ".join(stage_strs[3:]))

    # Types breakdown
    types_arabic = {"photo": "📸 صور", "text": "✍️ نصوص", "link": "🔗 فورمز", "pdf": "📑 مستندات"}
    t_parts = [f"{types_arabic.get(k, k)}: <b>{v}</b>" for k, v in by_type.items()]
    if t_parts:
        text_lines.append("\n📁 <b>تصنيف الوسائط:</b> " + " | ".join(t_parts))

    # Reminder info
    text_lines.append(f"\n⏰ <b>موعد التنبيه اليومي:</b> <b>{dh}:{r_m:02d} {period}</b> (بتوقيت مكة)")

    kb = [
        [
            InlineKeyboardButton("🌅 05:00 ص", callback_data="set_rem_5_0"),
            InlineKeyboardButton("☀️ 07:00 ص", callback_data="set_rem_7_0"),
            InlineKeyboardButton("🌇 05:00 م", callback_data="set_rem_17_0"),
            InlineKeyboardButton("🌙 08:00 م", callback_data="set_rem_20_0"),
        ],
        [InlineKeyboardButton("🔔 تجربة إرسال التنبيه الآن", callback_data="test_nudge_now")],
        [InlineKeyboardButton("🏠 العودة للقائمة الرئيسية", callback_data="main_menu")],
    ]

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


# ─── Stats Callbacks Router ───────────────────────────────────────────────────

async def handle_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data

    if data == "stats_menu":
        text, kb = build_stats_view()
        await safe_edit(query, text, kb)
        return True

    if data.startswith("set_rem_"):
        parts = data.split("_")
        h = int(parts[2])
        m = int(parts[3])
        db.set_setting("reminder_hour", str(h))
        db.set_setting("reminder_minute", str(m))

        # Re-schedule job
        try:
            from bot import schedule_daily_reminder
            schedule_daily_reminder(context.application.job_queue, update.effective_chat.id, h, m)
        except Exception:
            pass

        period = "ص" if h < 12 else "م"
        dh = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
        await query.answer(f"✅ تم ضبط موعد التنبيه اليومي: {dh}:{m:02d} {period}", show_alert=True)
        text, kb = build_stats_view()
        await safe_edit(query, text, kb)
        return True

    if data == "test_nudge_now":
        await query.answer("🔔 تم إرسال التنبيه التجريبي!")
        from bot import send_daily_reminder_to_chat
        await send_daily_reminder_to_chat(context, update.effective_chat.id)
        return True

    return False
