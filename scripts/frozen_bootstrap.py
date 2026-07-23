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


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def _install_project_if_needed() -> None:
    seed = _bundle_root() / "project_seed"
    if PROJECT_ROOT.exists():
        return
    if not seed.is_dir():
        raise RuntimeError(f"Mitgeliefertes Projekt nicht gefunden: {seed}")
    PROJECT_ROOT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed, PROJECT_ROOT)


def main() -> None:
    _install_project_if_needed()
    os.environ["CIA_PROJECT_ROOT"] = str(PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    scripts_dir = PROJECT_ROOT / "scripts"
    sys.path.insert(0, str(scripts_dir))

    if "--self-test" in sys.argv:
        required = [
            PROJECT_ROOT / ".env",
            PROJECT_ROOT / "scripts" / "image_generator_ui.py",
            PROJECT_ROOT / "scripts" / "generate_images.py",
            PROJECT_ROOT / "secrets" / "service_account.json",
            PROJECT_ROOT / ".git",
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
