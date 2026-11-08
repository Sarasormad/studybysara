"""App-wide settings for practice, points and streaks. Change rules here, not in the code."""
from zoneinfo import ZoneInfo

# Day boundaries ("today", streak days, the weekly leaderboard reset) use this time zone.
PRACTICE_TZ = ZoneInfo("America/Los_Angeles")

# Streaks
DAILY_GOAL_SECONDS = 10 * 60          # active practice needed for a day to count
MAX_SECONDS_PER_QUESTION = 3 * 60     # cap per question so an idle open tab can't run up the clock
STREAK_BONUS_CAP = 10                 # streak points per day: min(streak day + 1, cap)

# Points
FUNDAMENTALS_POINTS = 10              # per correct Fundamentals answer (the only practice that earns points)
FUNDAMENTALS_ROUND_SIZE = 10

# Leaderboard
LEADERBOARD_SIZE = 50

# Dashboards
WEAK_TOPIC_MIN_ATTEMPTS = 3           # a topic needs this many answers before it can be called "weak"
WEAK_TOPICS_SHOWN = 2                 # how many weakest topics to highlight
RECENT_ATTEMPTS_SHOWN = 10
