import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Admin user ID — only this user can access admin commands and upload to public bank
_admin_id_raw = os.getenv("ADMIN_USER_ID", "0")
try:
    ADMIN_USER_ID = int(_admin_id_raw)
except (ValueError, TypeError):
    ADMIN_USER_ID = 0

def is_admin(user_id: int) -> bool:
    """Return True only if the given user_id is the registered admin."""
    return ADMIN_USER_ID != 0 and user_id == ADMIN_USER_ID

# Private channel ID — bot only works inside this channel
# Set this in .env: ALLOWED_CHANNEL_ID=-100xxxxxxxxxx
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID", "0"))

# Spaced repetition intervals in days
REVIEW_INTERVALS = [1, 3, 7, 30]

# Database file path
DB_PATH = "memory_qudrat.db"

# Gemini model
GEMINI_MODEL = "gemini-2.0-flash"

# Upload limits (security)
MAX_JSON_FILE_SIZE_BYTES = 500 * 1024  # 500 KB max
MAX_QUESTIONS_PER_QUIZ = 200           # max questions per upload
