import datetime
import pytz

DEFAULT_REVIEW_INTERVALS = [1, 3, 7, 14, 30]
MAX_STAGE = 5


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


def stage_label(stage: int, is_mastered: bool = False) -> str:
    """Returns human-friendly Arabic label for the card review stage."""
    if is_mastered or stage >= MAX_STAGE:
        return "متقن في الذاكرة الدائمة 🌟"
    labels = [
        "المرحلة 1 (غداً 🧠)",
        "المرحلة 2 (بعد 3 أيام ⚡)",
        "المرحلة 3 (بعد أسبوع 🎯)",
        "المرحلة 4 (بعد أسبوعين 💎)",
        "المرحلة 5 (بعد شهر 🏆)",
    ]
    try:
        stg = int(stage)
        if 0 <= stg < len(labels):
            return labels[stg]
    except (ValueError, TypeError):
        pass
    return "مكتمل ومثبت 🌟"


def calculate_next_schedule(
    current_stage: int, rating: str, ease_factor: float = 2.5
) -> dict:
    """
    Computes new stage, next review date, ease factor, and mastery status.
    Rating options:
      - 'easy': User found it effortless. Jump +2 stages (or reach mastery), ease factor increases.
      - 'good': User recalled after some thought. Standard advance +1 stage.
      - 'hard': User forgot or made a mistake. Reset to stage 0 (tomorrow), ease factor drops.
    """
    today = get_riyadh_today()
    ease = max(1.3, float(ease_factor or 2.5))
    is_mastered = 0
    lapsed = False

    if rating == "easy":
        ease = min(3.0, ease + 0.15)
        new_stage = current_stage + 2
        if new_stage >= MAX_STAGE:
            new_stage = MAX_STAGE
            is_mastered = 1
            days_to_add = 60  # Maintenance check
        else:
            days_to_add = DEFAULT_REVIEW_INTERVALS[min(new_stage, len(DEFAULT_REVIEW_INTERVALS) - 1)]

    elif rating == "good":
        new_stage = current_stage + 1
        if new_stage >= MAX_STAGE:
            new_stage = MAX_STAGE
            is_mastered = 1
            days_to_add = 45
        else:
            days_to_add = DEFAULT_REVIEW_INTERVALS[min(new_stage, len(DEFAULT_REVIEW_INTERVALS) - 1)]

    else:  # 'hard'
        lapsed = True
        ease = max(1.3, ease - 0.20)
        new_stage = 0
        days_to_add = 1  # Re-test tomorrow

    next_date = today + datetime.timedelta(days=days_to_add)

    return {
        "new_stage": new_stage,
        "next_review_date": next_date.isoformat(),
        "ease_factor": round(ease, 2),
        "is_mastered": is_mastered,
        "lapsed": lapsed,
        "days_to_add": days_to_add,
    }


def get_initial_review_date() -> str:
    """First review is scheduled for tomorrow (1 day interval)."""
    return (get_riyadh_today() + datetime.timedelta(days=1)).isoformat()
