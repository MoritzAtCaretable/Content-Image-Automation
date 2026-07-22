# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


ROOT = Path.cwd()
SEED = ROOT / "build" / "macos-arm64" / "project_seed"

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

a = Analysis(
    [str(ROOT / "scripts" / "frozen_bootstrap.py")],
    pathex=[str(ROOT / "scripts")],
    binaries=[],
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
