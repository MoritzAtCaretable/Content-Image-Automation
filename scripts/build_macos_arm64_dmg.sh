#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_VENV="$PROJECT_ROOT/.venv-build-3.13"
BUILD_PYTHON="$BUILD_VENV/bin/python"
PYTHON_RUNTIME="$PROJECT_ROOT/.build-tools/python-3.13.13/runtime"
PYTHON_FRAMEWORK="$PYTHON_RUNTIME/Python.framework"
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

if [ ! -x "$BUILD_PYTHON" ]; then
    echo "Fehler: Kompatibles Build-Python fehlt."
    echo "Führe zuerst scripts/setup_macos_build_python.sh aus."
    exit 1
fi

export DYLD_FRAMEWORK_PATH="$PYTHON_RUNTIME:$PYTHON_FRAMEWORK/Versions/3.13/Frameworks"
export DYLD_LIBRARY_PATH="$PYTHON_FRAMEWORK/Versions/3.13:$PYTHON_FRAMEWORK/Versions/3.13/lib"

if [ "$("$BUILD_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.13" ]; then
    echo "Fehler: Der ARM-Build muss mit der vorbereiteten Python-3.13-Laufzeit erfolgen."
    exit 1
fi

if ! "$BUILD_PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
    echo "Fehler: PyInstaller fehlt in .venv-build-3.13."
    exit 1
fi

echo "→ Alte ARM-Build-Artefakte entfernen …"
rm -rf "$BUILD_ROOT" "$DIST_DIR"
mkdir -p "$SEED_DIR" "$DIST_DIR"

echo "→ Beschreibbaren Projekt-Seed inklusive Git, Konfiguration und Keys erstellen …"
rsync -a \
    --exclude '.venv/' \
    --exclude '.venv-build-3.13/' \
    --exclude '.build-tools/' \
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
"$BUILD_PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$DIST_DIR" \
    --workpath "$PYI_WORK" \
    "$PROJECT_ROOT/macos_arm64.spec"

echo "→ App-Bundle und eingefrorene Laufzeit prüfen …"
plutil -lint "$APP_PATH/Contents/Info.plist"
file "$APP_PATH/Contents/MacOS/Content Image Automation"
"$BUILD_PYTHON" "$PROJECT_ROOT/scripts/check_macos_compatibility.py" \
    "$APP_PATH" "11.0"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

SELF_TEST_HOME="$BUILD_ROOT/self-test-home"
mkdir -p "$SELF_TEST_HOME"
env HOME="$SELF_TEST_HOME" \
    "$APP_PATH/Contents/MacOS/Content Image Automation" --self-test

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

hdiutil verify "$DMG_PATH"

echo ""
echo "Fertig:"
echo "$DMG_PATH"
