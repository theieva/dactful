"""
Word (.docx) surgical text redaction.

Design choice: we do NOT rebuild the document. We open the .docx (a zip of XML
parts), edit only the text nodes in place, and re-zip. Everything else - styles,
tables, images, numbering, formatting - is preserved byte-for-byte because we
never touch it.

The hard problem: Word splits a single visible string across many
<w:r> runs unpredictably. "Acme Corp" may live in three runs, or be split
mid-word. Naive per-run find-and-replace misses these and fails SILENTLY, which
is the worst possible failure for a redaction tool.

Our fix: for each paragraph (<w:p>) we concatenate its own text nodes (<w:t>)
into one string, run the matcher against that string, then distribute the result
back across the text nodes - placing each replacement in the node that owns the
match's start (so the tag inherits that run's formatting).

This single per-paragraph mechanism covers every surface that contains
paragraphs: body, tables (incl. nested), headers, footers, footnotes, endnotes,
comments, and text boxes. Document properties are handled separately.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Callable, Dict, List, Tuple

from lxml import etree

from .matching import Match, Rule, find_matches

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_T = f"{{{W}}}t"
_P = f"{{{W}}}p"

# --- Untrusted-input hardening ---------------------------------------------
# A .docx is a zip of XML, and both the uploaded document and the "finished"
# doc coming back from an LLM are untrusted. Two classes of malicious file are
# defended here:
#   1. XML entity-expansion / external-entity attacks (billion laughs, XXE) -
#      we parse with entity resolution and network access disabled.
#   2. Zip bombs / malformed member paths - we cap entry count and total
#      uncompressed size, and reject absolute or traversal member names,
#      before reading anything into memory.

MAX_ZIP_ENTRIES = 4096
MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024  # 300 MB


class UnsafeDocxError(Exception):
    """Raised when an uploaded file looks malicious rather than merely invalid."""


def _make_parser() -> etree.XMLParser:
    # A fresh parser per call: lxml parsers are not thread-safe and FastAPI's
    # sync endpoints run in a threadpool.
    return etree.XMLParser(
        resolve_entities=False,  # no entity expansion (billion laughs)
        no_network=True,         # no external entity fetches
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def _fromstring(data: bytes) -> etree._Element:
    return etree.fromstring(data, _make_parser())


def _validate_zip(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise UnsafeDocxError("This file has too many internal parts to be a normal document.")
    total = 0
    for info in infos:
        name = info.filename
        parts = name.replace("\\", "/").split("/")
        if name.startswith("/") or name.startswith("\\") or ".." in parts:
            raise UnsafeDocxError("This file contains an unsafe internal path.")
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise UnsafeDocxError("This file is too large to process safely.")

# XML parts we sweep for redactable paragraph text.
_CONTENT_PART_RE = re.compile(
    r"^word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$"
)

# Document-property fields that can leak identity (author, company, etc.).
_PROP_PARTS = ("docProps/core.xml", "docProps/app.xml")

# The only property fields that carry user identity. Everything else in these
# parts is Word boilerplate ("Normal.dotm", "Microsoft Word", version numbers)
# and must NOT be treated as document text - it pollutes detection and context.
_IDENTITY_FIELDS = {
    "creator", "lastModifiedBy", "title", "subject",
    "description", "keywords", "Company", "Manager",
}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _own_text_nodes(p: etree._Element) -> List[etree._Element]:
    """Return the <w:t> nodes belonging to THIS paragraph, excluding any that
    live inside a nested paragraph (e.g. a text box's own <w:p>). Every <w:t> is
    owned by exactly one <w:p>, so sweeping all paragraphs covers each node once.
    """
    nodes: List[etree._Element] = []
    for t in p.iter(_T):
        anc = t.getparent()
        nested = False
        while anc is not None and anc is not p:
            if anc.tag == _P:
                nested = True
                break
            anc = anc.getparent()
        if not nested:
            nodes.append(t)
    return nodes


def _rewrite_paragraph(p: etree._Element, rules: List[Rule]) -> int:
    """Redact one paragraph. Returns the number of replacements applied."""
    nodes = _own_text_nodes(p)
    if not nodes:
        return 0

    seg_text = [(n.text or "") for n in nodes]
    full = "".join(seg_text)
    if not full:
        return 0

    matches = find_matches(full, rules)
    if not matches:
        return 0

    # Segment start offsets within `full`.
    starts: List[int] = []
    off = 0
    for txt in seg_text:
        starts.append(off)
        off += len(txt)
    total = off

    outputs = [""] * len(nodes)

    def seg_of(pos: int) -> int:
        # Last segment whose start is <= pos (segments are contiguous).
        lo, hi = 0, len(starts) - 1
        ans = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if starts[mid] <= pos:
                ans = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return ans

    def emit_original(a: int, b: int) -> None:
        # Copy original text[a:b] back into its owning segments (preserves the
        # per-run formatting of untouched text).
        while a < b:
            si = seg_of(a)
            seg_end = starts[si] + len(seg_text[si])
            chunk_end = min(b, seg_end)
            outputs[si] += seg_text[si][a - starts[si]: chunk_end - starts[si]]
            a = chunk_end

    cursor = 0
    for m in sorted(matches, key=lambda x: x.start):
        emit_original(cursor, m.start)
        outputs[seg_of(m.start)] += m.rule.replacement  # tag adopts start run's style
        cursor = m.end
    emit_original(cursor, total)

    for node, new_text in zip(nodes, outputs):
        node.text = new_text
        # Preserve significant whitespace so leading/trailing spaces survive.
        if new_text != new_text.strip():
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

    return len(matches)


def _redact_xml_part(data: bytes, rules: List[Rule]) -> Tuple[bytes, int]:
    """Redact every paragraph in one XML part."""
    root = _fromstring(data)
    count = 0
    for p in root.iter(_P):
        count += _rewrite_paragraph(p, rules)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), count


def _redact_props_part(data: bytes, rules: List[Rule]) -> Tuple[bytes, int]:
    """Redact document-property text (author, company, title, etc.).

    These are plain single text nodes, not run-fragmented, so a direct text
    replacement is correct.
    """
    root = _fromstring(data)
    count = 0
    for el in root.iter():
        if el.text and el.text.strip() and _local(el.tag) in _IDENTITY_FIELDS:
            matches = find_matches(el.text, rules)
            if matches:
                out: List[str] = []
                pos = 0
                for m in sorted(matches, key=lambda x: x.start):
                    out.append(el.text[pos:m.start])
                    out.append(m.rule.replacement)
                    pos = m.end
                out.append(el.text[pos:])
                el.text = "".join(out)
                count += len(matches)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), count


def redact_docx(input_path: str, output_path: str, rules: List[Rule]) -> int:
    """Redact a .docx in place across all surfaces. Returns total replacements."""
    total = 0
    with zipfile.ZipFile(input_path, "r") as zin:
        _validate_zip(zin)
        items = zin.infolist()
        blobs: Dict[str, bytes] = {i.filename: zin.read(i.filename) for i in items}

    for name in list(blobs):
        if _CONTENT_PART_RE.match(name):
            blobs[name], c = _redact_xml_part(blobs[name], rules)
            total += c
        elif name in _PROP_PARTS:
            blobs[name], c = _redact_props_part(blobs[name], rules)
            total += c

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in items:
            zout.writestr(item, blobs[item.filename])

    return total


def extract_text(input_path: str) -> str:
    """Extract all human-visible + metadata text from a .docx, one paragraph per
    line, including hidden surfaces. Used for detection and for the post-redaction
    validation sweep."""
    chunks: List[str] = []
    with zipfile.ZipFile(input_path, "r") as z:
        _validate_zip(z)
        for name in z.namelist():
            if _CONTENT_PART_RE.match(name):
                root = _fromstring(z.read(name))
                for p in root.iter(_P):
                    nodes = _own_text_nodes(p)
                    line = "".join((n.text or "") for n in nodes)
                    if line:
                        chunks.append(line)
            elif name in _PROP_PARTS:
                root = _fromstring(z.read(name))
                for el in root.iter():
                    if el.text and el.text.strip() and _local(el.tag) in _IDENTITY_FIELDS:
                        chunks.append(el.text)
    return "\n".join(chunks)


def find_all(input_path: str, rules: List[Rule]) -> List[Match]:
    """Run the matcher across the whole extracted document (for analysis)."""
    return find_matches(extract_text(input_path), rules)
