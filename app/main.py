"""
Dactful local web server.

Binds to 127.0.0.1 only. The redaction engine (this module's imports) has no
HTTP client of its own - the only network surface is this local server, which
never sends a document anywhere. Everything happens on the machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, dictionary, mappings_store, service
from .detect import analyze
from .docx_redact import UnsafeDocxError
from .mapping import load_mapping_json
from .restore import restore_docx

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = FastAPI(title="Dactful", version=__version__)

# Hostnames that are legitimately "this machine". Anything else in the Host or
# Origin header is an attacker trying to reach the local server through a domain
# that resolves to 127.0.0.1 (DNS rebinding) or a cross-origin page.
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_MAX_UPLOAD = 50 * 1024 * 1024  # 50 MB cap on any request body


@app.middleware("http")
async def _local_only_guard(request: Request, call_next):
    """Defenses for a browser-reachable localhost app:

    1. Host allowlist - blocks DNS-rebinding: an attacker domain pointed at
       127.0.0.1 arrives with its own name in the Host header, not ours.
    2. CSRF guard on state-changing /api calls - blocks a malicious page you
       happen to have open from driving Dactful. It must both (a) carry no
       foreign Origin and (b) send the X-Dactful-App header, which browsers
       refuse to set cross-origin without a CORS preflight this server never
       grants.
    3. Body-size cap - rejects oversized uploads before they are read, so a
       huge file can't exhaust memory.
    """
    host = request.headers.get("host", "")
    hostname = host.rsplit(":", 1)[0].strip("[]") if host else ""
    if hostname and hostname not in _ALLOWED_HOSTS:
        return PlainTextResponse("Forbidden: unexpected host.", status_code=403)

    if request.method not in _SAFE_METHODS:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > _MAX_UPLOAD:
            return PlainTextResponse("That upload is too large (50 MB max).", status_code=413)

        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None:
                origin_host = urlparse(origin).hostname or ""
                if origin_host not in _ALLOWED_HOSTS:
                    return PlainTextResponse("Cross-origin request blocked.", status_code=403)
            if request.headers.get("x-dactful-app") != "1":
                return PlainTextResponse("Forbidden: missing app header.", status_code=403)

    return await call_next(request)


@app.exception_handler(UnsafeDocxError)
async def _unsafe_docx_handler(request: Request, exc: UnsafeDocxError):
    # A malicious/malformed file is a client error, not a server crash.
    return JSONResponse({"detail": str(exc)}, status_code=400)


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif", ".heic"}


def _kind_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".docx":
        return "docx"
    if ext == ".pdf":
        return "pdf"
    if ext in _IMAGE_EXTS:
        return "image"
    raise HTTPException(400, "Unsupported file type. Use .docx, .pdf, a screenshot/image, or paste text.")


@app.get("/api/health")
def health():
    from .detect import _load_spacy
    from .ocr import available as ocr_available

    return {
        "app": "Dactful",
        "version": __version__,
        "ner_available": _load_spacy() is not None,
        "ocr_available": ocr_available(),
        "native_folder_picker": sys.platform == "darwin",
    }


@app.post("/api/analyze")
async def api_analyze(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    include_money: bool = Form(False),
    use_ner: bool = Form(True),
):
    if file is not None and file.filename:
        kind = _kind_for(file.filename)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1])
        tmp.write(await file.read())
        tmp.close()
        try:
            session = service.create_session(kind, file.filename, upload_path=tmp.name)
        except Exception as e:
            raise HTTPException(400, str(e))
        finally:
            os.unlink(tmp.name)
        orig_name = file.filename
    elif text and text.strip():
        session = service.create_session("text", "pasted.txt", text=text)
        orig_name = "pasted.txt"
    else:
        raise HTTPException(400, "Provide a .docx/.pdf file or paste some text.")

    doc_text = service.session_text(session)
    suggestions = analyze(
        doc_text,
        dictionary=dictionary.load(),
        include_money=include_money,
        use_ner=use_ner,
    )
    from .detect import _load_spacy

    return {
        "session_id": session.id,
        "orig_name": orig_name,
        "char_count": len(doc_text),
        "ner_available": _load_spacy() is not None,
        "images": [
            {"id": im["id"], "thumb": im["thumb"], "warn": im["warn"],
             "page": im["page"], "width": im["width"], "height": im["height"]}
            for im in session.images
        ],
        "suggestions": [
            {
                "term": s.term,
                "type": s.type,
                "source": s.source,
                "tag": s.tag,
                "count": s.count,
                "contexts": s.contexts,
                "checked": s.checked,
            }
            for s in suggestions
        ],
    }


class RedactBody(BaseModel):
    session_id: str
    entries: List[dict]  # [{term, tag}]
    redact_filename: bool = False
    keep_images: List[int] = []  # ids of PDF images to embed in the output


@app.post("/api/redact")
def api_redact(body: RedactBody):
    session = service.get_session(body.session_id)
    if not session:
        raise HTTPException(404, "Session expired. Please re-analyze the document.")
    if not body.entries:
        raise HTTPException(400, "Select at least one term to redact.")
    result = service.perform_redaction(
        session, body.entries,
        redact_filename=body.redact_filename,
        keep_images=body.keep_images,
    )
    return {
        "ok": result.ok,
        "replacements": result.replacements,
        "leaked": result.leaked,
        "entries": result.entries,
        "guide_text": result.guide_text,
        "files": result.files,
        "mapping_id": result.mapping_id,
        "redacted_text": result.redacted_text,
    }


@app.get("/api/image/{session_id}/{image_id}")
def api_image(session_id: str, image_id: int):
    """Full-size PNG of an extracted PDF image, for the enlarge view."""
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    img = next((im for im in session.images if im["id"] == image_id), None)
    if not img or not os.path.isfile(img["path"]):
        raise HTTPException(404, "Image not found.")
    return FileResponse(img["path"], media_type="image/png")


@app.get("/api/download/{session_id}/{kind}")
def api_download(session_id: str, kind: str):
    session = service.get_session(session_id)
    if not session or kind not in session.outputs:
        raise HTTPException(404, "File not found.")
    path = session.outputs[kind]
    return FileResponse(path, filename=os.path.basename(path))


@app.post("/api/restore")
async def api_restore(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    mapping_file: Optional[UploadFile] = File(None),
    mapping_json: Optional[str] = Form(None),
):
    # Resolve which mapping to use: an uploaded file, inline json, or (default)
    # the union of every saved redaction (tag ids are globally unique, so only
    # the tags present in this document fire).
    if mapping_file is not None and mapping_file.filename:
        try:
            mapping = load_mapping_json((await mapping_file.read()).decode("utf-8"))
        except Exception:
            raise HTTPException(400, "Could not read that mapping file.")
    elif mapping_json:
        try:
            mapping = load_mapping_json(mapping_json)
        except Exception:
            raise HTTPException(400, "Could not read that mapping.")
    else:
        mapping = mappings_store.all_entries()

    if not mapping:
        raise HTTPException(
            400,
            "No saved redactions found yet. Redact a document first, or add its mapping file.",
        )

    # Pasted text: restore in place and return the text directly.
    if text and text.strip():
        from .restore import restore_text

        restored, report = restore_text(text, mapping)
        return {"restored_text": restored, "report": report.to_dict()}

    # A finished .docx: restore into a downloadable copy.
    if file is None or not file.filename:
        raise HTTPException(400, "Add your finished document, or paste the finished text.")
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Restore expects a .docx file, or paste the text instead.")

    session = service.create_workspace(file.filename)
    finished = os.path.join(session.work_dir, "finished.docx")
    with open(finished, "wb") as f:
        f.write(await file.read())

    base = os.path.splitext(os.path.basename(file.filename))[0]
    out_path = os.path.join(session.work_dir, f"{base}_restored.docx")
    report = restore_docx(finished, out_path, mapping)
    session.outputs["restored"] = out_path

    return {
        "session_id": session.id,
        "report": report.to_dict(),
        "file": f"{base}_restored.docx",
    }


@app.get("/api/mappings")
def api_mappings():
    """Recent saved redactions (metadata only - no sensitive values)."""
    return {"mappings": mappings_store.list_recent()}


class ForgetBody(BaseModel):
    mapping_id: str


@app.post("/api/mappings/forget")
def api_mappings_forget(body: ForgetBody):
    return {"ok": mappings_store.delete(body.mapping_id)}


@app.post("/api/dictionary/clear")
def api_dict_clear():
    dictionary.clear()
    return {"ok": True}


@app.get("/api/dictionary")
def api_dict():
    return {"entries": dictionary.load()}


class DictEntry(BaseModel):
    term: str
    tag: str


@app.post("/api/dictionary/add")
def api_dict_add(body: DictEntry):
    from .tags import normalize_tag

    term = (body.term or "").strip()
    tag = normalize_tag(body.tag or "")
    if not term:
        raise HTTPException(400, "Enter the real value you want Dactful to remember.")
    if not tag:
        raise HTTPException(400, "Enter a tag for it (e.g. client1_name).")
    return {"entries": dictionary.upsert([{"term": term, "tag": tag, "source": "manual"}])}


class DictTerm(BaseModel):
    term: str


@app.post("/api/dictionary/delete")
def api_dict_delete(body: DictTerm):
    return {"entries": dictionary.remove(body.term)}


# ---- dictionary storage location ----

_HOME = os.path.expanduser("~")


def _pretty_provider(name: str) -> str:
    base = name.split("-")[0].strip()
    known = {
        "dropbox": "Dropbox", "googledrive": "Google Drive", "onedrive": "OneDrive",
        "box": "Box", "iclouddrive": "iCloud Drive", "icloud": "iCloud Drive",
        "pcloud": "pCloud", "mega": "MEGA", "protondrive": "Proton Drive",
        "sync": "Sync.com",
    }
    return known.get(base.lower().replace(" ", ""), base or "a cloud service")


def _detect_sync(folder: str):
    """Best-effort check (macOS, path-based): is this folder in a known cloud-
    sync location? Returns (synced: bool, provider: str|None). It can't catch
    every third-party setup, so a False means 'not in a known cloud folder',
    not a hard guarantee."""
    if sys.platform != "darwin":
        return False, None
    p = os.path.abspath(os.path.expanduser(folder))

    cloud_root = os.path.join(_HOME, "Library", "CloudStorage")
    if p == cloud_root or p.startswith(cloud_root + os.sep):
        top = os.path.relpath(p, cloud_root).split(os.sep)[0]
        return True, _pretty_provider(top)

    icloud = os.path.join(_HOME, "Library", "Mobile Documents", "com~apple~CloudDocs")
    if p == icloud or p.startswith(icloud + os.sep):
        return True, "iCloud Drive"

    if p.startswith(_HOME + os.sep):
        first = os.path.relpath(p, _HOME).split(os.sep)[0].lower()
        legacy = {"dropbox": "Dropbox", "google drive": "Google Drive",
                  "onedrive": "OneDrive", "box sync": "Box", "box": "Box"}
        if first in legacy:
            return True, legacy[first]

    # iCloud "Desktop & Documents" sync: those folders, only if it's enabled
    # (the iCloud mirror folder exists).
    for name in ("Documents", "Desktop"):
        base = os.path.join(_HOME, name)
        if (p == base or p.startswith(base + os.sep)) and os.path.isdir(os.path.join(icloud, name)):
            return True, "iCloud Drive"

    return False, None


def _settings_payload() -> dict:
    path = dictionary._path()
    synced, provider = _detect_sync(os.path.dirname(path))
    return {
        "dict_path": path,
        "dict_dir": os.path.dirname(path),
        "default_dir": dictionary.DEFAULT_DIR,
        "is_default": os.path.abspath(path) == os.path.abspath(dictionary.DEFAULT_PATH),
        "count": len(dictionary.load()),
        "synced": synced,
        "provider": provider,
    }


@app.get("/api/settings")
def api_settings():
    return _settings_payload()


@app.post("/api/pick-folder")
def api_pick_folder():
    """Open the native macOS folder chooser and return the chosen path.

    The browser can't expose a filesystem path, but this local backend can: it
    asks macOS (via osascript) to show the standard Finder folder dialog. The
    script is a fixed literal - no user input is interpolated, so there's no
    shell/AppleScript injection surface. The dialog is inherently user-gated
    (nothing is returned unless the user actively picks a folder)."""
    if sys.platform != "darwin":
        raise HTTPException(400, "The native folder picker needs macOS. Paste the path instead.")
    script = (
        'POSIX path of (choose folder with prompt '
        '"Choose a folder for your Dactful dictionary")'
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=300,
        )
    except Exception:
        raise HTTPException(500, "Could not open the folder picker.")
    if proc.returncode != 0:
        return {"path": None, "cancelled": True}  # user closed the dialog
    return {"path": proc.stdout.strip() or None}


class DictLocation(BaseModel):
    folder: str


@app.post("/api/settings/dictionary-location")
def api_set_dict_location(body: DictLocation):
    folder = os.path.abspath(os.path.expanduser((body.folder or "").strip()))
    if not folder:
        raise HTTPException(400, "Enter a folder path.")
    if not os.path.isdir(folder):
        raise HTTPException(400, "That folder doesn't exist. Create it first, then paste its path.")
    if not os.access(folder, os.W_OK):
        raise HTTPException(400, "Dactful can't write to that folder (permission denied).")

    old_path = dictionary._path()
    new_path = os.path.join(folder, dictionary.DICT_FILENAME)
    if os.path.abspath(new_path) == os.path.abspath(old_path):
        return {**_settings_payload(), "moved": False}

    # Merge current entries into whatever may already be at the destination
    # (e.g. a dictionary synced there from another machine), then switch over.
    current = dictionary._read(old_path)
    existing = dictionary._read(new_path)
    merged = dictionary.merge(existing, current)
    dictionary._write(new_path, merged)
    config.set_dict_dir(folder)
    # Move semantics: remove the old copy so there's a single source of truth.
    if os.path.isfile(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass
    return {**_settings_payload(), "moved": True, "adopted_existing": bool(existing)}


@app.post("/api/settings/dictionary-location/reset")
def api_reset_dict_location():
    old_path = dictionary._path()
    default_path = dictionary.DEFAULT_PATH
    if os.path.abspath(old_path) != os.path.abspath(default_path):
        current = dictionary._read(old_path)
        existing = dictionary._read(default_path)
        dictionary._write(default_path, dictionary.merge(existing, current))
    config.clear_dict_dir()
    # We do NOT delete the file in the user's chosen folder on reset - it's in
    # their own directory and may be shared with another machine.
    return _settings_payload()


# Static frontend (mounted last so /api/* wins).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
