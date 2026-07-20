"""
On-device OCR for screenshots and images, via Apple's Vision framework.

Runs entirely on the Mac - Vision is part of macOS, so no model download, no
binary to bundle, and (crucially for Dactful) the image never leaves the
machine. Text is extracted, then flows through the exact same detection and
redaction path as pasted text.

Availability is guarded like NER: if the Vision bindings aren't present (e.g. a
non-Mac build), the app degrades gracefully and simply won't offer image input.
"""

from __future__ import annotations

from typing import List, Tuple

_AVAILABLE = None


class OcrUnavailable(Exception):
    pass


class OcrError(Exception):
    pass


def available() -> bool:
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            import Quartz  # noqa: F401
            import Vision  # noqa: F401

            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def image_to_text(path: str) -> str:
    """Return the text found in an image file, in reading order (top→bottom,
    left→right). Raises OcrUnavailable if Vision isn't present, OcrError if the
    file can't be read as an image."""
    if not available():
        raise OcrUnavailable(
            "On-device OCR needs macOS (Apple Vision). This build can't read images."
        )

    import Quartz
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(path)
    ci_image = Quartz.CIImage.imageWithContentsOfURL_(url)
    if ci_image is None:
        raise OcrError("That file couldn't be read as an image.")

    handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(ci_image, {})
    request = Vision.VNRecognizeTextRequest.alloc().init()
    # 0 = accurate (worth the extra time for redaction; 1 = fast).
    request.setRecognitionLevel_(0)
    # Language correction OFF on purpose: it "fixes" text like "jane@acme.example"
    # into "jane @acme.example", which would split an identifier and cause a
    # missed redaction. For a redaction tool, raw fidelity beats prettiness.
    request.setUsesLanguageCorrection_(False)

    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise OcrError("The OCR pass failed on that image.")

    observations = request.results() or []
    items: List[Tuple[float, float, str]] = []
    for obs in observations:
        candidates = obs.topCandidates_(1)
        if candidates and len(candidates):
            box = obs.boundingBox()  # normalized, origin bottom-left
            items.append((box.origin.y, box.origin.x, candidates[0].string()))

    # Vision's origin is bottom-left, so larger y is higher on the page.
    items.sort(key=lambda t: (-t[0], t[1]))
    return "\n".join(text for _, _, text in items)
