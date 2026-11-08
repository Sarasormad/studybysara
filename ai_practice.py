"""AI-generated practice questions (Claude API).

The flow for every AI request:
  1. Ask Claude for JSON that matches QUESTIONS_SCHEMA (structured outputs make the
     API return valid JSON in that shape).
  2. Validate it ourselves anyway (clean_questions): exactly the fields we need, answer
     keys our grader can check, multiple-choice options that aren't secretly equal.
  3. If anything is off, try once more; then give the student a friendly error.

The API key is read by the SDK from the ANTHROPIC_API_KEY environment variable on the
server. It is never sent to the browser.
"""
import json
import logging

import anthropic

from config import AI_EFFORT, AI_MODEL, AI_SET_SIZE
from grading import answers_match, parse_number

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"   # retry a declined request on another model


class AIError(Exception):
    """Something went wrong; str(error) is a friendly message safe to show a student."""


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

SYSTEM_PROMPT = f"""You write math practice questions for a tutoring app used by students, many of them minors.

Write exactly {AI_SET_SIZE} questions. Mix the formats: at least 2 multiple_choice and at least 2 short_answer.

Every answer is graded automatically, so the answer key must be exactly right and unambiguous:
- Work out each answer yourself and double-check it before writing it down.
- multiple_choice: exactly 4 choices with clearly different values, and correct_answer copied character-for-character from one of the choices. Wrong choices should be realistic mistakes.
- short_answer: correct_answer is a bare value: one number (like 12, -3, 3/4, 0.75 or 2.5) or one short word. No units, no variable names, no "x =", no words like "about". Leave choices as an empty list.
- If an answer isn't a whole number, prefer a fraction in lowest terms (like 7/3) or a terminating decimal, and say in the question which form to use only if it matters.

Write math in plain text that reads well in a browser: use ^ for powers (x^2), / for fractions, sqrt() or √ for roots, π for pi, and × or * for multiplication. Never use LaTeX or dollar-sign math.

Questions must not depend on a picture or graph the student can't see. Keep wording short and friendly. The explanation is 1 to 3 sentences showing the key steps."""


def topic_prompt(topic, grade_level):
    who = grade_level or "a middle school student"
    if topic["group"] == "test_prep":
        return (f"Topic: {topic['name']}. Write questions in the style and difficulty of the real test, "
                f"for a student currently in {who}.")
    return f"Topic: {topic['name']}. The student is in {who}; match that level."


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
        "system": SYSTEM_PROMPT,
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
    except anthropic.AnthropicError as e:            # e.g. no credentials configured at all
        log.error("Claude API client error: %s", e)
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
