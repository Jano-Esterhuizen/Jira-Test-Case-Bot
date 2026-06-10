@echo off
title Jira Test Case Bot

REM Use UTF-8 so the script's output (e.g. the checkmark) prints without errors.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM Move to this batch file's own folder (handles the space in "Production Apps").
cd /d "%~dp0"

echo ============================================
echo  Jira Test Case Bot
echo  Folder: %CD%
echo ============================================
echo.

REM No venv in this project, so use Python from PATH.
python poll_and_generate.py

echo.
echo ============================================
echo  Finished with exit code %ERRORLEVEL%
echo ============================================
pause
