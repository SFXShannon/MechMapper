@echo off
rem Builds dist\MW5_MECHMAPPER.exe with PyInstaller.
setlocal
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1 || python -m pip install pyinstaller || goto :fail

set ICON_ARGS=
if exist mech_mapper.ico set ICON_ARGS=--icon mech_mapper.ico --add-data "mech_mapper.ico;."
if not exist mech_mapper.ico echo Note: mech_mapper.ico not found - building without a custom icon.

python -m PyInstaller --noconfirm --onefile --windowed --uac-admin ^
    --add-data "vendor;vendor" --collect-all vgamepad %ICON_ARGS% ^
    MW5_MECHMAPPER.py || goto :fail

echo.
echo Built: %~dp0dist\MW5_MECHMAPPER.exe
exit /b 0

:fail
echo Build failed.
exit /b 1
