"""
Durable local store of mapping files, so Restore can offer a recent redaction
back to the user without them hand-managing a .json.

These files contain the sensitive values that were removed - the same class of
data the dictionary already persists - so they get the same treatment: a private
per-user folder, 0600 files, and an explicit "Forget" control in the UI. Nothing
here ever leaves the machine.
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Dict, List, Optional

DEFAULT_DIR = os.path.expanduser("~/.dactful/mappings")

# Mapping ids come from session uuids (hex). Anything reaching get()/delete()
# from a request is validated against this before touching the filesystem, so a
# crafted id can't escape the store directory.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _dir() -> str:
    return os.environ.get("DACTFUL_MAPPINGS", DEFAULT_DIR)


def _secure_write(path: str, text: str) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _valid_id(mapping_id: str) -> bool:
    return bool(mapping_id) and bool(_ID_RE.match(mapping_id))


def save(mapping_id: str, mapping_json_text: str) -> None:
    if not _valid_id(mapping_id):
        return
    _secure_write(os.path.join(_dir(), f"{mapping_id}.json"), mapping_json_text)


def list_recent(limit: int = 25) -> List[Dict]:
    """Metadata only - document name, date, item count. Never the values, so the
    picker list can be shown without surfacing sensitive info."""
    d = _dir()
    if not os.path.isdir(d):
        return []
    files = sorted(glob.glob(os.path.join(d, "*.json")), key=os.path.getmtime, reverse=True)
    out: List[Dict] = []
    for p in files[:limit]:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        out.append(
            {
                "id": os.path.splitext(os.path.basename(p))[0],
                "source": data.get("source", "document"),
                "generated": data.get("generated", ""),
                "count": len(data.get("entries", [])),
            }
        )
    return out


def count() -> int:
    """How many redactions have been saved (used for the 'job number' in a
    redacted output filename)."""
    d = _dir()
    if not os.path.isdir(d):
        return 0
    return len(glob.glob(os.path.join(d, "*.json")))


def all_entries() -> List[Dict]:
    """Union of {tag, value, count} across every saved mapping, keyed by tag.

    Safe because tag ids are globally unique: restore can apply them all at once
    and only the tags actually present in the finished document will fire, so the
    user never has to say which redaction a document came from."""
    d = _dir()
    if not os.path.isdir(d):
        return []
    files = sorted(glob.glob(os.path.join(d, "*.json")), key=os.path.getmtime)  # oldest first
    merged: Dict[str, Dict] = {}
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for e in data.get("entries", []):
            tag = e.get("tag")
            if tag:
                merged[tag] = e  # newest wins on the rare legacy collision
    return list(merged.values())


def get(mapping_id: str) -> Optional[str]:
    if not _valid_id(mapping_id):
        return None
    p = os.path.join(_dir(), f"{mapping_id}.json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def delete(mapping_id: str) -> bool:
    if not _valid_id(mapping_id):
        return False
    p = os.path.join(_dir(), f"{mapping_id}.json")
    if os.path.isfile(p):
        os.remove(p)
        return True
    return False
