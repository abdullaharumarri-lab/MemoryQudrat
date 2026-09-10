#!/usr/bin/env python3
"""
reset_and_seed.py — تصفير قاعدة بيانات البوت بالكامل وإعادة زراعة جدول الضرب (1 - 30)
يعمل محلياً وعلى السيرفر VPS مباشرة.
"""
import os
import sys

# Ensure UTF-8 output on all platforms
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import database as db
import seed_tables

def main():
    print("🧹 جاري تهيئة وفرمتة قاعدة البيانات بالكامل...")
    db.init_db()
    db.format_database_from_roots(keep_admin_user=True)
    print("✅ تم تصفير وفرمتة قاعدة البيانات بنجاح تام.")

    print("\n➕ جاري إضافة مجلد وكويزات جدول الضرب (1 - 30)...")
    seed_tables.main()
    print("\n🎉 اكتملت العملية بنجاح! البوت الآن نظيف ومفرمت ويحتوي فقط على جدول الضرب 🌟")

if __name__ == "__main__":
    main()
