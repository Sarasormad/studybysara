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


def streak_ending(qualifying_days, day, shielded=frozenset()):
    """How many real qualifying days are in the run ending on `day` (0 if the run is broken).

    Shielded days (missed days covered by a streak shield) keep the run going but don't
    add to the count."""
    count = 0
    while day in qualifying_days or day in shielded:
        if day in qualifying_days:
            count += 1
        day -= timedelta(days=1)
    return count


def current_streak(qualifying_days, today, shielded=frozenset()):
    """Today counts if it's already secured; otherwise the streak is still alive through yesterday."""
    if today in qualifying_days:
        return streak_ending(qualifying_days, today, shielded)
    return streak_ending(qualifying_days, today - timedelta(days=1), shielded)


def best_streak(qualifying_days, shielded=frozenset()):
    linked = set(qualifying_days) | set(shielded)
    best = 0
    for day in linked:
        if day - timedelta(days=1) not in linked:                # start of a run
            best = max(best, streak_ending_forward(qualifying_days, day, shielded))
    return best


def streak_ending_forward(qualifying_days, start, shielded=frozenset()):
    count = 0
    while start in qualifying_days or start in shielded:
        if start in qualifying_days:
            count += 1
        start += timedelta(days=1)
    return count


def days_a_shield_would_cover(linked_days, today, shields_held):
    """Missed days since the last practice day (before today) that held shields can cover.

    Returns those days if there are between 1 and `shields_held` of them, else an empty set
    (nothing missed, no practice history, or too many missed days to save the streak)."""
    earlier = [d for d in linked_days if d < today]
    if not earlier or shields_held <= 0:
        return set()
    last = max(earlier)
    missed = {last + timedelta(days=n) for n in range(1, (today - last).days)}
    return missed if 1 <= len(missed) <= shields_held else set()


def streak_points(streak_day):
    """Points for securing day N of a streak: day 1 = 2, day 2 = 3 ... day 9+ = 10."""
    return min(streak_day + 1, STREAK_BONUS_CAP)


def week_start_utc(now):
    """Monday 12:00 AM local time of the current week, as an aware UTC datetime."""
    local = now.astimezone(PRACTICE_TZ)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.astimezone(timezone.utc)
