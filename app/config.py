"""
Local settings pointer.

This tiny file always lives on THIS machine (~/.dactful/config.json) even when
the user moves their dictionary into a cloud-synced folder - it just records
*where* the dictionary lives. It holds no sensitive values, only a path.
"""

from __future__ import annotations

import json
import os
from typing import Optional

DEFAULT_DIR = os.path.expanduser("~/.dactful")


def _config_path() -> str:
    return os.environ.get("DACTFUL_CONFIG", os.path.join(DEFAULT_DIR, "config.json"))


def _load() -> dict:
    p = _config_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(cfg: dict) -> None:
    p = _config_path()
    d = os.path.dirname(p)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    # Only a folder path, but keep it private for consistency (0600).
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def get_dict_dir() -> Optional[str]:
    """The folder the user chose for their dictionary, or None for default."""
    d = _load().get("dict_dir")
    return d or None


def set_dict_dir(path: str) -> None:
    cfg = _load()
    cfg["dict_dir"] = path
    _save(cfg)


def clear_dict_dir() -> None:
    cfg = _load()
    cfg.pop("dict_dir", None)
    _save(cfg)
