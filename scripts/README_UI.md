# Content Image Automation UI

Kleine macOS-GUI für dein bestehendes `generate_images.py`.

## Dateien

- `image_generator_ui.py` — eigentliche GUI
- `Content Image Automation.command` — Doppelklick-Launcher
- `create_macos_app.sh` — erstellt eine `.app`

## Installation

Kopiere `image_generator_ui.py` in deinen Projektordner:

```bash
cp image_generator_ui.py scripts/image_generator_ui.py
```

Kopiere den Launcher in den Projektordner:

```bash
cp "Content Image Automation.command" .
chmod +x "Content Image Automation.command"
```

Dann kannst du `Content Image Automation.command` im Finder doppelklicken.

## Optional: echte .app erstellen

```bash
cp create_macos_app.sh scripts/create_macos_app.sh
chmod +x scripts/create_macos_app.sh
bash scripts/create_macos_app.sh
```

Danach liegt im Projektordner:

```text
Content Image Automation.app
```

## Funktionen

- Generierungsprozess starten
- Prozess stoppen
- Log im Fenster anzeigen
- Google Sheet öffnen
- Outputs öffnen
- References öffnen
- Projektordner öffnen
- Statuscheck für `.env`, `.venv`, Service-Account-Datei und `generate_images.py`
