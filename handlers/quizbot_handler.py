# -*- coding: utf-8 -*-
"""
handlers/quizbot_handler.py — معالج كويزات تيليجرام الرسمية (@QuizBot)
التعرف التلقائي على كويزات QuizBot، استخراج بياناتها، وجدولتها في التكرار المتباعد
"""

import re
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin, ADMIN_USER_ID
from spaced_repetition import stage_label, days_until
from utils import safe_edit, send_clean_message

logger = logging.getLogger(__name__)


def parse_quizbot_message(update: Update) -> dict | None:
    """
    Analyzes an incoming message to detect whether it's a forwarded or shared @QuizBot quiz.
    Extracts the quiz title, direct start URL, question count, and time limit.
    """
    msg_obj = update.message or update.effective_message
    if not msg_obj:
        return None

    raw_text = (msg_obj.text or msg_obj.caption or "").strip()

    # 1. Search for QuizBot URL in inline keyboard buttons
    quiz_url = None
    if msg_obj.reply_markup and msg_obj.reply_markup.inline_keyboard:
        for row in msg_obj.reply_markup.inline_keyboard:
            for btn in row:
                btn_url = getattr(btn, "url", None)
                if btn_url and re.search(r'(?:t\.me|telegram\.me)/(?:QuizBot|quizbot)\?(?:start|startquiz)=', btn_url, re.IGNORECASE):
                    quiz_url = btn_url
                    break
            if quiz_url:
                break

    # 2. Search for QuizBot URL in raw text if not found in buttons
    if not quiz_url and raw_text:
        url_match = re.search(r'https?://(?:t\.me|telegram\.me)/(?:QuizBot|quizbot)\?(?:start|startquiz)=[^\s]+', raw_text, re.IGNORECASE)
        if url_match:
            quiz_url = url_match.group(0)

    # Check via_bot / forward_from or text indicators
    via_bot = getattr(msg_obj, "via_bot", None)
    is_via_quizbot = via_bot and getattr(via_bot, "username", "").lower() == "quizbot"
    fwd_user = getattr(msg_obj, "forward_from", None)
    is_fwd_quizbot = fwd_user and getattr(fwd_user, "username", "").lower() == "quizbot"
    has_dice_quiz = "🎲" in raw_text and ("Quiz" in raw_text or "quiz" in raw_text or "كويز" in raw_text)

    # If it has neither a QuizBot URL nor a QuizBot signature, not a QuizBot message
    if not quiz_url and not (is_via_quizbot or is_fwd_quizbot or has_dice_quiz):
        return None

    # 3. Extract Quiz Title
    # Pattern: 🎲 'title' Quiz or 🎲 "title" Quiz or 🎲 «title» Quiz
    title = None
    title_match = re.search(r"🎲\s*['\"](.+?)['\"]\s*(?:Quiz|quiz|كويز)", raw_text, re.DOTALL)
    if not title_match:
        title_match = re.search(r"🎲\s*«(.+?)»\s*(?:Quiz|quiz|كويز)", raw_text, re.DOTALL)
    if not title_match:
        title_match = re.search(r"🎲\s*(.+?)\s*(?:Quiz|quiz|كويز)", raw_text)

    if title_match:
        title = title_match.group(1).strip()
    elif raw_text:
        first_line = raw_text.splitlines()[0].strip()
        clean_first = re.sub(r'^(?:🎲|Quiz|كويز|•)\s*', '', first_line).strip()
        if clean_first and len(clean_first) > 2 and not clean_first.startswith("http"):
            title = clean_first


    if not title:
        title = "كويز تيليجرام"

    # 4. Extract Question Count (e.g., 13 questions, 13 سؤال)
    q_count = None
    q_match = re.search(r'(\d+)\s*(?:questions?|أسئلة|سؤال)', raw_text, re.IGNORECASE)
    if q_match:
        q_count = int(q_match.group(1))

    # 5. Extract Duration / Timer (e.g., 30 sec, 1 min)
    duration = None
    dur_match = re.search(r'(\d+)\s*(?:sec|seconds?|ثانية|ثواني)', raw_text, re.IGNORECASE)
    if dur_match:
        duration = f"{dur_match.group(1)} ثانية"
    else:
        min_match = re.search(r'(\d+)\s*(?:min|minutes?|دقيقة|دقائق)', raw_text, re.IGNORECASE)
        if min_match:
            duration = f"{min_match.group(1)} دقيقة"

    # Build formatted notes
    notes_parts = []
    if q_count:
        notes_parts.append(f"{q_count} سؤال")
    if duration:
        notes_parts.append(f"⏱ {duration}")
    notes_str = " · ".join(notes_parts) if notes_parts else "كويز عبر @QuizBot"

    # Fallback URL if not found in button
    if not quiz_url:
        quiz_url = "https://t.me/QuizBot"

    return {
        "name": title,
        "url": quiz_url,
        "questions_count": q_count,
        "duration": duration,
        "notes": notes_str
    }


async def handle_quizbot_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if the incoming message is a @QuizBot quiz. If so, saves it, schedules it
    for Spaced Repetition, and presents interactive actions to the user.
    """
    info = parse_quizbot_message(update)
    if not info:
        return False

    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id if user else (chat_id or ADMIN_USER_ID)
    is_adm = is_admin(user_id)

    name = info["name"]
    quiz_url = info["url"]
    notes = info["notes"]

    # Display name with 🎲 icon
    display_name = name if name.startswith("🎲") else f"🎲 {name}"

    # Save into DB
    quiz_id = db.save_quiz_without_review(
        name=display_name,
        questions=[],
        owner_id=user_id,
        is_public=1 if is_adm else 0,
        url=quiz_url,
        item_type="quiz_bot",
        notes=notes
    )

    # Schedule first review for tomorrow
    db.schedule_first_review(quiz_id, user_id=user_id, start_today=False)

    q_info = f"📝 <b>المحتوى:</b> {html.escape(notes)}\n" if notes else ""

    text = (
        f"🎉 <b>تم استلام وتوثيق كويز @QuizBot بنجاح!</b> 🎲\n\n"
        f"📌 <b>{html.escape(display_name)}</b>\n"
        f"{q_info}"
        f"🔗 <b>الرابط المباشر:</b> <code>{html.escape(quiz_url)}</code>\n\n"
        f"📅 <b>جدول المراجعات الذكية (التكرار المتباعد):</b>\n"
        f"• <b>المراجعة 1:</b> غداً (تثبيت الحفظ الأولي)\n"
        f"• <b>المراجعة 2:</b> بعد 3 أيام\n"
        f"• <b>المراجعة 3:</b> بعد أسبوع\n"
        f"• <b>المراجعة 4:</b> بعد أسبوعين\n"
        f"• <b>المراجعة 5:</b> بعد شهر (تثبيت دائم في الذاكرة 🌟)\n\n"
        f"👇 يمكنك حل الكويز فوراً في @QuizBot عبر الزر أدناه:"
    )

    buttons = [
        [InlineKeyboardButton("🚀 ابدأ الكويز في @QuizBot ↗", url=quiz_url)],
        [
            InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")
        ]
    ]

    # Category assignment for admin
    if is_adm:
        categories = db.get_categories(is_public=1)
        if categories:
            cat_row = []
            for c in categories[:4]:
                icon = c.get("icon", "📁")
                cat_row.append(InlineKeyboardButton(f"{icon} {c['name']}", callback_data=f"set_quiz_cat_{quiz_id}_{c['id']}"))
            buttons.append(cat_row)

    buttons.append([
        InlineKeyboardButton("📋 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_id}"),
        InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")
    ])

    await send_clean_message(context, chat_id, text, update=update, reply_markup=InlineKeyboardMarkup(buttons))
    return True


async def handle_quizbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles callback queries for QuizBot reviews."""
    query = update.callback_query
    if not query or not query.data:
        return False

    data = query.data
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID

    # ── 1. View QuizBot Review Card ──
    if data.startswith("view_quizbot_review_"):
        parts = data.split("_")
        quiz_id = int(parts[3])
        review_id = int(parts[4])

        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await query.answer("❌ الكويز غير موجود.", show_alert=True)
            return True

        review = db.get_quiz_review(quiz_id, user_id=user_id)
        stage_idx = review["stage"] if review else 0
        lbl = stage_label(stage_idx)

        name = html.escape(quiz.get("name", "كويز تيليجرام"))
        quiz_url = quiz.get("url") or "https://t.me/QuizBot"
        notes = quiz.get("notes") or ""
        notes_display = f"\n📝 <b>تفاصيل الكويز:</b> {html.escape(notes)}\n" if notes else ""

        text = (
            f"🎲 <b>مراجعة كويز @QuizBot</b> 🧠\n\n"
            f"📌 <b>{name}</b>\n"
            f"📊 المرحلة الحالية: <b>{lbl}</b>\n"
            f"{notes_display}\n"
            f"👇 <b>اضغط الزر بالأسفل لحل الكويز في @QuizBot، ثم قيّم إتقانك:</b>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 ابدأ الكويز في @QuizBot ↗", url=quiz_url)],
            [InlineKeyboardButton("✅ راجعت وأتقنت (ترقية للمرحلة التالية)", callback_data=f"quizbot_advance_{review_id}_{quiz_id}")],
            [InlineKeyboardButton("🔁 واجهت صعوبة (إعادة لغداً)", callback_data=f"quizbot_retry_{review_id}_{quiz_id}")],
            [
                InlineKeyboardButton("🗑️ حذف الكويز", callback_data=f"quizbot_delete_{quiz_id}"),
                InlineKeyboardButton("🔙 مراجعات اليوم", callback_data="due_reviews")
            ]
        ])
        await safe_edit(query, text, kb)
        return True

    # ── 2. Advance QuizBot Review (Mastered) ──
    elif data.startswith("quizbot_advance_"):
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])

        quiz = db.get_quiz(quiz_id)
        name = html.escape(quiz["name"]) if quiz else "الكويز"

        db.advance_quiz_review(review_id, user_id=user_id)

        new_rev = db.get_quiz_review(quiz_id, user_id=user_id)
        if not new_rev:
            sr_status = "🏆 <b>مبروك! أتممت جميع مراحل التكرار المتباعد لهذا الكويز وتم تثبيته نهائياً في الذاكرة! 🌟</b>"
        else:
            d = days_until(new_rev.get("next_review_date", ""))
            lbl = stage_label(new_rev.get("stage", 1))
            sr_status = f"✅ تم ترقية الكويز إلى <b>{lbl}</b>!\n📅 موعد مراجعتك القادمة بعد <b>{d}</b> يوم."

        text = (
            f"🌟 <b>إنجاز ممتاز!</b>\n\n"
            f"📌 <b>{name}</b>\n\n"
            f"{sr_status}"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
                InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")
            ],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return True

    # ── 3. Retry QuizBot Review (Difficulty) ──
    elif data.startswith("quizbot_retry_"):
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])

        quiz = db.get_quiz(quiz_id)
        name = html.escape(quiz["name"]) if quiz else "الكويز"

        db.reset_quiz_review(review_id, user_id=user_id)

        text = (
            f"🔁 <b>تمت إعادة جدولة الكويز لغداً!</b>\n\n"
            f"📌 <b>{name}</b>\n\n"
            f"إعادة حل الكويز وتثبيت الأخطاء هي مفتاح الحصول على 100% في القدرات 💪.\n"
            f"ستظهر لك مراجعته غداً لتكراره بنجاح!"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
                InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")
            ],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return True

    # ── 4. Delete QuizBot Quiz ──
    elif data.startswith("quizbot_delete_"):
        quiz_id = int(data.split("_")[-1])
        db.delete_quiz_or_topic(quiz_id, user_id=user_id)

        text = "🗑️ <b>تم حذف كويز QuizBot وإلغاء مراجعاته المجدولة بنجاح.</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
        ])
        await safe_edit(query, text, kb)
        return True

    return False
