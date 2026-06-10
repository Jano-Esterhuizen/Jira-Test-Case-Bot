@echo off
cd /d %~dp0
python poll_and_generate.py >> bot_log.txt 2>&1
