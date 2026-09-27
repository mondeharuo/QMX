@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0."
"%~dp0.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name QMX --icon "%~dp0docs\assets\qmx-icon.ico" --add-data "%~dp0docs\assets\qmx-icon.ico;assets" --specpath "%~dp0." --distpath "%~dp0dist" --workpath "%~dp0build" "%~dp0main.py"
if errorlevel 1 (
  echo Build failed. Review the output above.
  pause
  exit /b 1
)
echo Build complete: %~dp0dist\QMX\QMX.exe
pause
