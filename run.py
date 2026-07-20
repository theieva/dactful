#!/usr/bin/env python3
"""
Dactful launcher. Starts the local server on 127.0.0.1 and opens a browser.

    python run.py

Nothing binds to a public interface; the app is reachable only from this machine.
"""

import os
import sys
import threading
import webbrowser

HOST = "127.0.0.1"
PORT = int(os.environ.get("DACTFUL_PORT", "8000"))


def _open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}/")


def main():
    try:
        import uvicorn
    except ImportError:
        sys.exit(
            "Dactful needs its dependencies installed first:\n"
            "    pip install -r requirements.txt\n"
        )

    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, _open_browser).start()

    print(f"\n  Dactful is running at  http://{HOST}:{PORT}/")
    print("  Your documents never leave this machine. Press Ctrl+C to stop.\n")

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
