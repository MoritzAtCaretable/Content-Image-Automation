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
