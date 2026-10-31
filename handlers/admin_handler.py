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
    channel = db.get_setting("official_channel", config.OFFICIAL_CHANNEL) or "غير مربوطة (استخدم /set_channel)"

    text_lines = [
        "👑 <b>لوحة تحكم المالك — ذاكرة القدرات (Admin OS)</b>\n",
        "📊 <b>المؤشرات التجارية ونمو الطلاب:</b>",
        f"• 👥 إجمالي الطلاب المسجلين: <b>{stats['total_users']}</b> طالب",
        f"• ⭐ المشتركين النشطين: <b>{stats['active_subscribers']}</b> مشترك",
        f"• 🎁 طلاب الفترة التجريبية: <b>{stats['trial_users']}</b> طالب",
        f"• 🔒 الاشتراكات المنتهية: <b>{stats['expired_users']}</b> طالب",
        f"• 💰 أرباح نجوم تيليجرام: <b>{stats['total_stars']}</b> نجمة ⭐\n",
        "📚 <b>مؤشرات المحتوى والمذاكرة:</b>",
        f"• 📝 المسائل المحفوظة بالمنصة: <b>{stats['total_items']}</b> مسألة",
        f"• 🔄 جلسات المراجعة المكتملة: <b>{stats['total_reviews']}</b> جلسة\n",
        f"📢 <b>القناة الرسمية للإذاعات:</b> <code>{channel}</code>\n",
        "🛠️ <b>أوامر الإدارة السريعة:</b>",
        "• <code>/broadcast [النص]</code> — إرسال إذاعة لجميع الطلاب ونشرها بالقناة.",
        "• <code>/set_channel @channel</code> — ربط أو تغيير القناة الرسمية.",
        "• <code>/gift_sub [user_id] [days]</code> — إهداء اشتراك مجاني لطالب.",
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
    """Command /broadcast <message> to announce to all registered students and official channel."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    if not user or not config.is_admin(user.id):
        return

    # Track admin's /broadcast command message so it can be cleaned
    if update.message:
        db.track_chat_message(chat_id, update.message.message_id)

    if not context.args:
        prompt = await update.message.reply_text(
            "⚠️ <b>يرجى كتابة نص الرسالة بعد الأمر:</b>\n"
            "<code>/broadcast السلام عليكم يا أبطال...</code>\n\n"
            "💡 <i>لربط القناة الرسمية أو تغييرها:</i>\n"
            "<code>/set_channel @channel_username</code>",
            parse_mode="HTML",
        )
        db.track_chat_message(chat_id, prompt.message_id)
        return

    raw_text = update.message.text.partition(" ")[2].strip()
    users = db.get_all_users_for_reminders()

    status_msg = await update.message.reply_text(f"⏳ جاري إرسال الإذاعة إلى {len(users)} طالب...")
    db.track_chat_message(chat_id, status_msg.message_id)

    student_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 فتح لوحة المذاكرة | تم الاطلاع ✅", callback_data="dismiss_broadcast")]
    ])

    sent_cnt = 0
    fail_cnt = 0
    for u in users:
        uid = u["id"]
        # Skip sending to the admin themself if present in users list
        if uid == user.id:
            continue
        try:
            b_msg = await context.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>إعلان من إدارة «ذاكرة القدرات»:</b>\n\n{raw_text}",
                parse_mode="HTML",
                reply_markup=student_kb,
            )
            # Track message for the student so it auto-cleans on their next action
            db.track_chat_message(uid, b_msg.message_id)
            sent_cnt += 1
        except Exception:
            fail_cnt += 1

    # Publish to official channel if configured
    channel_user = db.get_setting("official_channel", config.OFFICIAL_CHANNEL)
    channel_status = ""
    if channel_user:
        try:
            bot_username = (await context.bot.get_me()).username
            channel_text = (
                f"📢 <b>إعلان من «ذاكرة القدرات»:</b>\n\n"
                f"{raw_text}\n\n"
                f"🤖 <b>جرب البوت الآن مجاناً:</b>\n"
                f"@{bot_username}"
            )
            await context.bot.send_message(
                chat_id=channel_user,
                text=channel_text,
                parse_mode="HTML",
            )
            channel_status = f"\n📢 تم النشر بنجاح في القناة الرسمية (<code>{channel_user}</code>) ✅"
        except Exception as e:
            channel_status = f"\n⚠️ تعذر النشر في القناة: {e}\n<i>(تأكد أن البوت مشرف في القناة وله صلاحية نشر الرسائل)</i>"

    done_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ إغلاق التقرير والعودة للوحة الإدارة", callback_data="admin_menu")],
    ])

    await status_msg.edit_text(
        f"✅ <b>اكتملت الإذاعة بنجاح!</b>\n\n"
        f"• 👥 تم الإرسال إلى: <b>{sent_cnt}</b> طالب\n"
        f"• 🚫 تعذر الإرسال: <b>{fail_cnt}</b> (قاموا بحظر البوت أو حذف حساباتهم)"
        f"{channel_status}",
        parse_mode="HTML",
        reply_markup=done_kb,
    )


async def set_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to configure official channel for announcements."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    if not user or not config.is_admin(user.id):
        return

    if update.message:
        db.track_chat_message(chat_id, update.message.message_id)

    if not context.args:
        current = db.get_setting("official_channel", config.OFFICIAL_CHANNEL) or "غير مضبوطة"
        msg = await update.message.reply_text(
            f"📢 <b>القناة الرسمية الحالية:</b> <code>{current}</code>\n\n"
            "لتغيير القناة، أرسل الأمر مع المعرف:\n"
            "<code>/set_channel @channel_username</code>\n\n"
            "⚠️ <i>ملاحظة: تأكد من إضافة البوت كـ مشرف (Admin) في القناة لينشر فيها.</i>",
            parse_mode="HTML",
        )
        db.track_chat_message(chat_id, msg.message_id)
        return

    new_channel = context.args[0].strip()
    if not new_channel.startswith("@") and not new_channel.startswith("-100"):
        new_channel = f"@{new_channel}"

    db.set_setting("official_channel", new_channel)
    msg = await update.message.reply_text(
        f"✅ تم ربط القناة الرسمية بنجاح: <code>{new_channel}</code>\n\n"
        "الآن أي إذاعة ترسلها عبر <code>/broadcast</code> ستُنشر تلقائياً في القناة أيضاً!\n\n"
        "⚠️ <i>تأكد أن البوت مضاف كـ مشرف (Admin) في القناة.</i>",
        parse_mode="HTML",
    )
    db.track_chat_message(chat_id, msg.message_id)


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return False

    if data == "admin_menu" or data == "admin_refresh":
        # Clean any tracked auxiliary messages in admin chat (like broadcast status or commands)
        aux_ids = db.get_and_clear_chat_messages(query.message.chat_id, keep_message_id=query.message.message_id)
        for mid in aux_ids:
            try:
                await context.bot.delete_message(chat_id=query.message.chat_id, message_id=mid)
            except Exception:
                pass
        text, kb = build_admin_dashboard()
        await safe_edit(query, text, kb)
        return True

    if data == "admin_backup_db":
        await query.answer("جاري تجهيز النسخة الاحتياطية...")
        from handlers.main_menu import backup_command
        await backup_command(update, context)
        return True

    return False
