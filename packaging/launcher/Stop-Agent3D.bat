@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONNOUSERSITE=1
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" "%~dp0stop.py"
) else (
  python "%~dp0stop.py"
)
pause
