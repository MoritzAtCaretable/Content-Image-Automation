#!/usr/bin/env python3
"""Fail a macOS build when a bundled Mach-O needs a newer macOS version."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def minimum_versions(path: Path) -> list[str]:
    file_result = subprocess.run(
        ["file", str(path)], capture_output=True, text=True, check=False
    )
    if "Mach-O" not in file_result.stdout:
        return []

    result = subprocess.run(
        ["vtool", "-show-build", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    versions = []
    for line in result.stdout.splitlines():
        # ``version`` below the LD tool is the linker version, not a macOS
        # deployment target. Modern ARM binaries expose the latter as ``minos``.
        match = re.match(r"\s+minos\s+(\d+(?:\.\d+)+)\s*$", line)
        if match:
            versions.append(match.group(1))
    return versions


def main() -> int:
    if len(sys.argv) != 3:
        print("Aufruf: check_macos_compatibility.py APP_PATH MAX_VERSION")
        return 2

    app_path = Path(sys.argv[1]).resolve()
    maximum = version_tuple(sys.argv[2])
    incompatible: list[tuple[Path, str]] = []
    checked = 0

    for path in app_path.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        versions = minimum_versions(path)
        if versions:
            checked += 1
        for version in versions:
            if version_tuple(version) > maximum:
                incompatible.append((path, version))

    if incompatible:
        print(f"Fehler: {len(incompatible)} Binärdatei(en) verlangen mehr als macOS {sys.argv[2]}:")
        for path, version in incompatible:
            print(f"  macOS {version}: {path.relative_to(app_path)}")
        return 1

    print(f"✓ {checked} Mach-O-Dateien sind mit macOS {sys.argv[2]} kompatibel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
