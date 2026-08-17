@echo off
REM Start ngrok tunnel with reserved domain -> local port 8501 (Streamlit DCA app)
REM Public URL: https://sudoku-manhood-argue.ngrok-free.dev
REM Requires the Streamlit app to be already running on port 8501.
REM
REM ngrok.exe lives in bin\ next to this script (ships with the project, not in git).
REM Resolution order: script-relative bin\ -> PATH. No absolute paths anywhere.
REM
REM NOTE: keep this file ASCII-only. cmd.exe reads .bat using the OEM codepage
REM (936 on zh-CN Windows); UTF-8 Chinese comments get mis-decoded and cmd may try
REM to execute the garbled fragments. Chinese docs live in DEPLOY.md instead.

setlocal

set "NGROK=%~dp0bin\ngrok.exe"
if exist "%NGROK%" goto found

where /Q ngrok.exe
if %ERRORLEVEL%==0 (
    set "NGROK=ngrok.exe"
    goto found
)

echo [ERROR] ngrok.exe not found.
echo         Not in %~dp0bin\ and not on PATH.
echo         Download from https://ngrok.com/download and drop it into %~dp0bin\
pause
exit /b 1

:found
tasklist /FI "IMAGENAME eq ngrok.exe" 2>NUL | find /I "ngrok.exe" >NUL
if %ERRORLEVEL%==0 (
    echo ngrok is already running - nothing to do.
) else (
    start "ngrok" /MIN "%NGROK%" http --url=sudoku-manhood-argue.ngrok-free.dev 8501
    echo ngrok tunnel started.
)
timeout /t 3 >NUL
echo.
echo Public URL: https://sudoku-manhood-argue.ngrok-free.dev
pause
