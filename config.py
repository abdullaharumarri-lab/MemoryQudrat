import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DB_PATH = "memory_qudrat.db"

# Private channel ID — bot only works inside this channel
# Set this in .env: ALLOWED_CHANNEL_ID=-100xxxxxxxxxx
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID", "0"))

# Spaced repetition intervals in days
REVIEW_INTERVALS = [1, 3, 7, 30]

# Database file path
DB_PATH = "memory_qudrat.db"

# Gemini model
GEMINI_MODEL = "gemini-2.0-flash"
