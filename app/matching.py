"""
Dactful core matching engine.

This module is the heart of the tool. Everything else is plumbing around it.
It provides ONE mechanism used in three places:

  - analyze  : find sensitive spans to propose to the user
  - redact   : replace sensitive spans with tags   (term  -> [[TAG]])
  - restore  : replace tags with original values   ([[TAG]] -> value)

The two correctness properties that must never break:

  1. Longest-match-first. "Clienty Corp" must win over "Clienty" so we never
     emit "[[CLIENT2_DBA]] Corp".
  2. Non-overlapping. Once a span is claimed, no shorter/overlapping match may
     also fire inside it.

Both are enforced in `resolve_overlaps`, and both are covered by tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# Character class used for whole-word boundaries. We deliberately use explicit
# alphanumerics rather than \b so that:
#   - "Acme" does NOT match inside "Acmes"      (safe: no accidental plural hit)
#   - "Acme" DOES match in "Acme's"             (the apostrophe is a boundary)
_WORD = r"[0-9A-Za-z]"


@dataclass
class Rule:
    """A single findable thing: a compiled pattern plus what to replace it with."""

    pattern: re.Pattern
    replacement: str
    label: str            # tag name (redact/restore) or detector type (analyze)
    source: str = "term"  # "dictionary" | "pattern" | "ner" | "tag" - provenance
    weight: int = 0       # tie-breaker when spans are equal length (higher wins)


@dataclass
class Match:
    start: int
    end: int
    rule: Rule
    text: str  # the exact source text that matched

    @property
    def length(self) -> int:
        return self.end - self.start


def _flexible_core(term: str) -> str:
    """Escape a term but let internal whitespace match across spaces, multiple
    spaces, tabs, or line breaks - so a name split across a line still matches."""
    tokens = [re.escape(tok) for tok in term.split()]
    return r"\s+".join(tokens) if tokens else re.escape(term)


def term_rule(
    term: str,
    tag: str,
    *,
    case_insensitive: bool = True,
    whole_word: bool = True,
    weight: int = 0,
    source: str = "term",
) -> Rule:
    """Build a redaction rule: match `term`, replace with `tag`.

    Whole-word matching leaves possessives intact: "Acme's" -> "[[TAG]]'s",
    which keeps restore perfectly lossless.
    """
    core = _flexible_core(term)
    left = f"(?<!{_WORD})" if whole_word else ""
    right = f"(?!{_WORD})" if whole_word else ""
    flags = re.IGNORECASE if case_insensitive else 0
    return Rule(
        pattern=re.compile(left + core + right, flags),
        replacement=tag,
        label=tag,
        source=source,
        weight=weight,
    )


def resolve_overlaps(candidates: List[Match]) -> List[Match]:
    """Given every candidate match, return a non-overlapping subset that
    prefers the longest span starting at the earliest position.

    Sort key: start ascending, then length descending, then weight descending.
    Then a single greedy left-to-right sweep, accepting a match only if it
    begins at or after the end of the last accepted one.
    """
    candidates.sort(key=lambda m: (m.start, -m.length, -m.rule.weight))
    selected: List[Match] = []
    last_end = -1
    for m in candidates:
        if m.start >= last_end:
            selected.append(m)
            last_end = m.end
    return selected


def find_matches(text: str, rules: List[Rule]) -> List[Match]:
    """Find all non-overlapping matches of `rules` in `text`."""
    candidates: List[Match] = []
    for rule in rules:
        for m in rule.pattern.finditer(text):
            if m.end() > m.start():  # ignore zero-width
                candidates.append(Match(m.start(), m.end(), rule, m.group(0)))
    return resolve_overlaps(candidates)


def apply_to_text(
    text: str,
    matches: List[Match],
    resolve: Optional[Callable[[Match], str]] = None,
) -> str:
    """Apply matches to a plain string, left to right."""
    resolve = resolve or (lambda m: m.rule.replacement)
    out: List[str] = []
    pos = 0
    for m in sorted(matches, key=lambda x: x.start):
        out.append(text[pos:m.start])
        out.append(resolve(m))
        pos = m.end
    out.append(text[pos:])
    return "".join(out)


def redact_text(text: str, rules: List[Rule]) -> str:
    """Convenience: find + apply in one call, for pasted-text and testing."""
    return apply_to_text(text, find_matches(text, rules))
