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

# AI practice (topic + photo). The API key is read by the Anthropic SDK from ANTHROPIC_API_KEY;
# it never goes to the browser.
import os as _os
AI_MODEL = _os.environ.get("STUDYBYSARA_AI_MODEL", "claude-opus-5-5")
AI_EFFORT = _os.environ.get("STUDYBYSARA_AI_EFFORT", "medium")   # low | medium | high
AI_DAILY_LIMIT = 20                   # topic + photo generations per student per day
AI_SET_SIZE = 5                       # questions per AI set
PHOTO_MAX_SIDE = 1568                 # px; photos are shrunk to this before going to the API
