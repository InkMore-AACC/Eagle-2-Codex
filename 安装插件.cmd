@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 "%~dp0install.py"
) else (
  python -X utf8 "%~dp0install.py"
)
pause
