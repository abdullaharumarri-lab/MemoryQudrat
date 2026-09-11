#!/usr/bin/env python3
"""
seed_tables.py — إضافة مجلد وكويزات جدول الضرب (1 - 30)
بدون أي مكتبات خارجية (Standard Library Only: sqlite3, json, random)
"""
import sqlite3
import json
import random
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import ADMIN_USER_ID
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_qudrat.db")

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Create category
    cursor.execute(
        "INSERT INTO categories (name, icon, sort_order, is_public, owner_id) VALUES (?, ?, ?, ?, ?)",
        ("✖️ جدول الضرب (1 - 30)", "✖️", 1, 1, ADMIN_USER_ID)
    )
    cat_id = cursor.lastrowid
    print(f"📁 تم إنشاء مجلد جدول الضرب بنجاح (معرف المجلد: {cat_id})")

    # 2. Create 30 quizzes
    for n in range(1, 31):
        quiz_name = f"جدول ضرب {n}"
        cursor.execute(
            "INSERT INTO quizzes (name, category_id, owner_id, is_public) VALUES (?, ?, ?, ?)",
            (quiz_name, cat_id, ADMIN_USER_ID, 1)
        )
        quiz_id = cursor.lastrowid

        for k in range(1, 13):
            correct = n * k
            opts = generate_distractors(n, k, correct)
            cursor.execute(
                """INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    quiz_id,
                    f"ما ناتج: {n} × {k} = ؟",
                    json.dumps(opts, ensure_ascii=False),
                    str(correct),
                    f"💡 {n} × {k} = {correct}"
                )
            )
        print(f" ✅ تم إنشاء: {quiz_name} (12 سؤالاً)")

    conn.commit()
    conn.close()
    print("\n🎉 اكتمل إنشاء كافة الجداول الـ 30 (360 سؤالاً) بنجاح تام!")

if __name__ == "__main__":
    main()
