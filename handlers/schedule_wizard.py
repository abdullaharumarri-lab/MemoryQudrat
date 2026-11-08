import re
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from handlers.image_generator import render_schedule_images
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


async def handle_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles wizard inline button triggers."""
    query = update.callback_query
    if not query:
        return False
    data = query.data
    user = update.effective_user
    if not user:
        return False
    uid = user.id

    if data == "wizard_start":
        # Check subscription paywall
        sub = db.check_user_subscription(uid)
        if not sub.get("is_active"):
            text = (
                "🔒 <b>انتهت الفترة التجريبية</b>\n\n"
                "يا هلا يا بطل! انتهت أيام التجربة المجانية (7 أيام).\n"
                "اشترك الآن بنجوم تيلجرام لتتمكن من إنشاء جداول غير محدودة وتفعيل التكرار المتباعد الذكي! ⭐"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ باقات الاشتراك السريعة", callback_data="subscription_menu")],
                [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")],
            ])
            await safe_edit(query, text, kb)
            return True

        context.user_data["wizard_state"] = "awaiting_subject_name"
        prompt = (
            "➕ <b>سوي جدول جديد في دقيقة! 🎯</b>\n\n"
            "يا هلا والله! وش اسم المادة أو الدورة اللي ودك نجدولها؟\n"
            "<i>(مثال: قدرات كمي، أو أحياء، أو إنجليزي، أو تجميعات...)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")]])
        await safe_edit(query, prompt, kb)
        return True

    return False


async def handle_wizard_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Processes interactive text input during the 3-step schedule wizard."""
    user = update.effective_user
    message = update.message
    if not user or not message or not message.text:
        return False

    uid = user.id
    chat_id = update.effective_chat.id
    state = context.user_data.get("wizard_state")
    if not state:
        return False

    text = message.text.strip()

    # Step 1: Subject Name
    if state == "awaiting_subject_name":
        if len(text) < 2:
            await message.reply_text("⚠️ يرجى كتابة اسم واضح للمادة (حرفين على الأقل):")
            return True

        context.user_data["wizard_name"] = text
        context.user_data["wizard_state"] = "awaiting_lessons_count"

        prompt = (
            f"كفو! مادة «<b>{html.escape(text)}</b>» 📚\n\n"
            "كم عدد الدروس أو المواضيع اللي ودك تخلصها؟\n\n"
            "💡 <b>خيارين سهلة وسريعة:</b>\n"
            "1️⃣ <b>بالرقم فقط:</b> اكتب عدد الدروس برقم (مثلاً: <code>30</code>) وبنرقمها لك تلقائياً.\n"
            "2️⃣ <b>بالأسماء:</b> الصق أسماء الدروس سطر بسطر هنا مباشرة."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")]])
        await send_clean_message(context, chat_id, prompt, update=update, reply_markup=kb)
        return True

    # Step 2: Lessons count or lesson titles list
    if state == "awaiting_lessons_count":
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        # Option A: Single number
        if len(lines) == 1 and re.match(r"^\d+$", lines[0]):
            count = int(lines[0])
            if count <= 0 or count > 300:
                await message.reply_text("⚠️ يرجى إدخال عدد دروس معقول بين 1 و 300 درس:")
                return True
            context.user_data["wizard_total_lessons"] = count
            context.user_data["wizard_lesson_titles"] = None
        else:
            # Option B: List of lesson titles
            if len(lines) < 1 or len(lines) > 300:
                await message.reply_text("⚠️ يرجى إدخال درس واحد على الأقل وبحد أقصى 300 درس:")
                return True
            context.user_data["wizard_total_lessons"] = len(lines)
            context.user_data["wizard_lesson_titles"] = lines

        total = context.user_data["wizard_total_lessons"]
        context.user_data["wizard_state"] = "awaiting_target_days"

        prompt = (
            f"ممتاز جداً! سجلنا <b>{total}</b> درس ✨\n\n"
            "في كم يوم ودك تخلص كامل المنهج؟ 🎯\n"
            "<i>(اكتب عدد الأيام برقم فقط، مثلاً: <code>20</code> أو <code>15</code> أو <code>30</code>)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")]])
        await send_clean_message(context, chat_id, prompt, update=update, reply_markup=kb)
        return True

    # Step 3: Target Days & Schedule Generation
    if state == "awaiting_target_days":
        if not re.match(r"^\d+$", text):
            await message.reply_text("⚠️ يرجى كتابة عدد الأيام كرقم صحيح (مثال: 20):")
            return True

        days = int(text)
        if days <= 0 or days > 365:
            await message.reply_text("⚠️ يرجى إدخال عدد أيام بين 1 و 365 يوم:")
            return True

        sub_name = context.user_data.pop("wizard_name", "مادتي الجديدة")
        total_lessons = context.user_data.pop("wizard_total_lessons", 20)
        lesson_titles = context.user_data.pop("wizard_lesson_titles", None)
        context.user_data.pop("wizard_state", None)

        # Temporary loading status
        wait_msg = await message.reply_text("⏳ <b>جاري تصميم جدولك وتوليد الصور الحصرية بشعار البوت...</b>", parse_mode="HTML")

        # Create in database
        sub_id = db.create_subject(
            user_id=uid,
            name=sub_name,
            total_lessons=total_lessons,
            target_days=days,
            lesson_titles=lesson_titles,
        )

        subject = db.get_subject(sub_id, uid)
        lessons = db.get_subject_lessons(sub_id, uid)
        user_name = user.full_name or user.username or "يا بطل"

        # Generate A4 and Wallpaper images
        a4_img, wp_img = render_schedule_images(subject, lessons, user_name)

        # Delete waiting message
        try:
            await wait_msg.delete()
        except Exception:
            pass

        # Send A4 Image
        if a4_img:
            with open(a4_img, "rb") as a4_f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=a4_f,
                    filename=f"جدول_{sub_name}_طباعة_A4.png",
                    caption=f"📄 <b>قالب الطباعة A4 لمادة «{html.escape(sub_name)}»</b>\n<i>مناسب للطباعة الورقية والتعليق على المكتب!</i>",
                    parse_mode="HTML"
                )

        # Send Wallpaper Image
        if wp_img:
            with open(wp_img, "rb") as wp_f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=wp_f,
                    caption=f"📱 <b>قالب خلفية شاشة الجوال (Wallpaper)</b>\n<i>ضعه في شاشة القفل ليذكرك بإنجازك كل يوم!</i>",
                    parse_mode="HTML"
                )

        # Success celebration and action buttons
        pace = max(1, round(total_lessons / max(1, days), 1))
        summary_text = (
            f"🎉 <b>تم إنشاء وتنسيق جدولك بنجاح استثنائي!</b> 🌟\n\n"
            f"📚 <b>المادة:</b> {html.escape(sub_name)}\n"
            f"📝 <b>إجمالي الدروس:</b> <b>{total_lessons}</b> درس\n"
            f"📅 <b>المدة المستهدفة:</b> <b>{days}</b> يوم\n"
            f"⚡ <b>المعدل اليومي:</b> <b>{pace}</b> درس/يوم\n\n"
            f"💪 <i>تم توزيع الدروس بانتظام، وأي درس تخلصه اضغط «تم» ليجدول البوت مراجعته الذكية تلقائياً.</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 ابدأ مهام اليوم الأول", callback_data="today_tasks")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, summary_text, update=update, reply_markup=kb)
        return True

    return False
