"""
Restore: put the sensitive values back into the finished document.

Same segment-aware engine as redaction, run in the opposite direction:
[[TAG]] -> original value. Because the finished document comes back from an LLM,
two things are handled beyond a naive replace:

  - Tags may be fragmented across runs (same problem as redaction) -> we reuse
    the paragraph-level run engine, so a split tag still restores.
  - LLMs mangle tags: "**[[CLIENT1_NAME]]**", "[[ CLIENT1_NAME ]]",
    "[[Client1 Name]]". We detect these tolerant variants and repair them, but
    report them separately so the user knows a near-miss happened.

Restore always reports what it did AND what it did not do (tags that never
appeared), because a silently-dropped tag means leaked-looking output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

from .docx_redact import extract_text, redact_docx
from .matching import Rule, find_matches

_EXACT_WEIGHT = 100
_MANGLED_WEIGHT = 0


def _inner(tag: str) -> str:
    """CLIENT1_NAME from [[CLIENT1_NAME]]."""
    return tag[2:-2] if tag.startswith("[[") and tag.endswith("]]") else tag


def _mangled_pattern(tag: str) -> re.Pattern:
    """Tolerant matcher for a mangled tag: optional markdown emphasis around it,
    spaces inside the brackets, and space/underscore/case variants inside."""
    inner = _inner(tag)
    flexible = re.sub(r"_", r"[_ ]", re.escape(inner))
    return re.compile(
        r"[*_]{0,3}\[\[\s*" + flexible + r"\s*\]\][*_]{0,3}",
        re.IGNORECASE,
    )


def build_restore_rules(mapping: List[Dict]) -> List[Rule]:
    """From [{tag, value}, ...] build exact + tolerant rules per tag."""
    rules: List[Rule] = []
    for entry in mapping:
        tag, value = entry["tag"], entry["value"]
        rules.append(
            Rule(
                pattern=re.compile(re.escape(tag)),
                replacement=value,
                label=tag,
                source="exact",
                weight=_EXACT_WEIGHT,
            )
        )
        rules.append(
            Rule(
                pattern=_mangled_pattern(tag),
                replacement=value,
                label=tag,
                source="mangled",
                weight=_MANGLED_WEIGHT,
            )
        )
    return rules


# Any bracket token still present after restore = something we had no value for
# (a tag the AI invented or altered beyond repair). This is the useful signal -
# unlike "mapping tags not in the doc", which is meaningless when we apply the
# whole saved-mapping union at once.
_LEFTOVER_RE = re.compile(r"\[\[[^\]\n]{1,60}\]\]")


@dataclass
class RestoreReport:
    replaced: Dict[str, int]        # tag -> exact replacements
    mangled: Dict[str, int]         # tag -> tolerant/repaired replacements
    leftover: List[str]             # bracket tags still in the output (unknown)

    @property
    def total(self) -> int:
        return sum(self.replaced.values()) + sum(self.mangled.values())

    def to_dict(self) -> Dict:
        return {
            "replaced": self.replaced,
            "mangled": self.mangled,
            "leftover": self.leftover,
            "total": self.total,
        }


def _counts(text: str, mapping: List[Dict], rules: List[Rule]):
    replaced = {e["tag"]: 0 for e in mapping}
    mangled = {e["tag"]: 0 for e in mapping}
    for m in find_matches(text, rules):
        if m.rule.source == "exact":
            replaced[m.rule.label] += 1
        else:
            mangled[m.rule.label] += 1
    return replaced, mangled


def _leftover(text: str) -> List[str]:
    return sorted(set(_LEFTOVER_RE.findall(text)))


def restore_docx(input_path: str, output_path: str, mapping: List[Dict]) -> RestoreReport:
    """Restore a finished .docx and return a report of what happened."""
    rules = build_restore_rules(mapping)
    replaced, mangled = _counts(extract_text(input_path), mapping, rules)
    redact_docx(input_path, output_path, rules)  # same engine, restore rules
    leftover = _leftover(extract_text(output_path))
    return RestoreReport(replaced=replaced, mangled=mangled, leftover=leftover)


def restore_text(text: str, mapping: List[Dict]) -> tuple[str, RestoreReport]:
    """Restore a plain string (for pasted-text workflows)."""
    from .matching import apply_to_text

    rules = build_restore_rules(mapping)
    replaced, mangled = _counts(text, mapping, rules)
    restored = apply_to_text(text, find_matches(text, rules))
    return restored, RestoreReport(replaced=replaced, mangled=mangled, leftover=_leftover(restored))
