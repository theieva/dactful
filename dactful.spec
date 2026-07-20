# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Dactful desktop app (macOS).
#
#   .venv/bin/pyinstaller dactful.spec --noconfirm
#
# Produces dist/Dactful.app (windowed, onedir). Unsigned; see SIGNING.md.

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [("static", "static")]
binaries = []
hiddenimports = ["webview.platforms.cocoa", "app.main"]

# The spaCy English model ships as a pip package; spacy.load("en_core_web_sm")
# imports it and reads its dist metadata, so bundle both.
for pkg in ("en_core_web_sm",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += copy_metadata("en_core_web_sm")
datas += copy_metadata("spacy")

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Dactful",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Dactful",
)
app = BUNDLE(
    coll,
    name="Dactful.app",
    icon="assets/dactful.icns",
    bundle_identifier="com.verdantindustries.dactful",
    info_plist={
        "CFBundleName": "Dactful",
        "CFBundleDisplayName": "Dactful",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        # The app talks only to its own 127.0.0.1 server, over plain http.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSHumanReadableCopyright": "Verdant Industries LLC",
    },
)
