# PyInstaller spec for GameCapture - build with build.bat (or: pyinstaller --noconfirm GameCapture.spec)
#
# Output: dist\GameCapture\
#   GameCapture.exe      the desktop app (no console window)
#   GameCaptureCLI.exe   same program with a console: GameCaptureCLI.exe run | check | test | live
#   _internal\           Python, Qt, Assets\ and Bin\ffmpeg.exe
#
# Settings are not in here: they live in %APPDATA%\GameCapture, so a new build never resets them.
# A Bin\ffmpeg.exe placed next to GameCapture.exe overrides the bundled one.
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(SPECPATH)
sys.path.insert(0, ROOT)
from Core.version import AppInfo  # noqa: E402

FFMPEG = os.path.join(ROOT, "Bin", "ffmpeg.exe")
ICON = os.path.join(ROOT, "Assets", "app_icon.ico")

datas = [(os.path.join(ROOT, "Assets"), "Assets")]
binaries = []
if os.path.isfile(FFMPEG):
    binaries.append((FFMPEG, "Bin"))
else:
    print("WARNING: Bin\\ffmpeg.exe not found - the build can't record. Run: python Tools\\get_ffmpeg.py")

# Our own packages: several modules are imported lazily (games, editor, audio sources), so take them all.
hiddenimports = []
for package in ("Capture", "Core", "Games", "Theme", "UI"):
    hiddenimports += collect_submodules(package)

# Windows-only packages with native parts (WASAPI loopback, per-app audio capture, global hotkeys).
for package in ("pyaudiowpatch", "proctap", "pynput"):
    try:
        d, b, h = collect_all(package)
        datas, binaries, hiddenimports = datas + d, binaries + b, hiddenimports + h
    except Exception as exc:  # not installed on this machine: the build still works without that feature
        print(f"WARNING: {package} not collected ({exc})")
hiddenimports += ["pynput.keyboard._win32", "pynput.mouse._win32", "pynput._util.win32", "psutil", "numpy"]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "Test", "PyQt5", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)


def version_info():
    """File properties (right-click the .exe > Details): product name and version."""
    try:   # Windows-only helpers; a problem here must never stop the build
        from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                         VarFileInfo, VarStruct, VSVersionInfo)
        v = AppInfo.version_tuple()
        text = ".".join(map(str, v))
        return VSVersionInfo(
            ffi=FixedFileInfo(filevers=v, prodvers=v),
            kids=[StringFileInfo([StringTable("040904B0", [
                      StringStruct("ProductName", AppInfo.NAME), StringStruct("FileDescription", "Game recorder"),
                      StringStruct("FileVersion", text), StringStruct("ProductVersion", text),
                      StringStruct("OriginalFilename", "GameCapture.exe")])]),
                  VarFileInfo([VarStruct("Translation", [1033, 1200])])])
    except Exception as exc:
        print(f"WARNING: no version details in the .exe ({exc})")
        return None


common = dict(
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX-packed Qt DLLs trip antivirus and start slower
    icon=ICON,
    version=version_info(),
)
app = EXE(pyz, a.scripts, [], name="GameCapture", console=False, **common)
cli = EXE(pyz, a.scripts, [], name="GameCaptureCLI", console=True, **common)

coll = COLLECT(
    app,
    cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GameCapture",
)
