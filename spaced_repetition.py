import datetime
import pytz

REVIEW_INTERVALS = [1, 3, 7, 30]

def next_review_date(stage: int) -> str:
    if stage < 0 or stage >= len(REVIEW_INTERVALS):
        raise ValueError("Invalid stage")
    days_to_add = REVIEW_INTERVALS[stage]
    # Calculate next review relative to current day
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    now_riyadh = datetime.datetime.now(riyadh_tz)
    base_date = now_riyadh.date()
    
    # If before 6 PM, count today as day 0. If after 6 PM, count tomorrow as day 0.
    if now_riyadh.hour >= 18:
        base_date += datetime.timedelta(days=1)
        
    next_date = base_date + datetime.timedelta(days=days_to_add)
    return next_date.isoformat()


def days_until(target_date_str: str) -> int:
    target = datetime.date.fromisoformat(target_date_str)
    
    riyadh_tz = pytz.timezone("Asia/Riyadh")
    now_riyadh = datetime.datetime.now(riyadh_tz)
    today = now_riyadh.date()
    
    delta = (target - today).days
    return delta


def stage_label(stage: int) -> str:
    labels = ["المراجعة الأولى", "المراجعة الثانية", "المراجعة الثالثة", "المراجعة الرابعة"]
    if 0 <= stage < len(labels):
        return labels[stage]
    return "مكتمل"
