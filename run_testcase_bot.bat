@echo off
setlocal EnableDelayedExpansion
title Jira Bot

REM UTF-8 so the script's output (e.g. the checkmark) prints without errors.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM Capture the ESC character so we can use ANSI colour codes.
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "CY=!ESC![96m"
set "MG=!ESC![95m"
set "GY=!ESC![90m"
set "GN=!ESC![92m"
set "RD=!ESC![91m"
set "RS=!ESC![0m"

REM Move to this batch file's own folder (handles the space in "Production Apps").
cd /d "%~dp0"

cls
echo.
echo   !CY!    ## ###### #####   ####      #####   ####  ######!RS!
echo   !CY!    ##   ##   ##  ## ##  ##     ##  ## ##  ##   ##  !RS!
echo   !CY!    ##   ##   #####  ######     #####  ##  ##   ##  !RS!
echo   !CY!#   ##   ##   ## ##  ##  ##     ##  ## ##  ##   ##  !RS!
echo   !CY! ####  ###### ##  ## ##  ##     #####   ####    ##  !RS!
echo.
echo   !GY!========================================================!RS!
echo    !MG!AI Test Case Generator!RS!   !GY!-  polling Jira for tickets!RS!
echo   !GY!========================================================!RS!
echo.

REM No venv in this project, so use Python from PATH.
python poll_and_generate.py
set "RC=!ERRORLEVEL!"

echo.
echo   !GY!========================================================!RS!
if "!RC!"=="0" (
    echo    !GN![ OK ]  Done  -  exit code !RC!!RS!
) else (
    echo    !RD![FAIL] Finished with errors  -  exit code !RC!!RS!
)
echo   !GY!========================================================!RS!
echo.
pause
