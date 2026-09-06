#!/usr/bin/env python3
"""
seed_tables.py — إضافة مجلد وكويزات جدول الضرب (1 - 30)
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database as db
from config import ADMIN_USER_ID

def generate_distractors(n, k, correct):
    distractors = set()
    candidates = [
        n * (k + 1),
        n * (k - 1),
        (n + 1) * k,
        (n - 1) * k,
        correct + 2,
        correct - 2,
        correct + 10,
        correct - 10,
        correct + 4,
        correct - 4,
    ]
    for c in candidates:
        if c > 0 and c != correct:
            distractors.add(str(c))
        if len(distractors) >= 3:
            break
            
    step = 1
    while len(distractors) < 3:
        alt1 = correct + step
        alt2 = max(1, correct - step)
        if alt1 != correct: distractors.add(str(alt1))
        if alt2 != correct and len(distractors) < 3: distractors.add(str(alt2))
        step += 1

    options = [str(correct)] + list(distractors)[:3]
    random.shuffle(options)
    return options

def main():
    cat_id = db.create_category(
        name="✖️ جدول الضرب (1 - 30)",
        icon="✖️",
        is_public=1,
        owner_id=ADMIN_USER_ID
    )
    print(f"📁 تم إنشاء مجلد جدول الضرب (معرف: {cat_id})")

    for n in range(1, 31):
        quiz_name = f"جدول ضرب {n}"
        questions = []
        for k in range(1, 13):
            correct = n * k
            opts = generate_distractors(n, k, correct)
            questions.append({
                "question": f"ما ناتج: {n} × {k} = ؟",
                "options": opts,
                "answer": str(correct),
                "explanation": f"💡 {n} × {k} = {correct}"
            })

        quiz_id = db.save_quiz_without_review(
            name=quiz_name,
            questions=questions,
            category_id=cat_id,
            owner_id=ADMIN_USER_ID,
            is_public=1
        )
        print(f" ✅ تم إنشاء: {quiz_name} (12 سؤالاً)")

    print("\n🎉 اكتمل إنشاء كافة الجداول الـ 30 بنجاح تام!")

if __name__ == "__main__":
    main()
