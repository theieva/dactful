"""
Orchestration: ties the engine, ingest, mapping, and dictionary together and
owns the per-session temp workspace.

The one non-negotiable here is the validation sweep: after
redacting, we re-extract every surface of the generated file and assert that no
confirmed term survives. If any does, the export is marked UNSAFE and withheld,
rather than handed over as if it were clean. Failing loud beats false confidence.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import dictionary, mappings_store
from .docx_redact import extract_text, find_all, redact_docx
from .ingest import prepare_input
from .mapping import (
    MapEntry,
    build_guide_text,
    build_mapping_json,
    now_stamp,
)
from .matching import find_matches, redact_text, term_rule
from .tags import normalize_tag


@dataclass
class Session:
    id: str
    work_dir: str
    kind: str
    orig_name: str
    input_docx: str
    outputs: Dict[str, str] = field(default_factory=dict)  # kind -> path
    images: List[Dict] = field(default_factory=list)       # extracted PDF images
    source_text: str = ""                                  # raw pasted text (text sessions)


_SESSIONS: Dict[str, Session] = {}


def _base_name(orig_name: str) -> str:
    base = os.path.splitext(os.path.basename(orig_name))[0]
    return base or "document"


def create_session(kind: str, orig_name: str, *, upload_path: str = "", text: str = "") -> Session:
    sid = uuid.uuid4().hex
    work_dir = tempfile.mkdtemp(prefix=f"dactful_{sid}_")
    src = upload_path if kind != "text" else text
    input_docx = prepare_input(src, kind, work_dir)
    session = Session(sid, work_dir, kind, orig_name, input_docx)
    if kind == "text":
        session.source_text = text  # redact this directly for a text-in/text-out flow
    # A PDF may carry images (charts, logos) the text extraction can't see.
    # Index them so the user can keep or drop each one.
    if kind == "pdf":
        from .pdf_images import extract_and_index

        session.images = extract_and_index(upload_path, os.path.join(work_dir, "images"))
    _SESSIONS[sid] = session
    return session


def create_workspace(orig_name: str) -> Session:
    """A session with only a temp workspace (no input prep) - used by restore,
    where the caller writes the finished document in itself."""
    sid = uuid.uuid4().hex
    work_dir = tempfile.mkdtemp(prefix=f"dactful_{sid}_")
    session = Session(sid, work_dir, "restore", orig_name, input_docx="")
    _SESSIONS[sid] = session
    return session


def get_session(sid: str) -> Optional[Session]:
    return _SESSIONS.get(sid)


def cleanup_session(sid: str) -> None:
    s = _SESSIONS.pop(sid, None)
    if s and os.path.isdir(s.work_dir):
        shutil.rmtree(s.work_dir, ignore_errors=True)


def session_text(session: Session) -> str:
    return extract_text(session.input_docx)


@dataclass
class RedactResult:
    ok: bool
    replacements: int
    leaked: List[str]
    entries: List[Dict]
    guide_text: str
    files: Dict[str, str]  # download-kind -> filename
    mapping_id: str = ""   # id under which the mapping was saved for Restore
    redacted_text: Optional[str] = None  # set for pasted-text sessions (copy-box output)


def perform_redaction(
    session: Session,
    entries: List[Dict],
    redact_filename: bool = False,
    keep_images: Optional[List[int]] = None,
) -> RedactResult:
    """entries: [{term, tag}]. Redacts, validates, writes outputs, updates dict.

    If redact_filename is True, the downloaded document (and its mapping) gets a
    generic name like 'redacted-job-12.docx' instead of one derived from the
    original file name, in case the original name itself is sensitive."""
    # Normalize tags and drop blanks/dupes.
    clean: List[Dict] = []
    seen_terms = set()
    for e in entries:
        term = (e.get("term") or "").strip()
        tag = normalize_tag(e.get("tag") or "")
        if not term or not tag or term.lower() in seen_terms:
            continue
        seen_terms.add(term.lower())
        clean.append({"term": term, "tag": tag})

    rules = [term_rule(e["term"], e["tag"]) for e in clean]
    value_for_tag: Dict[str, str] = {e["tag"]: e["term"] for e in clean}
    counts: Dict[str, int] = {}
    is_text = session.kind == "text"

    if is_text:
        # Pasted text in, redacted text out: no document round-trip.
        source = session.source_text
        matches = find_matches(source, rules)
        for m in matches:
            counts[m.rule.label] = counts.get(m.rule.label, 0) + 1
        total = len(matches)
        redacted_text = redact_text(source, rules)
        out_text = redacted_text
        source_display = "pasted text"
        out_base = "redacted-text"
    else:
        for m in find_all(session.input_docx, rules):
            counts[m.rule.label] = counts.get(m.rule.label, 0) + 1
        if redact_filename:
            out_base = f"redacted-job-{mappings_store.count() + 1}"
            redacted_filename = f"{out_base}.docx"
            source_display = redacted_filename
        else:
            out_base = _base_name(session.orig_name)
            redacted_filename = f"{out_base}_redacted.docx"
            source_display = session.orig_name
        redacted_path = os.path.join(session.work_dir, redacted_filename)
        total = redact_docx(session.input_docx, redacted_path, rules)
        redacted_text = None
        out_text = extract_text(redacted_path)

    # --- validation sweep: the output must contain none of the source terms ---
    leaked = [e["term"] for e in clean if find_all_in_text(out_text, e["term"])]

    map_entries = [
        MapEntry(tag=tag, value=value_for_tag[tag], count=counts.get(tag, 0))
        for tag in value_for_tag
        if counts.get(tag, 0) > 0
    ]
    generated = now_stamp()
    guide_text = build_guide_text(map_entries, source_display, generated)
    mapping_json = build_mapping_json(map_entries, source_display, generated)

    files: Dict[str, str] = {}
    redacted_text_out = None
    if not leaked:
        # Mapping backup files (both text and document flows).
        txt_path = os.path.join(session.work_dir, f"{out_base}_mapping.txt")
        json_path = os.path.join(session.work_dir, f"{out_base}_mapping.json")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(guide_text)
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(mapping_json)
        session.outputs["mapping_txt"] = txt_path
        session.outputs["mapping_json"] = json_path
        files["mapping_txt"] = f"{out_base}_mapping.txt"
        files["mapping_json"] = f"{out_base}_mapping.json"

        if is_text:
            redacted_text_out = redacted_text
        else:
            # Embed kept images into the (text-clean) output; they pass through
            # as-is, which is why the user reviews each one first.
            if keep_images and session.images:
                from .pdf_images import embed_images

                keep_ids = set(keep_images)
                paths = [img["path"] for img in session.images if img["id"] in keep_ids]
                embed_images(redacted_path, paths)
            files["redacted"] = redacted_filename
            session.outputs["redacted"] = redacted_path

        # Remember the mapping locally so Restore can offer it back, and learn
        # the confirmed terms for next time.
        mappings_store.save(session.id, mapping_json)
        dictionary.upsert([{**e, "source": "redaction"} for e in clean])

    return RedactResult(
        ok=not leaked,
        replacements=total,
        leaked=leaked,
        entries=[{"tag": e.tag, "value": e.value, "count": e.count} for e in map_entries],
        guide_text=guide_text,
        files=files,
        mapping_id="" if leaked else session.id,
        redacted_text=redacted_text_out,
    )


def find_all_in_text(text: str, term: str) -> bool:
    """True if `term` still appears as a whole word in `text` (validation)."""
    from .matching import find_matches

    rule = term_rule(term, "[[X]]")
    return bool(find_matches(text, [rule]))
