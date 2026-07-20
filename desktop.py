#!/usr/bin/env python3
"""
Dactful desktop entry. Runs the local server in a background thread and opens
a native window (pywebview / WKWebView) instead of a browser tab.

    python desktop.py            # native window
    python desktop.py --smoke    # start server, verify it answers, exit (no window)

The server binds 127.0.0.1 on an OS-assigned free port, so a desktop launch
never collides with a `run.py` instance or anything else on port 8000.
Set DACTFUL_PORT to force a specific port.
"""

import os
import socket
import sys
import threading
import time
import urllib.request

HOST = "127.0.0.1"

WINDOW_TITLE = "Dactful"
WINDOW_SIZE = (1200, 850)
WINDOW_MIN_SIZE = (900, 650)


def _free_port() -> int:
    s = socket.socket()
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(port: int):
    import uvicorn

    # Import the app object directly; uvicorn's "app.main:app" string form
    # breaks under PyInstaller's frozen module loader.
    from app.main import app

    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _wait_ready(port: int, timeout: float = 30.0) -> bool:
    url = f"http://{HOST}:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def _smoke_scan(port: int):
    """Run a real analyze call through the server: proves the pattern engine
    and the bundled spaCy model both work inside a frozen build."""
    import json
    import urllib.parse

    body = urllib.parse.urlencode(
        {
            "text": "Contact Maria Alvarez at maria@example.com about the Denver office.",
            "use_ner": "true",
        }
    ).encode()
    req = urllib.request.Request(
        f"http://{HOST}:{port}/api/analyze",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Dactful-App": "1",
            "Origin": f"http://{HOST}:{port}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as e:
        return False, f"analyze call failed: {e}"

    found = json.dumps(payload)
    if "maria@example.com" not in found:
        return False, "pattern engine missed the email"
    if not any(t in found for t in ("PERSON", "COMPANY", "PLACE")):
        return False, "NER produced no suggestions (spaCy model missing from build?)"
    return True, f"server, pattern engine, and spaCy model all answered on {HOST}:{port}"


def main() -> int:
    port = int(os.environ.get("DACTFUL_PORT", "0")) or _free_port()
    server = _start_server(port)

    if not _wait_ready(port):
        print("Dactful could not start its local server.", file=sys.stderr)
        return 1

    if "--smoke" in sys.argv:
        ok, detail = _smoke_scan(port)
        print(f"smoke {'ok' if ok else 'FAILED'}: {detail}")
        server.should_exit = True
        return 0 if ok else 1

    import webview

    # Native save dialog when the page offers a download (redacted .docx, guide).
    webview.settings["ALLOW_DOWNLOADS"] = True

    webview.create_window(
        WINDOW_TITLE,
        f"http://{HOST}:{port}/",
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
    )
    webview.start()

    # Window closed: tell uvicorn to stop; the daemon thread dies with us.
    server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
