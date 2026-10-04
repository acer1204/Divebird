@echo off
rem Divebird launcher for Windows - just double-click this file.
rem (Kept ASCII-only on purpose: cmd.exe parses .bat files with the system code page.)
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "GUI=%ROOT%.venv\Scripts\divebird-gui.exe"
set "EXE=%ROOT%dist\Divebird\Divebird.exe"

rem 1) Source checkout with the project-local environment: always runs the latest code.
if exist "%GUI%" goto run_source
if exist "%PY%" goto make_gui

rem 2) Packaged build next to this file: needs nothing installed.
if exist "%EXE%" goto run_exe

rem 3) First run: build the self-contained environment inside this folder (.runtime\ and .venv\).
echo [Divebird] First run: setting up the self-contained environment. This can take a few minutes...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\setup.ps1"
if errorlevel 1 goto setup_failed
if not exist "%PY%" goto setup_failed

:make_gui
rem divebird-gui.exe = CPython's windowless venv launcher (uv's pythonw.exe would open a console window).
"%PY%" "%ROOT%scripts\win_gui_launcher.py" >nul
if not exist "%GUI%" goto setup_failed

:run_source
rem "start" returns immediately, so this window closes and Divebird runs without a console.
start "" "%GUI%" -m divebird %*
exit /b 0

:run_exe
start "" "%EXE%" %*
exit /b 0

:setup_failed
echo.
echo [Divebird] Setup failed. Check your network connection and run this file again.
pause
exit /b 1
