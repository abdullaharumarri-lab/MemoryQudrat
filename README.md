# MemoryQudrat 🧠

بوت تيليجرام للمراجعة الذكية باستخدام التكرار المتباعد.

## المزايا
- 📤 استخراج الأسئلة من PDF بالذكاء الاصطناعي (Gemini)
- 🔁 تكرار متباعد للكويزات: 1 → 3 → 7 → 30 يوم
- ❌ تتبع الأسئلة الخاطئة مع تكرار متباعد مستقل
- 🔔 تذكير يومي تلقائي الساعة 8 صباحاً
- 📊 نتائج فورية مع شرح الإجابات

## الإعداد

### 1. تثبيت المتطلبات
```bash
pip install -r requirements.txt
```

### 2. إعداد متغيرات البيئة
انسخ `.env.example` إلى `.env` وأضف:
```
TELEGRAM_BOT_TOKEN=your_token_here
GEMINI_API_KEY=your_gemini_key_here
```

**كيف تحصل على التوكنات:**
- **Telegram Token:** أرسل `/newbot` لـ [@BotFather](https://t.me/BotFather)
- **Gemini API Key:** من [Google AI Studio](https://aistudio.google.com/app/apikey)

### 3. تشغيل البوت
```bash
python bot.py
```

## هيكل الملفات
```
MemoryQudrat/
├── bot.py              # نقطة البداية
├── config.py           # الإعدادات
├── database.py         # قاعدة البيانات SQLite
├── ai_extractor.py     # استخراج الأسئلة بـ Gemini
├── spaced_repetition.py # منطق التكرار المتباعد
├── handlers/
│   ├── main_menu.py    # القائمة الرئيسية
│   ├── pdf_handler.py  # معالجة PDF
│   ├── quiz_handler.py # جلسات الكويز والمراجعة
│   └── review_handler.py
├── requirements.txt
└── .env
```

## نظام التكرار المتباعد
```
أول مرة → +1 يوم → +3 أيام → +7 أيام → +30 يوم ✅
```
