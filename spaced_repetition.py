import datetime
import pytz

DEFAULT_REVIEW_INTERVALS = [1, 3, 7, 14, 30]

def get_riyadh_today() -> datetime.date:
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    return datetime.datetime.now(riyadh_tz).date()


def next_review_date(stage: int, previous_date_str: str = None, intervals: list = None) -> str:
    if intervals is None:
        intervals = DEFAULT_REVIEW_INTERVALS

    try:
        stg = int(stage)
    except (ValueError, TypeError):
        stg = 0
    if stg < 0 or stg >= len(intervals):
        stg = len(intervals) - 1
        
    days_to_add = intervals[stg]
    base_date = get_riyadh_today()
    next_date = base_date + datetime.timedelta(days=days_to_add)
    return next_date.isoformat()


def days_until(target_date_str: str) -> int:
    if not target_date_str:
        return 0
    try:
        target = datetime.date.fromisoformat(str(target_date_str))
    except Exception:
        return 0
    
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    now_riyadh = datetime.datetime.now(riyadh_tz)
    today = now_riyadh.date()
    
    delta = (target - today).days
    return delta


def stage_label(stage: int) -> str:
    labels = [
        "المراجعة 1 (بعد يوم)",
        "المراجعة 2 (بعد 3 أيام)",
        "المراجعة 3 (بعد أسبوع)",
        "المراجعة 4 (بعد أسبوعين)",
        "المراجعة 5 (بعد شهر)",
    ]
    try:
        stg = int(stage)
        if 0 <= stg < len(labels):
            return labels[stg]
    except (ValueError, TypeError):
        pass
    return "مكتمل ومثبت 🌟"
