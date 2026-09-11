import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Admin user IDs — loaded from environment variable with backward-compatible fallback
ADMIN_IDS = set()
_env_admin = os.getenv("ADMIN_USER_ID")
if _env_admin:
    for part in _env_admin.split(","):
        try:
            val = int(part.strip())
            if val != 0:
                ADMIN_IDS.add(val)
        except (ValueError, TypeError):
            pass

if not ADMIN_IDS:
    ADMIN_IDS.add(6099429826)

ADMIN_USER_ID = next(iter(ADMIN_IDS))
ADMIN_USER_IDS = ADMIN_IDS

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
REVIEW_INTERVALS = [1, 3, 7, 14, 30]

# Database file path
DB_PATH = "memory_qudrat.db"

# Gemini model (State of the Art)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Upload limits (security)
MAX_JSON_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB (allows embedded passage screenshots as base64)
MAX_QUESTIONS_PER_QUIZ = 200           # max questions per upload

# Official Channel
OFFICIAL_CHANNEL_URL = "https://t.me/MemoryQudrat"
OFFICIAL_CHANNEL_USERNAME = "@MemoryQudrat"
