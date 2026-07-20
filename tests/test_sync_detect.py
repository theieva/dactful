"""Cloud-sync folder detection (macOS, path-based)."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS path heuristics")


def test_local_paths_are_not_synced():
    from app.main import _detect_sync

    assert _detect_sync("/tmp/whatever") == (False, None)
    assert _detect_sync("~/.dactful") == (False, None)


def test_cloudstorage_providers_detected():
    from app.main import _detect_sync

    assert _detect_sync("~/Library/CloudStorage/Dropbox/Dactful") == (True, "Dropbox")
    assert _detect_sync("~/Library/CloudStorage/GoogleDrive-me@x.com/D") == (True, "Google Drive")
    assert _detect_sync("~/Library/CloudStorage/OneDrive-Personal/D") == (True, "OneDrive")
    synced, prov = _detect_sync("~/Library/Mobile Documents/com~apple~CloudDocs/D")
    assert synced and prov == "iCloud Drive"
