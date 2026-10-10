@echo off
REM ==========================================================================
REM  START.BAT  -  single entry point for running this bot by double-click
REM
REM  NOTE: this file is intentionally ASCII-only. cmd.exe cannot parse
REM  non-ASCII (Persian) batch lines reliably, so every Persian message is
REM  printed by Python instead - see main.py SETUP_HELP and the error
REM  branches below. If you add Persian text here, it will show as garbage.
REM
REM  EVERY change to HOW this project is launched must be recorded in the
REM  CHANGE LOG block below, newest entry first.
REM ==========================================================================
REM
REM  CHANGE LOG (newest first)
REM  --------------------------------------------------------------------------
REM  v3  2026-10-07  main.py now returns meaningful exit codes, so start.bat
REM      can explain the failure instead of dumping a raw Python traceback.
REM        1 bad config      -> Persian setup guide (printed by main.py)
REM        2 already running -> stop the other copy (see below)
REM        3 invalid token   -> Persian "token rejected" note, no traceback
REM        4 network error   -> Persian "cannot reach Telegram" note
REM        anything else    -> last 25 lines of logs\bot.log
REM      Invalid token and network errors were the two most likely first-run
REM      failures, and both used to flood the window with a stack trace.
REM
REM  v2  2026-10-07  Added a real single-instance guard.
REM      - Before: start.bat only warned about a second instance, and the
REM        check itself was unreliable (a manually started "python main.py"
REM        has no folder in its command line, so a path pattern missed it).
REM        Two pollers on one token end in a Telegram Conflict error.
REM      - Now: core\singleton.py owns the guard. It writes this process id to
REM        data\bot.lock and checks whether that process is still alive, so a
REM        stale lock file never blocks a restart. main.py acquires it and
REM        returns exit code 2 when another copy is running; start.bat only
REM        reacts to that code and prints how to stop the other copy.
REM      - The guard is released in a finally block, so Ctrl+C frees the lock.
REM      - setup_task.ps1 now resolves .venv\Scripts\python.exe itself, so the
REM        scheduled run and the double-click run use the same interpreter.
REM
REM  v1  2026-10-07  Initial version.
REM      1. cd to this folder first, so all later paths stay relative. The
REM         folder name has spaces and non-ASCII characters; relative paths
REM         remove every quoting problem.
REM      2. chcp 65001 plus PYTHONUTF8 / PYTHONIOENCODING / PYTHONUNBUFFERED so
REM         Persian log lines are readable and appear immediately.
REM      3. Resolve a base interpreter: "python", then "py -3", then the common
REM         install folders. Each candidate is proven with a real
REM         "import sys" call, so the Microsoft Store alias stub is rejected
REM         instead of silently opening the Store.
REM      4. Create and use a virtual environment in .venv, so double-click runs
REM         never depend on system-wide packages. Created only once.
REM      5. Install requirements.txt ONLY when its SHA256 changed; the hash is
REM         kept in data\deps.stamp. Unchanged requirements means no network
REM         and no delay on later runs.
REM      6. Validate config.py (BOT_TOKEN filled, OWNER_IDS real ids) BEFORE
REM         starting, so mistakes show up here instead of half-way through.
REM      7. Run main.py. Single-instance protection lives in
REM         core\singleton.py; exit code 2 means another copy is already up.
REM      8. On any failure: keep this window open and show the log tail.
REM
REM  RELATED ENTRY POINTS (keep in sync with this file)
REM      main.py               real entry point; all handlers live there.
REM                             main() exit codes:
REM                               0 stopped normally
REM                               1 config.py not filled in
REM                               2 another instance already running
REM                               3 token rejected by Telegram
REM                               4 cannot reach Telegram (network)
REM                             "python main.py --setup-help" prints the Persian
REM                             configuration guide without starting the bot.
REM      setup_task.ps1        registers the SAME command in Task Scheduler
REM                             ("At log on", interactive logon type) and uses
REM                             .venv\Scripts\python.exe exactly like here.
REM      selftest.py           verification only, never starts the bot.
REM      capture_templates.py  maintenance tool, never starts the bot.
REM
REM  STOPPING THE BOT
REM      This window:  press Ctrl+C, or just close the window.
REM      Scheduled:    Stop-ScheduledTask -TaskName BlackControlTelegramBot
REM
REM  FILES PRODUCED BY A RUN
REM      logs\bot.log      full log (UTF-8)
REM      data\bot.lock     pid of the running instance
REM      data\deps.stamp   hash of the last installed requirements.txt
REM ==========================================================================

setlocal EnableExtensions
title Black Control System Bot
cd /d "%~dp0"

chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "VENV=.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "TASKNAME=BlackControlTelegramBot"

echo(
echo  ============================================================
echo    Black Control System Bot
echo    folder: %CD%
echo  ============================================================
echo(

REM --------------------------------------------------------------- 1/5 python
echo [1/5] Looking for Python ...
set "BASEPY="
python -c "import sys" >nul 2>&1
if not errorlevel 1 set "BASEPY=python"
if not defined BASEPY (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BASEPY=py -3"
)
if not defined BASEPY (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%ProgramFiles%\Python313\python.exe"
        "%ProgramFiles%\Python312\python.exe"
    ) do (
        if not defined BASEPY (
            if exist %%P (
                %%P -c "import sys" >nul 2>&1
                if not errorlevel 1 set "BASEPY=%%~P"
            )
        )
    )
)
if not defined BASEPY goto :no_python
echo       using: %BASEPY%
echo(

REM ------------------------------------------------------------------ 2/5 venv
echo [2/5] Virtual environment ...
if exist "%VPY%" (
    echo       reusing %VENV%
) else (
    echo       creating %VENV% , this happens only once ...
    %BASEPY% -m venv "%VENV%"
    if errorlevel 1 goto :venv_failed
)
if not exist "%VPY%" goto :venv_failed
echo(

REM ------------------------------------------------------------ 3/5 dependencies
REM  NOTE: %VPY% is used WITHOUT quotes inside the "for /f" line below on
REM  purpose. A quoted path as the first token of a for/f backtick command
REM  breaks cmd's parsing. This is safe only because %VENV% is a relative,
REM  space-free name - keep it that way.
echo [3/5] Dependencies ...
if not exist "data" mkdir "data" >nul 2>&1
set "REQHASH="
for /f "usebackq delims=" %%H in (`%VPY% -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())"`) do set "REQHASH=%%H"
if "%REQHASH%"=="" goto :deps_failed
set "STAMPHASH="
if exist "data\deps.stamp" set /p STAMPHASH=<"data\deps.stamp"
if "%REQHASH%"=="%STAMPHASH%" (
    echo       already up to date, requirements.txt has not changed
) else (
    echo       installing from requirements.txt ...
    "%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
    "%VPY%" -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 goto :deps_failed
    >"data\deps.stamp" echo %REQHASH%
    echo       done
)
echo(

REM ---------------------------------------------------------------- 4/5 config
echo [4/5] Checking config.py ...
"%VPY%" -c "import config,sys;ok=bool(config.BOT_TOKEN) and config.BOT_TOKEN!='PUT_YOUR_BOT_TOKEN_HERE' and bool(config.OWNER_IDS) and any(x!=111111111 for x in config.OWNER_IDS);sys.exit(0 if ok else 1)"
if errorlevel 1 goto :bad_config
echo       BOT_TOKEN and OWNER_IDS look fine
echo(

REM ------------------------------------------------------------------- 5/5 run
echo [5/5] Starting the bot ...
echo(
echo  ------------------------------------------------------------
echo   Running. Press Ctrl+C in THIS window to stop the bot.
echo   Log file: logs\bot.log
echo  ------------------------------------------------------------
echo(

"%VPY%" main.py
set "RC=%errorlevel%"

echo(
if "%RC%"=="0" (
    echo  Bot stopped normally.
) else if "%RC%"=="2" (
    echo  Bot did NOT start: another copy of it is already running.
    echo  Close that one first, or stop the scheduled task with:
    echo     Stop-ScheduledTask -TaskName %TASKNAME%
) else if "%RC%"=="1" (
    echo  Bot did NOT start: config.py is not filled in yet.
) else if "%RC%"=="3" (
    echo  Bot did NOT start: the token was rejected by Telegram.
) else if "%RC%"=="4" (
    echo  Bot did NOT start: could not reach Telegram.
) else (
    echo  Bot exited with code %RC% . Last lines of logs\bot.log :
    echo  ------------------------------------------------------------
    if exist "logs\bot.log" powershell -NoProfile -Command "Get-Content -LiteralPath 'logs\bot.log' -Encoding UTF8 -Tail 25"
)
echo(
pause
endlocal
exit /b %RC%

REM --------------------------------------------------------------------- errors
:no_python
echo(
echo  ERROR: Python was not found.
echo  Install Python 3.11 or newer from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH" during setup, then run this file again.
echo(
pause
endlocal
exit /b 9009

:venv_failed
echo(
echo  ERROR: could not create the virtual environment in %VENV%
echo  If your Python came from the Microsoft Store, install the real one from
echo  https://www.python.org/downloads/ and run this file again.
echo(
pause
endlocal
exit /b 1

:deps_failed
echo(
echo  ERROR: installing requirements.txt failed.
echo  Check the internet connection or proxy, then run this file again.
echo(
pause
endlocal
exit /b 1

:bad_config
echo(
echo  ERROR: config.py is not filled in yet.
echo(
if exist "%VPY%" "%VPY%" main.py --setup-help
echo(
echo  After editing config.py, run this file again.
echo(
pause
endlocal
exit /b 1