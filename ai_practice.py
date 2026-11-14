"""AI-generated practice questions (Claude API).

The flow for every AI request:
  1. Ask Claude for JSON that matches QUESTIONS_SCHEMA (structured outputs make the
     API return valid JSON in that shape).
  2. Validate it ourselves anyway (clean_questions): exactly the fields we need, answer
     keys our grader can check, multiple-choice options that aren't secretly equal.
  3. If anything is off, try once more; then give the student a friendly error.

The prompts themselves live in prompts/*.txt so they can be edited without touching code.

The API key is read by the SDK from the ANTHROPIC_API_KEY environment variable on the
server. It is never sent to the browser.
"""
import base64
import io
import json
import logging
import os

import anthropic
from PIL import Image, ImageOps, UnidentifiedImageError

from config import AI_EFFORT, AI_MODEL, AI_SET_SIZE, PHOTO_MAX_SIDE
from grading import answers_match, parse_number

log = logging.getLogger(__name__)

try:                                    # HEIC/HEIF (iPhone photos) needs the pillow-heif plugin
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

PHOTO_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "MPO"}   # MPO = some phone cameras' JPEGs

FALLBACK_BETA = "server-side-fallback-2026-07-01"   # retry a declined request on another model


class AIError(Exception):
    """Something went wrong; str(error) is a friendly message safe to show a student."""


class PhotoRejected(AIError):
    """The model looked at the photo and found no readable math problem (this still used an AI set)."""


class InvalidQuestions(ValueError):
    """The model's JSON didn't pass our checks (we retry once)."""


# ---------- Topic catalog ----------
# (key, display name, lowest grade, highest grade). Grades: K = 0, Grade 1-12, college 13, adult 14.
MATH_TOPICS = [
    ("counting", "Counting & addition", 0, 2),
    ("place_value", "Place value & rounding", 1, 5),
    ("multiplication", "Multiplication & division", 2, 6),
    ("fractions", "Fractions", 3, 8),
    ("decimals_percents", "Decimals & percents", 4, 9),
    ("geometry", "Geometry", 4, 12),
    ("ratios", "Ratios & proportions", 6, 9),
    ("expressions", "Expressions & equations", 6, 9),
    ("probability_stats", "Probability & statistics", 6, 14),
    ("linear_equations", "Linear equations", 7, 12),
    ("systems", "Systems of equations", 8, 12),
    ("exponents_radicals", "Exponents & radicals", 8, 12),
    ("functions", "Functions", 8, 14),
    ("quadratics", "Quadratics", 9, 14),
    ("polynomials", "Polynomials", 9, 14),
    ("trigonometry", "Trigonometry", 10, 14),
    ("logarithms", "Exponentials & logarithms", 10, 14),
]
TEST_PREP_TOPICS = [
    ("sat_algebra", "SAT: Heart of algebra"),
    ("sat_data", "SAT: Problem solving & data analysis"),
    ("sat_advanced", "SAT: Advanced math"),
    ("sat_geo_trig", "SAT: Geometry & trigonometry"),
    ("act_math", "ACT: Math mixed practice"),
]
TOPICS_BY_KEY = {k: {"key": k, "name": n, "group": "math", "lo": lo, "hi": hi} for k, n, lo, hi in MATH_TOPICS}
TOPICS_BY_KEY.update({k: {"key": k, "name": n, "group": "test_prep", "lo": 9, "hi": 14} for k, n in TEST_PREP_TOPICS})
TOPIC_KEY_BY_NAME = {t["name"]: k for k, t in TOPICS_BY_KEY.items()}


def grade_number(grade_level):
    if not grade_level:
        return 8
    if grade_level == "Kindergarten":
        return 0
    if grade_level.startswith("Grade "):
        return int(grade_level.split()[1])
    return 13 if grade_level.startswith("College") else 14


def topics_for(grade_level):
    """(fits, others, test_prep): math topics for the student's grade, the rest, and SAT/ACT."""
    g = grade_number(grade_level)
    fits = [TOPICS_BY_KEY[k] for k, *_ in MATH_TOPICS if TOPICS_BY_KEY[k]["lo"] <= g <= TOPICS_BY_KEY[k]["hi"]]
    others = [TOPICS_BY_KEY[k] for k, *_ in MATH_TOPICS if TOPICS_BY_KEY[k] not in fits]
    return fits, others, [TOPICS_BY_KEY[k] for k, _ in TEST_PREP_TOPICS]


# ---------- What we ask for ----------
QUESTION_ITEM = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "answer_type": {"type": "string", "enum": ["multiple_choice", "short_answer"]},
        "choices": {"type": "array", "items": {"type": "string"}},
        "correct_answer": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["question", "answer_type", "choices", "correct_answer", "explanation"],
    "additionalProperties": False,
}
QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": QUESTION_ITEM}},
    "required": ["questions"],
    "additionalProperties": False,
}

# ---------- Prompts (prompts/*.txt) ----------
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
PROMPT_FILES = {                      # file -> placeholders the app fills in
    "system": ("set_size",),
    "topic": ("topic", "grade"),
    "test_prep": ("topic", "grade"),
    "photo": ("set_size",),
}


def load_prompt(name, **values):
    """Read prompts/<name>.txt and fill in its {placeholders}.

    The file is read on every request, so edits apply to the next question set without
    restarting. Only the listed placeholders are replaced; other braces are left alone.
    """
    path = os.path.join(PROMPT_DIR, f"{name}.txt")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        log.error("Prompt file missing or unreadable: %s", path)
        raise AIError("The question generator had a problem. Please try again.")
    for key in PROMPT_FILES[name]:
        text = text.replace("{" + key + "}", str(values[key]))
    return text


def topic_prompt(topic, grade_level):
    """The per-request message for topic practice (math topic or SAT/ACT)."""
    name = "test_prep" if topic["group"] == "test_prep" else "topic"
    return load_prompt(name, topic=topic["name"], grade=grade_level or "a middle school student")


# ---------- Validation ----------
def _check_text(text, field, limit):
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text or len(text) > limit:
        raise InvalidQuestions(f"bad {field}")
    if "\\(" in text or "\\[" in text or "\\frac" in text:
        raise InvalidQuestions(f"LaTeX in {field}")
    return text


def clean_questions(data, topic_name, size=AI_SET_SIZE):
    """Turn the model's JSON into problem dicts for practice.create_set, or raise InvalidQuestions."""
    items = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) < size:
        raise InvalidQuestions("wrong number of questions")
    problems = []
    for item in items[:size]:
        if not isinstance(item, dict):
            raise InvalidQuestions("question is not an object")
        question = _check_text(item.get("question"), "question", 600)
        correct = _check_text(item.get("correct_answer"), "correct_answer", 120)
        explanation = _check_text(item.get("explanation"), "explanation", 1200)
        kind = item.get("answer_type")
        if kind == "multiple_choice":
            choices = [_check_text(c, "choice", 120) for c in item.get("choices") or []]
            if len(choices) != 4 or correct not in choices:
                raise InvalidQuestions("choices must be 4 and include the answer")
            # The grader compares by value, so "0.5" and "1/2" as two options would both be "right".
            for i in range(4):
                for j in range(i + 1, 4):
                    if answers_match(choices[i], choices[j]):
                        raise InvalidQuestions("two choices are equivalent")
            hint = None
        elif kind == "short_answer":
            choices = None
            is_number = parse_number(correct) is not None
            if len(correct) > 30 or "=" in correct or (not is_number and len(correct.split()) > 3):
                raise InvalidQuestions("short answer isn't a bare value")
            hint = ("Enter a number, like 12, -3, 3/4 or 0.75." if is_number
                    else "Type a short word or phrase.")
        else:
            raise InvalidQuestions("unknown answer_type")
        problems.append({"topic": topic_name, "question_text": question, "answer_type": kind,
                         "choices": choices, "correct_answer": correct, "explanation": explanation,
                         "hint": hint})
    return problems


# ---------- Calling the API ----------
_client = None


def get_client():
    """One shared client. Tests replace this function with a fake."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    return _client


def call_model(user_content, schema):
    """One API call. Returns the parsed JSON dict, or raises InvalidQuestions / AIError."""
    request = {
        "model": AI_MODEL,
        "max_tokens": 16000,
        "system": load_prompt("system", set_size=AI_SET_SIZE),
        "messages": [{"role": "user", "content": user_content}],
        "output_config": {"effort": AI_EFFORT, "format": {"type": "json_schema", "schema": schema}},
    }
    if "haiku" not in AI_MODEL:                     # Haiku has no server-side fallback
        request.update(betas=[FALLBACK_BETA], fallbacks="default")
    try:
        response = get_client().beta.messages.create(**request)
    except anthropic.AuthenticationError:
        log.error("Claude API: missing or invalid ANTHROPIC_API_KEY")
        raise AIError("AI practice isn't set up yet. Ask the site owner to add an API key.")
    except anthropic.RateLimitError:
        raise AIError("Lots of students are practicing right now. Try again in a minute.")
    except anthropic.APIStatusError as e:
        log.error("Claude API error %s: %s (request %s)", e.status_code, e.message, getattr(e, "request_id", None))
        raise AIError("The question generator had a problem. Please try again.")
    except anthropic.APIConnectionError:
        raise AIError("Couldn't reach the question generator. Check your connection and try again.")
    except anthropic.AnthropicError as e:
        log.error("Claude API client error: %s", e)
        raise AIError("The question generator had a problem. Please try again.")
    except TypeError as e:
        # With no API key at all, the SDK raises a plain TypeError ("Could not resolve
        # authentication method") before sending anything. Anything else is a real bug.
        if "authentication" not in str(e):
            raise
        log.error("Claude API: no API key. Set ANTHROPIC_API_KEY in .env or the terminal.")
        raise AIError("AI practice isn't set up yet. Ask the site owner to add an API key.")

    if response.stop_reason == "refusal":
        raise AIError("We couldn't make questions for that. Try a different topic or photo.")
    if response.stop_reason == "max_tokens":
        raise InvalidQuestions("response was cut off")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise InvalidQuestions("not valid JSON") from e


def with_one_retry(attempt):
    """Run attempt(); if the model's output fails validation, run it once more."""
    for tries in (1, 2):
        try:
            return attempt()
        except InvalidQuestions as e:
            log.warning("AI output rejected (try %s): %s", tries, e)
    raise AIError("The question generator gave us something we couldn't use. Please try again.")


def generate_topic_questions(topic_key, grade_level):
    """5 validated questions on a catalog topic, or AIError."""
    topic = TOPICS_BY_KEY.get(topic_key)
    if topic is None:
        raise AIError("Pick a topic from the list.")
    prompt = topic_prompt(topic, grade_level)
    return with_one_retry(lambda: clean_questions(call_model(prompt, QUESTIONS_SCHEMA), topic["name"]))


# ---------- Photo practice ----------
def prepare_photo(file_storage):
    """Read an uploaded photo in memory, fix its rotation, shrink it and re-encode it as JPEG.

    Returns base64 text for the API. Nothing is written to disk, and the original upload is
    dropped when the request ends, so the photo is never kept.
    """
    try:
        with Image.open(file_storage.stream) as img:
            if img.format not in PHOTO_FORMATS:
                raise AIError("Upload a JPG, PNG or HEIC photo.")
            img = ImageOps.exif_transpose(img)            # phones store rotation separately
            img = img.convert("RGB")                      # drops transparency and EXIF metadata
            img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise AIError("That file isn't a photo we can read. Try a JPG, PNG or HEIC photo.")
    finally:
        file_storage.stream.close()
    return base64.standard_b64encode(out.getvalue()).decode("ascii")


PHOTO_TOPICS = [name for _, name, *_ in MATH_TOPICS] + ["Other math"]
PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "unreadable", "not_math"]},
        "topic": {"type": "string", "enum": PHOTO_TOPICS},
        "concept": {"type": "string"},
        "questions": {"type": "array", "items": QUESTION_ITEM},
    },
    "required": ["status", "topic", "concept", "questions"],
    "additionalProperties": False,
}
PHOTO_MESSAGES = {
    "unreadable": "We couldn't read a math problem in that photo. Try again with the problem in focus, "
                  "good light, and the whole problem in the frame.",
    "not_math": "That photo doesn't look like a math problem. Snap a math question from your homework or textbook.",
}


def clean_photo_result(data):
    """Validate the model's answer for a photo: (topic, concept, problems) or an AIError for the student."""
    if not isinstance(data, dict) or data.get("status") not in ("ok", "unreadable", "not_math"):
        raise InvalidQuestions("bad status")
    if data["status"] != "ok":
        raise PhotoRejected(PHOTO_MESSAGES[data["status"]])
    topic = data.get("topic") if data.get("topic") in PHOTO_TOPICS else "Other math"
    concept = (data.get("concept") or "").strip()[:80] or topic
    return topic, concept, clean_questions(data, topic)


def generate_photo_questions(photo_b64):
    """(topic, concept, 5 problems) similar to the problem in the photo, or AIError."""
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}},
        {"type": "text", "text": load_prompt("photo", set_size=AI_SET_SIZE)},
    ]
    return with_one_retry(lambda: clean_photo_result(call_model(content, PHOTO_SCHEMA)))
