@echo off
REM Menjalankan antarmuka percakapan di terminal.
REM Klik dua kali berkas ini.
REM Untuk versi cepat tanpa GPU:  jalankan_terminal.bat --mesin ekstraktif

cd /d "%~dp0"

python src/app.py %*

echo.
echo ================================================
echo   Selesai.
echo   Kalau ada pesan error di atas, salin ke Claude.
echo ================================================
pause
