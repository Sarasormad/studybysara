"""Collectible figurines: the catalog, when each one can be bought, and the SVG art.

Every figurine is drawn in code from a few traits (body shape, colors, eyes, mouth,
accessories), so there are no image files. To add one, add an entry to CATALOG; the
tests check every entry draws correctly.

Rules (see shop.py for buying):
- Figurines cost points; points are the only way to get them (no money, no random packs).
- A student can own one copy of each figurine. Every copy gets a serial number.
- Limited editions can only be bought between their start and end dates (Pacific), and
  only `stock` copies will ever exist.
"""
import itertools
import math
from datetime import date

from markupsafe import Markup, escape

RARITIES = {
    "common":    {"label": "Common",          "order": 1, "pedestal": ("#e2e8f0", "#94a3b8")},
    "rare":      {"label": "Rare",            "order": 2, "pedestal": ("#bfdbfe", "#3b82f6")},
    "epic":      {"label": "Epic",            "order": 3, "pedestal": ("#ddd6fe", "#7c3aed")},
    "legendary": {"label": "Legendary",       "order": 4, "pedestal": ("#fef08a", "#d97706")},
    "limited":   {"label": "Limited Edition", "order": 5, "pedestal": None},   # rainbow
}

# ---------- The catalog ----------
# shape: circle | squircle | triangle | square | pentagon | hexagon | octagon | star
# eyes:  dots | happy | wide | sleepy | star | visor        mouth: smile | grin | o | cat
# accessories (any of): crown, wizard_hat, grad_cap, party_hat, beanie, pumpkin_hat, halo,
#   horns, antenna, glasses, headphones, bowtie, scarf, cape
CATALOG = [
    # Common
    {"id": "dot", "name": "Dot", "rarity": "common", "price": 150,
     "shape": "circle", "color": "#38bdf8", "accent": "#0369a1", "eyes": "dots", "mouth": "smile",
     "accessories": [], "bio": "The very first point on every graph. Small, but everything starts here."},
    {"id": "tri", "name": "Tri", "rarity": "common", "price": 150,
     "shape": "triangle", "color": "#fb923c", "accent": "#db2777", "eyes": "happy", "mouth": "grin",
     "accessories": ["party_hat"], "bio": "Always has three sides to every story, and a party for each one."},
    {"id": "cubby", "name": "Cubby", "rarity": "common", "price": 150,
     "shape": "square", "color": "#4ade80", "accent": "#dc2626", "eyes": "wide", "mouth": "smile",
     "accessories": ["bowtie"], "bio": "Four equal sides, four right angles, one very fancy bow tie."},
    {"id": "penta", "name": "Penta Pal", "rarity": "common", "price": 150,
     "shape": "pentagon", "color": "#f472b6", "accent": "#1e293b", "eyes": "dots", "mouth": "cat",
     "accessories": ["glasses"], "bio": "Counts everything in fives. Has read every book in the library twice."},
    {"id": "hexley", "name": "Hexley", "rarity": "common", "price": 150,
     "shape": "hexagon", "color": "#facc15", "accent": "#78350f", "eyes": "happy", "mouth": "smile",
     "accessories": ["antenna"], "bio": "Busy as a bee, tidy as a honeycomb. Picks up signals from other shapes."},
    {"id": "zero", "name": "Zero", "rarity": "common", "price": 150,
     "shape": "circle", "color": "#e2e8f0", "accent": "#64748b", "eyes": "wide", "mouth": "o",
     "accessories": [], "bio": "Nothing to see here... except it turns 1 into 10 just by standing next to it."},
    # Rare
    {"id": "professor-octo", "name": "Professor Octo", "rarity": "rare", "price": 400,
     "shape": "octagon", "color": "#2dd4bf", "accent": "#1e293b", "eyes": "dots", "mouth": "smile",
     "accessories": ["grad_cap", "glasses"], "bio": "Eight sides, eight degrees, and a lecture ready for any question."},
    {"id": "sir-squareroot", "name": "Sir Squareroot", "rarity": "rare", "price": 400,
     "shape": "square", "color": "#818cf8", "accent": "#facc15", "eyes": "wide", "mouth": "smile",
     "accessories": ["crown", "bowtie"], "bio": "Rules the kingdom of 4, 9, 16 and 25. Very polite about it."},
    {"id": "dj-decimal", "name": "DJ Decimal", "rarity": "rare", "price": 400,
     "shape": "circle", "color": "#e879f9", "accent": "#22d3ee", "eyes": "visor", "mouth": "grin",
     "accessories": ["headphones"], "bio": "Drops the beat exactly 0.5 seconds after you expect it."},
    {"id": "captain-hexagon", "name": "Captain Hexagon", "rarity": "rare", "price": 400,
     "shape": "hexagon", "color": "#f87171", "accent": "#1d4ed8", "eyes": "wide", "mouth": "grin",
     "accessories": ["cape"], "bio": "Six sides of pure courage. Saves students from word problems."},
    {"id": "fraction-phantom", "name": "Fraction Phantom", "rarity": "rare", "price": 400,
     "shape": "squircle", "color": "#c4b5fd", "accent": "#7c3aed", "eyes": "sleepy", "mouth": "o",
     "accessories": [], "bio": "Only half here. Or maybe two quarters. Nobody has ever been sure."},
    # Epic
    {"id": "wizard-ratio", "name": "Wizard Ratio", "rarity": "epic", "price": 900,
     "shape": "triangle", "color": "#a855f7", "accent": "#fde047", "eyes": "star", "mouth": "smile",
     "accessories": ["wizard_hat"], "bio": "Can scale any recipe with a flick of the wand. 3:2 is a favorite spell."},
    {"id": "astro-axis", "name": "Astro Axis", "rarity": "epic", "price": 900,
     "shape": "circle", "color": "#1e40af", "accent": "#f472b6", "eyes": "visor", "mouth": "smile",
     "accessories": ["antenna", "scarf"], "bio": "Has plotted a point on every planet. Still looking for the origin."},
    {"id": "inferno-integer", "name": "Inferno Integer", "rarity": "epic", "price": 900,
     "shape": "pentagon", "color": "#dc2626", "accent": "#fbbf24", "eyes": "wide", "mouth": "grin",
     "accessories": ["horns"], "bio": "Born below zero, burns hotter than any positive number."},
    # Legendary
    {"id": "queen-quadratica", "name": "Queen Quadratica", "rarity": "legendary", "price": 2000,
     "shape": "star", "color": "#fbbf24", "accent": "#be123c", "eyes": "happy", "mouth": "smile",
     "accessories": ["crown", "cape"], "bio": "Her parabolas reach the stars. Rules with two roots and a big heart."},
    {"id": "the-infinite", "name": "The Infinite", "rarity": "legendary", "price": 2000,
     "shape": "star", "color": "#6366f1", "accent": "#5eead4", "eyes": "star", "mouth": "smile",
     "accessories": ["halo"], "bio": "No beginning, no end. Has counted to infinity. Twice."},
    # Limited editions (dates are Pacific; "end" is the first day it's no longer sold)
    {"id": "pumpkin-pi", "name": "Pumpkin Pi", "rarity": "limited", "price": 1200,
     "shape": "circle", "color": "#f97316", "accent": "#15803d", "eyes": "happy", "mouth": "grin",
     "accessories": ["pumpkin_hat"], "bio": "3.14159 slices of autumn. Only appears in November.",
     "limited": {"start": "2026-11-01", "end": "2026-12-01", "stock": 100}},
    {"id": "galaxy-graph", "name": "Galaxy Graph", "rarity": "limited", "price": 3000,
     "shape": "star", "color": "#312e81", "accent": "#f0abfc", "eyes": "star", "mouth": "o",
     "accessories": ["halo", "cape"], "bio": "Plotted from real starlight. Only 25 will ever exist.",
     "limited": {"start": "2026-11-10", "end": "2026-11-25", "stock": 25}},
    {"id": "frosty-fraction", "name": "Frosty Fraction", "rarity": "limited", "price": 1500,
     "shape": "hexagon", "color": "#93c5fd", "accent": "#e11d48", "eyes": "happy", "mouth": "smile",
     "accessories": ["beanie", "scarf"], "bio": "Splits every snowflake into perfect sixths. Arrives with the first snow.",
     "limited": {"start": "2026-12-01", "end": "2027-01-01", "stock": 75}},
]
BY_ID = {f["id"]: f for f in CATALOG}


# ---------- Availability ----------
def limited_window(fig):
    lim = fig.get("limited")
    if not lim:
        return None
    return date.fromisoformat(lim["start"]), date.fromisoformat(lim["end"])


def availability(fig, today, sold):
    """'available', 'upcoming', 'retired' or 'sold_out' for a figurine on `today`."""
    window = limited_window(fig)
    if window is None:
        return "available"
    start, end = window
    if today < start:
        return "upcoming"
    if today >= end:
        return "retired"
    if sold >= fig["limited"]["stock"]:
        return "sold_out"
    return "available"


def days_left(fig, today):
    window = limited_window(fig)
    return (window[1] - today).days if window else None


# ---------- Drawing ----------
_ids = itertools.count(1)
INK = "#1f2937"


def _mix(hex_color, other, amount):
    """Blend two #rrggbb colors; amount 0 = first color, 1 = second."""
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def lighter(c, amt=0.45):
    return _mix(c, "#ffffff", amt)


def darker(c, amt=0.35):
    return _mix(c, "#000000", amt)


def _polygon(n, cx, cy, r, start_deg=-90, inner=None):
    pts = []
    steps = n * 2 if inner else n
    for i in range(steps):
        radius = (inner if inner and i % 2 else r)
        ang = math.radians(start_deg + 360 * i / steps)
        pts.append(f"{cx + radius * math.cos(ang):.1f},{cy + radius * math.sin(ang):.1f}")
    return "M" + " L".join(pts) + " Z"


BODY_CENTER = {"triangle": (100, 132), "star": (100, 126)}
FACE_Y = {"triangle": 142, "star": 128}


def body_path(shape):
    cx, cy = BODY_CENTER.get(shape, (100, 124))
    if shape == "circle":
        return f"M{cx - 62},{cy} a62,62 0 1,0 124,0 a62,62 0 1,0 -124,0 Z"
    if shape == "squircle":
        return "M70,62 H130 Q162,62 162,94 V156 Q162,188 130,188 H70 Q38,188 38,156 V94 Q38,62 70,62 Z"
    if shape == "triangle":
        return _polygon(3, cx, cy, 76)
    if shape == "square":
        return _polygon(4, cx, cy, 82, start_deg=-45)
    if shape == "pentagon":
        return _polygon(5, cx, cy, 68)
    if shape == "hexagon":
        return _polygon(6, cx, cy, 66)
    if shape == "octagon":
        return _polygon(8, cx, cy, 66, start_deg=-67.5)
    if shape == "star":
        return _polygon(5, cx, cy, 80, inner=46)
    raise ValueError(f"unknown shape {shape}")


def _eyes(kind, fy, accent):
    out = []
    for x in (80, 120):
        if kind == "dots":
            out.append(f'<circle cx="{x}" cy="{fy}" r="7" fill="{INK}"/><circle cx="{x - 2}" cy="{fy - 2.5}" r="2.4" fill="#fff"/>')
        elif kind == "happy":
            out.append(f'<path d="M{x - 9},{fy + 3} q9,-12 18,0" stroke="{INK}" stroke-width="5" fill="none" stroke-linecap="round"/>')
        elif kind == "wide":
            out.append(f'<circle cx="{x}" cy="{fy}" r="12" fill="#fff" stroke="{INK}" stroke-width="2.5"/>'
                       f'<circle cx="{x + 1.5}" cy="{fy + 2}" r="6" fill="{INK}"/><circle cx="{x - 0.5}" cy="{fy - 0.5}" r="2" fill="#fff"/>')
        elif kind == "sleepy":
            out.append(f'<path d="M{x - 9},{fy} q9,8 18,0" stroke="{INK}" stroke-width="4.5" fill="none" stroke-linecap="round"/>')
        elif kind == "star":
            out.append(f'<path d="{_polygon(5, x, fy, 10, inner=4.5)}" fill="#fde047" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>')
    if kind == "visor":
        return (f'<rect x="62" y="{fy - 11}" width="76" height="22" rx="11" fill="{accent}" stroke="{INK}" stroke-width="2.5"/>'
                f'<path d="M72,{fy - 4} H100" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity=".8"/>')
    return "".join(out)


def _mouth(kind, my):
    if kind == "smile":
        return f'<path d="M88,{my} q12,11 24,0" stroke="{INK}" stroke-width="4.5" fill="none" stroke-linecap="round"/>'
    if kind == "grin":
        return (f'<path d="M86,{my - 2} q14,22 28,0 Z" fill="{INK}" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>'
                f'<ellipse cx="100" cy="{my + 6}" rx="6" ry="3.5" fill="#fb7185"/>')
    if kind == "o":
        return f'<ellipse cx="100" cy="{my + 2}" rx="6" ry="7.5" fill="{INK}"/>'
    if kind == "cat":
        return f'<path d="M88,{my} q6,7 12,0 q6,7 12,0" stroke="{INK}" stroke-width="4" fill="none" stroke-linecap="round"/>'
    return ""


def _accessory(name, fy, color, accent):
    a, ad = accent, darker(accent, 0.3)
    gold, gold_d = "#facc15", "#b45309"
    parts = {
        "crown": f'<path d="M70,64 L72,34 L86,49 L100,28 L114,49 L128,34 L130,64 Z" fill="{gold}" stroke="{gold_d}" stroke-width="3" stroke-linejoin="round"/>'
                 f'<circle cx="100" cy="52" r="5" fill="#ef4444"/><circle cx="82" cy="57" r="3.5" fill="#3b82f6"/><circle cx="118" cy="57" r="3.5" fill="#22c55e"/>',
        "wizard_hat": f'<path d="M60,70 L108,2 L142,68 Z" fill="{darker(color, 0.25)}" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
                      f'<ellipse cx="101" cy="68" rx="50" ry="9" fill="{darker(color, 0.4)}" stroke="{INK}" stroke-width="3"/>'
                      f'<path d="{_polygon(5, 104, 36, 8, inner=3.5)}" fill="{a}"/><path d="{_polygon(5, 88, 55, 5, inner=2.2)}" fill="{a}"/>',
        "grad_cap": f'<rect x="76" y="50" width="48" height="18" rx="4" fill="{INK}"/>'
                    f'<path d="M56,48 L100,32 L144,48 L100,64 Z" fill="{INK}" stroke="#334155" stroke-width="2" stroke-linejoin="round"/>'
                    f'<path d="M100,48 L134,54 V74" stroke="{gold}" stroke-width="3" fill="none"/><circle cx="134" cy="77" r="4" fill="{gold}"/>',
        "party_hat": f'<path d="M80,62 L100,14 L120,62 Z" fill="{a}" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
                     f'<path d="M86,48 L114,48 M92,33 L108,33" stroke="#fff" stroke-width="4"/><circle cx="100" cy="13" r="7" fill="{gold}" stroke="{INK}" stroke-width="2"/>',
        "beanie": f'<path d="M58,72 Q58,30 100,30 Q142,30 142,72 Z" fill="{a}" stroke="{INK}" stroke-width="3"/>'
                  f'<rect x="54" y="64" width="92" height="14" rx="7" fill="{ad}" stroke="{INK}" stroke-width="3"/>'
                  f'<circle cx="100" cy="27" r="9" fill="#fff" stroke="{INK}" stroke-width="2.5"/>',
        "pumpkin_hat": f'<ellipse cx="100" cy="50" rx="26" ry="18" fill="#ea580c" stroke="{INK}" stroke-width="3"/>'
                       f'<path d="M92,34 Q86,50 92,66 M108,34 Q114,50 108,66" stroke="#9a3412" stroke-width="2.5" fill="none"/>'
                       f'<path d="M100,33 Q99,22 106,18" stroke="{a}" stroke-width="5" fill="none" stroke-linecap="round"/>',
        "halo": f'<ellipse cx="100" cy="36" rx="36" ry="9" fill="none" stroke="{gold}" stroke-width="7" opacity=".35"/>'
                f'<ellipse cx="100" cy="36" rx="34" ry="8" fill="none" stroke="{gold}" stroke-width="4"/>',
        "horns": f'<path d="M66,72 Q56,44 74,36 Q72,56 82,66 Z" fill="#fef3c7" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
                 f'<path d="M134,72 Q144,44 126,36 Q128,56 118,66 Z" fill="#fef3c7" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>',
        "antenna": f'<path d="M100,62 V30" stroke="{INK}" stroke-width="4"/><circle cx="100" cy="26" r="8" fill="{a}" stroke="{INK}" stroke-width="3"/>',
        "glasses": f'<circle cx="80" cy="{fy}" r="16" fill="#fff" fill-opacity=".25" stroke="{INK}" stroke-width="4"/>'
                   f'<circle cx="120" cy="{fy}" r="16" fill="#fff" fill-opacity=".25" stroke="{INK}" stroke-width="4"/>'
                   f'<path d="M96,{fy} H104" stroke="{INK}" stroke-width="4"/>',
        "headphones": f'<path d="M42,{fy + 4} A60,64 0 0,1 158,{fy + 4}" stroke="{INK}" stroke-width="8" fill="none" stroke-linecap="round"/>'
                      f'<rect x="28" y="{fy - 14}" width="20" height="38" rx="9" fill="{a}" stroke="{INK}" stroke-width="3"/>'
                      f'<rect x="152" y="{fy - 14}" width="20" height="38" rx="9" fill="{a}" stroke="{INK}" stroke-width="3"/>',
        "bowtie": f'<path d="M100,178 L80,167 L80,189 Z M100,178 L120,167 L120,189 Z" fill="{a}" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
                  f'<circle cx="100" cy="178" r="5" fill="{ad}" stroke="{INK}" stroke-width="2.5"/>',
        "scarf": f'<rect x="52" y="160" width="96" height="16" rx="8" fill="{a}" stroke="{INK}" stroke-width="3"/>'
                 f'<rect x="118" y="168" width="16" height="30" rx="5" fill="{a}" stroke="{INK}" stroke-width="3"/>'
                 f'<path d="M60,168 H140" stroke="#fff" stroke-width="2" stroke-dasharray="6 6" opacity=".6"/>',
    }
    return parts.get(name, "")


CAPE = '<path d="M58,92 Q24,168 42,206 L158,206 Q176,168 142,92 Z" fill="{fill}" stroke="#1f2937" stroke-width="3" stroke-linejoin="round"/>'
ACCESSORIES = {"crown", "wizard_hat", "grad_cap", "party_hat", "beanie", "pumpkin_hat", "halo", "horns",
               "antenna", "glasses", "headphones", "bowtie", "scarf", "cape"}
SHAPES = {"circle", "squircle", "triangle", "square", "pentagon", "hexagon", "octagon", "star"}
EYES = {"dots", "happy", "wide", "sleepy", "star", "visor"}
MOUTHS = {"smile", "grin", "o", "cat"}


def _sparkles():
    spots = [(28, 58, 7), (172, 76, 6), (164, 168, 5), (34, 160, 5)]
    return "".join(f'<path class="fig-sparkle" style="animation-delay:{i * 0.5}s" '
                   f'd="M{x},{y - s * 2} Q{x},{y} {x + s * 2},{y} Q{x},{y} {x},{y + s * 2} Q{x},{y} {x - s * 2},{y} Q{x},{y} {x},{y - s * 2} Z" fill="#fde047"/>'
                   for i, (x, y, s) in enumerate(spots))


def render(fig, size=160, serial=None):
    """The figurine as an inline <svg> (safe to put straight into a template)."""
    uid = f"fig{next(_ids)}"
    rarity = fig["rarity"]
    color, accent = fig["color"], fig["accent"]
    fy = FACE_Y.get(fig["shape"], 120)
    body = body_path(fig["shape"])
    accessories = fig.get("accessories", [])
    defs = [f'<linearGradient id="{uid}b" x1="0" y1="0" x2="0.6" y2="1">'
            f'<stop offset="0" stop-color="{lighter(color)}"/><stop offset=".55" stop-color="{color}"/>'
            f'<stop offset="1" stop-color="{darker(color, 0.2)}"/></linearGradient>',
            f'<radialGradient id="{uid}g"><stop offset="0" stop-color="{accent}" stop-opacity=".55"/>'
            f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>',
            f'<linearGradient id="{uid}r" x1="0" y1="0" x2="1" y2="1">'
            + "".join(f'<stop offset="{i / 5}" stop-color="{c}"/>' for i, c in
                      enumerate(["#f43f5e", "#f59e0b", "#84cc16", "#06b6d4", "#8b5cf6", "#ec4899"]))
            + "</linearGradient>"]
    back, front = [], []

    if rarity == "legendary":
        rays = "".join(f'<path d="M100,124 L{100 + 110 * math.cos(math.radians(a - 4)):.0f},{124 + 110 * math.sin(math.radians(a - 4)):.0f} '
                       f'L{100 + 110 * math.cos(math.radians(a + 4)):.0f},{124 + 110 * math.sin(math.radians(a + 4)):.0f} Z"/>'
                       for a in range(0, 360, 30))
        back.append(f'<g class="fig-rays" fill="{lighter(accent, 0.2)}" opacity=".35">{rays}</g>')
    if rarity in ("epic", "legendary", "limited"):
        back.append(f'<circle cx="100" cy="124" r="96" fill="url(#{uid}g)"/>')
    if "cape" in accessories:
        back.append(CAPE.format(fill=darker(accent, 0.1)))

    # Pedestal
    if rarity == "limited":
        top, side = f"url(#{uid}r)", "#4c1d95"
    else:
        top, side = RARITIES[rarity]["pedestal"]
    back.append(f'<path d="M34,206 V222 A66,12 0 0,0 166,222 V206 Z" fill="{side}"/>'
                f'<ellipse cx="100" cy="206" rx="66" ry="12" fill="{top}"/>'
                f'<ellipse cx="100" cy="200" rx="44" ry="6" fill="#000" opacity=".15"/>')
    if serial is not None:
        back.append(f'<text x="100" y="226" text-anchor="middle" font-size="12" font-weight="700" '
                    f'font-family="system-ui, sans-serif" fill="#fff">No. {int(serial)}</text>')

    outline = f"url(#{uid}r)" if rarity == "limited" else darker(color, 0.45)
    width = 6 if rarity == "limited" else 4
    front.append(f'<path d="{body}" fill="url(#{uid}b)" stroke="{outline}" stroke-width="{width}" stroke-linejoin="round"/>')
    front.append(f'<ellipse cx="78" cy="{fy - 34}" rx="14" ry="8" fill="#fff" opacity=".35" transform="rotate(-25 78 {fy - 34})"/>')
    front.append(f'<circle cx="70" cy="{fy + 15}" r="7" fill="#fb7185" opacity=".45"/>'
                 f'<circle cx="130" cy="{fy + 15}" r="7" fill="#fb7185" opacity=".45"/>')
    front.append(_eyes(fig["eyes"], fy, accent))
    front.append(_mouth(fig["mouth"], fy + 22))
    for name in accessories:
        if name != "cape":
            front.append(_accessory(name, fy, color, accent))
    if rarity != "common":
        front.append(_sparkles())

    label = escape(f'{fig["name"]}, {RARITIES[rarity]["label"]} figurine')
    return Markup(
        f'<svg class="figurine fig-{rarity}" viewBox="0 0 200 240" width="{size}" height="{size * 1.2:.0f}" '
        f'role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg">'
        f'<defs>{"".join(defs)}</defs>{"".join(back)}{"".join(front)}</svg>')
