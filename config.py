import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Admin user IDs — hardcoded trusted admins + env var
ADMIN_IDS = {6099429826}
_env_admin = os.getenv("ADMIN_USER_ID")
if _env_admin:
    try:
        val = int(_env_admin)
        if val != 0:
            ADMIN_IDS.add(val)
    except (ValueError, TypeError):
        pass

def is_admin(user_id: int) -> bool:
    """Return True only if the given user_id is the registered admin."""
    if not user_id:
        return False
    try:
        return int(user_id) in ADMIN_IDS
    except (ValueError, TypeError):
        return False

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
