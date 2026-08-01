import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Spaced repetition intervals in days
REVIEW_INTERVALS = [1, 3, 7, 30]

# Database file path
DB_PATH = "memory_qudrat.db"

# Gemini model
GEMINI_MODEL = "gemini-2.0-flash"
