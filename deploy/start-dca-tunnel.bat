@echo off
REM 一键启动 ngrok 固定域名隧道 -> 本地 8501 (Streamlit 定投决策台)
REM 公网固定地址: https://sudoku-manhood-argue.ngrok-free.dev
REM 注意: 需先启动 Streamlit 应用 (8501 端口)
REM 本脚本无位置依赖，放哪都能跑；ngrok 先找 PATH，再兜底找 %USERPROFILE%\bin

setlocal

set "NGROK=ngrok.exe"
where /Q ngrok.exe || set "NGROK=%USERPROFILE%\bin\ngrok.exe"
if not "%NGROK%"=="ngrok.exe" if not exist "%NGROK%" (
    echo [错误] 找不到 ngrok.exe：PATH 里没有，%USERPROFILE%\bin 下也没有
    pause
    exit /b 1
)

tasklist /FI "IMAGENAME eq ngrok.exe" 2>NUL | find /I "ngrok.exe" >NUL
if %ERRORLEVEL%==0 (
    echo ngrok 已在运行，无需重复启动
) else (
    start "ngrok" /MIN "%NGROK%" http --url=sudoku-manhood-argue.ngrok-free.dev 8501
    echo ngrok 隧道已启动
)
timeout /t 3 >NUL
echo.
echo 公网地址: https://sudoku-manhood-argue.ngrok-free.dev
pause
