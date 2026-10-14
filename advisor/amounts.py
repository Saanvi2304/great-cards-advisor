"""Find the numbers a user actually typed, in Indian notation.

Used for "number grounding": every amount the LLM puts into a proposed tool
call must match a number the user wrote. This stops a model from inventing
or silently changing rupee amounts.

Handles: 15000, 15,000, 1,50,000, ₹15000, Rs 15k, 1.5 lakh, 1.5L, 10 LPA,
2 crore, 2cr, and small number words (once, twice, four ...).
Not handled (will be flagged for clarification instead): long spelled-out
numbers such as "fifteen thousand".
"""
from __future__ import annotations

import re
import sys

_MULTIPLIERS = {
    "k": 1e3, "thousand": 1e3,
    "l": 1e5, "lac": 1e5, "lacs": 1e5, "lakh": 1e5, "lakhs": 1e5, "lpa": 1e5,
    "cr": 1e7, "crore": 1e7, "crores": 1e7,
}

_NUMBER_RE = re.compile(
    r"(?<!\d)(?<!\d\.)"                             # not inside another number (allows "Rs.15000")
    r"(\d{1,3}(?:,\d{2,3})+|\d+)(\.\d+)?"           # 15000 / 15,000 / 1,50,000 / 1.5
    r"(?:\s*(thousand|lakhs?|lacs?|lpa|lac|crores?|cr|k|l)(?![a-z]))?",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "once": 1, "twice": 2, "thrice": 3,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)


def extract_numbers(text: str) -> set[float]:
    """Return every value the text could mean (raw number and expanded value)."""
    values: set[float] = set()
    for whole, frac, suffix in _NUMBER_RE.findall(text):
        base = float(whole.replace(",", "") + (frac or ""))
        values.add(base)
        if suffix:
            values.add(base * _MULTIPLIERS[suffix.lower()])
    for word in _WORD_RE.findall(text):
        values.add(float(_NUMBER_WORDS[word.lower()]))
    return values


def is_grounded(value: float, texts: list[str]) -> bool:
    """True if ``value`` matches (within 0.5%) a number found in the texts."""
    candidates = set().union(*(extract_numbers(t) for t in texts)) if texts else set()
    return any(abs(value - c) <= max(0.5, 0.005 * abs(c)) for c in candidates)


def digits_typed(digits: str, texts: list[str]) -> bool:
    """True if this exact digit string appears as its own token in the texts."""
    pattern = re.compile(rf"(?<!\d){re.escape(digits)}(?!\d)")
    return any(pattern.search(t) for t in texts)


def format_inr(value: float) -> str:
    """Format a rupee amount with Indian digit grouping, e.g. 150000 -> ₹1,50,000.

    Falls back to 'Rs.' if the terminal can't display the ₹ symbol.
    """
    try:
        # Test if the terminal can handle ₹.
        "₹".encode(sys.stdout.encoding or "utf-8")
        prefix = "₹"
    except (UnicodeEncodeError, LookupError):
        prefix = "Rs."

    rounded = round(value)
    if abs(value - rounded) > 1e-9:
        return f"{prefix}{value:,.2f}"
    s = str(abs(int(rounded)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        s = f"{head},{tail}"
    return f"{'-' if rounded < 0 else ''}{prefix}{s}"

