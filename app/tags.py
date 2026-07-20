"""Tag normalization. Tags are always [[UPPER_SNAKE]]."""

from __future__ import annotations

import re


def normalize_tag(raw: str) -> str:
    """Coerce any user input into the canonical [[UPPER_SNAKE]] form.

    "client1 name" -> "[[CLIENT1_NAME]]"; "[[SSN_1]]" -> "[[SSN_1]]".
    """
    s = raw.strip()
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2]
    s = s.strip().upper()
    s = re.sub(r"[^A-Z0-9]+", "_", s).strip("_")
    return f"[[{s}]]" if s else ""


def is_tag(s: str) -> bool:
    return bool(re.fullmatch(r"\[\[[A-Z0-9_]+\]\]", s.strip()))


def norm_key(term: str) -> str:
    """Normalize a real value for *comparison only* (dedup): lowercase, drop
    punctuation, collapse whitespace. So "Company Name Inc." and "company Name
    inc" resolve to the same key. We never store this form - only the exact text
    the user entered - so redaction stays accurate."""
    s = (term or "").strip().lower()
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE)  # strip punctuation
    s = re.sub(r"\s+", " ", s).strip()               # collapse whitespace
    return s


def next_tag(prefix: str, taken: set) -> str:
    """Return a fresh [[PREFIX_N]] tag that isn't already in `taken`, and record
    it there. This guarantees globally-unique tag ids: two different emails never
    both become [[EMAIL_1]], because minting always skips numbers already used
    by the dictionary or earlier in the same scan.
    """
    pfx = re.sub(r"[^A-Z0-9]+", "_", prefix.upper()).strip("_") or "TAG"
    n = 1
    while True:
        tag = f"[[{pfx}_{n}]]"
        if tag not in taken:
            taken.add(tag)
            return tag
        n += 1
