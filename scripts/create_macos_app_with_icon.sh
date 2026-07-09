#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Content Image Automation"
APP_DIR="$PROJECT_ROOT/$APP_NAME.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
RES_DIR="$APP_DIR/Contents/Resources"

mkdir -p "$MACOS_DIR" "$RES_DIR"

# Copy app icon if available.
if [ -f "$PROJECT_ROOT/AppIcon.icns" ]; then
  cp "$PROJECT_ROOT/AppIcon.icns" "$RES_DIR/AppIcon.icns"
elif [ -f "$PROJECT_ROOT/resources/AppIcon.icns" ]; then
  cp "$PROJECT_ROOT/resources/AppIcon.icns" "$RES_DIR/AppIcon.icns"
elif [ -f "$PROJECT_ROOT/scripts/AppIcon.icns" ]; then
  cp "$PROJECT_ROOT/scripts/AppIcon.icns" "$RES_DIR/AppIcon.icns"
fi

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>CFBundleName</key>
    <string>Content Image Automation</string>
    <key>CFBundleDisplayName</key>
    <string>Content Image Automation</string>
    <key>CFBundleIdentifier</key>
    <string>local.content-image-automation</string>
    <key>CFBundleVersion</key>
    <string>0.1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleExecutable</key>
    <string>run</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
  </dict>
</plist>
PLIST

cat > "$MACOS_DIR/run" <<'RUNNER'
#!/bin/bash
set -e

APP_MACOS_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$APP_MACOS_DIR/../../.." && pwd)"

cd "$PROJECT_ROOT"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

exec "$PYTHON" "scripts/image_generator_ui.py"
RUNNER

chmod +x "$MACOS_DIR/run"

# Touch app bundle so Finder refreshes icon metadata.
touch "$APP_DIR"

echo "App erstellt:"
echo "$APP_DIR"
echo ""
echo "Du kannst sie jetzt im Finder doppelklicken."
