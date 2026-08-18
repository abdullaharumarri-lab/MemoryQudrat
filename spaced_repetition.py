import datetime
import pytz

DEFAULT_REVIEW_INTERVALS = [0, 3, 7, 14, 30]

def next_review_date(stage: int, previous_date_str: str, intervals: list = None) -> str:
    if intervals is None:
        intervals = DEFAULT_REVIEW_INTERVALS

    try:
        stg = int(stage)
    except (ValueError, TypeError):
        stg = 0
    if stg < 0 or stg >= len(intervals):
        stg = len(intervals) - 1
        
    days_to_add = intervals[stg]
    try:
        prev_date = datetime.date.fromisoformat(str(previous_date_str))
    except Exception:
        prev_date = datetime.date.today()
    
    next_date = prev_date + datetime.timedelta(days=days_to_add)
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
    labels = ["المراجعة الأولى", "المراجعة الثانية", "المراجعة الثالثة", "المراجعة الرابعة", "المراجعة الخامسة"]
    try:
        stg = int(stage)
        if 0 <= stg < len(labels):
            return labels[stg]
    except (ValueError, TypeError):
        pass
    return "مكتمل"
