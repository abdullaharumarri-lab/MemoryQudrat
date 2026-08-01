from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from spaced_repetition import days_until, stage_label
from utils import send_clean_message


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📋 رفع JSON", callback_data="upload_json")],
        [InlineKeyboardButton("📚 كويزاتي", callback_data="my_quizzes")],
        [
            InlineKeyboardButton("🔁 مراجعات اليوم", callback_data="due_reviews"),
            InlineKeyboardButton("❌ الأسئلة الضعيفة", callback_data="weak_questions"),
        ],
        [InlineKeyboardButton("📅 جدول المراجعة", callback_data="review_schedule")],
    ]
    return InlineKeyboardMarkup(keyboard)


def quizzes_keyboard(quizzes: list):
    keyboard = []
    for quiz in quizzes:
        keyboard.append([
            InlineKeyboardButton(
                f"📋 {quiz['name']}", callback_data=f"quiz_menu_{quiz['id']}"
            )
        ])
    keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)


def quiz_menu_keyboard(quiz_id: int):
    keyboard = [
        [InlineKeyboardButton("▶️ ابدأ الكويز", callback_data=f"start_quiz_{quiz_id}")],
        [InlineKeyboardButton("❌ حذف الكويز", callback_data=f"delete_quiz_{quiz_id}")],
        [InlineKeyboardButton("🔙 كويزاتي", callback_data="my_quizzes")],
    ]
    return InlineKeyboardMarkup(keyboard)


def due_reviews_keyboard(reviews: list):
    keyboard = []
    for r in reviews:
        label = stage_label(r["stage"])
        keyboard.append([
            InlineKeyboardButton(
                f"🔁 {r['quiz_name']} — {label}",
                callback_data=f"start_review_{r['id']}_{r['quiz_id']}",
            )
        ])
    keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)


def weak_quizzes_keyboard(quizzes_with_weak: list):
    """Show quizzes that have weak questions."""
    keyboard = []
    for item in quizzes_with_weak:
        keyboard.append([
            InlineKeyboardButton(
                f"❌ {item['quiz_name']} ({item['count']} سؤال)",
                callback_data=f"start_weak_{item['quiz_id']}",
            )
        ])
    keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu."""
    text = (
        "👋 أهلاً بك في *MemoryQudrat*!\n\n"
        "نظام مراجعة ذكي باستخدام التكرار المتباعد 🧠\n"
        "اختر ما تريد:"
    )
    if update.message:
        await send_clean_message(
            context, update.effective_chat.id, text, update=update, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline keyboard button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── Main menu ──
    if data == "main_menu":
        await main_menu_handler(update, context)

    # ── Upload JSON ──
    elif data == "upload_json":
        await query.edit_message_text(
            "📋 *رفع JSON مباشرة*\n\n"
            "أرسل ملف JSON بالصيغة التالية:\n\n"
            "```json\n"
            "{\n"
            '  "quiz_name": "اسم الكويز",\n'
            '  "questions": [\n'
            '    {\n'
            '      "question": "نص السؤال",\n'
            '      "options": ["أ","ب","ج","د"],\n'
            '      "answer": "الإجابة الصحيحة",\n'
            '      "explanation": "شرح (اختياري)"\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "```\n\n"
            "أرسل /template لتحميل نموذج جاهز 📥",
            parse_mode="Markdown",
        )

    # ── Review Schedule ──
    elif data == "review_schedule":
        quizzes = db.get_all_quizzes()
        if not quizzes:
            await query.edit_message_text(
                "📅 لا توجد كويزات بعد!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
            )
            return

        from spaced_repetition import days_until, stage_label
        conn = db.get_connection()
        cursor = conn.cursor()

        lines = ["📅 *جدول المراجعة*", ""]

        for quiz in quizzes:
            questions_count = len(db.get_questions(quiz["id"]))

            cursor.execute(
                "SELECT stage, next_review_date FROM quiz_reviews WHERE quiz_id = ?",
                (quiz["id"],)
            )
            review = cursor.fetchone()

            cursor.execute(
                "SELECT COUNT(*) FROM weak_questions WHERE quiz_id = ?",
                (quiz["id"],)
            )
            weak_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM weak_questions WHERE quiz_id = ? AND next_review_date <= date('now')",
                (quiz["id"],)
            )
            weak_due = cursor.fetchone()[0]

            lines.append(f"──────────────────")
            lines.append(f"📚 *{quiz['name']}*")
            lines.append(f"📝 {questions_count} سؤال")

            # Quiz spaced repetition status
            if review:
                days = days_until(review["next_review_date"])
                lbl = stage_label(review["stage"])
                if days == 0:
                    lines.append(f"🔁 {lbl}: *مستحقة اليوم!* ⚠️")
                elif days == 1:
                    lines.append(f"🔁 {lbl}: غدًا")
                else:
                    lines.append(f"🔁 {lbl}: بعد {days} يوم")
                # Show all stages progress
                stages_done = review["stage"]
                stages_total = 4
                bar = "✅" * stages_done + "◻️" * (stages_total - stages_done)
                lines.append(f"📊 التقدم: {bar} ({stages_done}/{stages_total})")
            else:
                lines.append("✅ اكتملت جميع مراجعات الكويز")

            # Weak questions status
            if weak_count > 0:
                if weak_due > 0:
                    lines.append(f"❌ {weak_count} سؤال ضعيف — *{weak_due} مستحقة اليوم* ⚠️")
                else:
                    lines.append(f"❌ {weak_count} سؤال ضعيف — لا يوجد مستحق اليوم")
            else:
                lines.append("🌟 لا توجد أسئلة ضعيفة")

            lines.append("")

        conn.close()

        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]]),
            parse_mode="Markdown",
        )

    # ── My Quizzes ──
    elif data == "my_quizzes":
        quizzes = db.get_all_quizzes()
        if not quizzes:
            await query.edit_message_text(
                "📭 لا يوجد كويزات بعد!\n\nارفع ملف PDF لإنشاء أول كويز.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 رفع PDF", callback_data="upload_pdf")],
                    [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")],
                ]),
            )
        else:
            await query.edit_message_text(
                f"📚 *كويزاتي* — {len(quizzes)} كويز",
                reply_markup=quizzes_keyboard(quizzes),
                parse_mode="Markdown",
            )

    # ── Quiz menu ──
    elif data.startswith("quiz_menu_"):
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        questions = db.get_questions(quiz_id)
        weak = db.get_weak_questions_by_quiz(quiz_id)

        # Get next review info
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM quiz_reviews WHERE quiz_id = ?", (quiz_id,)
        )
        review = cursor.fetchone()
        conn.close()

        review_text = ""
        if review:
            d = dict(review)
            days = days_until(d["next_review_date"])
            lbl = stage_label(d["stage"])
            review_text = f"\n🔁 {lbl} — بعد {days} يوم" if days > 0 else f"\n🔁 {lbl} — *اليوم!*"

        await query.edit_message_text(
            f"📋 *{quiz['name']}*\n"
            f"📝 {len(questions)} سؤال | ❌ {len(weak)} سؤال ضعيف"
            f"{review_text}",
            reply_markup=quiz_menu_keyboard(quiz_id),
            parse_mode="Markdown",
        )

    # ── Start quiz ──
    elif data.startswith("start_quiz_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="quiz")

    # ── Delete quiz ──
    elif data.startswith("delete_quiz_"):
        quiz_id = int(data.split("_")[-1])
        quiz = db.get_quiz(quiz_id)
        await query.edit_message_text(
            f"⚠️ هل أنت متأكد من حذف *{quiz['name']}*؟",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🗑️ نعم، احذف", callback_data=f"confirm_delete_{quiz_id}"),
                    InlineKeyboardButton("❌ إلغاء", callback_data=f"quiz_menu_{quiz_id}"),
                ]
            ]),
            parse_mode="Markdown",
        )

    elif data.startswith("confirm_delete_"):
        quiz_id = int(data.split("_")[-1])
        db.delete_quiz(quiz_id)
        await query.edit_message_text(
            "🗑️ تم حذف الكويز بنجاح.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 كويزاتي", callback_data="my_quizzes")]
            ]),
        )

    # ── Due reviews ──
    elif data == "due_reviews":
        reviews = db.get_due_quiz_reviews()
        if not reviews:
            await query.edit_message_text(
                "✅ لا توجد مراجعات اليوم!\n\nاستمر بالعمل الجيد 💪",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
                ]),
            )
        else:
            await query.edit_message_text(
                f"🔁 *مراجعات اليوم* — {len(reviews)} مراجعة",
                reply_markup=due_reviews_keyboard(reviews),
                parse_mode="Markdown",
            )

    # ── Start review session ──
    elif data.startswith("start_review_"):
        parts = data.split("_")
        review_id = int(parts[2])
        quiz_id = int(parts[3])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(
            update, context, quiz_id,
            session_type="review", review_id=review_id
        )

    # ── Weak questions ──
    elif data == "weak_questions":
        weak_list = db.get_due_weak_questions()
        if not weak_list:
            await query.edit_message_text(
                "✅ لا توجد أسئلة ضعيفة مستحقة اليوم!\n\nأداؤك ممتاز 🌟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 الرئيسية", callback_data="main_menu")]
                ]),
            )
            return

        # Group by quiz
        quiz_map = {}
        for wq in weak_list:
            qid = wq["quiz_id"]
            if qid not in quiz_map:
                quiz_map[qid] = {"quiz_id": qid, "quiz_name": wq["quiz_name"], "count": 0}
            quiz_map[qid]["count"] += 1

        await query.edit_message_text(
            f"❌ *الأسئلة الضعيفة* — {len(weak_list)} سؤال مستحق",
            reply_markup=weak_quizzes_keyboard(list(quiz_map.values())),
            parse_mode="Markdown",
        )

    # ── Start weak session ──
    elif data.startswith("start_weak_"):
        quiz_id = int(data.split("_")[-1])
        from handlers.quiz_handler import start_quiz_session
        await start_quiz_session(update, context, quiz_id, session_type="weak")
