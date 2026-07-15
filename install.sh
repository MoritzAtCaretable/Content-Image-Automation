#!/bin/bash
#
# install.sh — Einrichtung von "Content Image Automation" auf dem Mac.
#
# Benutzung: Doppelklick geht bei .sh nicht direkt — stattdessen im Terminal:
#     cd <projektordner>
#     chmod +x install.sh
#     ./install.sh
#
# Richtet alles ein: Homebrew (falls nötig), Python, git, venv, Pakete,
# .env-Vorlage und die "Content Image Automation.app". Danach nur nach einem
# Update mit neuen Paketen erneut nötig.

set -e
cd "$(dirname "$0")"
echo "════════════════════════════════════════"
echo "  Content Image Automation — Einrichtung (macOS)"
echo "════════════════════════════════════════"

# macOS schützt Schreibtisch/Dokumente/Downloads/iCloud (TCC). Eine per Finder
# gestartete, unsignierte App darf dort NICHT auf ihre eigenen Dateien zugreifen
# und schließt sich beim Doppelklick sofort. Wir merken uns das und warnen am Ende.
PROTECTED_LOCATION=""
case "$PWD/" in
  "$HOME/Desktop/"*|"$HOME/Documents/"*|"$HOME/Downloads/"*|"$HOME/Library/Mobile Documents/"*)
    PROTECTED_LOCATION="$PWD"
    echo ""
    echo "⚠️  ACHTUNG: Dieser Ordner liegt in einem von macOS geschützten Bereich"
    echo "   (Schreibtisch/Dokumente/Downloads/iCloud). Die App startet dann per"
    echo "   Doppelklick evtl. nicht. Empfehlung: Ordner nach ~/Applications oder"
    echo "   in den Benutzerordner (~/) verschieben. Details am Ende dieser Ausgabe."
    echo "";;
esac

# ─────────────────────────────────────────────
# HIER ANPASSEN, falls dein Repo anders heißt:
# ─────────────────────────────────────────────
REPO_URL="https://github.com/MoritzAtCaretable/Content-Image-Automation.git"

# 1. Homebrew
if ! command -v brew >/dev/null 2>&1; then
    echo "→ Homebrew wird installiert (einmalig)…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"; fi
    if [ -x /usr/local/bin/brew ]; then eval "$(/usr/local/bin/brew shellenv)"; fi
else
    echo "✓ Homebrew vorhanden"
fi

# 2. Python & git
echo "→ Python & git prüfen/installieren…"
brew list python >/dev/null 2>&1 || brew install python
command -v git >/dev/null 2>&1 || brew install git
echo "✓ Python: $(python3 --version)"
echo "✓ git: $(git --version)"

# 2b. Falls dieser Ordner KEIN Git-Checkout ist (ZIP-Download), nachträglich zu
#     einem machen — dann funktioniert der Update-Button. Es wird NICHTS gelöscht:
#     nur der .git-Ordner wird "aufgepfropft". .env & secrets/ bleiben unangetastet
#     (Git ignoriert sie ohnehin).
if [ ! -d ".git" ]; then
    echo "→ Kein Git-Checkout erkannt (vermutlich ZIP). Richte Git-Verbindung ein…"
    TMP_CLONE="$(mktemp -d)"
    if git clone --depth 1 "$REPO_URL" "$TMP_CLONE/repo" >/dev/null 2>&1; then
        mv "$TMP_CLONE/repo/.git" "./.git"
        rm -rf "$TMP_CLONE"
        git reset --hard HEAD >/dev/null 2>&1 || true
        echo "✓ Git-Verbindung hergestellt — Update-Button ist jetzt aktiv."
    else
        rm -rf "$TMP_CLONE"
        echo "⚠ Konnte Git-Verbindung nicht herstellen (kein Zugriff/Netz?)."
        echo "  Läuft trotzdem — nur der Update-Button bleibt inaktiv."
    fi
fi

# 3. venv + Pakete
if [ ! -d ".venv" ]; then
    echo "→ Virtuelle Umgebung anlegen…"
    python3 -m venv .venv
fi
echo "→ Pakete installieren…"
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# 4. .env & secrets vorbereiten
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "→ .env aus Vorlage erstellt — bitte Keys eintragen!"
fi
mkdir -p secrets references outputs

# 5. App bauen
echo "→ 'Content Image Automation.app' bauen…"
bash scripts/create_macos_app_with_icon.sh

echo ""
echo "════════════════════════════════════════"
echo "  ✅ Fertig!"
echo "════════════════════════════════════════"
echo "  Nächste Schritte:"
echo "  1. .env öffnen und GEMINI_API_KEY + GOOGLE_SHEET_ID eintragen"
echo "  2. service_account.json in den Ordner 'secrets/' legen"
echo "  3. Google Sheet mit der Service-Account-E-Mail als Bearbeiter teilen"
echo "  4. 'Content Image Automation.app' per Doppelklick starten"
echo "════════════════════════════════════════"

if [ -n "$PROTECTED_LOCATION" ]; then
    echo ""
    echo "⚠️  WICHTIG — geschützter Ort erkannt:"
    echo "   $PROTECTED_LOCATION"
    echo ""
    echo "   macOS lässt die App hier per Doppelklick evtl. NICHT auf ihre Dateien"
    echo "   zugreifen (sie öffnet und schließt sich sofort). Zwei Lösungen:"
    echo ""
    echo "   A) EMPFOHLEN — Ordner verschieben (danach funktioniert alles ohne"
    echo "      Extra-Rechte). Im Terminal z. B.:"
    echo "         mkdir -p ~/Applications"
    echo "         mv \"$PROTECTED_LOCATION\" ~/Applications/"
    echo "         cd ~/Applications/$(basename "$PROTECTED_LOCATION")"
    echo "         ./install.sh"
    echo ""
    echo "   B) ODER der App Zugriff geben: Systemeinstellungen → Datenschutz &"
    echo "      Sicherheit → Festplattenvollzugriff → '+' → diese App auswählen"
    echo "      und aktivieren. Nach jedem Neu-Bauen ggf. erneut nötig."
    echo "════════════════════════════════════════"
fi
