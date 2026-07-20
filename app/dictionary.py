"""
Local dictionary of confirmed terms.

The one thing Dactful persists between sessions. It makes the tool fast by the
fifth document: terms you've confirmed before are pre-checked next time. It
contains sensitive terms, lives in a single visible local file, and can be
cleared at any time.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

from . import config

# Default: the user's home under a visible, self-describing folder. The user can
# move the dictionary elsewhere (e.g. a cloud-synced folder) via settings; the
# chosen folder is remembered in config, and _path() resolves it here.
DEFAULT_DIR = os.path.expanduser("~/.dactful")
DICT_FILENAME = "dactful_dictionary.json"
LEGACY_DICT_FILENAME = "dictionary.json"  # pre-rename name, auto-migrated
DEFAULT_PATH = os.path.join(DEFAULT_DIR, DICT_FILENAME)


def _path() -> str:
    # Precedence: explicit env override (tests) > user-chosen folder > default.
    env = os.environ.get("DACTFUL_DICT")
    if env:
        return env
    folder = config.get_dict_dir() or DEFAULT_DIR
    new_path = os.path.join(folder, DICT_FILENAME)
    # One-time migration: if only the old-named file exists here, rename it.
    legacy = os.path.join(folder, LEGACY_DICT_FILENAME)
    if not os.path.exists(new_path) and os.path.exists(legacy):
        try:
            os.rename(legacy, new_path)
        except OSError:
            return legacy  # couldn't rename - keep reading the old file in place
    return new_path


def _read(path: str) -> List[Dict]:
    """Read entries from a specific dictionary file ([] if missing/unreadable)."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("entries", []) if isinstance(data, dict) else data
    except Exception:
        return []


def _write(path: str, entries: List[Dict]) -> None:
    """Write entries to a specific file with crown-jewels perms (0700 dir, 0600 file)."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    # Create with restrictive perms from the start (no world-readable window).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(
            {
                "note": "Dactful saved terms. Sensitive, keep private. Safe to delete.",
                "entries": entries,
            },
            f,
            indent=2,
        )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def merge(base: List[Dict], incoming: List[Dict]) -> List[Dict]:
    """Union two entry lists, keyed by normalized term (case/punctuation/
    whitespace-insensitive). On a collision, `incoming` wins (more recent
    intent)."""
    from .tags import norm_key

    by_term = {norm_key(e.get("term", "")): e for e in base if e.get("term")}
    for e in incoming:
        if e.get("term", "").strip():
            by_term[norm_key(e["term"])] = e
    return list(by_term.values())


def load() -> List[Dict]:
    return _read(_path())


def save(entries: List[Dict]) -> None:
    _write(_path(), entries)


def upsert(new_entries: List[Dict]) -> List[Dict]:
    """Merge confirmed {term, tag, source?} pairs into the dictionary (dedup by
    normalized term, so "Inc." and "inc" don't create two entries).

    `source` records how a term first entered the dictionary ("manual" or
    "redaction"); it is preserved for a term already present, so re-seeing a
    manually-added term in a redaction doesn't relabel it."""
    from .tags import norm_key

    entries = load()
    by_term = {norm_key(e["term"]): e for e in entries if e.get("term")}
    for e in new_entries:
        term = e.get("term", "").strip()
        tag = e.get("tag", "").strip()
        if term and tag:
            key = norm_key(term)
            existing = by_term.get(key)
            source = (existing.get("source") if existing else None) or e.get("source") or "manual"
            by_term[key] = {"term": term, "tag": tag, "source": source}
    merged = list(by_term.values())
    save(merged)
    return merged


def remove(term: str) -> List[Dict]:
    """Delete the entry for `term` (normalized match). Returns the remaining list."""
    from .tags import norm_key

    key = norm_key(term)
    entries = [e for e in load() if norm_key(e.get("term", "")) != key]
    save(entries)
    return entries


def clear() -> None:
    p = _path()
    if os.path.exists(p):
        os.remove(p)
