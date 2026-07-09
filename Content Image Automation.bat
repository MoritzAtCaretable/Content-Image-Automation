@echo off
REM Startet die "Content Image Automation"-Oberflaeche (Windows).
REM Doppelklick genuegt. Fuer eine Desktop-Verknuepfung:
REM   Rechtsklick -> Senden an -> Desktop (Verknuepfung erstellen)
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "scripts\image_generator_ui.py"
) else (
    echo [!] .venv nicht gefunden - bitte zuerst install.bat ausfuehren.
    pause
)
