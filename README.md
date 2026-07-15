# Content Image Automation

Automatische Bildgenerierung aus einem Google Sheet über **Google Gemini
(Nano Banana 2)** — mit KI-gestützter Motivplanung, automatischer
Qualitätskontrolle (QC) und Ablage der besten Ergebnisse in einem Output-Ordner.

Jobs, Style Presets, Prompt Templates und Inhalte lassen sich **direkt in der
App** anlegen und bearbeiten (Schritt-für-Schritt-Assistenten) — das Google
Sheet bleibt als Speicherort und für Detailanpassungen erhalten.

---

## Voraussetzungen

- **Google Sheet** mit den Tabs `01_Jobs_Batches`, `02_Content_Items`,
  `03_Style_Presets`, `04_Prompt_Templates` (Vorlage: `Image Content Automation.xlsx`)
- **Service-Account-Datei** (`service_account.json`) mit **Bearbeiter**-Zugriff auf das Sheet
- **Gemini API-Key** (https://aistudio.google.com/apikey)
- **GitHub-Zugriff** auf dieses Repo (für den Update-Button)

> Python und Git müssen NICHT vorab installiert sein — die Installer kümmern
> sich bei Bedarf selbst darum.

---

## Installation

### Projekt holen — zwei Wege

**A) Per Git (empfohlen):**
```
git clone https://github.com/MoritzAtCaretable/Content-Image-Automation.git content-image-automation
cd content-image-automation
```

**B) Per ZIP:** Auf GitHub „Code → Download ZIP", entpacken, in den Ordner
wechseln. Der Installer richtet die Git-Verbindung nachträglich selbst ein,
damit der Update-Button trotzdem funktioniert.

### macOS

Im Terminal im Projektordner:
```
chmod +x install.sh
./install.sh
```
Installiert Homebrew, Python und Git (falls nötig), legt die `.venv` an,
installiert alle Pakete und baut `Content Image Automation.app`. Bei einem
ZIP-Download stellt es zusätzlich die Git-Verbindung her (ohne vorhandene
Dateien zu löschen).

### Windows

Doppelklick auf **`install.bat`**.
Fehlt Git, installiert das Skript es per winget — danach das Fenster schließen,
ein **neues** öffnen und `install.bat` erneut ausführen (damit Git im PATH landet).

---

## Konfiguration (beide Systeme)

1. **`.env` ausfüllen** (wurde beim Install aus `.env.example` erstellt):
   ```
   GEMINI_API_KEY=...
   GOOGLE_SHEET_ID=...
   GOOGLE_SERVICE_ACCOUNT_FILE=secrets/service_account.json
   ```
   Die `GOOGLE_SHEET_ID` ist der lange Teil aus der Sheet-URL zwischen `/d/` und `/edit`.
2. **`service_account.json`** in den Ordner **`secrets/`** legen.
3. Das Google Sheet mit der Service-Account-E-Mail (steht in der JSON) als
   **Bearbeiter** teilen.

> ⚠️ `.env` und `secrets/` enthalten Geheimnisse und dürfen **nie** ins Git-Repo.
> Die `.gitignore` schließt sie bereits aus. Der Installer lässt beide beim
> ZIP→Git-Schritt unangetastet.

---

## Starten

- **macOS:** `Content Image Automation.app` (im Projektordner) doppelklicken.
  Ins Dock ziehen für schnellen Zugriff. Beim ersten Start ggf. Rechtsklick →
  „Öffnen" (Gatekeeper).
- **Windows:** `Content Image Automation.bat` doppelklicken. Für eine
  Desktop-Verknüpfung: Rechtsklick → „Senden an" → „Desktop (Verknüpfung erstellen)".

---

## Arbeiten in der App

Die Oberfläche hat oben Tabs:

- **Generieren** — Pipeline starten/stoppen, Live-Log, schnelle Ordner-Buttons.
  Bei `DRY_RUN=true` werden keine echten Bilder erzeugt (Testlauf).
- **Jobs** — alle Aufträge einsehen, Status direkt umschalten (`todo`/`redo`/`done`),
  per Assistent neu anlegen oder bearbeiten. Nur `todo`/`redo` werden verarbeitet.
- **Inhalte** — Content Items (z. B. einzelne Witze) für `content_linked`-Jobs.
- **Styles** — wiederverwendbare Stilregeln (Style Presets).
- **Templates** — Prompt-Vorlagen mit `{platzhaltern}`.

IDs werden automatisch im bestehenden Muster vergeben (`JOB-0001`, `ITEM-0001`,
`STYLE-<KÜRZEL>-001`, `TPL-<KÜRZEL>-001`). Löschen fragt vorher nach und warnt,
wenn ein Style/Template noch von Jobs verwendet wird.

---

## Job-Typen

- **`batch_theme`** — das Skript plant selbst Motive zum Thema (keine Content
  Items nötig). Zielanzahl über `target_count` steuern.
- **`content_linked`** — genau ein Bild pro Content Item (z. B. ein Hintergrund
  pro Witz). Inhalte lassen sich beim Anlegen des Jobs direkt miterfassen.

Pro Motiv erzeugt das Skript mehrere Varianten (`variants_per_item`), bewertet
sie per QC und legt die beste in den Output-Ordner des Jobs.

---

## Updates

In der App auf **„Update suchen"** klicken — das prüft GitHub auf eine neuere
Version. Ist eine verfügbar, fragt die App, ob geladen und **automatisch neu
gestartet** werden soll. Funktioniert bei beiden Installationswegen (Git wie
ZIP), da der Installer immer eine Git-Verbindung herstellt.

Alternativ im Terminal im Projektordner:
```
git pull
```
Bringt ein Update **neue Abhängigkeiten** mit, einmal den Installer erneut
ausführen (`./install.sh` bzw. `install.bat`).

---

## Problembehebung

| Symptom | Lösung |
|---|---|
| `install.sh` startet nicht per Doppelklick | Im Terminal: `chmod +x install.sh && ./install.sh` |
| **App öffnet & schließt sich sofort (Mac)** | macOS-Datenschutz (TCC): Liegt der Ordner in **Schreibtisch/Dokumente/Downloads/iCloud**, darf die per Doppelklick gestartete App nicht auf ihre Dateien zugreifen. **Lösung A (empfohlen):** Ordner nach `~/Applications` oder `~/` verschieben und dort einmal `./install.sh` ausführen. **Lösung B:** Systemeinstellungen → Datenschutz & Sicherheit → **Festplattenvollzugriff** → App hinzufügen & aktivieren. Der Fehler steht im Log `~/Library/Logs/ContentImageAutomation.log`. |
| App startet nicht (Mac), andere Ursache | Log prüfen: `~/Library/Logs/ContentImageAutomation.log`; App neu bauen: `bash scripts/create_macos_app_with_icon.sh` |
| Gemini „API key not valid" | Key prüfen; „Generative Language API" im Google-Cloud-Projekt aktivieren |
| `.env` wird nicht gefunden | Muss exakt `.env` heißen und im Projektordner liegen |
| Zugriff aufs Sheet scheitert | Sheet mit der Service-Account-E-Mail als **Bearbeiter** teilen |
| `service_account.json` nicht gefunden | Datei in `secrets/` legen; `GOOGLE_SERVICE_ACCOUNT_FILE=secrets/service_account.json` |
| Update-Button meldet „nicht mit Git verbunden" | `install.sh`/`install.bat` erneut ausführen — stellt die Git-Verbindung her |

---

## Projektstruktur

| Pfad | Zweck |
|---|---|
| `scripts/generate_images.py` | Pipeline (Kern: Planung, Generierung, QC) |
| `scripts/image_generator_ui.py` | Grafische Oberfläche |
| `scripts/sheet_admin.py` | Lese-/Schreibschicht fürs Google Sheet (Verwaltung in der App) |
| `scripts/create_macos_app_with_icon.sh` | Baut das macOS-App-Bundle |
| `install.sh` / `install.bat` | Einrichtung (macOS / Windows) |
| `Content Image Automation.bat` | Starter (Windows) |
| `requirements.txt` | Python-Abhängigkeiten |
| `.env.example` | Vorlage für die Konfiguration |
| `secrets/` | `service_account.json` (nicht im Git) |
| `outputs/` | generierte Bilder & Metadaten (nicht im Git) |
