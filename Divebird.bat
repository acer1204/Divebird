@echo off
rem Divebird launcher for Windows - just double-click this file.
rem After the first run, Divebird.exe in this folder starts Divebird without this console window.
rem ".\Divebird.bat /repair" (run in this folder) rebuilds .venv, e.g. after the project folder
rem was moved, renamed or copied.
rem (Kept ASCII-only on purpose: cmd.exe parses .bat files with the system code page.)
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "GUI=%ROOT%.venv\Scripts\divebird-gui.exe"
set "EXE=%ROOT%dist\Divebird\Divebird.exe"

if /i "%~1"=="/repair" goto repair

rem 1) Source checkout with the project-local environment: always runs the latest code.
if exist "%GUI%" goto run_source
rem    .venv without the launcher: an earlier setup did not finish, so finish it.
if exist "%ROOT%.venv" goto update_env

rem 2) Packaged build next to this file: needs nothing installed.
if exist "%EXE%" goto run_exe

rem 3) First run: build the self-contained environment inside this folder (.runtime\ and .venv\).
echo [Divebird] First run: setting up the self-contained environment. This can take a few minutes...
call :setup
set "RC=%errorlevel%"
if not "%RC%"=="0" goto setup_error
goto start_source

:run_source
rem --check exit codes: 0 = ready; 2 = .venv belongs to another folder (project copied);
rem 3 = setup unfinished or out of date (e.g. after git pull). Anything else means the
rem .venv cannot even start Python (project folder moved or renamed).
"%PY%" "%ROOT%scripts\win_gui_launcher.py" --check >nul 2>&1
set "RC=%errorlevel%"
if "%RC%"=="0" goto start_source
if "%RC%"=="3" goto update_env
goto broken_env

:update_env
echo [Divebird] Updating the environment in .venv ...
call :setup
set "RC=%errorlevel%"
if "%RC%"=="0" goto start_source
if "%RC%"=="3" goto setup_error
if not exist "%GUI%" goto setup_error
echo.
echo [Divebird] The update did not finish - see the messages above.
echo Press any key to start Divebird with the current environment anyway, or close this window.
pause >nul

:start_source
if not exist "%GUI%" goto setup_failed
rem "start" returns immediately, so this window closes and Divebird runs without a console.
start "" "%GUI%" -m divebird %*
exit /b 0

:run_exe
start "" "%EXE%" %*
exit /b 0

:broken_env
echo [Divebird] The environment in .venv belongs to another folder
echo (the project folder was probably moved, renamed or copied) and needs to be rebuilt.
echo Press any key to rebuild it now (usually under a minute), or close this window to cancel.
pause >nul

:repair
echo [Divebird] Rebuilding the environment in .venv ...
call :setup -Recreate
set "RC=%errorlevel%"
if not "%RC%"=="0" goto setup_error
if not exist "%GUI%" goto setup_failed
start "" "%GUI%" -m divebird
exit /b 0

:setup
rem setup.ps1 prints its own messages. Exit codes: 3 = another window is already setting up;
rem 4 = a program from .venv (usually Divebird itself) is still running.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\setup.ps1" %*
exit /b %errorlevel%

:setup_error
if "%RC%"=="3" goto setup_blocked
if "%RC%"=="4" goto setup_blocked
goto setup_failed

:setup_blocked
echo.
echo [Divebird] See the message above, then run this file again.
pause
exit /b 1

:setup_failed
echo.
echo [Divebird] Setup did not finish - see the messages above.
echo Check your network connection and run this file again. If it keeps failing,
echo open a terminal in this folder and run:  .\Divebird.bat /repair
pause
exit /b 1
