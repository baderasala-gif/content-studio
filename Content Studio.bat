@echo off
chcp 65001 >nul
title Content Studio
cd /d "%~dp0"
echo Content Studio - starting the engine...
python --version >nul 2>&1
if errorlevel 1 (
  py -3 server.py --open
) else (
  python server.py --open
)
if errorlevel 1 (
  echo.
  echo The engine stopped with an error. See the messages above.
  pause
)
