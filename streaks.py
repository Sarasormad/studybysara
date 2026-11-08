"""Pure streak and time rules (no database), so they're easy to test."""
from datetime import timedelta, timezone

from config import MAX_SECONDS_PER_QUESTION, PRACTICE_TZ, STREAK_BONUS_CAP


def active_seconds(served_at, submitted_at):
    """Time credited for one question: submit time minus when it was shown, capped.
    Both are aware datetimes. A question with no recorded served_at earns nothing."""
    if served_at is None:
        return 0
    seconds = int((submitted_at - served_at).total_seconds())
    return max(0, min(seconds, MAX_SECONDS_PER_QUESTION))


def practice_day(moment):
    """The local calendar date (in PRACTICE_TZ) that an aware datetime falls on."""
    return moment.astimezone(PRACTICE_TZ).date()


def streak_ending(qualifying_days, day):
    """How many consecutive qualifying days end on `day` (0 if `day` doesn't qualify)."""
    count = 0
    while day in qualifying_days:
        count += 1
        day -= timedelta(days=1)
    return count


def current_streak(qualifying_days, today):
    """Today counts if it's already secured; otherwise the streak is still alive through yesterday."""
    if today in qualifying_days:
        return streak_ending(qualifying_days, today)
    return streak_ending(qualifying_days, today - timedelta(days=1))


def best_streak(qualifying_days):
    best = 0
    for day in qualifying_days:
        if day - timedelta(days=1) not in qualifying_days:      # start of a run
            best = max(best, streak_ending_forward(qualifying_days, day))
    return best


def streak_ending_forward(qualifying_days, start):
    count = 0
    while start in qualifying_days:
        count += 1
        start += timedelta(days=1)
    return count


def streak_points(streak_day):
    """Points for securing day N of a streak: day 1 = 2, day 2 = 3 ... day 9+ = 10."""
    return min(streak_day + 1, STREAK_BONUS_CAP)


def week_start_utc(now):
    """Monday 12:00 AM local time of the current week, as an aware UTC datetime."""
    local = now.astimezone(PRACTICE_TZ)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.astimezone(timezone.utc)
