"""Stable entry point for the self-contained macOS application.

The Python runtime and third-party packages are frozen into the .app.  The
actual project stays as a writable Git checkout in Application Support so the
existing ``git pull`` updater can continue to update the Python sources.
"""

from __future__ import annotations

import importlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import traceback
from pathlib import Path


APP_SUPPORT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Caretable"
    / "Content Image Automation"
)
PROJECT_ROOT = APP_SUPPORT / "project"
LOG_FILE = Path.home() / "Library" / "Logs" / "ContentImageAutomation.log"
BUNDLED_PROJECT_VERSION = "0.2.0"
VERSION_MARKER = ".caretable_bundle_version"
PRESERVED_PROJECT_ENTRIES = {
    ".env", ".git", "secrets", "outputs", "references",
}


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def _install_project_if_needed() -> None:
    seed = _bundle_root() / "project_seed"
    if not seed.is_dir():
        raise RuntimeError(f"Mitgeliefertes Projekt nicht gefunden: {seed}")
    marker = PROJECT_ROOT / VERSION_MARKER
    current_version = ""
    try:
        current_version = marker.read_text(encoding="utf-8").strip()
    except OSError:
        pass

    if not PROJECT_ROOT.exists():
        PROJECT_ROOT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed, PROJECT_ROOT)
    elif current_version != BUNDLED_PROJECT_VERSION:
        # Eine neue DMG muss auch bestehende Installationen aktualisieren. Nur
        # Programmdateien werden ueberlagert; lokale Konfiguration, Keys,
        # Git-Metadaten, Referenzen und Ergebnisse bleiben erhalten.
        for source in seed.iterdir():
            if source.name in PRESERVED_PROJECT_ENTRIES:
                continue
            destination = PROJECT_ROOT / source.name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)

    marker.write_text(BUNDLED_PROJECT_VERSION + "\n", encoding="utf-8")


def _ensure_writable_directories() -> None:
    for directory_name in ("outputs", "references"):
        (PROJECT_ROOT / directory_name).mkdir(parents=True, exist_ok=True)


def main() -> None:
    _install_project_if_needed()
    _ensure_writable_directories()
    os.environ["CIA_PROJECT_ROOT"] = str(PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    scripts_dir = PROJECT_ROOT / "scripts"
    sys.path.insert(0, str(scripts_dir))

    if "--self-test" in sys.argv:
        required = [
            PROJECT_ROOT / ".env",
            PROJECT_ROOT / "scripts" / "image_generator_ui.py",
            PROJECT_ROOT / "scripts" / "generate_images.py",
            PROJECT_ROOT / "scripts" / "restoration_defaults.py",
            PROJECT_ROOT / "secrets" / "service_account.json",
            PROJECT_ROOT / ".git",
            PROJECT_ROOT / "outputs",
            PROJECT_ROOT / "references",
        ]
        missing = [str(path) for path in required if not path.exists()]
        import_errors = {}
        for module_name in (
            "customtkinter",
            "gspread",
            "google.auth",
            "google.genai",
            "pandas",
            "PIL.Image",
            "image_generator_ui",
            "generate_images",
            "sheet_admin",
            "restoration_defaults",
        ):
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                import_errors[module_name] = f"{type(exc).__name__}: {exc}"
        print(json.dumps({
            "project_root": str(PROJECT_ROOT),
            "missing": missing,
            "import_errors": import_errors,
            "ok": not missing and not import_errors,
        }))
        raise SystemExit(0 if not missing and not import_errors else 1)

    if "--run-pipeline" in sys.argv:
        sys.argv = [str(scripts_dir / "generate_images.py")]
        runpy.run_path(str(scripts_dir / "generate_images.py"), run_name="__main__")
        return

    sys.argv = [str(scripts_dir / "image_generator_ui.py")]
    runpy.run_path(str(scripts_dir / "image_generator_ui.py"), run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(traceback.format_exc(), encoding="utf-8")
        # Opening the log in the default text editor is more reliable than an
        # AppleScript dialog and still makes GUI-launch failures immediately
        # visible to non-technical users.
        subprocess.run(["open", str(LOG_FILE)], check=False)
        raise SystemExit(1) from exc
