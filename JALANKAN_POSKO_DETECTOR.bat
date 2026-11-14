@echo off
title Posko Detector - AI Tent Monitor & Web Dashboard
cd /d "%~dp0"
echo ============================================================
echo   POSKO DETECTOR - SISTEM DISTRIBUSI LOGISTIK & AI KAMERA
echo ============================================================
echo Menjalankan Web Server dan Auto-Sync Kamera Tenda AI...
echo Buka browser di: http://localhost:8000
echo.
set AUTO_START_TENT_CAMERA=1
start "" http://localhost:8000
python app.py
pause

