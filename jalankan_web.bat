@echo off
REM ============================================================
REM  Menjalankan antarmuka web demo CKG.
REM  Klik dua kali berkas ini. JANGAN tutup jendelanya selama demo --
REM  server hidup hanya selama jendela ini terbuka.
REM
REM  Bawaan: mesin ekstraktif (siap ~10 detik, tanpa model bahasa).
REM  Untuk jawaban sungguhan:
REM      jalankan_web.bat --mesin llm --model Qwen/Qwen2.5-1.5B-Instruct
REM ============================================================

cd /d "%~dp0"

set ARG=%*
if "%ARG%"=="" set ARG=--mesin ekstraktif

echo ================================================
echo   Asisten Tanya-Jawab CKG - antarmuka web
echo ================================================
echo.
echo Tunggu sampai muncul baris:
echo     Running on http://127.0.0.1:5000
echo lalu buka alamat itu di browser.
echo.
echo JANGAN tutup jendela ini selama memakai web-nya.
echo Untuk berhenti: tekan Ctrl+C
echo.

python web/app_web.py %ARG%

echo.
echo ================================================
echo   Server berhenti.
echo   Kalau ada pesan error di atas, salin ke Claude.
echo ================================================
pause
