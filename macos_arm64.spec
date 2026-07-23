# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller import compat
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


ROOT = Path.cwd()
SEED = ROOT / "build" / "macos-arm64" / "project_seed"
PYTHON_FRAMEWORK = (
    ROOT
    / ".build-tools"
    / "python-3.13.13"
    / "runtime"
    / "Python.framework"
    / "Versions"
    / "3.13"
)

# PyInstaller launches probe processes through Apple's ``arch`` command. It
# forwards DYLD_LIBRARY_PATH itself, but not DYLD_FRAMEWORK_PATH, which is
# required by the locally extracted official Python framework used for this
# reproducible build.
_original_wrap_python = compat.__wrap_python


def _wrap_python_with_framework_path(args, kwargs):
    cmdargs, kwargs = _original_wrap_python(args, kwargs)
    framework_path = os.environ.get("DYLD_FRAMEWORK_PATH")
    if framework_path and cmdargs and cmdargs[0] == "arch":
        cmdargs[2:2] = ["-e", f"DYLD_FRAMEWORK_PATH={framework_path}"]
    return cmdargs, kwargs


compat.__wrap_python = _wrap_python_with_framework_path

hiddenimports = [
    "customtkinter",
    "darkdetect",
    "gspread",
    "pandas",
    "numpy",
    "PIL.Image",
    "requests",
    "dotenv",
    "certifi",
    "pydantic",
]
for package in ("google.auth", "google.genai"):
    hiddenimports += collect_submodules(
        package,
        filter=lambda name: ".tests" not in name and not name.endswith(".tests"),
    )

datas = [(str(SEED), "project_seed")]
datas += collect_data_files("customtkinter")
datas += [
    (str(PYTHON_FRAMEWORK / "Frameworks" / "Tcl.framework" / "Versions" / "8.6" / "Resources" / "Scripts"), "_tcl_data"),
    (str(PYTHON_FRAMEWORK / "Frameworks" / "Tk.framework" / "Versions" / "8.6" / "Resources" / "Scripts"), "_tk_data"),
]
for distribution in (
    "google-genai",
    "google-auth",
    "gspread",
    "pandas",
    "pillow",
    "requests",
    "python-dotenv",
    "customtkinter",
):
    datas += copy_metadata(distribution)

binaries = [
    (str(PYTHON_FRAMEWORK / "lib" / "libcrypto.3.dylib"), "."),
    (str(PYTHON_FRAMEWORK / "lib" / "libssl.3.dylib"), "."),
    (str(PYTHON_FRAMEWORK / "lib" / "libncurses.6.dylib"), "."),
    (str(PYTHON_FRAMEWORK / "Frameworks" / "Tcl.framework" / "Versions" / "8.6" / "Tcl"), "."),
    (str(PYTHON_FRAMEWORK / "Frameworks" / "Tk.framework" / "Versions" / "8.6" / "Tk"), "."),
]

a = Analysis(
    [str(ROOT / "scripts" / "frozen_bootstrap.py")],
    pathex=[str(ROOT / "scripts")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Content Image Automation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Content Image Automation",
)
app = BUNDLE(
    coll,
    name="Content Image Automation.app",
    icon=str(ROOT / "scripts" / "AppIcon.icns"),
    bundle_identifier="de.caretable.content-image-automation",
    version="0.1.0",
    info_plist={
        "CFBundleDisplayName": "Content Image Automation",
        "CFBundleName": "Content Image Automation",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    },
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
