import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from handlers.image_generator import render_schedule_images
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def build_subjects_menu(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the subjects overview list with progress bars."""
    subjects = db.get_user_subjects(user_id)

    if not subjects:
        text = (
            "📚 <b>موادك وجداولك الدراسية</b>\n\n"
            "يا هلا والله! ما عندك أي جدول حالياً.\n"
            "اضغط «➕ سوي جدول جديد» ونرتب لك خطتك في ثواني! 🎯"
        )
        kb = [
            [InlineKeyboardButton("➕ سوي جدول جديد", callback_data="wizard_start")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ]
        return text, InlineKeyboardMarkup(kb)

    text_lines = [
        "📚 <b>موادك وجداولك الحالية:</b>\n",
        "اضغط على أي مادة لاستعراض تفاصيلها، أو تحميل صور الجدول بجودة عالية، أو إعادة ضبط الخطة:\n",
    ]

    kb = []
    for s in subjects:
        total = s["total_lessons"]
        comp = s["completed_count"]
        pct = int((comp / max(1, total)) * 100)
        target = s["target_days"]
        cur_day = s["current_day"]

        # Visual progress bar
        blocks = int(pct / 10)
        bar = "🟩" * blocks + "⬜" * (10 - blocks)

        text_lines.append(f"<b>{s['icon']} {html.escape(s['name'])}</b>")
        text_lines.append(f"• الإنجاز: {bar} <b>{pct}%</b> ({comp}/{total} درس)")
        text_lines.append(f"• الوقت: اليوم <b>{cur_day}</b> من أصل <b>{target}</b> يوم\n")

        kb.append([
            InlineKeyboardButton(
                f"{s['icon']} إدارة مادة: {s['name']}",
                callback_data=f"sub_detail_{s['id']}",
            )
        ])

    kb.append([InlineKeyboardButton("➕ إضافة مادة جديدة", callback_data="wizard_start")])
    kb.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


def build_subject_detail_view(subject_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Builds detailed view for a single subject."""
    subject = db.get_subject(subject_id, user_id)
    if not subject:
        return None

    total = subject["total_lessons"]
    comp = subject["completed_count"]
    pct = int((comp / max(1, total)) * 100)
    target = subject["target_days"]
    cur_day = subject["current_day"]
    rem_days = subject["days_remaining"]
    rem_lessons = total - comp

    text = (
        f"{subject['icon']} <b>تفاصيل مادة: {html.escape(subject['name'])}</b>\n\n"
        f"📊 <b>نسبة الإنجاز الكلية:</b> <b>{pct}%</b>\n"
        f"✅ <b>الدروس المنجزة:</b> {comp} من أصل {total} درس\n"
        f"⏳ <b>الدروس المتبقية:</b> {rem_lessons} درس\n\n"
        f"📅 <b>الخطة الزمنية:</b>\n"
        f"• أنت الآن في اليوم: <b>{cur_day}</b> من {target} يوم\n"
        f"• الأيام المتبقية في الخطة: <b>{rem_days}</b> يوم\n\n"
        "💡 <i>يمكنك تحميل بوستر الجدول كملف A4 أو كخلفية شاشة للجوال، أو إعادة توزيع الدروس المتبقية عند التأخر!</i>"
    )

    kb = [
        [InlineKeyboardButton("🖼️ إرسال صور الجدول (A4 + خلفية جوال)", callback_data=f"sub_images_{subject_id}")],
        [InlineKeyboardButton("🔄 تدارك وتعديل الجدول المتبقي", callback_data=f"sub_resched_{subject_id}")],
        [InlineKeyboardButton("🗑️ حذف المادة", callback_data=f"sub_del_confirm_{subject_id}")],
        [InlineKeyboardButton("🔙 العودة لقائمة المواد", callback_data="subjects_menu")],
    ]

    return text, InlineKeyboardMarkup(kb)


async def show_subjects_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /subjects or callback 'subjects_menu'."""
    user = update.effective_user
    if not user:
        return
    text, kb = build_subjects_menu(user.id)
    if update.callback_query:
        await safe_edit(update.callback_query, text, kb)
    else:
        await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def handle_subjects_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles subjects view, detail, rescheduling, image requests, and deletion."""
    query = update.callback_query
    if not query:
        return False
    data = query.data
    user = update.effective_user
    if not user:
        return False
    uid = user.id
    chat_id = update.effective_chat.id

    if data == "subjects_menu":
        await show_subjects_menu(update, context)
        return True

    # View subject detail
    if data.startswith("sub_detail_"):
        try:
            sub_id = int(data.split("_")[2])
        except (IndexError, ValueError):
            return False

        detail = build_subject_detail_view(sub_id, uid)
        if not detail:
            await query.answer("المادة غير موجودة أو تم حذفها.", show_alert=True)
            return True

        text, kb = detail
        await safe_edit(query, text, kb)
        return True

    # Reschedule / Catch-up from subject detail
    if data.startswith("sub_resched_"):
        try:
            sub_id = int(data.split("_")[2])
        except (IndexError, ValueError):
            return False

        res = db.reschedule_subject(sub_id, uid)
        msg = res.get("message", "تم التعديل بنجاح!")
        await query.answer(msg, show_alert=True)

        detail = build_subject_detail_view(sub_id, uid)
        if detail:
            text, kb = detail
            await safe_edit(query, text, kb)
        return True

    # Re-send high-res schedule images
    if data.startswith("sub_images_"):
        try:
            sub_id = int(data.split("_")[2])
        except (IndexError, ValueError):
            return False

        subject = db.get_subject(sub_id, uid)
        if not subject:
            await query.answer("المادة غير موجودة.", show_alert=True)
            return True

        await query.answer("جاري تجهيز وتوليد صور الجدول...", show_alert=False)
        lessons = db.get_subject_lessons(sub_id, uid)
        user_name = user.full_name or user.username or "يا بطل"

        a4_img, wp_img = render_schedule_images(subject, lessons, user_name)

        if a4_img:
            with open(a4_img, "rb") as a4_f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=a4_f,
                    filename=f"جدول_{subject['name']}_A4.png",
                    caption=f"📄 <b>قالب الطباعة A4 لمادة «{html.escape(subject['name'])}»</b>",
                    parse_mode="HTML"
                )

        if wp_img:
            with open(wp_img, "rb") as wp_f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=wp_f,
                    caption=f"📱 <b>قالب خلفية شاشة الجوال لمادة «{html.escape(subject['name'])}»</b>",
                    parse_mode="HTML"
                )

        # Re-display detail view
        detail = build_subject_detail_view(sub_id, uid)
        if detail:
            text, kb = detail
            await send_clean_message(context, chat_id, text, update=update, reply_markup=kb)
        return True

    # Confirm delete subject
    if data.startswith("sub_del_confirm_"):
        try:
            sub_id = int(data.split("_")[3])
        except (IndexError, ValueError):
            return False

        subject = db.get_subject(sub_id, uid)
        if not subject:
            await query.answer("المادة غير موجودة.", show_alert=True)
            return True

        prompt = (
            f"⚠️ <b>هل أنت متأكد من حذف مادة «{html.escape(subject['name'])}»؟</b>\n\n"
            "سيتم حذف جميع دروسها ومواعيد مراجعاتها نهائياً ولا يمكن التراجع عن هذا الإجراء!"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ نعم، احذف المادة نهائياً", callback_data=f"sub_del_do_{sub_id}")],
            [InlineKeyboardButton("❌ تراجع", callback_data=f"sub_detail_{sub_id}")],
        ])
        await safe_edit(query, prompt, kb)
        return True

    # Execute delete subject
    if data.startswith("sub_del_do_"):
        try:
            sub_id = int(data.split("_")[3])
        except (IndexError, ValueError):
            return False

        db.delete_subject(sub_id, uid)
        await query.answer("تم حذف المادة بنجاح.", show_alert=True)

        text, kb = build_subjects_menu(uid)
        await safe_edit(query, text, kb)
        return True

    return False
