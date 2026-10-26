import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Telegram Bot Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Personal Single-User Admin ID
_env_admin = os.getenv("ADMIN_USER_ID", "6099429826")
try:
    ADMIN_USER_ID = int(_env_admin.split(",")[0].strip())
except (ValueError, TypeError):
    ADMIN_USER_ID = 6099429826

ADMIN_IDS = {ADMIN_USER_ID}


def is_authorized(user_id: int) -> bool:
    """Ensures bot only responds to the personal owner."""
    if not user_id:
        return False
    try:
        return int(user_id) == ADMIN_USER_ID
    except (ValueError, TypeError):
        return False


# Compatibility alias
is_admin = is_authorized

# Timezone & Scheduling
TIMEZONE = "Asia/Riyadh"
REVIEW_INTERVALS = [1, 3, 7, 14, 30]

# Database Path
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_qudrat.db")

# Media Storage Dirs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
DOCUMENTS_DIR = os.path.join(DATA_DIR, "documents")

os.makedirs(PHOTOS_DIR, exist_ok=True)
os.makedirs(DOCUMENTS_DIR, exist_ok=True)
