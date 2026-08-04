import html
import pytz
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from spaced_repetition import days_until, stage_label
from utils import send_clean_message


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 رفع JSON", callback_data="upload_json")],
        [InlineKeyboardButton("📚 كويزاتي", callback_data="my_quizzes")],
        [
            InlineKeyboardButton("🔁 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("❌ الأسئلة الضعيفة", callback_data="weak_questions"),
        ],
        [InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")],
    ])


def quizzes_keyboard(quizzes: list):
    kb = []
    for q in quizzes:
        kb.append([InlineKeyboardButton(f"📋 {q['name']}", callback_data=f"quiz_menu_{q['id']}")])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)


def quiz_menu_keyboard(quiz_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ ابدأ الكويز (تجربة)", callback_data=f"start_practice_{quiz_id}")],
        [InlineKeyboardButton("❌ حذف الكويز", callback_data=f"delete_quiz_{quiz_id}")],
        [InlineKeyboardButton("🔙 كويزاتي", callback_data="my_quizzes")],
    ])


def due_reviews_keyboard(reviews: list):
    kb = []
    for r in reviews:
        label = stage_label(r["stage"])
        kb.append([InlineKeyboardButton(
            f"🔁 {r['quiz_name']} — {label}",
            callback_data=f"start_review_{r['id']}_{r['quiz_id']}"
        )])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)


def weak_quizzes_keyboard(quizzes_with_weak: list):
    kb = []
    for item in quizzes_with_weak:
        kb.append([InlineKeyboardButton(
            f"❌ {item['quiz_name']} ({item['count']} سؤال)",
            callback_data=f"start_weak_{item['quiz_id']}"
        )])
    kb.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)


# ─── Safe edit helper ──────────────────────────────────────────────────────────

async def safe_edit(query, text, reply_markup=None, parse_mode="HTML"):
    """Edit message, fall back to answering query on failure."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            pass


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 أهلاً بك في <b>MemoryQudrat</b>!\n\n"
        "نظام مراجعة ذكي باستخدام التكرار المتباعد 🧠\n"
        "اختر ما تريد:"
    )
    if update.message:
        await send_clean_message(
            context, update.effective_chat.id, text,
            update=update, reply_markup=main_menu_keyboard()
        )
    elif update.callback_query:
        await safe_edit(update.callback_query, text, main_menu_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    back_btn = [[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]

    # ── Main menu ──
    if data == "main_menu":
        await main_menu_handler(update, context)

    # ── Upload JSON ──
    elif data == "upload_json":
        await safe_edit(
            query,
            "📋 <b>رفع JSON مباشرة</b>\n\n"
            "أرسل ملف JSON بهذه الصيغة:\n\n"
            '<pre>{\n  "quiz_name": "اسم الكويز",\n'
            '  "questions": [\n    {\n'
            '      "question": "نص السؤال",\n'
            '      "options": ["أ","ب","ج","د"],\n'
            '      "answer": "الإجابة الصحيحة",\n'
            '      "explanation": "شرح (اختياري)"\n'
            '    }\n  ]\n}</pre>\n\n'
            "أرسل /template لتحميل نموذج جاهز 📥",
            reply_markup=InlineKeyboardMarkup(back_btn),
        )

    # ── My Quizzes ──
    elif data == "my_quizzes":
        quizzes = db.get_all_quizzes()
        if not quizzes:
            await safe_edit(query,
                "📭 لا يوجد كويزات بعد!\n\nارفع ملف JSON لإنشاء أول كويز.",
                InlineKeyboardMarkup(back_btn)
            )
        else:
            await safe_edit(query,
                f"📚 <b>كويزاتي</b> — {len(quizzes)} كويز",
                quizzes_keyboard(quizzes)
            )

    # ── Quiz menu ──
    elif data.startswith("quiz_menu_"):
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        if not quiz:
            await safe_edit(query, "❌ الكويز غير موجود.", InlineKeyboardMarkup(back_btn))
            return
        questions = db.get_questions(quiz_id)
        weak = db.get_weak_questions_by_quiz(quiz_id)

        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quiz_reviews WHERE quiz_id = ?", (quiz_id,))
        review = cursor.fetchone()
        conn.close()

        review_text = ""
        if review:
            d = dict(review)
            days = days_until(d["next_review_date"])
            lbl = stage_label(d["stage"])
            review_text = f"\n🔁 {lbl} — بعد {days} يوم" if days > 0 else f"\n🔁 {lbl} — <b>اليوم!</b>"

        name_safe = html.escape(quiz["name"])
        await safe_edit(
            query,
            f"📋 <b>{name_safe}</b>\n"
            f"📝 {len(questions)} سؤال | ❌ {len(weak)} سؤال ضعيف"
            f"{review_text}",
            quiz_menu_keyboard(quiz_id)
        )

    # ── Schedule review ──
    elif data.startswith("sched_today_"):
        quiz_id = int(data.split("_")[-1])
        db.schedule_first_review(quiz_id, start_today=True)
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        await safe_edit(
            query,
            f"✅ <b>تم!</b> ستصلك مراجعة <b>{name_safe}</b> اليوم الساعة 6 المساء. 🔔",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ ابدأ الكويز (تجربة)", callback_data=f"start_practice_{quiz_id}")],
                [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
            ])
        )

    elif data.startswith("sched_tomorrow_"):
        quiz_id = int(data.split("_")[-1])
        db.schedule_first_review(quiz_id, start_today=False)
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        await safe_edit(
            query,
            f"✅ <b>تم!</b> ستصلك مراجعة <b>{name_safe}</b> غداً الساعة 6 المساء. 🔔",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ ابدأ الكويز (تجربة)", callback_data=f"start_practice_{quiz_id}")],
                [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
            ])
        )

    # ── Start quiz ──
    elif data.startswith("start_quiz_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="quiz")

    # ── Start practice ──
    elif data.startswith("start_practice_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="practice")

    # ── Delete quiz ──
    elif data.startswith("delete_quiz_"):
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        name_safe = html.escape(quiz["name"]) if quiz else "الكويز"
        await safe_edit(
            query,
            f"⚠️ هل أنت متأكد من حذف <b>{name_safe}</b>؟",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ نعم، احذف", callback_data=f"confirm_delete_{quiz_id}"),
                InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_menu_{quiz_id}"),
            ]])
        )

    elif data.startswith("confirm_delete_"):
        quiz_id = int(data.split("_")[-1])
        db.delete_quiz(quiz_id)
        await safe_edit(query, "🗑️ تم حذف الكويز بنجاح.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 كويزاتي", callback_data="my_quizzes")]]))

    # ── Due reviews ──
    elif data == "due_reviews":
        riyadh_tz = pytz.timezone("Asia/Riyadh")
        now = datetime.now(riyadh_tz)
        today_date = now.date().isoformat()
        
        reviews = db.get_due_quiz_reviews()
        if not reviews:
            await safe_edit(query,
                "✅ لا توجد مراجعات اليوم!\n\nاستمر بالعمل الجيد 💪",
                InlineKeyboardMarkup(back_btn)
            )
            return

        overdue_reviews = [r for r in reviews if r["next_review_date"] < today_date]
        due_today_reviews = [r for r in reviews if r["next_review_date"] >= today_date]

        if now.hour >= 18:
            open_reviews = reviews
            locked_reviews = []
        else:
            open_reviews = overdue_reviews
            locked_reviews = due_today_reviews

        text = ""
        kb = []

        if open_reviews:
            text += f"🔁 <b>المراجعات المتاحة الآن</b> — {len(open_reviews)} مراجعة\n\n"
            kb = due_reviews_keyboard(open_reviews).inline_keyboard
            
        if locked_reviews:
            if open_reviews:
                text += "──────────────\n\n"
            text += f"🔒 <b>مراجعات مجدولة لليوم</b> — ({len(locked_reviews)} مراجعة)\n"
            text += "ستتاح لك الساعة 6 مساءً بتوقيت الرياض."

        if not kb:
            kb = back_btn
        elif not open_reviews:
            # If we didn't add due_reviews_keyboard, we need to add the back button
            kb.append(back_btn[0])

        await safe_edit(
            query,
            text,
            InlineKeyboardMarkup(kb)
        )

    # ── Start review ──
    elif data.startswith("start_review_"):
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="review", review_id=review_id)

    # ── Weak questions ──
    elif data == "weak_questions":
        weak_list = db.get_due_weak_questions()
        if not weak_list:
            await safe_edit(query,
                "✅ لا توجد أسئلة ضعيفة مستحقة اليوم!\n\nأداؤك ممتاز 🌟",
                InlineKeyboardMarkup(back_btn)
            )
            return
        quiz_map = {}
        for wq in weak_list:
            qid = wq["quiz_id"]
            if qid not in quiz_map:
                quiz_map[qid] = {"quiz_id": qid, "quiz_name": wq["quiz_name"], "count": 0}
            quiz_map[qid]["count"] += 1
        await safe_edit(query,
            f"❌ <b>الأسئلة الضعيفة</b> — {len(weak_list)} سؤال مستحق",
            weak_quizzes_keyboard(list(quiz_map.values()))
        )

    # ── Start weak ──
    elif data.startswith("start_weak_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="weak")

    # ── Review schedule ──
    elif data == "review_schedule":
        quizzes = db.get_all_quizzes()
        if not quizzes:
            await safe_edit(query, "📅 لا توجد كويزات بعد!", InlineKeyboardMarkup(back_btn))
            return

        conn = db.get_connection()
        cursor = conn.cursor()
        lines = ["📅 <b>جدول المراجعة</b>", ""]

        for quiz in quizzes:
            questions_count = len(db.get_questions(quiz["id"]))
            cursor.execute("SELECT stage, next_review_date FROM quiz_reviews WHERE quiz_id = ?", (quiz["id"],))
            review = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM weak_questions WHERE quiz_id = ?", (quiz["id"],))
            weak_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM weak_questions WHERE quiz_id = ? AND next_review_date <= date('now')",
                (quiz["id"],)
            )
            weak_due = cursor.fetchone()[0]

            name_safe = html.escape(quiz["name"])
            lines.append("──────────────────")
            lines.append(f"📚 <b>{name_safe}</b>")
            lines.append(f"📝 {questions_count} سؤال")

            if review:
                days = days_until(review["next_review_date"])
                lbl = stage_label(review["stage"])
                if days == 0:
                    lines.append(f"🔁 {lbl}: <b>مستحقة اليوم!</b> ⚠️")
                elif days == 1:
                    lines.append(f"🔁 {lbl}: غداً")
                else:
                    lines.append(f"🔁 {lbl}: بعد {days} يوم")
                stages_done = review["stage"]
                bar = "✅" * stages_done + "◻️" * (5 - stages_done)
                lines.append(f"📊 التقدم: {bar} ({stages_done}/5)")
            else:
                lines.append("✅ اكتملت جميع مراجعات الكويز")

            if weak_count > 0:
                if weak_due > 0:
                    lines.append(f"❌ {weak_count} سؤال ضعيف — <b>{weak_due} مستحقة اليوم</b> ⚠️")
                else:
                    lines.append(f"❌ {weak_count} سؤال ضعيف — لا يوجد مستحق اليوم")
            else:
                lines.append("🌟 لا توجد أسئلة ضعيفة")
            lines.append("")

        conn.close()
        await safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(back_btn))
