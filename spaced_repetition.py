import datetime
import math
import pytz

REVIEW_INTERVALS = {
    1: 3,   # Review 1 after 3 days
    2: 7,   # Review 2 after 7 days
    3: 14,  # Review 3 after 14 days
}
MAX_REVIEW_STAGE = 3


def get_riyadh_now() -> datetime.datetime:
    """Returns current datetime in Riyadh timezone."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Riyadh"))
    except ImportError:
        riyadh_tz = pytz.timezone("Asia/Riyadh")
        return datetime.datetime.now(riyadh_tz)


def get_riyadh_today() -> datetime.date:
    """Returns current date in Riyadh timezone."""
    return get_riyadh_now().date()


def get_riyadh_today_iso() -> str:
    """Returns YYYY-MM-DD string for today in Riyadh timezone."""
    return get_riyadh_today().isoformat()


def days_until(target_date_str: str) -> int:
    """Returns integer delta in days between today and target date."""
    if not target_date_str:
        return 0
    try:
        target = datetime.date.fromisoformat(str(target_date_str)[:10])
    except Exception:
        return 0
    today = get_riyadh_today()
    return (target - today).days


def distribute_lessons(total_lessons: int, target_days: int) -> list[int]:
    """
    Distributes total_lessons (1..N) across target_days (1..D).
    Returns a list of length total_lessons where index i has scheduled_day (1..D).
    """
    if total_lessons <= 0:
        return []
    target_days = max(1, target_days)
    
    distribution = []
    for i in range(total_lessons):
        # Calculate day index 1..target_days
        day = min(target_days, math.floor(i * target_days / total_lessons) + 1)
        distribution.append(day)
    return distribution


def calculate_next_review(current_stage: int) -> dict:
    """
    Calculates next review stage, interval days, and due date.
    Returns:
      {
        "next_stage": int,
        "days_to_add": int,
        "due_date": "YYYY-MM-DD",
        "is_mastered": bool
      }
    """
    next_stage = current_stage + 1
    today = get_riyadh_today()

    if next_stage > MAX_REVIEW_STAGE:
        return {
            "next_stage": MAX_REVIEW_STAGE,
            "days_to_add": 0,
            "due_date": today.isoformat(),
            "is_mastered": True,
        }

    days_to_add = REVIEW_INTERVALS.get(next_stage, 3)
    due_date = today + datetime.timedelta(days=days_to_add)

    return {
        "next_stage": next_stage,
        "days_to_add": days_to_add,
        "due_date": due_date.isoformat(),
        "is_mastered": False,
    }


def stage_label(stage: int, is_mastered: bool = False) -> str:
    """Returns friendly Arabic label for review stage."""
    if is_mastered or stage >= MAX_REVIEW_STAGE:
        return "مثبت في الذاكرة الدائمة 🌟"
    labels = {
        1: "مراجعة 1 (تثبيت بعد 3 أيام ⚡)",
        2: "مراجعة 2 (ترسيخ بعد أسبوع 🎯)",
        3: "مراجعة 3 (إتقان نهائي 🏆)",
    }
    return labels.get(stage, f"مراجعة مرحلة {stage}")
