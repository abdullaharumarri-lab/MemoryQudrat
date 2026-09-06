#!/usr/bin/env python3
"""
format_bot.py — سكربت فرمتة البوت وتصفير قاعدة البيانات من جذورها
"""
import shutil
import os
import database as db

if __name__ == "__main__":
    db_file = "memory_qudrat.db"
    if os.path.exists(db_file):
        bak_file = "memory_qudrat_pre_format.db.bak"
        shutil.copyfile(db_file, bak_file)
        print(f"✅ تم حفظ نسخة احتياطية: {bak_file}")

    db.format_database_from_roots(keep_admin_user=True)
    db.init_db()
    print("🚀 تم فرمتة البوت وقاعدة البيانات بنجاح تام!")
