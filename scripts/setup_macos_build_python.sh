#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_VERSION="3.13.13"
PYTHON_PKG_NAME="python-$PYTHON_VERSION-macos11.pkg"
PYTHON_URL="https://www.python.org/ftp/python/$PYTHON_VERSION/$PYTHON_PKG_NAME"
PYTHON_SHA256="a909cb655af5db67d5a90b3603437a1d58bec3446d624e4034e278ac62023cc9"
TOOLS_ROOT="$PROJECT_ROOT/.build-tools/python-$PYTHON_VERSION"
PKG_PATH="$TOOLS_ROOT/$PYTHON_PKG_NAME"
EXPANDED_DIR="$TOOLS_ROOT/expanded"
RUNTIME_PARENT="$TOOLS_ROOT/runtime"
FRAMEWORK="$RUNTIME_PARENT/Python.framework"
BASE_PYTHON="$FRAMEWORK/Versions/3.13/bin/python3.13"
BUILD_VENV="$PROJECT_ROOT/.venv-build-3.13"

mkdir -p "$TOOLS_ROOT"

if [ ! -f "$PKG_PATH" ]; then
    echo "→ Offizielles Python $PYTHON_VERSION von python.org laden …"
    curl -fL "$PYTHON_URL" -o "$PKG_PATH"
fi

ACTUAL_SHA256="$(shasum -a 256 "$PKG_PATH" | awk '{print $1}')"
if [ "$ACTUAL_SHA256" != "$PYTHON_SHA256" ]; then
    echo "Fehler: SHA-256 des Python-Installers stimmt nicht."
    echo "Erwartet: $PYTHON_SHA256"
    echo "Erhalten: $ACTUAL_SHA256"
    exit 1
fi

if [ ! -x "$BASE_PYTHON" ]; then
    echo "→ Kompatible Python-Laufzeit lokal extrahieren …"
    rm -rf "$EXPANDED_DIR" "$RUNTIME_PARENT"
    pkgutil --expand-full "$PKG_PATH" "$EXPANDED_DIR"
    mkdir -p "$RUNTIME_PARENT"
    ln -s "$EXPANDED_DIR/Python_Framework.pkg/Payload" "$FRAMEWORK"
fi

export DYLD_FRAMEWORK_PATH="$RUNTIME_PARENT:$FRAMEWORK/Versions/3.13/Frameworks"
export DYLD_LIBRARY_PATH="$FRAMEWORK/Versions/3.13:$FRAMEWORK/Versions/3.13/lib"
export PYTHONHOME="$FRAMEWORK/Versions/3.13"

if [ ! -x "$BUILD_VENV/bin/python" ]; then
    echo "→ Isolierte Python-3.13-Buildumgebung erstellen …"
    "$BASE_PYTHON" -m venv "$BUILD_VENV"
fi

unset PYTHONHOME

echo "→ App- und Build-Abhängigkeiten installieren …"
"$BUILD_VENV/bin/python" -m pip install --upgrade pip
"$BUILD_VENV/bin/python" -m pip install \
    -c "$PROJECT_ROOT/requirements-macos-build.txt" \
    -r "$PROJECT_ROOT/requirements.txt" \
    -r "$PROJECT_ROOT/requirements-build.txt"

echo ""
echo "Build-Python bereit:"
"$BUILD_VENV/bin/python" --version
vtool -show-build "$FRAMEWORK/Versions/3.13/Python" | \
    sed -n '/platform MACOS/,+2p'
