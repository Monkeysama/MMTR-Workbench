@echo off
cd /d "%~dp0"
if exist "release\MMTR-Workbench\MMTR Workbench.exe" (
  start "" "release\MMTR-Workbench\MMTR Workbench.exe"
) else (
  echo Please run npm install and npm run desktop:build first.
  pause
)
