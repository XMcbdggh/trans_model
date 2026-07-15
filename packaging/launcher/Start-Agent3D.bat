@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Agent3D
set PYTHONNOUSERSITE=1
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" "%~dp0launch.py"
) else (
  python "%~dp0launch.py"
)
if errorlevel 1 pause
