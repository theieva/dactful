"""
Dictionary storage-location tests - fully isolated in a temp dir (never touches
the real ~/.dactful). Covers the move+merge+adopt behavior and reset.
"""

import os

import pytest
from fastapi import HTTPException

from app import dictionary
from app.main import (
    DictLocation,
    api_reset_dict_location,
    api_set_dict_location,
)


@pytest.fixture
def iso(tmp_path, monkeypatch):
    default_dir = tmp_path / "home" / ".dactful"
    monkeypatch.setenv("DACTFUL_CONFIG", str(default_dir / "config.json"))
    monkeypatch.delenv("DACTFUL_DICT", raising=False)
    monkeypatch.setattr(dictionary, "DEFAULT_DIR", str(default_dir))
    monkeypatch.setattr(dictionary, "DEFAULT_PATH", str(default_dir / dictionary.DICT_FILENAME))
    return tmp_path


def test_move_merges_with_existing_then_reset(iso, tmp_path):
    # Seed the default dictionary.
    dictionary.save([{"term": "Acme", "tag": "[[C1]]"}])
    assert {e["term"] for e in dictionary.load()} == {"Acme"}

    # A cloud folder that already holds a dictionary from "another machine".
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    dictionary._write(str(cloud / dictionary.DICT_FILENAME), [{"term": "Globex", "tag": "[[C2]]"}])

    # Move to the cloud folder: should merge Acme + Globex, remove old file.
    res = api_set_dict_location(DictLocation(folder=str(cloud)))
    assert res["moved"] is True
    assert res["adopted_existing"] is True
    assert res["is_default"] is False
    assert {e["term"] for e in dictionary.load()} == {"Acme", "Globex"}
    assert not os.path.exists(dictionary.DEFAULT_PATH)  # move semantics

    # File perms preserved on the destination.
    mode = os.stat(cloud / dictionary.DICT_FILENAME).st_mode & 0o777
    assert mode == 0o600

    # Reset: merges back into the default location; cloud file left intact.
    res2 = api_reset_dict_location()
    assert res2["is_default"] is True
    assert {e["term"] for e in dictionary.load()} == {"Acme", "Globex"}
    assert os.path.exists(cloud / dictionary.DICT_FILENAME)  # user's folder untouched


def test_conflict_incoming_tag_wins(iso, tmp_path):
    dictionary.save([{"term": "Acme", "tag": "[[LOCAL]]"}])
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    dictionary._write(str(cloud / dictionary.DICT_FILENAME), [{"term": "acme", "tag": "[[REMOTE]]"}])
    api_set_dict_location(DictLocation(folder=str(cloud)))
    entries = dictionary.load()
    assert len(entries) == 1
    # current (local) wins the merge - its tag is the more recent intent.
    assert entries[0]["tag"] == "[[LOCAL]]"


def test_legacy_filename_auto_migrates(iso, tmp_path):
    # An old-named dictionary.json in the active folder is renamed on access,
    # with its entries preserved.
    import os as _os

    legacy = _os.path.join(dictionary.DEFAULT_DIR, dictionary.LEGACY_DICT_FILENAME)
    dictionary._write(legacy, [{"term": "Legacy Co", "tag": "[[OLD]]"}])
    assert _os.path.exists(legacy)

    entries = dictionary.load()  # triggers migration via _path()
    assert {e["term"] for e in entries} == {"Legacy Co"}
    assert _os.path.exists(dictionary.DEFAULT_PATH)      # new name now exists
    assert not _os.path.exists(legacy)                    # old name renamed away


def test_upsert_dedupes_by_normalized_term(iso):
    # "Inc." and "inc" (punctuation + case) must not create two entries.
    dictionary.upsert([{"term": "Company Name Inc.", "tag": "[[COMPANY_1]]"}])
    dictionary.upsert([{"term": "company Name  inc", "tag": "[[COMPANY_1]]"}])
    entries = dictionary.load()
    assert len(entries) == 1


def test_bad_folder_rejected(iso, tmp_path):
    with pytest.raises(HTTPException) as e:
        api_set_dict_location(DictLocation(folder=str(tmp_path / "does-not-exist")))
    assert e.value.status_code == 400


def test_move_to_same_path_is_noop(iso, tmp_path):
    dictionary.save([{"term": "Acme", "tag": "[[C1]]"}])
    current_dir = os.path.dirname(dictionary._path())
    res = api_set_dict_location(DictLocation(folder=current_dir))
    assert res["moved"] is False
    assert {e["term"] for e in dictionary.load()} == {"Acme"}
