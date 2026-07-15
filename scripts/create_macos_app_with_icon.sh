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
# Launcher für "Content Image Automation.app".
# Startet die GUI mit der venv-Python (absoluter Pfad). Fehler landen in einem
# Log AUSSERHALB des Projektordners (der kann z. B. im Schreibtisch durch macOS
# gesperrt sein) und werden als Dialog gezeigt — die App schließt sich also nie
# mehr stumm.

APP_MACOS_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$APP_MACOS_DIR/../../.." && pwd)"

LOGDIR="$HOME/Library/Logs"
mkdir -p "$LOGDIR" 2>/dev/null || LOGDIR="/tmp"
LOGFILE="$LOGDIR/ContentImageAutomation.log"

PYTHON="$PROJECT_ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || echo /usr/bin/python3)"

cd "$PROJECT_ROOT" 2>/dev/null

if "$PYTHON" "$PROJECT_ROOT/scripts/image_generator_ui.py" 2>"$LOGFILE"; then
  exit 0
fi

# Fehlstart: Ursache aus dem Log lesen und passend melden. Dialogtexte bewusst
# in reinem ASCII — beim Finder-Start ist das Locale evtl. nicht UTF-8, dann
# wuerden Umlaute/Sonderzeichen den AppleScript-Parser sprengen.
ERR="$(tail -c 1400 "$LOGFILE" 2>/dev/null)"
if printf '%s' "$ERR" | grep -qiE "not permitted|PermissionError"; then
  osascript -e 'display dialog "Zugriff verweigert: Content Image Automation darf nicht auf seinen Projektordner zugreifen. macOS blockiert Apps aus geschuetzten Ordnern (Schreibtisch, Dokumente, Downloads, iCloud). Loesung A (empfohlen): Projektordner an einen ungeschuetzten Ort verschieben (z.B. ~/Applications) und die App dort per install.sh neu bauen. Loesung B: Systemeinstellungen - Datenschutz und Sicherheit - Festplattenvollzugriff - diese App hinzufuegen und aktivieren, dann neu starten." buttons {"OK"} default button "OK" with icon caution with title "Content Image Automation - Zugriff verweigert"'
else
  osascript -e "display dialog \"Content Image Automation konnte nicht starten. Details im Log: $LOGFILE\" buttons {\"OK\"} default button \"OK\" with icon stop with title \"Content Image Automation\""
fi
exit 1
RUNNER

chmod +x "$MACOS_DIR/run"

# Quarantäne-Flag entfernen und bei LaunchServices neu registrieren (hilft nach
# einem Rebuild, dass Finder das aktuelle Bundle nimmt).
xattr -cr "$APP_DIR" 2>/dev/null || true
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APP_DIR" >/dev/null 2>&1 || true

# Touch app bundle so Finder refreshes icon metadata.
touch "$APP_DIR"

echo "App erstellt:"
echo "$APP_DIR"
echo ""
echo "Du kannst sie jetzt im Finder doppelklicken."
