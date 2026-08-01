from datetime import date, timedelta
from config import REVIEW_INTERVALS


def next_review_date(stage: int) -> str:
    """
    Calculate the next review date based on the current stage.
    Stage 0 → +1 day, Stage 1 → +3 days, Stage 2 → +7 days, Stage 3 → +30 days
    """
    if stage >= len(REVIEW_INTERVALS):
        # Already completed all stages
        return date.today().isoformat()
    interval = REVIEW_INTERVALS[stage]
    return (date.today() + timedelta(days=interval)).isoformat()


def is_due(review_date_str: str) -> bool:
    """Check if a review date is today or in the past."""
    review_date = date.fromisoformat(review_date_str)
    return review_date <= date.today()


def stage_label(stage: int) -> str:
    """Human-readable label for the current review stage."""
    labels = ["📅 المراجعة الأولى", "📅 المراجعة الثانية",
              "📅 المراجعة الثالثة", "📅 المراجعة الرابعة"]
    if stage < len(labels):
        return labels[stage]
    return "✅ مكتملة"


def days_until(date_str: str) -> int:
    """Return how many days until a review date."""
    target = date.fromisoformat(date_str)
    delta = target - date.today()
    return max(0, delta.days)
