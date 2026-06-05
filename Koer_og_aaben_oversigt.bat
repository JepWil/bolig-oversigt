@echo off
setlocal
cd /d %~dp0

set PY=c:/Users/Jeppe/Desktop/Bolig/.venv/Scripts/python.exe

if not exist "%PY%" (
  echo Python miljø blev ikke fundet: %PY%
  pause
  exit /b 1
)

echo [1/2] Opdaterer beriget data...
"%PY%" enrich_bolig_csv.py
if errorlevel 1 (
  echo Fejl under enrich_bolig_csv.py
  pause
  exit /b 1
)

echo [2/2] Genererer moderne oversigt...
"%PY%" build_bolig_showcase.py
if errorlevel 1 (
  echo Fejl under build_bolig_showcase.py
  pause
  exit /b 1
)

echo Aabner Bolig_oversigt_modern.html i standard-browser...
start "" "Bolig_oversigt_modern.html"

echo Faerdig.
endlocal
