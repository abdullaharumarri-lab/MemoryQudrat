import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
import config
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def build_admin_dashboard() -> tuple[str, InlineKeyboardMarkup]:
    """Builds the Super Admin commercial metrics dashboard."""
    stats = db.get_admin_platform_stats()

    text_lines = [
        "👑 <b>لوحة تحكم المالك — ذاكرة القدرات (Admin OS)</b>\n",
        "📊 <b>المؤشرات التجارية ونمو الطلاب:</b>",
        f"• 👥 إجمالي الطلاب المسجلين: <b>{stats['total_users']}</b> طالب",
        f"• ⭐ المشتركين النشطين (مدفوع/دائم): <b>{stats['active_subscribers']}</b> مشترك",
        f"• 🎁 طلاب الفترة التجريبية: <b>{stats['trial_users']}</b> طالب",
        f"• 🔒 الاشتراكات المنتهية: <b>{stats['expired_users']}</b> طالب",
        f"• 💰 أرباح نجوم تيليجرام: <b>{stats['total_stars']}</b> نجمة ⭐\n",
        "📚 <b>مؤشرات المحتوى والمذاكرة:</b>",
        f"• 📝 إجمالي المسائل المحفوظة بالمنصة: <b>{stats['total_items']}</b> مادة",
        f"• 🔄 إجمالي جلسات المراجعة المكتملة: <b>{stats['total_reviews']}</b> جلسة\n",
        "🛠️ <b>الأوامر السريعة:</b>",
        "• <code>/gift_sub [user_id] [days]</code> — إهداء اشتراك لطالب.",
        "• <code>/broadcast [نص الرسالة]</code> — إرسال تعميم لكافة الطلاب.",
    ]

    kb = [
        [
            InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="admin_refresh"),
            InlineKeyboardButton("💾 تحميل نسخة احتياطية", callback_data="admin_backup_db"),
        ],
        [InlineKeyboardButton("🏠 العودة للرئيسية", callback_data="main_menu")],
    ]

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /admin."""
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return

    text, kb = build_admin_dashboard()
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def gift_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /gift_sub <user_id> <days> to grant free subscription days."""
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ <b>طريقة الاستخدام:</b>\n<code>/gift_sub [user_id] [days]</code>\nمثال: <code>/gift_sub 12345678 30</code>", parse_mode="HTML")
        return

    try:
        target_uid = int(args[0])
        days = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال أرقام صحيحة للآيدي وعدد الأيام.")
        return

    target_user = db.get_user(target_uid)
    if not target_user:
        await update.message.reply_text("❌ لم يتم العثور على هذا الطالب في قاعدة البيانات.")
        return

    res = db.extend_user_subscription(user_id=target_uid, days=days, stars_paid=0, payload="admin_gift")

    # Notify admin
    await update.message.reply_text(
        f"✅ تم تفعيل/تمديد اشتراك الطالب (<code>{target_uid}</code>) بنجاح!\n📅 الصلاحية الجديدة حتى: <b>{res['new_expires_at']}</b> (+{days} يوم).",
        parse_mode="HTML",
    )

    # Notify student
    try:
        nudge = (
            f"🎁 <b>مبروك يا بطل! تم إهداؤك اشتراكاً مجانياً!</b> 🎉\n\n"
            f"تم تمديد اشتراكك في ذاكرة القدرات لمدة <b>{days}</b> يوماً إضافية!\n"
            f"📅 <b>صلاحية اشتراكك حتى:</b> <b>{res['new_expires_at']}</b>\n\n"
            "استغل هذا الوقت في المذاكرة وحل المسائل، وبالتوفيق والدرجة الكاملة بإذن الله 💪"
        )
        await context.bot.send_message(chat_id=target_uid, text=nudge, parse_mode="HTML")
    except Exception as e:
        logger.debug("Could not message gifted student %s: %s", target_uid, e)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /broadcast <message> to announce to all registered students."""
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ يرجى كتابة نص الرسالة بعد الأمر:\n<code>/broadcast السلام عليكم...</code>", parse_mode="HTML")
        return

    raw_text = update.message.text.partition(" ")[2].strip()
    users = db.get_all_users_for_reminders()

    status_msg = await update.message.reply_text(f"⏳ جاري إرسال الإذاعة إلى {len(users)} طالب...")

    sent_cnt = 0
    fail_cnt = 0
    for u in users:
        uid = u["id"]
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 <b>إعلان من إدارة ذاكرة القدرات:</b>\n\n{raw_text}", parse_mode="HTML")
            sent_cnt += 1
        except Exception:
            fail_cnt += 1

    await status_msg.edit_text(f"✅ اكتملت الإذاعة بنجاح!\n• تم الإرسال: <b>{sent_cnt}</b> طالب\n• تعذر الإرسال: <b>{fail_cnt}</b> (قاموا بحظر البوت أو حذف حساباتهم).", parse_mode="HTML")


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return False

    if data == "admin_menu" or data == "admin_refresh":
        text, kb = build_admin_dashboard()
        await safe_edit(query, text, kb)
        return True

    if data == "admin_backup_db":
        await query.answer("جاري تجهيز النسخة الاحتياطية...")
        from handlers.main_menu import backup_command
        await backup_command(update, context)
        return True

    return False
