"""
handlers/main_menu.py — ذاكرة القدرات (Router المعماري الموحد)
مبسط | معياري | عالي الأداء
"""
import html
import json
import logging
import re
from datetime import date, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_admin, ADMIN_USER_ID
from utils import safe_edit, send_clean_message, normalize_arabic_digits

# Re-exports for bot.py and external modules backward compatibility
from handlers.review_handler import (
    _build_due_reviews,
    _build_review_schedule,
    today_command,
    schedule_command,
    handle_review_callback,
)
from handlers.weak_handler import (
    _build_weak_questions,
    weak_command,
    handle_weak_callback,
)
from handlers.stats_handler import (
    _build_my_stats,
    stats_command,
    handle_stats_callback,
)
from handlers.browse_handler import (
    _build_browse_view,
    _build_quiz_detail,
    _build_quiz_preview,
    handle_browse_callback,
)
from handlers.fixstage_handler import (
    fixstage_command,
    _build_admin_cat_panel,
    handle_fixstage_callback,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  الصفحة الرئيسية
# ═══════════════════════════════════════════════════════════════

def main_menu_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("📚 الكويزات", callback_data="browse_root")],
        [
            InlineKeyboardButton("🔔 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule"),
        ],
        [
            InlineKeyboardButton("❓ الأسئلة الضعيفة", callback_data="weak_questions"),
            InlineKeyboardButton("📊 إحصائياتي", callback_data="my_stats"),
        ],
        [
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu"),
        ],
    ]
    if user_id and is_admin(user_id):
        kb.append([InlineKeyboardButton("➕ إنشاء / رفع كويز", callback_data="create_upload_menu")])
    return InlineKeyboardMarkup(kb)


def main_menu_text(user_id: int = None) -> str:
    due = db.get_due_quiz_reviews(user_id=user_id) if user_id else []
    weak = db.get_due_weak_questions(user_id=user_id) if user_id else []
    lines = ["🧠 <b>ذاكرة القدرات</b>\n"]
    if due:
        lines.append(f"🔔 لديك <b>{len(due)}</b> مراجعة مستحقة اليوم")
    if weak:
        lines.append(f"❓ لديك <b>{len(weak)}</b> سؤال ضعيف مستحق")
    if not due and not weak:
        lines.append("✅ لا توجد مراجعات مستحقة اليوم — أحسنت!")
    lines.append("\nاختر ما تريد 👇")
    return "\n".join(lines)


async def _cleanup_and_return_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Returns to the main menu.
    - If coming from a QUIZ (has cleanup_message_ids or active session): wipes quiz messages then sends fresh menu.
    - If just browsing normally: safe-edits the current message in place (fast, no flicker).
    """
    user = update.effective_user
    user_id = user.id if user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    text = main_menu_text(user_id)
    kb = main_menu_keyboard(user_id)

    # ── Collect quiz-related message IDs if any ──
    quiz_msg_ids = list(context.user_data.pop("cleanup_message_ids", []))
    active_sess = None
    if user_id:
        try:
            active_sess = db.get_session(user_id=user_id)
            if active_sess:
                sess_ids = active_sess.get("session_message_ids", [])
                if sess_ids:
                    quiz_msg_ids.extend(sess_ids)
                db.clear_session(user_id=user_id)
        except Exception as e:
            logger.warning("Could not clear active session on home cleanup: %s", e)

    context.user_data.pop(f"active_passage_{chat_id}", None)

    has_quiz_cleanup = bool(quiz_msg_ids)

    from utils import delete_messages_bulk

    if has_quiz_cleanup:
        # ── QUIZ MODE: delete ALL quiz messages then send fresh main menu ──
        query = update.callback_query
        if query and query.message:
            quiz_msg_ids.append(query.message.message_id)

        # Also include anything tracked in DB
        tracked = db.get_and_clear_chat_messages(chat_id, keep_message_id=None)
        quiz_msg_ids.extend(tracked)

        all_to_delete = list(dict.fromkeys(int(m) for m in quiz_msg_ids if m and int(m) > 0))
        if all_to_delete:
            await delete_messages_bulk(context, chat_id, all_to_delete)

        # Reset DB state cleanly via database helper (no raw SQL)
        db.clear_last_message_id(chat_id)

        # Send fresh, clean main menu
        try:
            new_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=kb,
                parse_mode="HTML"
            )
            db.set_last_message_id(chat_id, new_msg.message_id)
            db.track_chat_message(chat_id, new_msg.message_id)
        except Exception as e:
            logger.error("Failed sending fresh main menu after quiz: %s", e)

    else:
        # ── NORMAL BROWSE MODE: just edit the current message in place ──
        query = update.callback_query
        if query:
            await safe_edit(query, text, kb)
        elif update.message:
            # Command triggered (e.g. /menu) — delete user command + send fresh
            extra = [update.message.message_id]
            tracked = db.get_and_clear_chat_messages(chat_id, keep_message_id=None)
            extra.extend(tracked)
            last_id = db.get_last_message_id(chat_id)
            if last_id:
                extra.append(last_id)
            await delete_messages_bulk(context, chat_id, extra)
            db.clear_last_message_id(chat_id)
            try:
                new_msg = await context.bot.send_message(
                    chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML"
                )
                db.set_last_message_id(chat_id, new_msg.message_id)
                db.track_chat_message(chat_id, new_msg.message_id)
            except Exception as e:
                logger.error("Failed sending fresh main menu from command: %s", e)


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _cleanup_and_return_home(update, context)


# ═══════════════════════════════════════════════════════════════
#  الإعدادات
# ═══════════════════════════════════════════════════════════════

def _build_settings(user_id):
    user_row = db.get_user(user_id)
    h = user_row.get("reminder_hour", 4) if user_row else 4
    m = user_row.get("reminder_minute", 30) if user_row else 30
    period = "ص" if h < 12 else "م"
    dh = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    text = (f"⚙️ <b>الإعدادات</b>\n\n"
            f"⏰ وقت التذكير اليومي: <b>{dh}:{m:02d} {period}</b>\n\n"
            "اختر وقتاً مناسباً لجدولك اليومي:")
    kb = [
        [InlineKeyboardButton("🌅 4:30 ص", callback_data="set_reminder_4_30"),
         InlineKeyboardButton("☀️ 6:00 ص", callback_data="set_reminder_6_0"),
         InlineKeyboardButton("☀️ 7:00 ص", callback_data="set_reminder_7_0")],
        [InlineKeyboardButton("☀️ 8:00 ص", callback_data="set_reminder_8_0"),
         InlineKeyboardButton("🌤 2:00 م", callback_data="set_reminder_14_0"),
         InlineKeyboardButton("🌇 5:00 م", callback_data="set_reminder_17_0")],
        [InlineKeyboardButton("🌙 8:00 م", callback_data="set_reminder_20_0"),
         InlineKeyboardButton("🌙 9:00 م", callback_data="set_reminder_21_0"),
         InlineKeyboardButton("🌙 10:00 م", callback_data="set_reminder_22_0")],
        [InlineKeyboardButton("🔔 تجربة إرسال التذكير الآن", callback_data="test_reminder_now")],
        [InlineKeyboardButton("ℹ️ كيف يعمل نظام التكرار المتباعد؟", callback_data="how_it_works")],
        [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
    ]
    return text, InlineKeyboardMarkup(kb)


def _build_create_menu():
    text = "➕ <b>إنشاء / رفع كويز</b>\n\nاختر طريقة الإضافة المناسبة:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ إنشاء كويز يدوياً", callback_data="create_manual_quiz")],
        [InlineKeyboardButton("📊 رفع ملف Excel / CSV", callback_data="upload_excel")],
        [InlineKeyboardButton("📥 تحميل قالب Excel", callback_data="download_excel_template")],
        [InlineKeyboardButton("📋 رفع ملف JSON", callback_data="upload_json")],
        [InlineKeyboardButton("🔗 إضافة كويز كرابط", callback_data="upload_url")],
        [InlineKeyboardButton("📁 إدارة المجلدات", callback_data="admin_cat_0")],
        [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
    ])
    return text, kb


# ═══════════════════════════════════════════════════════════════
#  الأوامر النصية العامة
# ═══════════════════════════════════════════════════════════════

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💡 <b>شرح نظام التكرار المتباعد</b>\n\n"
        "بعد حل أي كويز، اضغط <b>«أضف لمراجعاتي»</b> فيُذكّرك البوت في مواعيد:\n"
        "• بعد يوم → بعد 3 أيام → بعد 7 أيام → بعد 14 يوم → بعد 30 يوم\n\n"
        "<b>الأسئلة الضعيفة:</b> كل سؤال تخطئ فيه يُحفظ تلقائياً.\n\n"
        "<b>النصيحة:</b> راجع يومياً في نفس الوقت 🧠"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    args = context.args or []
    query_text = " ".join(args).strip()
    if not query_text:
        text = "🔍 <b>البحث</b>\n\nأرسل: <code>/find اسم الكويز</code>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
        await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)
        return
    all_quizzes = db.get_all_public_quizzes()
    norm = normalize_arabic_digits(query_text.lower())
    results = [q for q in all_quizzes if norm in normalize_arabic_digits(q.get("name", "").lower())]
    if not results:
        text = f"🔍 لا توجد نتائج لـ «{html.escape(query_text)}»"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]])
    else:
        text = f"🔍 «{html.escape(query_text)}» — {len(results)} كويز:"
        kb_rows = [[InlineKeyboardButton(f"📝 {q['name']}", callback_data=f"quiz_detail_{q['id']}")] for q in results[:15]]
        kb_rows.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
        kb = InlineKeyboardMarkup(kb_rows)
    await send_clean_message(context, update.effective_chat.id, text, update=update, reply_markup=kb)


# ═══════════════════════════════════════════════════════════════
#  معالج النصوص
# ═══════════════════════════════════════════════════════════════

async def url_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    msg = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    is_adm = is_admin(user_id)

    if is_adm and context.user_data.get("waiting_for_json_update"):
        quiz_update_id = context.user_data.pop("waiting_for_json_update")
        try:
            data = json.loads(msg)
            from handlers.pdf_handler import _validate_json_upload, process_json_quiz_data
            _validate_json_upload(None, data)
            await process_json_quiz_data(data, user, context, chat_id, update, quiz_update_id=quiz_update_id)
        except Exception as e:
            await send_clean_message(context, chat_id, f"❌ خطأ: {html.escape(str(e))}", update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]))
        return

    if is_adm and context.user_data.get("waiting_for_json_new"):
        context.user_data.pop("waiting_for_json_new", None)
        try:
            data = json.loads(msg)
            from handlers.pdf_handler import _validate_json_upload, process_json_quiz_data
            _validate_json_upload(None, data)
            await process_json_quiz_data(data, user, context, chat_id, update)
        except Exception as e:
            await send_clean_message(context, chat_id, f"❌ خطأ: {html.escape(str(e))}", update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]))
        return

    # ── Google Forms Direct AI Extraction & Solving ──────────────────────
    if is_adm and ("docs.google.com/forms/" in msg or context.user_data.get("waiting_for_url_quiz")):
        context.user_data.pop("waiting_for_url_quiz", None)
        if "docs.google.com/forms/" in msg:
            import re
            m = re.search(r'https?://docs\.google\.com/forms/[^\s]+', msg)
            form_url = m.group(0) if m else msg.strip()

            await send_clean_message(
                context, chat_id,
                "⏳ <b>جاري سحب نموذج Google Forms وفحص الأسئلة بالذكاء الاصطناعي...</b> 🧠\n\n"
                "🔍 نقوم الآن بقراءة الأسئلة والخيارات والقطع والصور، وحل الأسئلة غير المجابة بدقة...\n"
                "<i>يرجى الانتظار بضع ثوانٍ...</i>",
                update=update
            )
            try:
                from ai_extractor import extract_and_solve_google_form
                from handlers.pdf_handler import process_json_quiz_data
                quiz_data = await extract_and_solve_google_form(form_url)
                if not quiz_data.get("questions"):
                    await send_clean_message(
                        context, chat_id,
                        "❌ <b>لم يتم العثور على أي أسئلة داخل هذا النموذج.</b>\n"
                        "تأكد من أن الرابط متاح للعامة وليس مقفلاً أو يتطلب تسجيل دخول المؤسسة.",
                        update=update,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")]])
                    )
                    return
                # Save and schedule via standard quiz data processor
                await process_json_quiz_data(quiz_data, user, context, chat_id, update)
                return
            except Exception as e:
                logger.error("Failed to extract Google Form: %s", e, exc_info=True)
                await send_clean_message(
                    context, chat_id,
                    f"❌ <b>حدث خطأ أثناء تحليل النموذج:</b>\n<code>{html.escape(str(e))}</code>",
                    update=update,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")]])
                )
                return

        if not msg.startswith("http"):
            await send_clean_message(context, chat_id, "❌ الرابط غير صحيح.", update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="create_upload_menu")]]))
            return
        quiz_id = db.save_quiz_without_review("كويز رابط", [], owner_id=user_id, is_public=1, url=msg)
        db.schedule_first_review(quiz_id, user_id=user_id, start_today=False)

        categories = db.get_categories(is_public=1)
        kb = []
        if categories:
            cat_row = []
            for c in categories:
                icon = c.get("icon", "📁")
                cat_row.append(InlineKeyboardButton(f"{icon} {c['name']}", callback_data=f"set_quiz_cat_{quiz_id}_{c['id']}"))
                if len(cat_row) == 2:
                    kb.append(cat_row)
                    cat_row = []
            if cat_row:
                kb.append(cat_row)
        kb.append([InlineKeyboardButton("📂 البقاء في الرئيسية (بدون مجلد)", callback_data=f"quiz_detail_{quiz_id}")])
        kb.append([InlineKeyboardButton("📋 تفاصيل الكويز", callback_data=f"quiz_detail_{quiz_id}"),
                   InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])

        await send_clean_message(context, chat_id,
                                 f"✅ <b>تم حفظ رابط الكويز بنجاح!</b>\n🔗 <code>{html.escape(msg)}</code>\n\n"
                                 f"📁 <b>اختر المجلد الذي ترغب بإضافة الكويز إليه:</b>",
                                 update=update, reply_markup=InlineKeyboardMarkup(kb))
        return

    if is_adm and context.user_data.get("waiting_for_quiz_rename"):
        quiz_id = context.user_data.pop("waiting_for_quiz_rename")
        db.rename_quiz(quiz_id, msg)
        await send_clean_message(context, chat_id, f"✅ تم تغيير الاسم إلى: <b>{html.escape(msg)}</b>",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 الكويز", callback_data=f"quiz_detail_{quiz_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_new_folder") is not None:
        parent_id = context.user_data.pop("waiting_for_new_folder")
        real_parent = parent_id if parent_id != 0 else None
        db.create_category(msg, parent_id=real_parent, is_public=1, owner_id=user_id)
        await send_clean_message(context, chat_id, f"✅ تم إنشاء مجلد: <b>{html.escape(msg)}</b>",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📁 إدارة المجلدات", callback_data=f"admin_cat_{parent_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_cat_rename"):
        cat_id = context.user_data.pop("waiting_for_cat_rename")
        db.rename_category(cat_id, msg)
        await send_clean_message(context, chat_id, f"✅ تم تغيير اسم المجلد إلى: <b>{html.escape(msg)}</b>",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📁 المجلد", callback_data=f"admin_cat_{cat_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_fixdate_custom") is not None:
        quiz_id = context.user_data.pop("waiting_for_fixdate_custom")
        raw = normalize_arabic_digits(msg.strip())
        parsed_date = None
        day_label = ""
        if re.match(r"^[+-]?\d+$", raw):
            days_offset = int(raw)
            if days_offset <= 0:
                parsed_date = (date.today() - timedelta(days=1)).isoformat()
                day_label = "الآن فوراً 🔴"
            else:
                parsed_date = (date.today() + timedelta(days=days_offset)).isoformat()
                day_label = f"بعد {days_offset} يوم"
        else:
            for pattern, order in [(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", "ymd"),
                                    (r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", "dmy")]:
                m = re.match(pattern, raw)
                if m:
                    try:
                        td = (date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if order == "ymd"
                              else date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
                        parsed_date = td.isoformat()
                        d = (td - date.today()).days
                        day_label = "الآن فوراً 🔴" if d <= 0 else ("غداً 🟡" if d == 1 else f"بعد {d} يوم")
                    except ValueError:
                        pass
                    break

        last_fpage = context.user_data.get("last_fixstage_page", 1)
        if parsed_date:
            db.set_quiz_review_next_date(quiz_id, user_id, parsed_date)
            quiz = db.get_quiz(quiz_id)
            q_name = html.escape(quiz.get("name", "كويز")) if quiz else "كويز"
            await send_clean_message(context, chat_id,
                                     f"✅ تم تحديد موعد مراجعة <b>{q_name}</b> إلى {parsed_date} ({day_label})",
                                     update=update,
                                     reply_markup=InlineKeyboardMarkup([
                                         [InlineKeyboardButton(f"🔙 صفحة {last_fpage}", callback_data=f"fixstage_page_{last_fpage}")],
                                         [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
                                     ]))
        else:
            context.user_data["waiting_for_fixdate_custom"] = quiz_id
            await send_clean_message(context, chat_id,
                                     "❌ صيغة غير صحيحة! أرسل كـ <code>2026-09-15</code> أو عدد أيام كـ <code>7</code>",
                                     update=update,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"fixstage_menu_{quiz_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_qtext_edit"):
        info = context.user_data.pop("waiting_for_qtext_edit")
        q_id = info["q_id"]
        quiz_id = info["quiz_id"]
        db.update_question_text(q_id, msg)
        await send_clean_message(context, chat_id, "✅ تم تحديث نص السؤال بنجاح!",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عرض وتدقيق السؤال", callback_data=f"fixstage_qedit_{quiz_id}_{q_id}")]]))
        return

    if is_adm and context.user_data.get("waiting_for_qexp_edit"):
        info = context.user_data.pop("waiting_for_qexp_edit")
        q_id = info["q_id"]
        quiz_id = info["quiz_id"]
        db.update_question_explanation(q_id, msg)
        await send_clean_message(context, chat_id, "✅ تم تحديث الشرح بنجاح!",
                                 update=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عرض وتدقيق السؤال", callback_data=f"fixstage_qedit_{quiz_id}_{q_id}")]]))
        return

    from handlers.creation_handler import handle_creation_text_input
    if await handle_creation_text_input(update, context):
        return

    from handlers.admin_handler import handle_broadcast_input
    await handle_broadcast_input(update, context)


# ═══════════════════════════════════════════════════════════════
#  معالج الأزرار الرئيسي (Router المعماري)
# ═══════════════════════════════════════════════════════════════

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception as e:
        logger.debug("query.answer() ignored exception: %s", e)

    data = query.data
    user = update.effective_user
    user_id = user.id if user else ADMIN_USER_ID
    is_adm = is_admin(user_id)

    if data == "noop":
        return

    if data == "main_menu":
        await _cleanup_and_return_home(update, context)
        return

    # ── 1. Settings & General Menus ──
    if data == "settings_menu":
        text, kb = _build_settings(user_id)
        await safe_edit(query, text, kb)
        return

    if data == "how_it_works":
        text = (
            "🧠 <b>نظام التكرار المتباعد (Spaced Repetition)</b>\n\n"
            "💡 <b>لماذا نستخدم هذا النظام؟</b>\n"
            "وفق منحنى النسيان العلمي، ينسى الإنسان أكثر من 70% من المعلومات الجديدة بعد مرور يوم واحد فقط إذا لم تتم مراجعتها!\n\n"
            "🎯 <b>كيف يساعدك البوت على ترسيخ المعلومات؟</b>\n"
            "عندما تضيف كويزاً لجدول مراجعاتك، يجدوله البوت في فترات متباعدة ذكية ومدروسة:\n"
            "• <b>المرحلة 1:</b> بعد 1 إلى 3 أيام (تثبيت الحفظ الأولي)\n"
            "• <b>المرحلة 2:</b> بعد 7 أيام (نقل المعلومة للذاكرة المتوسطة)\n"
            "• <b>المرحلة 3:</b> بعد 14 يوماً (ترسيخ الفهم والسرعة)\n"
            "• <b>المرحلة 4:</b> بعد 30 يوماً (تثبيت دائم في الذاكرة طويلة المدى 🌟)\n\n"
            "❌ <b>بنك الأسئلة الضعيفة:</b>\n"
            "أي سؤال تخطئ فيه في أي كويز، يوثقه البوت تلقائياً في قائمة الأسئلة الضعيفة لتتدرب عليه وتتقنه 100%!"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 ابدأ تصفح الكويزات", callback_data="browse_root")],
            [InlineKeyboardButton("🔙 الإعدادات", callback_data="settings_menu")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
        ])
        await safe_edit(query, text, kb)
        return

    if data.startswith("set_reminder_"):
        parts = data.split("_")
        h, m = int(parts[2]), int(parts[3])
        db.update_user_reminder(user_id, h, m)
        chat_id = update.effective_chat.id
        try:
            from bot import schedule_reminder
            schedule_reminder(context.job_queue, chat_id, hour=h, minute=m)
        except Exception:
            pass
        period = "ص" if h < 12 else "م"
        dh = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
        await safe_edit(query, f"✅ تم تعيين وقت التذكير: <b>{dh}:{m:02d} {period}</b>",
                        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]))
        return

    if data == "test_reminder_now":
        await query.answer("🔔 تم إرسال التذكير التجريبي!")
        uid = query.from_user.id
        chat_id = update.effective_chat.id
        reviews = db.get_due_quiz_reviews(user_id=uid)
        weak = db.get_due_weak_questions(user_id=uid)
        if reviews or weak:
            parts = []
            if reviews: parts.append(f"🔁 {len(reviews)} مراجعة كويز")
            if weak: parts.append(f"❌ {len(weak)} سؤال ضعيف")
            t = (
                "🔔 <b>[تذكير تجريبي] — ذاكرة القدرات 🧠</b>\n\n"
                "لديك مهام مراجعة مستحقة اليوم:\n" + "\n".join(f"• {p}" for p in parts) +
                "\n\nافتح البوت وابدأ المراجعة لترسيخ معلوماتك 💪"
            )
            k = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ حل مراجعات اليوم", callback_data="due_reviews")]])
        else:
            t = (
                "🔔 <b>[تذكير تجريبي] — ذاكرة القدرات 🧠</b>\n\n"
                "أهلاً بك يا بطل! 🌟\n"
                "نظام التذكير اليومي يعمل بنجاح 100% 🎯!\n\n"
                "لا توجد مراجعات متراكمة عليك اليوم. سيصلك التذكير تلقائياً كل يوم في الوقت الذي حددته."
            )
            k = InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 تصفح الكويزات", callback_data="browse_root")],
                [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu")]
            ])
        await send_clean_message(context=context, chat_id=chat_id, text=t, reply_markup=k)
        return

    if data == "create_upload_menu":
        if not is_adm:
            await query.answer("❌ للمشرف فقط.", show_alert=True)
            return
        text, kb = _build_create_menu()
        await safe_edit(query, text, kb)
        return

    if data == "upload_excel":
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        await safe_edit(query,
                        "📊 <b>رفع كويز عبر ملف Excel</b>\n\nأرسل الآن ملف <code>.xlsx</code> أو <code>.csv</code> مباشرة في المحادثة:",
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("📥 تحميل قالب Excel", callback_data="download_excel_template")],
                            [InlineKeyboardButton("❌ إلغاء", callback_data="create_upload_menu")]
                        ]))
        return

    if data == "download_excel_template":
        from handlers.pdf_handler import template_command
        await template_command(update, context)
        return

    if data == "upload_json":
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        context.user_data["waiting_for_json_new"] = True
        await safe_edit(query, "📋 أرسل ملف .json أو الصق نص الـ JSON:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="create_upload_menu")]]))
        return

    if data == "upload_url":
        if not is_adm:
            await query.answer("❌", show_alert=True)
            return
        context.user_data["waiting_for_url_quiz"] = True
        await safe_edit(query, "🔗 أرسل الآن رابط الكويز:",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="create_upload_menu")]]))
        return

    if data == "upload_media_note":
        if not is_adm:
            await query.answer("❌ للمشرف فقط.", show_alert=True)
            return
        context.user_data["waiting_for_media_note"] = True
        await safe_edit(query,
                        "📁 <b>إدراج مادة للمراجعة في التكرار المتباعد</b> 🧠\n\n"
                        "أرسل الآن:\n"
                        "• 📸 <b>صورة</b> (مثل ملخص أو قوانين)\n"
                        "• 📄 <b>ملف</b> (PDF أو مستند)\n"
                        "• ✍️ أو <b>نص ملاحظة مباشرة</b>\n\n"
                        "سيتم إدراجها وجدولتها تلقائياً لتصلك مراجعاتها الدورية 💪.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="create_upload_menu")]]))
        return

    # ── 2. Creation Handler Callbacks ──
    if data in ("create_manual_quiz", "manual_cancel", "manual_save_quiz", "manual_dashboard") or data.startswith("manual_set_correct_"):
        from handlers.creation_handler import handle_manual_quiz_callback
        await handle_manual_quiz_callback(update, context)
        return

    # ── 3. Review Handler Callbacks ──
    if await handle_review_callback(update, context, data, user_id):
        return

    # ── 4. Weak Questions Callbacks ──
    if await handle_weak_callback(update, context, data, user_id):
        return

    # ── 5. Student Stats Callbacks ──
    if await handle_stats_callback(update, context, data, user_id):
        return

    # ── 6. Browse & Quiz Detail Callbacks ──
    if await handle_browse_callback(update, context, data, user_id, is_adm):
        return

    # ── 7. Admin Handler Callbacks ──
    if data.startswith("admin_"):
        # Could be an admin category callback handled by fixstage, check fixstage first
        if await handle_fixstage_callback(update, context, data, user_id, is_adm):
            return
        from handlers.admin_handler import admin_button_handler
        await admin_button_handler(update, context)
        return

    # ── 8. Fixstage & Question Audit Callbacks ──
    if await handle_fixstage_callback(update, context, data, user_id, is_adm):
        return

    logger.warning("Unhandled callback: %s from user %s", data, user_id)
