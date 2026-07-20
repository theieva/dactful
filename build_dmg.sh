#!/bin/sh
# Build a distributable Dactful .dmg from the PyInstaller output.
#
#   .venv/bin/pyinstaller dactful.spec --noconfirm
#   ./build_dmg.sh
#
# Produces dist/Dactful-<version>.dmg with the app and an /Applications link.
set -eu

APP="dist/Dactful.app"
VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")
DMG="dist/Dactful-$VERSION.dmg"
STAGE=$(mktemp -d)

[ -d "$APP" ] || { echo "error: $APP not found. Run pyinstaller first." >&2; exit 1; }

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

rm -f "$DMG"
hdiutil create -volname "Dactful" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

echo "Built $DMG"
