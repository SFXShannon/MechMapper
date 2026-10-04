@echo off
rem Builds dist\MW5_MECHMAPPER.exe (portable) and dist\MechMapper-Setup-<version>.exe.
setlocal
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1 || python -m pip install pyinstaller || goto :fail

set ICON_ARGS=
if exist mech_mapper.ico set ICON_ARGS=--icon mech_mapper.ico --add-data "mech_mapper.ico;."
if not exist mech_mapper.ico echo Note: mech_mapper.ico not found - building without a custom icon.

python -m PyInstaller --noconfirm --onefile --windowed --uac-admin ^
    --add-data "vendor;vendor" --collect-all vgamepad %ICON_ARGS% ^
    --add-data "LICENSE.txt;." --add-data "THIRD_PARTY_NOTICES.txt;." --add-data "mech_mapper.png;." ^
    MW5_MECHMAPPER.py || goto :fail

echo.
echo Built: %~dp0dist\MW5_MECHMAPPER.exe

rem Setup program (needs Inno Setup 6; skipped if it isn't installed)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_installer.ps1" || goto :fail
exit /b 0

:fail
echo Build failed.
exit /b 1
