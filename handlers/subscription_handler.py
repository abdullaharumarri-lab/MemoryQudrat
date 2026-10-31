import html
import logging
from telegram import Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
import config
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


# ─── Subscription Dashboard Builder ──────────────────────────────────────────

def build_subscription_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the subscription and upgrade view with Telegram Stars packages."""
    sub = db.check_user_subscription(user_id)
    user = db.get_user(user_id)
    name = (user.get("full_name") or user.get("username") or "يا بطل").strip()

    status_badge = ""
    if sub.get("status") == "lifetime" or sub.get("is_admin"):
        status_badge = "👑 <b>اشتراك VIP دائم (المالك والمشرف)</b>"
    elif sub.get("is_trial"):
        status_badge = f"🎁 <b>فترة تجريبية مجانية (متبقي {sub['days_left']} أيام — حتى {sub['expires_at']})</b>"
    elif sub.get("is_active"):
        status_badge = f"⭐ <b>اشتراك نشط ومفعّل (متبقي {sub['days_left']} يوم — حتى {sub['expires_at']})</b>"
    else:
        status_badge = "🔒 <b>انتهت فترة اشتراكك الحالية</b>"

    text_lines = [
        "⭐ <b>اشتراك ذاكرة القدرات (Active Recall OS)</b>\n",
        f"👤 <b>المشترك:</b> {html.escape(name)}",
        f"📊 <b>الحالة:</b> {status_badge}\n",
        "💡 <b>ماذا يقدم لك الاشتراك؟</b>",
        "• 🧠 بنك حفظ غير محدود للمسائل، القوانين، والتجميعات.",
        "• 🔄 خوارزمية التكرار المتباعد SM-2 لحماية معلوماتك من النسيان.",
        "• 📸 التقاط متعدد الوسائط: صور، نصوص، روابط فورمز، ومستندات PDF.",
        "• 📁 تنظيم شجري لمجلداتك ومسائلك حسب رغبتك.",
        "• ⏰ منبّه مراجعة ذكي صباح كل يوم بتوقيت الرياض.",
        "• ⚡ دفع آمن وسريع بنقرة واحدة داخل تيليجرام عبر <b>Apple Pay</b> أو <b>Google Pay</b>.\n",
        "👇 <b>اختر باقة الاشتراك المناسبة لك:</b>",
    ]

    kb = [
        [
            InlineKeyboardButton(
                f"⭐ اشتراك شهري (30 يوم) — {config.STARS_MONTHLY_PRICE} نجمة",
                callback_data="stars_buy_month",
            )
        ],
        [
            InlineKeyboardButton(
                f"🌟 باقة الموسم (3 أشهر) — {config.STARS_SEASON_PRICE} نجمة (خصم 20%)",
                callback_data="stars_buy_season",
            )
        ],
        [InlineKeyboardButton("🏠 العودة للقائمة الرئيسية", callback_data="main_menu")],
    ]

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


# ─── Access Guard Helper ──────────────────────────────────────────────────────

def can_user_capture(user_id: int) -> tuple[bool, str]:
    """
    Checks if student is authorized to add new items.
    If subscription expired, returns False and paywall prompt.
    """
    sub = db.check_user_subscription(user_id)
    if sub.get("is_active"):
        return True, ""

    msg = (
        "🔒 <b>عفواً، انتهت فترة اشتراكك الحالية!</b>\n\n"
        "✨ <b>مسائلك السابقة محفوظة بأمان تام في مجلداتك</b> ويمكنك استعراضها ومراجعتها بأي وقت.\n\n"
        "🚀 <i>لإضافة مسائل جديدة وتفعيل التكرار المتباعد الذكي، اشترك الآن بنجوم تيليجرام بنقرة واحدة:</i>"
    )
    return False, msg


# ─── Commands & Callbacks Router ──────────────────────────────────────────────

async def subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /subscribe and /pricing."""
    user = update.effective_user
    if not user:
        return
    text, kb = build_subscription_view(user.id)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def handle_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles subscription menu clicks and invoice generation."""
    query = update.callback_query
    if not query:
        return False
    data = query.data
    user = update.effective_user
    if not user:
        return False

    chat_id = update.effective_chat.id

    if data == "subscription_menu":
        text, kb = build_subscription_view(user.id)
        await safe_edit(query, text, kb)
        return True

    # 1. Monthly Plan (250 Stars)
    if data == "stars_buy_month":
        await query.answer("جاري تجهيز فاتورة الاشتراك الشهري...")
        await send_stars_invoice(
            context=context,
            chat_id=chat_id,
            user_id=user.id,
            plan_name="اشتراك شهري (30 يوماً)",
            plan_days=30,
            stars_amount=config.STARS_MONTHLY_PRICE,
            payload=f"sub_month_{user.id}",
        )
        return True

    # 2. Season Plan (600 Stars / 90 days)
    if data == "stars_buy_season":
        await query.answer("جاري تجهيز فاتورة باقة الموسم...")
        await send_stars_invoice(
            context=context,
            chat_id=chat_id,
            user_id=user.id,
            plan_name="باقة موسم القدرات (90 يوماً)",
            plan_days=90,
            stars_amount=config.STARS_SEASON_PRICE,
            payload=f"sub_season_{user.id}",
        )
        return True

    return False


async def send_stars_invoice(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    plan_name: str,
    plan_days: int,
    stars_amount: int,
    payload: str,
):
    """Sends native Telegram Stars invoice with Apple Pay / Google Pay support."""
    title = f"🧠 ذاكرة القدرات — {plan_name}"
    description = (
        f"تفعيل كامل لميزات التكرار المتباعد الذكي وحفظ المسائل لمدة {plan_days} يوماً."
    )
    prices = [LabeledPrice(label=plan_name, amount=stars_amount)]

    try:
        msg = await context.bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=description,
            payload=payload,
            currency="XTR",  # Telegram Stars official currency code
            prices=prices,
        )
        db.track_chat_message(chat_id, msg.message_id)
        logger.info("Sent Telegram Stars invoice to user %s: %s (%d XTR)", user_id, plan_name, stars_amount)
    except Exception as e:
        logger.error("Failed to send Telegram Stars invoice to user %s: %s", user_id, e)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ تعذر إنشاء فاتورة النجوم: {e}\nيرجى التأكد من تحديث تطبيق تيليجرام لآخر إصدار.",
        )


# ─── Payment Webhooks & Verification ──────────────────────────────────────────

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approves pre-checkout query before charging Telegram Stars."""
    query = update.pre_checkout_query
    if not query:
        return
    payload = query.invoice_payload or ""
    if payload.startswith("sub_"):
        await query.answer(ok=True)
        logger.info("Approved pre-checkout query for user %s (payload: %s)", query.from_user.id, payload)
    else:
        await query.answer(ok=False, error_message="طلب غير صالح أو منتهي الصلاحية.")


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles successful Telegram Stars payment, extends subscription, and notifies student."""
    payment = update.message.successful_payment
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not payment or not user:
        return

    payload = payment.invoice_payload or ""
    charge_id = payment.telegram_payment_charge_id
    total_stars = payment.total_amount

    # Determine duration
    days = 90 if "sub_season" in payload else 30

    # Extend user subscription in DB
    result = db.extend_user_subscription(
        user_id=user.id,
        days=days,
        stars_paid=total_stars,
        charge_id=charge_id,
        payload=payload,
    )

    celebration = (
        "🎉 <b>تم تأكيد الدفع بنجاح واشتراكك مفعّل الآن!</b> ⭐\n\n"
        f"🌟 <b>العملية:</b> دفع <b>{total_stars}</b> نجمة تيليجرام بنجاح.\n"
        f"📅 <b>صلاحية الاشتراك:</b> حتى <b>{result['new_expires_at']}</b> ({days} يوماً).\n"
        "🚀 تم فتح جميع الميزات لك بالكامل وبلا حدود.\n\n"
        "<i>استمر في مذاكرة وحفظ المسائل، وفالك الدرجة الكاملة 100% بإذن الله! 💪</i>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 ابدأ المذاكرة والمراجعة", callback_data="main_menu")],
    ])
    await send_clean_message(context, chat_id, celebration, update=update, reply_markup=kb)

    # Notify Super Admin of new subscription
    if config.ADMIN_USER_ID and config.ADMIN_USER_ID != user.id:
        try:
            admin_nudge = (
                f"🔔 <b>مشترك جديد دفع بنجوم تيليجرام!</b> 💰\n\n"
                f"👤 <b>الطالب:</b> {html.escape(user.full_name or user.username or str(user.id))} (<code>{user.id}</code>)\n"
                f"⭐ <b>المبلغ:</b> {total_stars} نجمة (XTR)\n"
                f"📅 <b>المدة:</b> {days} يوماً (حتى {result['new_expires_at']})"
            )
            await context.bot.send_message(chat_id=config.ADMIN_USER_ID, text=admin_nudge, parse_mode="HTML")
        except Exception as e:
            logger.debug("Could not notify admin of payment: %s", e)
