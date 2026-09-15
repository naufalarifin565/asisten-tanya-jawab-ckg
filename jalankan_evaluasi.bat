@echo off
REM ============================================================
REM  Menjalankan SELURUH evaluasi lalu menulis laporannya.
REM  Klik dua kali berkas ini.
REM
REM  Bawaan: mesin LLM (angka yang sebenarnya) -- perlu GPU, ~15 menit.
REM  Tanpa GPU / cuma ingin cepat:
REM      jalankan_evaluasi.bat --mesin ekstraktif
REM ============================================================

cd /d "%~dp0"

set ARG=%*
if "%ARG%"=="" set ARG=--mesin llm

echo ================================================
echo   Evaluasi Asisten Tanya-Jawab CKG
echo ================================================
echo.

REM --- Cari Python yang BENAR ---------------------------------
REM Windows menaruh stub Microsoft Store bernama python.exe di
REM %LOCALAPPDATA%\Microsoft\WindowsApps. Kalau stub itu yang lebih
REM dulu di PATH, mengetik `python` MEMBUKA MICROSOFT STORE, bukan
REM menjalankan Python -- dan jendela .bat langsung tertutup tanpa
REM pesan apa pun. Urutan PATH saat diklik dari Explorer bisa beda
REM dari saat diketik di terminal, jadi jangan diandalkan.
set PY=
for /f "delims=" %%p in ('where python 2^>NUL') do (
  echo %%p | %SystemRoot%\System32\find.exe /I "WindowsApps" >NUL
  if errorlevel 1 if not defined PY set "PY=%%p"
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  echo   GAGAL: Python tidak ditemukan.
  echo.
  echo   Yang terbaca di PATH:
  where python 2^>NUL
  echo.
  echo   Kalau yang muncul cuma jalur WindowsApps, itu stub Microsoft
  echo   Store, bukan Python sungguhan. Pasang Python dari python.org
  echo   dan centang "Add Python to PATH".
  goto :selesai
)
echo [periksa] Python: %PY%
"%PY%" --version
if errorlevel 1 (
  echo   GAGAL: Python ditemukan tapi tidak bisa dijalankan.
  goto :selesai
)

REM --- Periksa dulu, sebelum model dimuat -------------------
REM Gagal di menit ke-10 karena hal yang bisa diketahui di detik
REM pertama itu pemborosan waktu yang paling menyakitkan.

echo [periksa] Proses Python lain yang masih berjalan...
REM find.exe dipatok penuh: kalau Git Bash ada di PATH, `find` menunjuk ke
REM find versi Unix yang tidak mengenal /I dan pemeriksaan ini jadi salah.
tasklist /FI "IMAGENAME eq python.exe" 2>NUL | %SystemRoot%\System32\find.exe /I "python.exe" >NUL
if not errorlevel 1 (
  echo.
  echo   PERINGATAN: masih ada proses Python berjalan.
  echo   VRAM 8 GB tidak cukup untuk dua model sekaligus, dan yang
  echo   terjadi bukan pesan error rapi melainkan macet.
  echo.
  tasklist /FI "IMAGENAME eq python.exe"
  echo.
  echo   Tutup dulu jendela lain ^(server web / terminal chat^),
  echo   atau hentikan paksa dengan:  taskkill /F /IM python.exe
  echo.
  choice /C YN /M "   Lanjut saja"
  if errorlevel 2 goto :selesai
)

echo [periksa] Korpus dan indeks...
if not exist "data\processed\korpus.jsonl" (
  echo   GAGAL: data\processed\korpus.jsonl tidak ada.
  echo   Jalankan dulu:  python src\chunk.py
  goto :selesai
)
if not exist "data\processed\indeks.npz" (
  echo   GAGAL: data\processed\indeks.npz tidak ada.
  echo   Jalankan dulu:  python src\index.py
  goto :selesai
)
if not exist "eval\pertanyaan_uji.jsonl" (
  echo   GAGAL: eval\pertanyaan_uji.jsonl tidak ada.
  echo   Jalankan dulu:  python eval\buat_pertanyaan_uji.py
  goto :selesai
)

echo [periksa] VRAM yang tersedia...
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>NUL
if errorlevel 1 echo   ^(nvidia-smi tidak ada -- lewati pemeriksaan ini^)

echo.
echo ================================================
echo   Mulai. Yang akan terjadi:
echo.
echo     [1/4] Korpus         - seketika
echo     [2/4] Guardrail      - seketika
echo     [3/4] Pencarian      - sekitar 1 menit
echo     [4/4] Jawaban        - INI YANG LAMA
echo.
echo   Langkah 4 memuat model bahasa dulu ^(~1 menit^), lalu
echo   menjawab pertanyaan satu per satu. Tiap pertanyaan
echo   dicetak beserta perkiraan sisa waktu, jadi kalau
echo   layarnya bergerak berarti tidak macet.
echo.
echo   ARTI TIAP BARIS:
echo     benar     menjawab   ada jawabannya, dijawab            OK
echo     benar     menolak    tidak ada jawabannya, ditolak      OK
echo     SALAH     menolak    ada jawabannya tapi ditolak        mengecewakan
echo     MENGARANG            tidak ada jawabannya tapi dijawab  BERBAHAYA
echo.
echo   Dari 87 pertanyaan uji, 14 di antaranya SENGAJA tidak ada
echo   jawabannya di korpus. Untuk yang 14 itu, menolak = lulus.
echo.
echo   JANGAN tutup jendela ini sampai selesai.
echo ================================================
echo.

"%PY%" eval\evaluate.py %ARG%

echo.
echo ================================================
if errorlevel 1 (
  echo   Evaluasi BERHENTI dengan galat.
  echo   Salin pesan di atas ke Claude.
) else (
  echo   Selesai. Laporan: eval\laporan_evaluasi.md
)
echo ================================================

:selesai
echo.
pause
