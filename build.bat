@echo off
rem Builds GameCapture into dist\GameCapture\ (GameCapture.exe + GameCaptureCLI.exe).
rem
rem   build.bat               set up .venv if needed, test, build
rem   build.bat --skip-tests  build without running the tests
rem   build.bat --zip         also make dist\GameCapture-<version>.zip to share (version: Core\version.py)
rem   build.bat --clean       delete build\ and dist\ first
setlocal EnableExtensions
cd /d "%~dp0"

set "SKIP_TESTS="
set "MAKE_ZIP="
set "CLEAN="
:args
if "%~1"=="" goto args_done
if /i "%~1"=="--skip-tests" set "SKIP_TESTS=1"
if /i "%~1"=="--zip" set "MAKE_ZIP=1"
if /i "%~1"=="--clean" set "CLEAN=1"
shift
goto args
:args_done

rem ---------------------------------------------------------------- Python + packages
if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating virtual environment .venv ...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto fail_venv
) else (
    echo [1/5] Using existing .venv
)
set "PY=%CD%\.venv\Scripts\python.exe"

echo [2/5] Installing requirements + PyInstaller ...
"%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%PY%" -m pip install -r requirements.txt pyinstaller --quiet --disable-pip-version-check
if errorlevel 1 goto fail

rem ---------------------------------------------------------------- ffmpeg
if not exist "Bin\ffmpeg.exe" (
    echo [3/5] Downloading ffmpeg ...
    "%PY%" Tools\get_ffmpeg.py
    if errorlevel 1 goto fail
) else (
    echo [3/5] ffmpeg found in Bin\
)

rem Settings used to live in config.json next to the code: move them to %%APPDATA%%\GameCapture now,
rem so the built app starts with them (does nothing if they were moved already).
"%PY%" -c "from Core.config import AppConfig; AppConfig.load()" >nul

rem ---------------------------------------------------------------- tests
if defined SKIP_TESTS (
    echo [4/5] Tests skipped
) else (
    echo [4/5] Running tests ...
    "%PY%" -m unittest discover -s Test
    if errorlevel 1 goto fail_tests
)

rem ---------------------------------------------------------------- build
echo [5/5] Building ...
if defined CLEAN (
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
)
if exist "dist\GameCapture\GameCapture.exe" (
    tasklist /fi "imagename eq GameCapture.exe" | find /i "GameCapture.exe" >nul && goto fail_running
)
"%PY%" -m PyInstaller --noconfirm GameCapture.spec
if errorlevel 1 goto fail

copy /y "Tools\Read me first.txt" "dist\GameCapture\" >nul
for /f "delims=" %%v in ('call "%PY%" -c "from Core.version import AppInfo; print(AppInfo.VERSION)"') do set "VERSION=%%v"
set "ZIP=dist\GameCapture-%VERSION%.zip"

if defined MAKE_ZIP (
    echo Zipping ...
    powershell -NoProfile -Command "Compress-Archive -Path 'dist\GameCapture' -DestinationPath '%ZIP%' -Force"
    if errorlevel 1 goto fail
)

echo.
echo Done:  %CD%\dist\GameCapture\GameCapture.exe  (version %VERSION%)
if defined MAKE_ZIP echo Zip:   %CD%\%ZIP%  - ready to send
echo Copy the whole dist\GameCapture folder - the .exe needs the _internal folder next to it.
exit /b 0

:fail_venv
echo.
echo Could not create a virtual environment. Install Python 3.11+ from python.org
echo (tick "Add python.exe to PATH") and run build.bat again.
exit /b 1

:fail_tests
echo.
echo Tests failed - not building. Fix them, or run: build.bat --skip-tests
exit /b 1

:fail_running
echo.
echo GameCapture is running from dist\ - quit it (tray icon ^> Quit) and run build.bat again.
exit /b 1

:fail
echo.
echo Build failed - see the messages above.
exit /b 1
