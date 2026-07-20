"""
Mapping artifacts: the human-readable guide and the JSON map.

Two files are produced alongside every redacted document:
  - <name>_mapping.txt  : human-readable guide (the "how to put it back" note)
  - <name>_mapping.json : machine-readable, consumed by the Restore tab

Both files contain exactly the sensitive information the user just removed, so
both are labelled as the crown jewels and must stay local.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List


@dataclass
class MapEntry:
    tag: str
    value: str
    count: int = 0


def build_guide_text(entries: List[MapEntry], source_name: str, generated: str) -> str:
    # Longest tag first - the order a manual find-and-replace must follow.
    ordered = sorted(entries, key=lambda e: (-len(e.tag), e.tag))
    tag_w = max((len(e.tag) for e in ordered), default=10) + 2
    val_w = max((len(e.value) for e in ordered), default=10) + 2

    lines = [
        f"REDACTION MAPPING: {source_name}",
        f"Generated {generated}",
        "",
        "When you're ready with your completed doc, you can add back the sensitive",
        "info with find and replace using the following tags. Replace in the order",
        "listed below (longer tags first), or drop the finished document into the",
        "Restore tab and the app will do it for you.",
        "",
    ]
    for e in ordered:
        occ = f"({e.count} occurrence{'s' if e.count != 1 else ''})"
        lines.append(f"  {e.tag:<{tag_w}}->  {e.value:<{val_w}}{occ}")
    lines += [
        "",
        "*** This file contains the sensitive information you just removed. ***",
        "*** Keep it local. Do not upload it anywhere. ***",
    ]
    return "\n".join(lines) + "\n"


def build_mapping_json(entries: List[MapEntry], source_name: str, generated: str) -> str:
    return json.dumps(
        {
            "source": source_name,
            "generated": generated,
            "tool": "Dactful",
            "note": "Contains sensitive values. Keep local.",
            "entries": [
                {"tag": e.tag, "value": e.value, "count": e.count}
                for e in sorted(entries, key=lambda e: (-len(e.tag), e.tag))
            ],
        },
        indent=2,
    )


def load_mapping_json(raw: str) -> List[Dict]:
    """Parse a mapping.json back into [{tag, value, count}] for restore."""
    data = json.loads(raw)
    return data["entries"] if isinstance(data, dict) else data


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
