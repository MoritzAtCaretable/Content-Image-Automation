#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="$PROJECT_ROOT/build/macos-arm64"
SEED_DIR="$BUILD_ROOT/project_seed"
PYI_WORK="$BUILD_ROOT/pyinstaller"
PYI_CACHE="$BUILD_ROOT/pyinstaller-cache"
DMG_STAGE="$BUILD_ROOT/dmg-stage"
DIST_DIR="$PROJECT_ROOT/dist/macos-arm64"
APP_PATH="$DIST_DIR/Content Image Automation.app"
DMG_PATH="$DIST_DIR/Content-Image-Automation-arm64.dmg"

cd "$PROJECT_ROOT"

if [ "$(uname -m)" != "arm64" ]; then
    echo "Fehler: Dieser Build muss auf einem ARM-Mac ausgeführt werden."
    exit 1
fi

if [ ! -f ".env" ] || [ ! -f "secrets/service_account.json" ]; then
    echo "Fehler: .env und secrets/service_account.json müssen für den Firmen-Build vorhanden sein."
    exit 1
fi

if ! .venv/bin/python -m PyInstaller --version >/dev/null 2>&1; then
    echo "Fehler: PyInstaller fehlt. Installiere zuerst requirements-build.txt in .venv."
    exit 1
fi

echo "→ Alte ARM-Build-Artefakte entfernen …"
rm -rf "$BUILD_ROOT" "$DIST_DIR"
mkdir -p "$SEED_DIR" "$DIST_DIR"

echo "→ Beschreibbaren Projekt-Seed inklusive Git, Konfiguration und Keys erstellen …"
rsync -a \
    --exclude '.venv/' \
    --exclude 'build/' \
    --exclude 'dist/' \
    --exclude 'outputs/' \
    --exclude 'Content Image Automation.app/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    "$PROJECT_ROOT/" "$SEED_DIR/"
mkdir -p "$SEED_DIR/outputs" "$SEED_DIR/references"

echo "→ Eigenständige ARM-App bauen …"
export PYINSTALLER_CONFIG_DIR="$PYI_CACHE"
.venv/bin/python -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$DIST_DIR" \
    --workpath "$PYI_WORK" \
    "$PROJECT_ROOT/macos_arm64.spec"

echo "→ Drag-&-Drop-DMG erstellen …"
mkdir -p "$DMG_STAGE"
ditto "$APP_PATH" "$DMG_STAGE/Content Image Automation.app"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create \
    -volname "Content Image Automation" \
    -srcfolder "$DMG_STAGE" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

echo "→ Ergebnis prüfen …"
plutil -lint "$APP_PATH/Contents/Info.plist"
file "$APP_PATH/Contents/MacOS/Content Image Automation"
hdiutil verify "$DMG_PATH"

echo ""
echo "Fertig:"
echo "$DMG_PATH"
