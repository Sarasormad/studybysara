"""Fundamentals: randomized basic-math questions generated in code (no AI, no stored bank).

Each generator returns a Question with the exact answer and a few *likely mistakes*,
which become the wrong options for multiple choice. Difficulty comes from the
student's grade level ("tier" 0-4).
"""
import random
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from grading import format_number

TOPICS = {
    "integers": "Integer arithmetic",
    "order": "Order of operations",
    "fractions": "Fractions",
    "decimals": "Decimals",
    "percents": "Percents",
    "exponents": "Exponents",
    "negatives": "Negative numbers",
}

# Which topics each tier practices.
TIER_TOPICS = {
    0: ["integers"],
    1: ["integers", "order", "fractions"],
    2: ["integers", "order", "fractions", "decimals", "percents", "exponents"],
    3: list(TOPICS),
    4: list(TOPICS),
}


@dataclass
class Question:
    topic: str                      # display name, e.g. "Fractions"
    text: str
    answer: Fraction
    explanation: str
    mistakes: list = field(default_factory=list)
    style: str = "number"           # "number" or "decimal": how answers are written
    hint: str = ""                  # format tip shown for short answers


def tier_for_grade(grade_level):
    """Kindergarten-2 -> 0, 3-4 -> 1, 5-6 -> 2, 7-8 -> 3, 9-12 / college / adult -> 4."""
    if not grade_level or grade_level == "Kindergarten":
        return 0 if grade_level else 4
    if grade_level.startswith("Grade "):
        n = int(grade_level.split()[1])
        return 0 if n <= 2 else 1 if n <= 4 else 2 if n <= 6 else 3 if n <= 8 else 4
    return 4


def fmt(value, style="number"):
    value = Fraction(value)
    if style == "decimal":
        d = Decimal(value.numerator) / Decimal(value.denominator)
        return format(d.normalize(), "f")
    return format_number(value)


def proper_fraction(rng, max_den):
    """A fraction between 0 and 1 that isn't a whole number, e.g. 3/5."""
    den = rng.randint(2, max_den)
    return Fraction(rng.randint(1, den - 1), den)


# ---------- Generators ----------
def gen_integers(rng, tier):
    if tier == 0:
        a, b = rng.randint(2, 20), rng.randint(1, 12)
        if rng.random() < 0.5:
            return Question(TOPICS["integers"], f"What is {a} + {b}?", Fraction(a + b),
                            f"{a} + {b} = {a + b}.", [a + b + 1, a + b - 1, a + b + 10])
        a, b = max(a, b), min(a, b)
        return Question(TOPICS["integers"], f"What is {a} − {b}?", Fraction(a - b),
                        f"{a} − {b} = {a - b}.", [a - b + 1, a - b - 1, a + b])
    if tier == 1:
        a, b = rng.randint(2, 12), rng.randint(2, 12)
        if rng.random() < 0.5:
            return Question(TOPICS["integers"], f"What is {a} × {b}?", Fraction(a * b),
                            f"{a} × {b} = {a * b}.", [a * b + a, a * b - b, a + b])
        return Question(TOPICS["integers"], f"What is {a * b} ÷ {a}?", Fraction(b),
                        f"{a} × {b} = {a * b}, so {a * b} ÷ {a} = {b}.", [b + 1, b - 1, a * b - a])
    big = {2: 99, 3: 199}.get(tier, 999)
    a, b = rng.randint(12, big), rng.randint(3, 12 if tier == 2 else 25)
    kind = rng.choice(["mul", "div", "sub"])
    if kind == "mul":
        return Question(TOPICS["integers"], f"What is {a} × {b}?", Fraction(a * b),
                        f"{a} × {b} = {a * b}.", [a * b + 10, a * b - b, a * (b + 1)])
    if kind == "div":
        return Question(TOPICS["integers"], f"What is {a * b} ÷ {b}?", Fraction(a),
                        f"{b} × {a} = {a * b}, so {a * b} ÷ {b} = {a}.", [a + 1, a - 1, a * 10])
    c = rng.randint(100, 999)
    return Question(TOPICS["integers"], f"What is {c + a} − {a}?", Fraction(c),
                    f"Subtracting {a} undoes adding it: {c + a} − {a} = {c}.", [c + 10, c - 10, c + 1])


def gen_order(rng, tier):
    if tier <= 1:
        a, b, c = rng.randint(1, 10), rng.randint(2, 6), rng.randint(2, 6)
        return Question(TOPICS["order"], f"What is {a} + {b} × {c}?", Fraction(a + b * c),
                        f"Multiply first: {b} × {c} = {b * c}. Then add: {a} + {b * c} = {a + b * c}.",
                        [(a + b) * c, a + b + c, a * b + c])
    if tier == 2:
        a, b, c = rng.randint(2, 6), rng.randint(1, 9), rng.randint(1, 9)
        d = rng.randint(1, a * (b + c) - 1)
        ans = a * (b + c) - d
        return Question(TOPICS["order"], f"What is {a} × ({b} + {c}) − {d}?", Fraction(ans),
                        f"Parentheses first: {b} + {c} = {b + c}. Then {a} × {b + c} = {a * (b + c)}. "
                        f"Then subtract: {a * (b + c)} − {d} = {ans}.",
                        [a * b + c - d, a * (b + c - d), ans + d])
    a, b, c = rng.randint(1, 20), rng.randint(2, 5), rng.randint(2, 5)
    ans = a + b * c ** 2
    return Question(TOPICS["order"], f"What is {a} + {b} × {c}²?", Fraction(ans),
                    f"Exponent first: {c}² = {c * c}. Then multiply: {b} × {c * c} = {b * c * c}. "
                    f"Then add: {a} + {b * c * c} = {ans}.",
                    [(a + b) * c ** 2, a + (b * c) ** 2, a + b * c * 2])


def gen_fractions(rng, tier):
    hint = "Enter a fraction like 3/4, a mixed number like 1 1/2, or a decimal."
    if tier <= 1:
        d = rng.randint(3, 10)
        a, b = rng.randint(1, d - 1), rng.randint(1, d - 1)
        ans = Fraction(a + b, d)
        return Question(TOPICS["fractions"], f"What is {a}/{d} + {b}/{d}?", ans,
                        f"Same denominator, so add the numerators: {a} + {b} = {a + b}, giving {a + b}/{d}"
                        + (f" = {fmt(ans)}." if Fraction(a + b, d).denominator != d else "."),
                        [Fraction(a + b, 2 * d), Fraction(a * b, d), Fraction(a + b + 1, d)], hint=hint)
    x, y = proper_fraction(rng, 6), proper_fraction(rng, 9)
    while y == x:                       # avoid "3/4 ÷ 3/4"
        y = proper_fraction(rng, 9)
    if tier == 2:
        if rng.random() < 0.5 or x <= y:
            ans = x + y
            return Question(TOPICS["fractions"], f"What is {fmt(x)} + {fmt(y)}?", ans,
                            f"Use a common denominator: {fmt(x)} + {fmt(y)} = {fmt(ans)}.",
                            [Fraction(x.numerator + y.numerator, x.denominator + y.denominator),
                             ans + Fraction(1, ans.denominator), x * y], hint=hint)
        ans = x - y
        return Question(TOPICS["fractions"], f"What is {fmt(x)} − {fmt(y)}?", ans,
                        f"Use a common denominator: {fmt(x)} − {fmt(y)} = {fmt(ans)}.",
                        [Fraction(abs(x.numerator - y.numerator), abs(x.denominator - y.denominator) or 1),
                         x + y, ans + Fraction(1, ans.denominator)], hint=hint)
    if tier == 4 and rng.random() < 0.4:
        x = -x
    if rng.random() < 0.5:
        ans = x * y
        return Question(TOPICS["fractions"], f"What is {fmt(x)} × {fmt(y)}?", ans,
                        f"Multiply tops and bottoms: {fmt(x)} × {fmt(y)} = {fmt(ans)}.",
                        [x / y, x + y, -ans], hint=hint)
    ans = x / y
    return Question(TOPICS["fractions"], f"What is {fmt(x)} ÷ {fmt(y)}?", ans,
                    f"Dividing by {fmt(y)} means multiplying by its reciprocal {fmt(1 / y)}: "
                    f"{fmt(x)} × {fmt(1 / y)} = {fmt(ans)}.",
                    [x * y, y / x, -ans], hint=hint)


def gen_decimals(rng, tier):
    hint = "Enter a decimal like 2.5 (a fraction works too)."
    a = Fraction(rng.randint(11, 999), 100 if rng.random() < 0.5 else 10)
    b = Fraction(rng.randint(11, 99), 10)
    kind = rng.choice(["add", "sub"] if tier <= 2 else ["add", "sub", "mul"])
    if kind == "add":
        ans = a + b
        return Question(TOPICS["decimals"], f"What is {fmt(a, 'decimal')} + {fmt(b, 'decimal')}?", ans,
                        f"Line up the decimal points: {fmt(a, 'decimal')} + {fmt(b, 'decimal')} = {fmt(ans, 'decimal')}.",
                        [ans * 10, ans / 10, ans + Fraction(1, 10)], "decimal", hint)
    if kind == "sub":
        a, b = max(a, b), min(a, b)
        ans = a - b
        return Question(TOPICS["decimals"], f"What is {fmt(a, 'decimal')} − {fmt(b, 'decimal')}?", ans,
                        f"Line up the decimal points: {fmt(a, 'decimal')} − {fmt(b, 'decimal')} = {fmt(ans, 'decimal')}.",
                        [ans * 10, ans + Fraction(1, 10), a + b], "decimal", hint)
    c = rng.randint(2, 9)
    ans = b * c
    return Question(TOPICS["decimals"], f"What is {fmt(b, 'decimal')} × {c}?", ans,
                    f"{fmt(b * 10, 'decimal')} × {c} = {fmt(ans * 10, 'decimal')}, then move the decimal "
                    f"point one place left: {fmt(ans, 'decimal')}.",
                    [ans * 10, ans / 10, b + c], "decimal", hint)


def gen_percents(rng, tier):
    if tier <= 2:
        p = rng.choice([10, 20, 25, 50, 75])
        n = rng.choice([k for k in range(20, 401, 4) if p * k % 100 == 0])
    else:
        if rng.random() < 0.35:
            whole = rng.choice([20, 25, 40, 50, 80, 200])
            p = rng.choice([q for q in range(5, 100, 5) if q * whole % 100 == 0])
            part = p * whole // 100
            return Question(TOPICS["percents"], f"{part} is what percent of {whole}?", Fraction(p),
                            f"{part} ÷ {whole} = {fmt(Fraction(part, whole), 'decimal')}, and "
                            f"{fmt(Fraction(part, whole), 'decimal')} × 100 = {p}%.",
                            [Fraction(whole, part) if part else 1, Fraction(part), 100 - p],
                            hint="Enter the percent as a number, like 40 (the % sign is optional).")
        p = rng.choice(range(5, 100, 5))
        n = rng.choice([k for k in range(20, 1001, 20) if p * k % 100 == 0])
    ans = Fraction(p * n, 100)
    return Question(TOPICS["percents"], f"What is {p}% of {n}?", ans,
                    f"{p}% means {p}/100, so {p}/100 × {n} = {fmt(ans)}.",
                    [n - ans, ans * 10, Fraction(n, p) if p else 1])


def gen_exponents(rng, tier):
    if tier <= 2:
        a = rng.randint(2, 12)
        return Question(TOPICS["exponents"], f"What is {a}²?", Fraction(a * a),
                        f"{a}² means {a} × {a} = {a * a}.", [a * 2, a * a + a, (a + 1) ** 2])
    kind = rng.choice(["cube", "pow2", "neg", "product"] if tier == 4 else ["cube", "pow2"])
    if kind == "cube":
        a = rng.randint(2, 6)
        return Question(TOPICS["exponents"], f"What is {a}³?", Fraction(a ** 3),
                        f"{a}³ = {a} × {a} × {a} = {a ** 3}.", [a * 3, a ** 2, (a + 1) ** 3])
    if kind == "pow2":
        n = rng.randint(3, 10)
        return Question(TOPICS["exponents"], f"What is 2^{n}?", Fraction(2 ** n),
                        f"Multiply 2 by itself {n} times: 2^{n} = {2 ** n}.", [2 * n, 2 ** (n - 1), n ** 2])
    if kind == "neg":
        a = rng.randint(2, 9)
        if rng.random() < 0.5:
            return Question(TOPICS["exponents"], f"What is (−{a})²?", Fraction(a * a),
                            f"The parentheses square the negative too: (−{a}) × (−{a}) = {a * a}.",
                            [-a * a, -2 * a, 2 * a])
        return Question(TOPICS["exponents"], f"What is −{a}²?", Fraction(-a * a),
                        f"Without parentheses, square first and then apply the minus: −({a}²) = −{a * a}.",
                        [a * a, -2 * a, 2 * a])
    m, n = rng.randint(2, 5), rng.randint(2, 4)
    return Question(TOPICS["exponents"], f"What is 2^{m} × 2^{n}?", Fraction(2 ** (m + n)),
                    f"Same base, so add the exponents: 2^{m + n} = {2 ** (m + n)}.",
                    [2 ** (m * n), 4 ** (m + n), 2 ** m + 2 ** n])


def gen_negatives(rng, tier):
    top = 20 if tier == 3 else 60
    a, b = rng.randint(2, top), rng.randint(2, top)
    kind = rng.choice(["add", "sub", "mul", "div"])
    if kind == "add":
        ans = -a + b
        return Question(TOPICS["negatives"], f"What is −{a} + {b}?", Fraction(ans),
                        f"Start at −{a} and move {b} to the right: {ans}.", [-ans, -a - b, a + b])
    if kind == "sub":
        ans = -a + b
        return Question(TOPICS["negatives"], f"What is −{a} − (−{b})?", Fraction(ans),
                        f"Subtracting a negative is adding: −{a} + {b} = {ans}.", [-a - b, -ans, a + b])
    a, b = rng.randint(2, 12), rng.randint(2, 12)
    if kind == "mul":
        return Question(TOPICS["negatives"], f"What is −{a} × {b}?", Fraction(-a * b),
                        f"Negative × positive is negative: −{a * b}.", [a * b, -a - b, a - b])
    return Question(TOPICS["negatives"], f"What is −{a * b} ÷ (−{a})?", Fraction(b),
                    f"Negative ÷ negative is positive: {a * b} ÷ {a} = {b}.", [-b, -a * b + a, b + 1])


GENERATORS = {
    "integers": gen_integers, "order": gen_order, "fractions": gen_fractions,
    "decimals": gen_decimals, "percents": gen_percents, "exponents": gen_exponents,
    "negatives": gen_negatives,
}


# ---------- Building a round ----------
def choices_for(q, rng):
    """The correct answer plus 3 distinct wrong answers (distinct by value), shuffled."""
    seen = {q.answer}
    wrong = []
    candidates = [Fraction(m) for m in q.mistakes]
    step = Fraction(1, 10) if q.style == "decimal" else Fraction(1, q.answer.denominator)
    offsets = [step, -step, 2 * step, -2 * step, 3 * step, 10 * step]
    candidates += [q.answer + o for o in offsets]
    for c in candidates:
        if c not in seen and (q.answer < 0 or c >= 0 or q.topic in (TOPICS["negatives"], TOPICS["exponents"])):
            seen.add(c)
            wrong.append(c)
        if len(wrong) == 3:
            break
    options = [fmt(v, q.style) for v in [q.answer, *wrong]]
    rng.shuffle(options)
    return options


def make_round(grade_level, size=10, rng=None):
    """A list of `size` problem dicts, half multiple choice and half short answer."""
    rng = rng or random.Random()
    tier = tier_for_grade(grade_level)
    topics = TIER_TOPICS[tier][:]
    order = []
    while len(order) < size:            # spread topics evenly, in random order
        rng.shuffle(topics)
        order += topics
    order = order[:size]
    mc_positions = set(rng.sample(range(size), size // 2))
    problems = []
    for i, key in enumerate(order):
        q = GENERATORS[key](rng, tier)
        is_mc = i in mc_positions
        problems.append({
            "topic": q.topic,
            "question_text": q.text,
            "answer_type": "multiple_choice" if is_mc else "short_answer",
            "choices": choices_for(q, rng) if is_mc else None,
            "correct_answer": fmt(q.answer, q.style),
            "explanation": q.explanation,
            "hint": None if is_mc else (q.hint or "Enter a number."),
        })
    return problems
