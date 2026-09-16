"""Menyusun jawaban dari potongan korpus, lengkap dengan sumber dan guardrail.

    python src/answer.py "peserta tidak punya NIK, bagaimana?" --sasaran nakes
    python src/answer.py "syarat ikut CKG apa saja?" --sasaran masyarakat
    python src/answer.py "berapa dosis obat hipertensi?" --sasaran masyarakat
    python src/answer.py "..." --mesin ekstraktif      # tanpa LLM sama sekali

=== PRINSIP UTAMA: GUARDRAIL TIDAK DITITIPKAN KE LLM ===

Godaan terbesar saat membangun RAG adalah menulis perintah panjang ke LLM
("jangan mengarang, jangan memberi saran medis, sertakan sumber") lalu
menganggap urusan selesai. Itu rapuh: model kecil sering melanggar perintahnya
sendiri, dan pelanggarannya baru ketahuan saat sudah dipakai orang.

Di sini pembagiannya tegas:

  DIKERJAKAN KODE (pasti, bisa diuji):
    1. Menolak pertanyaan klinis    -> sebelum pencarian dijalankan
    2. Menolak saat konteks kosong  -> sebelum LLM dipanggil
    3. Menolak jawaban yang tidak berpijak pada dokumen -> setelah LLM menjawab
    4. Menempelkan sumber           -> diambil dari metadata potongan
    5. Membuang tautan karangan LLM -> disaring dari keluarannya

  DIKERJAKAN LLM (hanya ini):
    6. Merangkai kalimat dari konteks yang sudah disediakan

Jadi walaupun LLM-nya berhalusinasi, ia tidak bisa mengarang alamat sumber,
tidak bisa menjawab pertanyaan klinis, dan tidak bisa menjawab saat memang
tidak ada dokumennya.

=== KENAPA PENOLAKAN TIDAK MEMAKAI AMBANG SKOR ===

Sudah diukur di eval/uji_retrieval.py: skor terendah pertanyaan yang ADA
jawabannya (0,8267) lebih rendah daripada skor tertinggi pertanyaan yang TIDAK
ada jawabannya (0,8275). Rentangnya bertumpang tindih, jadi aturan "kalau skor
< X maka tolak" pasti salah -- entah menolak pertanyaan sah, atau menjawab
pertanyaan yang seharusnya ditolak.

Yang dipakai sebagai gantinya: LLM diminta menuliskan kata kunci khusus
(TIDAK_ADA_DI_DOKUMEN) kalau konteksnya tidak menjawab, dan KODE yang mendeteksi
kata kunci itu lalu mengganti seluruh jawaban dengan penolakan baku. LLM cuma
memberi sinyal; keputusannya tetap di kode.

=== DUA MESIN ===

  llm         : menyusun jawaban dengan model bahasa (butuh unduhan model)
  ekstraktif  : menyalin potongan paling relevan apa adanya, tanpa LLM sama
                sekali. Tidak mungkin berhalusinasi karena tidak mengarang satu
                kata pun. Berguna sebagai pembanding saat evaluasi, dan sebagai
                cadangan kalau model bahasa tidak bisa dijalankan.

BATAS MESIN EKSTRAKTIF -- PENTING, JANGAN DIPAKAI SENDIRIAN:
Mesin ekstraktif TIDAK BISA MENOLAK MENJAWAB. Ia selalu menyalin potongan
paling mirip, apa pun pertanyaannya. Sudah dicoba: "Bagaimana cara mengganti
oli motor?" dijawab dengan tanggal mulai program CKG. Penolakan mustahil
dipasang di sini karena ambang skor sudah terbukti tidak memisahkan (lihat
penjelasan di atas), sedangkan mesin ini tidak punya cara lain untuk menilai
apakah potongannya benar-benar menjawab.

Jadi mesin ekstraktif itu TITIK ACUAN pembanding saat evaluasi, bukan sistem
yang boleh dipakai pengguna. Guardrail penolakan hanya lengkap di mesin llm.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from retrieve import Hasil, Pencari
from utils import cocok_sasaran, lapor, senyapkan_log_model

MODEL_BAWAAN = "Qwen/Qwen2.5-3B-Instruct"
SANDI_TIDAK_ADA = "TIDAK_ADA_DI_DOKUMEN"

# Batas panjang jawaban. Sempat 400 dan itu TERLALU PENDEK: jawaban untuk
# "bagaimana cara mendaftar CKG Sekolah?" terputus di tengah kalimat pada
# langkah ketiga, dan tampil seolah itu jawaban utuh. Prosedur pendaftaran di
# korpus ini memang panjang -- beberapa memuat tiga jalur pendaftaran sekaligus.
#
# Kalau tetap tidak cukup, jawaban DIBERI KETERANGAN bahwa ia terpotong
# (lihat CATATAN_TERPOTONG). Jawaban yang berhenti diam-diam lebih buruk
# daripada jawaban yang mengaku belum selesai -- pembaca tidak punya cara tahu
# ada langkah yang hilang.
MAKS_TOKEN_JAWABAN = 700

# Atap terpisah untuk MODE RINCI (lihat minta_rinci di bawah).
#
# Perlu ditegaskan, karena mudah salah sangka: atap 700 token TIDAK PERNAH
# menjadi penyebab jawaban terasa pendek. Diukur pada lima pertanyaan:
#
#     pertanyaan                                          kata   terpotong?
#     "Bagaimana alur pencatatan dan pelaporan CKG?"        97      tidak
#     "Jelaskan sedetail mungkin alur pencatatan CKG."     218      tidak
#     "Apa tugas pihak sekolah dalam pelaksanaan CKG?"      98      tidak
#     "Jelaskan lengkap dan rinci tugas pihak sekolah."    133      tidak
#     "Bagaimana cara mendaftar CKG?"                      106      tidak
#
# 700 token kira-kira 450-500 kata. Yang terpanjang cuma 218. Jadi yang
# memendekkan jawaban bukan atapnya, melainkan PERINTAHNYA sendiri: GAYA
# menyuruh "jawab ringkas" dan "kalimat pendek". Menaikkan atap saja tidak
# akan mengubah apa pun -- yang harus diganti adalah perintahnya.
#
# Atap ini tetap dinaikkan untuk mode rinci, supaya jawaban yang memang
# panjang punya ruang; bukan supaya jawaban biasa jadi panjang.
MAKS_TOKEN_RINCI = 1400

CATATAN_TERPOTONG = (
    "\n\n_(Jawaban ini terpotong karena terlalu panjang. "
    "Silakan buka dokumen sumber di bawah untuk langkah selengkapnya.)_"
)

BANTUAN_RESMI = (
    "Untuk bantuan lebih lanjut, hubungi Helpdesk Kemenkes di helpdesk@kemkes.go.id "
    "atau Administrator ASIK di Puskesmas/instansi Anda."
)

# ---------------------------------------------------------------------------
# Penapis pertanyaan klinis (prinsip wajib no. 3)
#
# Sistem ini BUKAN alat klinis. Ia tidak menafsirkan hasil pemeriksaan
# seseorang, tidak memberi saran pengobatan, dan tidak menegakkan diagnosis.
#
# Penapisan dilakukan di KODE dan SEBELUM pencarian, bukan diserahkan ke LLM.
# Sebabnya sederhana: kalau LLM yang diminta menahan diri, sesekali ia akan
# lolos -- dan sekali lolos untuk pertanyaan pengobatan sudah cukup berbahaya.
#
# Polanya sengaja dipersempit ke pertanyaan yang meminta TAFSIR ATAU TINDAKAN
# MEDIS PRIBADI. "Cara mencatat hasil gula darah di ASIK" harus tetap dijawab,
# karena itu pertanyaan pencatatan, bukan pertanyaan medis. Tingkat kesalahan
# penapis ini diuji di eval/uji_guardrail.py.
# ---------------------------------------------------------------------------
POLA_KLINIS = [
    re.compile(r"\b(dosis|resep|obat)\b.*\b(apa|berapa|untuk|minum|harus)\b", re.I),
    re.compile(r"\b(berapa|apa)\b.*\b(dosis|obat|resep)\b", re.I),
    re.compile(r"\b(diagnosa|diagnosis|didiagnosa)\b", re.I),
    re.compile(r"\bapakah (saya|aku|ibu saya|bapak saya|anak saya)\b.*\b(sakit|menderita|kena|bahaya|normal)\b", re.I),
    # Menafsirkan hasil milik penanya sendiri: "gula darah saya 250 artinya apa?"
    re.compile(r"\b(hasil|gula darah|tekanan darah|tensi|kolesterol|asam urat|berat badan)\b"
               r"[^?]{0,40}\b(saya|aku|anak saya|ibu saya)\b[^?]{0,40}"
               r"\b(artinya|berbahaya|normal|bahaya|tinggi|rendah|kenapa|maksudnya)\b", re.I),
    re.compile(r"\b(saya|aku)\b[^?]{0,30}\b(gula darah|tensi|kolesterol|asam urat)\b[^?]{0,20}\d", re.I),
    re.compile(r"\b(pengobatan|terapi|cara menyembuhkan|cara mengobati)\b", re.I),
]

PENOLAKAN_KLINIS = (
    "Maaf, saya tidak bisa menjawab pertanyaan itu.\n\n"
    "Sistem ini hanya membantu pertanyaan seputar **pencatatan dan tata cara** program "
    "Cek Kesehatan Gratis. Sistem ini bukan alat klinis: tidak menafsirkan hasil pemeriksaan, "
    "tidak memberi saran pengobatan, dan tidak menegakkan diagnosis.\n\n"
    "Silakan tanyakan langsung kepada tenaga kesehatan di Puskesmas atau fasilitas "
    "kesehatan terdekat, karena hanya mereka yang bisa menilai kondisi Anda."
)

PENOLAKAN_TIDAK_ADA = (
    "Maaf, informasi itu tidak saya temukan di dokumen yang saya miliki.\n\n"
    "Saya hanya menjawab berdasarkan Pusat Bantuan ASIK dan FAQ Cek Kesehatan Gratis "
    "di SATUSEHAT. Supaya tidak menyesatkan, saya tidak akan menebak.\n\n" + BANTUAN_RESMI
)


# ---------------------------------------------------------------------------
# "Apakah saya hipertensi?" -- lubang yang sempat lolos
#
# Daftar POLA_KLINIS di atas menangkap kata KERJA klinis (obat, dosis, terapi)
# dan kata sifat penilaian (normal, berbahaya). Yang TIDAK tertangkap adalah
# bentuk paling wajar yang diketik orang: menyebut NAMA PENYAKITNYA langsung.
#
#     "Tekanan darah saya 150/95, apakah saya hipertensi?"   <- lolos
#     "Obat apa untuk gula darah tinggi?"                    <- tertahan
#
# Dua sebabnya:
#   1. pola "apakah saya ..." hanya mengenal sakit|menderita|kena|bahaya|normal.
#      Nama penyakitnya sendiri tidak ada di daftar itu.
#   2. pola angka mensyaratkan "saya" MENDAHULUI nama ukuran, padahal urutan
#      wajar Bahasa Indonesia justru sebaliknya: "tekanan darah saya 150/95".
#
# Ditemukan saat menguji antarmuka web, bukan lewat berkas uji -- karena
# frasa persis ini memang belum ada di sana. Sekarang sudah ditambahkan.
#
# KENAPA PAKAI FUNGSI, BUKAN REGEX PANJANG: nama penyakit saja belum tentu
# klinis. "Apakah saya harus periksa kolesterol?" dan "Apakah anemia diperiksa
# di CKG?" adalah pertanyaan PROGRAM yang sah dan wajib tetap dijawab. Yang
# membedakan bukan kata-katanya, melainkan apakah penyakit itu dilekatkan pada
# DIRI penanya tanpa kata kerja program di antaranya. Itu lebih jujur
# ditulis sebagai beberapa baris kode daripada satu regex yang tidak bisa
# dibaca siapa pun.
# ---------------------------------------------------------------------------
PENYAKIT = (
    r"hipertensi|darah tinggi|prehipertensi|diabetes|kencing manis|prediabetes|"
    r"hipoglikemia|hiperglikemia|anemia|stunting|obesitas|kegemukan|gizi buruk|"
    r"gizi kurang|jantung|stroke|kanker|tumor|tbc|tuberkulosis|paru basah|"
    r"gagal ginjal|hepatitis|hiv|aids|sifilis|malaria|kusta|skabies|kudis|"
    r"talasemia|thalasemia|katarak|glaukoma|asam urat|kolesterol|tiroid|"
    r"depresi|gangguan jiwa|cacingan|karies"
)
UKURAN = (
    r"tekanan darah|tensi|gula darah|gds|gdp|kolesterol|asam urat|hemoglobin|"
    r"\bhb\b|imt|bmi|lingkar perut|berat badan|tinggi badan|kadar"
)
# Kata yang menandakan pertanyaannya tentang PROGRAM, bukan tentang kondisi diri.
POLA_PROGRAM = re.compile(
    r"\b(periksa|pemeriksaan|skrining|layanan|dapat|dapatkan|ikut|mengikuti|"
    r"daftar|mendaftar|catat|dicatat|mencatat|input|entri|termasuk|paket|"
    r"dilayani|syarat|biaya|jadwal)\b", re.I)

POLA_PENYAKIT = re.compile(rf"\b({PENYAKIT})\b", re.I)
POLA_UKURAN = re.compile(rf"\b({UKURAN})\b", re.I)
POLA_ORANG = re.compile(
    r"\b(saya|aku|anak saya|ibu saya|bapak saya|istri saya|suami saya|"
    r"orang tua saya)\b", re.I)
POLA_ANGKA_HASIL = re.compile(r"\d")


def _melekat_pada_diri(pertanyaan: str, pola_hal: re.Pattern) -> bool:
    """Apakah `pola_hal` dilekatkan pada diri penanya, tanpa kata program di antaranya?

    Urutan tidak dipersoalkan: "saya hipertensi" dan "hipertensi saya" sama saja.
    Jarak dibatasi 30 huruf supaya dua kata yang kebetulan ada di satu kalimat
    panjang tidak dianggap saling melekat.
    """
    m_orang = POLA_ORANG.search(pertanyaan)
    m_hal = pola_hal.search(pertanyaan)
    if not (m_orang and m_hal):
        return False
    awal, akhir = sorted([m_orang.span(), m_hal.span()], key=lambda s: s[0])
    if akhir[0] < awal[1]:          # tumpang tindih, mis. "gula darah saya"
        return True
    antara = pertanyaan[awal[1]:akhir[0]]
    if len(antara) > 30:
        return False
    return not POLA_PROGRAM.search(antara)


def tanya_kondisi_diri(pertanyaan: str) -> bool:
    """"Apakah saya hipertensi?" / "anak saya stunting ya?" -- menegakkan diagnosis."""
    return _melekat_pada_diri(pertanyaan, POLA_PENYAKIT)


def tanya_angka_hasil_sendiri(pertanyaan: str) -> bool:
    """"Tekanan darah saya 150/95, gimana?" -- menafsirkan angka milik penanya."""
    if not POLA_ANGKA_HASIL.search(pertanyaan):
        return False
    return _melekat_pada_diri(pertanyaan, POLA_UKURAN)


def pertanyaan_klinis(pertanyaan: str) -> bool:
    if any(p.search(pertanyaan) for p in POLA_KLINIS):
        return True
    return tanya_kondisi_diri(pertanyaan) or tanya_angka_hasil_sendiri(pertanyaan)


# ---------------------------------------------------------------------------
# Pertanyaan TENTANG SISTEM, bukan tentang isi dokumen
#
# "Saya bisa tanya apa saja?" adalah pertanyaan pertama yang paling wajar
# diketik orang, dan sistem sempat menolaknya -- karena memang tidak ada
# dokumen berjudul "daftar pertanyaan yang bisa diajukan". Penolakan itu
# benar secara aturan, tapi buruk sebagai pengalaman pemakaian.
#
# Ini BUKAN pelanggaran prinsip wajib no. 2. Larangannya adalah mengarang
# jawaban TENTANG ISI DOKUMEN. Menjelaskan cakupan sistem itu perkara lain --
# asalkan daftarnya dibangkitkan dari korpus yang benar-benar ada dan TIDAK
# diberi sitasi, supaya tidak ada yang mengira ini kutipan dokumen.
#
# Karena daftarnya dihitung dari korpus, ia ikut berubah sendiri begitu
# sumber baru ditambahkan (mis. Juknis CKG) tanpa perlu menyunting kode.
# ---------------------------------------------------------------------------
POLA_META = [
    re.compile(r"\b(pertanyaan apa|tanya apa|bisa tanya apa)\b.*\b(ajukan|tanya|bantu)", re.I),
    re.compile(r"\bapa saja yang bisa\b.*\b(ditanya|ajukan|dibantu|kamu bantu)", re.I),
    re.compile(r"\b(kamu|anda|sistem ini|ini)\b\s*(bisa|dapat)\s*(bantu\s*)?apa\b", re.I),
    re.compile(r"\bsiapa (kamu|anda)\b", re.I),
    re.compile(r"^\s*(bantuan|help|menu|mulai|halo|hai)\s*[?!.]*\s*$", re.I),
    # Kalimat yang BERAKHIR dengan "tanya apa" -- dipatok ke ujung kalimat
    # supaya "mau tanya apa syarat ikut CKG" tidak ikut tertangkap.
    re.compile(r"\btanya apa\s*(saja)?\s*[?.!]*$", re.I),
]

LABEL_KATEGORI = {
    "ptm": "Skrining PTM (Penyakit Tidak Menular) — tempat skrining CKG dicatat",
    "data-individu": "Pencatatan data individu (sasaran vs pengunjung, sinyal buruk)",
    "akun": "Akun, PIN, OTP, dan aktivasi pengguna",
    "user-management": "User Management: peran, izin akses, dan administrator",
    "imunisasi": "Imunisasi",
    "ibu-hamil": "Layanan ibu hamil",
    "bayi-balita": "Layanan bayi dan balita",
    "remaja": "Remaja dan usia sekolah",
    "informasi-umum": "Pengenalan aplikasi ASIK (mobile dan website)",
    "Cek Kesehatan Gratis - Fasyankes": "Pertanyaan petugas seputar CKG di fasyankes",
    "Cek Kesehatan Gratis Ulang Tahun / Umum": "Cek Kesehatan Gratis untuk masyarakat",
}


# Contoh pertanyaan dibedakan per kelompok pembaca. Menyodorkan contoh
# "data capaian tidak muncul di dashboard" kepada warga biasa hanya membuat
# bingung -- dashboard itu urusan petugas, bukan peserta.
CONTOH_PERTANYAAN = {
    "nakes": '"peserta tidak punya NIK, bagaimana?", "data capaian PTM tidak muncul di dashboard"',
    "masyarakat": '"bagaimana cara daftar Cek Kesehatan Gratis?", "apa saja syaratnya?"',
    "sekolah": '"bagaimana sekolah didaftarkan sebagai Sarana Binaan Puskesmas?"',
}


def pertanyaan_tentang_sistem(pertanyaan: str) -> bool:
    return any(p.search(pertanyaan) for p in POLA_META)


def daftar_topik(potongan: list[dict], sasaran: str) -> str:
    """Susun daftar topik dari korpus yang benar-benar ada untuk sasaran itu."""
    hitung: dict[str, int] = {}
    for x in potongan:
        if cocok_sasaran(x, sasaran):
            k = x.get("kategori", "")
            hitung[k] = hitung.get(k, 0) + 1

    baris = [
        f"- {LABEL_KATEGORI.get(k, k)}"
        for k, _ in sorted(hitung.items(), key=lambda x: -x[1])
        if k
    ]
    if not baris:
        return "Belum ada dokumen untuk kelompok pembaca ini."

    return (
        "Saya membantu pertanyaan seputar **pencatatan dan tata cara** program "
        "Cek Kesehatan Gratis. Topik yang tersedia untuk Anda:\n\n"
        + "\n".join(baris)
        + f"\n\nContoh pertanyaan: {CONTOH_PERTANYAAN.get(sasaran, CONTOH_PERTANYAAN['nakes'])}\n\n"
        "Saya TIDAK bisa menafsirkan hasil pemeriksaan, memberi saran pengobatan, "
        "atau menegakkan diagnosis. Untuk itu silakan hubungi tenaga kesehatan.\n\n"
        "(Keterangan ini menjelaskan cakupan sistem, jadi tidak berasal dari dokumen "
        "mana pun dan tidak disertai sumber.)"
    )


# ---------------------------------------------------------------------------
# Perintah untuk LLM
# ---------------------------------------------------------------------------
GAYA = {
    "nakes": (
        "Pembacanya tenaga kesehatan atau kader. Boleh memakai istilah teknis aplikasi "
        "(ASIK, dashboard, BNBA, NIK). Jawab ringkas dan runtut. Kalau dokumennya berisi "
        "langkah-langkah, tulis ulang sebagai langkah bernomor dengan urutan yang sama."
    ),
    "masyarakat": (
        "Pembacanya masyarakat umum, bukan petugas kesehatan. WAJIB memakai bahasa "
        "sehari-hari yang sederhana. Hindari istilah teknis; kalau terpaksa dipakai, "
        "jelaskan artinya dengan singkat. Kalimat pendek. Sapa dengan 'Anda'."
    ),
    "sekolah": (
        "Pembacanya pihak sekolah. Bahasa sederhana, hindari istilah teknis aplikasi "
        "yang hanya dipakai petugas kesehatan."
    ),
}

# ---------------------------------------------------------------------------
# MODE RINCI
#
# Setiap GAYA di atas menyuruh model menjawab ringkas -- pilihan yang benar
# untuk pertanyaan biasa, karena jawaban bertele-tele justru menyembunyikan
# langkah yang penting. Tapi jadi salah begitu pengguna MEMINTA penjelasan
# lengkap: perintah sistem melawan permintaan pengguna, dan perintah sistem
# yang menang.
#
# Jadi yang diganti bukan cuma atap tokennya, melainkan kalimat perintahnya.
# Kata "ringkas"/"kalimat pendek" dibuang dari gaya, lalu ditambahkan
# instruksi tandingan di bawah ini.
#
# ATURAN 1-6 TIDAK IKUT BERUBAH. Mode ini hanya mengatur PANJANG dan
# KELENGKAPAN, bukan kewenangan: larangan mengarang, larangan menafsirkan
# hasil pemeriksaan, dan kewajiban bersumber tetap berlaku sama persis.
# Pengguna tidak bisa membuka kunci apa pun dengan meminta "jelaskan detail".
GAYA_RINCI = (
    " PENGGUNA MEMINTA PENJELASAN RINCI. Uraikan selengkap yang didukung "
    "KONTEKS: jelaskan setiap langkah satu per satu, sebutkan syarat dan "
    "pengecualian yang tertulis, dan jangan meringkas apa pun yang ada di "
    "KONTEKS. Tetap dilarang menambah apa pun yang tidak ada di KONTEKS -- "
    "menjadi rinci berarti menggali lebih dalam dokumennya, BUKAN mengarang "
    "tambahan."
)

# Kata yang menandakan pengguna memang ingin jawaban panjang.
#
# "lengkap" dan "detail" TIDAK dimasukkan sebagai kata lepas, karena keduanya
# lebih sering menjadi kata sifat yang menerangkan benda, bukan permintaan:
#
#     "Apakah data lengkap wajib diisi?"      <- bukan minta jawaban panjang
#     "Detail peserta tidak muncul"           <- bukan minta jawaban panjang
#
# Keduanya baru dihitung kalau muncul dalam susunan yang memang meminta:
# didahului kata kerja permintaan, atau bentuk "secara/lebih ... lengkap".
POLA_RINCI = re.compile(
    r"\b(?:"
    r"rinci|terperinci|perinci|sedetail\w*|selengkap\w*|menyeluruh|"
    r"uraikan|jabarkan|"
    r"jelaskan\s+semua|panjang\s+lebar|step\s*by\s*step|"
    r"langkah\s+demi\s+langkah|satu\s+per\s+satu"
    r"|(?:secara|lebih|yang|dan)\s+(?:lengkap|detail|detil)"
    r"|(?:jelaskan|uraikan|jabarkan|jawab|tolong|minta|mau|ingin|bisa)"
    r"(?:\s+\w+){0,3}\s+(?:lengkap|detail|detil)"
    r"|(?:lengkap|detail|detil)\s+(?:dan|serta)\s+(?:rinci|lengkap|detail|jelas)"
    r"|penjelasan\s+(?:lengkap|detail|detil)"
    r")\b", re.I)


def minta_rinci(pertanyaan: str) -> bool:
    """Apakah penggunanya meminta jawaban yang lebih panjang dan lengkap?"""
    return bool(POLA_RINCI.search(pertanyaan))


# ---------------------------------------------------------------------------
# PENOLAKAN KELIRU PADA KALIMAT PERMINTAAN — hasil negatif, tidak diperbaiki
#
# Ditemukan dari pemakaian nyata. Isi sama, konteks sama, hanya bentuk
# kalimatnya berbeda:
#
#     "berikan panduan dan pedoman pencatatan data pasien"   -> DITOLAK
#     "Bagaimana panduan pencatatan data pasien?"            -> dijawab
#
# Pencariannya BENAR: hasil teratasnya "Panduan Penggunaan Data Individu" dan
# "BAB VI Pencatatan dan pelaporan". Model menafsirkan "berikan panduan"
# secara harfiah sebagai "serahkan dokumen panduannya", lalu menyimpulkan
# konteks tidak memuatnya karena yang tersedia cuma kutipan.
#
# TIGA CARA SUDAH DICOBA, DIUKUR, DAN DIBUANG SEMUA.
#
# Diuji pada 6 kalimat permintaan yang jawabannya ADA di korpus, plus 15
# pertanyaan pembanding yang jawabannya TIDAK ada (5 bentuk permintaan +
# 10 dari berkas uji):
#
#   konfigurasi                                    terjawab   mengarang
#   apa adanya (tanpa perubahan apa pun)              4/6          0
#   perintah sistem diperjelas                        3/6          0
#   perintah diperjelas + coba-ulang bercatatan       3/6          0
#   apa adanya + coba-ulang bercatatan                4/6          0
#   perintah diperjelas + pertanyaan ditulis ulang    5/6          1  <-- !
#
# Yang menaikkan angka justru MENGARANG. Sebabnya halus: menulis ulang
# "berikan panduan X" menjadi "Bagaimana panduan X?" mengubah makna kalau
# katanya ambigu --
#
#     "berikan DAFTAR puskesmas yang melayani CKG di Bandung"  (senarai)
#       -> "Bagaimana DAFTAR puskesmas ...?"                   (mendaftar)
#       -> model menjawab cara mendaftar, padahal yang diminta senarai
#
# Sisanya nol manfaat atau justru memperburuk. Jadi kode ini dikembalikan
# apa adanya, dan tidak ada satu pun perubahan yang dipertahankan.
#
# PELAJARANNYA: memperjelas perintah sistem TIDAK selalu memperbaiki. Pada
# model 3B kuantisasi 4-bit, menambah kalimat aturan bisa menggeser perilaku
# ke arah yang tidak diduga -- 4/6 turun jadi 3/6 hanya karena aturan 2
# diberi penjelasan tambahan.
#
# Dua pola di bawah dipertahankan karena dipakai berkas uji untuk memisahkan
# kalimat permintaan dari kalimat tanya saat mengukur penolakan berlebihan.
POLA_KALIMAT_TANYA = re.compile(
    r"\?|^\s*(apa|apakah|bagaimana|gimana|kenapa|mengapa|siapa|kapan|"
    r"di\s*mana|dimana|ke\s*mana|berapa|bisakah|dapatkah|adakah|bolehkah)\b", re.I)


def gaya_untuk(sasaran: str, rinci: bool) -> str:
    """Susun kalimat gaya, dengan perintah ringkas dicabut kalau mode rinci."""
    gaya = GAYA.get(sasaran, GAYA["nakes"])
    if not rinci:
        return gaya
    # Cabut perintah yang menyuruh pendek, kalau tidak ia akan bertabrakan
    # dengan GAYA_RINCI dan model harus menebak mana yang menang.
    gaya = gaya.replace("Jawab ringkas dan runtut. ", "")
    gaya = gaya.replace(" Kalimat pendek.", "")
    return gaya + GAYA_RINCI

PERINTAH = """Kamu asisten yang menjawab pertanyaan seputar pencatatan program Cek Kesehatan Gratis (CKG) di Indonesia.

ATURAN YANG TIDAK BOLEH DILANGGAR:
1. Jawab HANYA memakai isi KONTEKS di bawah. Dilarang memakai pengetahuan lain, walaupun kamu merasa tahu jawabannya.
2. Kalau KONTEKS tidak memuat jawabannya, balas dengan satu baris saja: {sandi}
3. Dilarang menafsirkan hasil pemeriksaan, memberi saran pengobatan, atau menegakkan diagnosis.
4. Dilarang menulis alamat web atau tautan. Sumber akan ditambahkan otomatis oleh sistem.
5. Dilarang menyebut kata "konteks", "dokumen nomor", atau "berdasarkan potongan".
6. Jawab dalam Bahasa Indonesia.

GAYA BAHASA: {gaya}"""


def susun_prompt(pertanyaan: str, hasil: list[Hasil], sasaran: str,
                 rinci: bool = False) -> tuple[str, str]:
    konteks = "\n\n".join(
        f"--- Dokumen {i} ---\n{h.potongan['teks']}" for i, h in enumerate(hasil, 1)
    )
    sistem = PERINTAH.format(sandi=SANDI_TIDAK_ADA, gaya=gaya_untuk(sasaran, rinci))
    pengguna = f"KONTEKS:\n{konteks}\n\nPERTANYAAN: {pertanyaan}"
    return sistem, pengguna


# ---------------------------------------------------------------------------
@dataclass
class Jawaban:
    teks: str
    sumber: list[dict] = field(default_factory=list)
    ditolak: bool = False
    alasan_tolak: str = ""
    hasil_cari: list[Hasil] = field(default_factory=list)

    def cetak(self) -> str:
        bagian = [self.teks]
        if self.sumber:
            bagian.append("\nSumber:")
            for i, s in enumerate(self.sumber, 1):
                bagian.append(f"  [{i}] {s['nama']}\n      {s['url']}")
        return "\n".join(bagian)


# LLM tidak boleh menulis tautan sendiri (aturan 4). Kalau tetap ditulis, kode
# yang membuangnya -- sumber HANYA boleh berasal dari metadata potongan.
POLA_TAUTAN = re.compile(r"\(?\bhttps?://\S+\)?|\bwww\.\S+", re.I)

# Kata yang terlalu umum untuk dijadikan bukti bahwa sebuah dokumen dipakai.
KATA_UMUM = {
    "yang", "dan", "atau", "untuk", "pada", "dengan", "dari", "dapat", "akan",
    "tidak", "ini", "itu", "adalah", "juga", "sudah", "bisa", "ada", "jika",
    "agar", "oleh", "dalam", "secara", "kemudian", "silakan", "melalui", "saat",
    "anda", "kami", "harus", "berikut", "setelah", "maka", "lalu", "serta",
}


# Dua ambang, dua keperluan berbeda:
#
#   AMBANG_SITASI    -- potongan mana yang layak dicantumkan sebagai sumber.
#   AMBANG_BERPIJAK  -- jauh lebih rendah. Kalau TIDAK ADA satu pun potongan
#                       yang mencapai ambang serendah ini, artinya jawaban
#                       hampir tidak punya kesamaan apa pun dengan dokumen yang
#                       disodorkan -- tanda kuat ia dikarang dari pengetahuan
#                       model, bukan dari korpus.
#
# Ambang kedua sengaja dibuat longgar. Tugasnya menangkap jawaban yang
# BENAR-BENAR melayang, bukan menghakimi jawaban yang kebetulan diparafrase
# dengan bebas. Menolak jawaban sah lebih merugikan daripada membiarkan satu
# jawaban lemah lolos ke pemeriksaan berikutnya.
AMBANG_SITASI = 0.35
AMBANG_BERPIJAK = 0.15


def liputan_potongan(jawaban: str, h: Hasil) -> float:
    """Berapa bagian kata isi jawaban yang benar-benar muncul di potongan ini."""
    kata_jawaban = {
        k for k in re.findall(r"[a-zA-Z]{4,}", jawaban.lower()) if k not in KATA_UMUM
    }
    if not kata_jawaban:
        return 0.0
    kata_potongan = set(re.findall(r"[a-zA-Z]{4,}", h.potongan["teks"].lower()))
    return len(kata_jawaban & kata_potongan) / len(kata_jawaban)


def berpijak_pada_dokumen(jawaban: str, hasil: list[Hasil]) -> bool:
    """Apakah jawaban ini benar-benar berpijak pada dokumen yang disodorkan?

    Dipasang setelah evaluasi menunjukkan model kecil (Qwen2.5-1.5B) menjawab
    "Bagaimana cara klaim BPJS Kesehatan?", "Siapa Menteri Kesehatan saat ini?",
    dan "Apa efek samping vaksin COVID-19?" -- padahal ketiganya tidak ada di
    korpus. Model menjawab dari pengetahuannya sendiri.

    Perintah "jawab hanya dari konteks" ternyata tidak cukup: model kecil
    melanggarnya tanpa memberi sinyal apa pun. Pemeriksaan ini tidak bertanya
    apakah model MAU patuh, melainkan memeriksa hasilnya -- jawaban yang tidak
    berbagi kata isi dengan satu pun potongan tidak mungkin berasal dari
    potongan itu.
    """
    return any(liputan_potongan(jawaban, h) >= AMBANG_BERPIJAK for h in hasil)


def sumber_yang_dipakai(jawaban: str, hasil: list[Hasil], ambang: float = AMBANG_SITASI) -> list[Hasil]:
    """Sisakan hanya potongan yang isinya benar-benar tercermin di jawaban.

    Mengapa perlu: pencarian mengambil 4 potongan sebagai bahan, tapi jawaban
    biasanya hanya memakai satu. Kalau keempatnya tetap dicantumkan sebagai
    sumber, pembaca dibuat mengira keempat dokumen itu mendukung jawaban --
    padahal tiga di antaranya tidak pernah dipakai. Rujukan yang menyesatkan
    lebih buruk daripada rujukan yang sedikit.

    Caranya sengaja sederhana dan bisa diperiksa: hitung berapa bagian kata isi
    pada jawaban yang benar-benar muncul di potongan itu. Bukan cara yang
    canggih, tapi tidak butuh model tambahan, hasilnya sama setiap dijalankan,
    dan mudah dijelaskan ke orang lain.

    Potongan peringkat satu selalu disertakan supaya jawaban tidak pernah
    tampil tanpa satu pun sumber.
    """
    kata_jawaban = {
        k for k in re.findall(r"[a-zA-Z]{4,}", jawaban.lower()) if k not in KATA_UMUM
    }
    if not kata_jawaban:
        return hasil[:1]

    dipakai = []
    for h in hasil:
        kata_potongan = set(re.findall(r"[a-zA-Z]{4,}", h.potongan["teks"].lower()))
        liputan = len(kata_jawaban & kata_potongan) / len(kata_jawaban)
        if liputan >= ambang:
            dipakai.append(h)
    return dipakai or hasil[:1]


# Panjang maksimum nama dokumen saat ditulis di tengah kalimat.
MAKS_NAMA_DOKUMEN = 62


def nama_dokumen_pendek(potongan: dict) -> str:
    """Nama dokumen yang cukup pendek untuk ditulis di tengah kalimat.

    TIDAK memakai judul_dokumen mentah-mentah, karena bentuknya berbeda jauh
    antar sumber dan dua di antaranya tidak bisa dipakai apa adanya:

      * juknis dan juknis_sekolah SAMA-SAMA punya "BAB I Pendahuluan". Tanpa
        nama dokumennya, pembaca tidak tahu Kepmenkes yang mana.
      * judul FAQ adalah KALIMAT PERTANYAAN UTUH, kadang lebih dari 150 huruf.
        Ditempel di tengah kalimat, hasilnya tidak terbaca:
            ...gunakan prosedur validasi dalam "Saat mendaftar CKG Sekolah
            atau mengisi skrining mandiri melalui tautan yang dikirim muncul
            notifikasi "Data pasien tidak sesuai...", apa yang harus...".

    Yang dipakai adalah `sumber_nama`, yang sudah berpola tetap di semua
    sumber: "<nama sumber> — <judul>". Bagian sebelum tanda pisah itulah nama
    sumbernya. Untuk Juknis, nomor babnya ikut disertakan karena bab memang
    membedakan isi; untuk FAQ cukup nama kategorinya, sebab pertanyaan
    persisnya toh sudah tercantum di daftar sumber di bawah jawaban.
    """
    sumber_nama = (potongan.get("sumber_nama") or "").strip()
    judul = (potongan.get("judul_dokumen") or "").strip()
    sumber_id = potongan.get("sumber_id", "")

    pangkal = sumber_nama.split(" — ")[0].strip() if sumber_nama else ""
    # "Juknis CKG (Kepmenkes HK.01.07/MENKES/84/2026)" -> "Juknis CKG"
    pangkal = re.sub(r"\s*\([^)]*\)", "", pangkal).strip()

    if sumber_id.startswith("juknis") and judul:
        nama = f"{pangkal} {judul}" if pangkal else judul
    elif sumber_id == "asik" and judul and judul not in pangkal:
        nama = f"{pangkal}: {judul}" if pangkal else judul
    else:
        nama = pangkal or judul

    if not nama:
        return "dokumen sumber"
    if len(nama) > MAKS_NAMA_DOKUMEN:
        potong = nama[:MAKS_NAMA_DOKUMEN].rsplit(" ", 1)[0]
        nama = (potong or nama[:MAKS_NAMA_DOKUMEN]).rstrip(" ,;:-—") + "…"
    return nama


# ---------------------------------------------------------------------------
# "Dokumen 1", "Dokumen 2", ... hanya ada di dalam prompt
#
# Konteks disusun sebagai "--- Dokumen 1 ---", "--- Dokumen 2 ---" supaya model
# bisa membedakan potongan. Tapi penomoran itu TIDAK PERNAH terlihat pengguna,
# jadi jawaban yang berbunyi "lihat Dokumen 2" tidak ada artinya.
#
# Perlakuan lama: "Dokumen 2" diganti kata "dokumen". Itu KELIRU, dan cacatnya
# baru ketahuan pada jawaban nyata yang berisi daftar bernomor:
#
#     1. dokumen: Ini menjelaskan prosedur pelaksanaan CKG di tempat kerja...
#     2. dokumen: Dokumen ini menjelaskan sistem pencatatan dan pelaporan...
#
# Pertanyaannya justru "panduan apa yang perlu saya baca?", dan jawabannya
# menghapus persis bagian yang ditanyakan. Menghapus rujukan yang salah tidak
# menjadikannya benar -- hanya menjadikannya rusak.
#
# Sekarang penomoran itu DITUKAR dengan nama dokumen yang sebenarnya, diambil
# dari metadata potongan yang memang dipakai. Namanya tidak mungkin dikarang
# model, karena bukan model yang menuliskannya.
# ---------------------------------------------------------------------------
POLA_RUJUKAN_NOMOR = re.compile(
    r"\b[Dd]okumen\s*(?:ke-?\s*)?(\d+)\b")


def dokumen_dirujuk(teks: str, jumlah: int) -> list[int]:
    """Nomor potongan (mulai 0) yang secara terang-terangan dirujuk model.

    Dipanggil pada keluaran MENTAH, sebelum penomoran ditukar nama. Gunanya
    menutup ketimpangan yang tampak pada jawaban nyata: badan jawaban menyebut
    "Juknis CKG BAB VI Pencatatan dan pelaporan", sementara daftar sumber di
    bawahnya hanya memuat BAB IV -- karena penyaring sitasi menilai kemiripan
    kata, dan BAB VI tidak lolos ambangnya.

    Untuk sistem yang janji utamanya adalah sitasi, menyebut nama dokumen di
    badan jawaban tetapi tidak mencantumkannya di daftar sumber adalah cacat:
    pembaca tidak punya cara memeriksa dokumen yang barusan disuruh dibaca.
    Aturannya sekarang sederhana -- kalau disebut, wajib dikutip.
    """
    nomor = set()
    for m in POLA_RUJUKAN_NOMOR.finditer(teks):
        i = int(m.group(1)) - 1
        if 0 <= i < jumlah:
            nomor.add(i)
    return sorted(nomor)


def bersihkan_keluaran(teks: str, hasil: list[Hasil] | None = None) -> str:
    teks = POLA_TAUTAN.sub("", teks)

    if hasil:
        def tukar(m: re.Match) -> str:
            i = int(m.group(1)) - 1
            if 0 <= i < len(hasil):
                return f'"{nama_dokumen_pendek(hasil[i].potongan)}"'
            # Nomor di luar jangkauan berarti model mengarang rujukan.
            # Dikembalikan menjadi kata umum, bukan nama tebakan.
            return "dokumen sumber"
        teks = POLA_RUJUKAN_NOMOR.sub(tukar, teks)
    else:
        teks = POLA_RUJUKAN_NOMOR.sub("dokumen sumber", teks)

    # Sisa pola "dokumen sumber: Dokumen ini menjelaskan..." dirapikan supaya
    # tidak berbunyi seperti kalimat yang belum selesai.
    teks = re.sub(r"[ \t]{2,}", " ", teks)
    return re.sub(r"\n{3,}", "\n\n", teks).strip()


class Penjawab:
    def __init__(
        self,
        pencari: Pencari | None = None,
        mesin: str = "llm",
        model: str = MODEL_BAWAAN,
        k: int = 4,
        hibrida: bool = True,
        kuantisasi: str = "otomatis",
    ) -> None:
        self.pencari = pencari or Pencari()
        self.mesin = mesin
        self.hibrida = hibrida
        self.kuantisasi = kuantisasi
        self.nama_model = model
        self.k = k
        self._llm = None
        self._tok = None

    def _muat_llm(self):
        if self._llm is not None:
            return
        senyapkan_log_model()
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        # ------------------------------------------------------------------
        # PEMILIHAN MODE OTOMATIS
        #
        # Sebelumnya pengguna harus tahu sendiri kapan memakai --4bit, dan
        # kalau lupa, sistemnya mati di tengah demo tanpa pesan yang jelas.
        # Itu beban yang tidak seharusnya ditanggung pengguna: komputernya
        # sendiri yang tahu berapa VRAM tersedia.
        #
        # Kebutuhan nyata, hasil pengukuran di RTX 4060 Laptop (8,19 GB):
        #     3B fp16  : 6,2 model bahasa + 1,1 pencarian + ~0,8 kerja = ~8,1 GB
        #     3B 4-bit : 2,1 model bahasa + 1,1 pencarian + ~0,8 kerja = ~4,0 GB
        #
        # Jadi fp16 praktis tidak muat di kartu 8 GB. Yang pertama kali dicoba
        # justru gagal persis begitu: lolos pemeriksaan saat start, lalu mati
        # saat pertanyaan pertama karena model pencarian belum terhitung.
        # ------------------------------------------------------------------
        if self.kuantisasi == "otomatis":
            if not torch.cuda.is_available():
                self.kuantisasi = "tidak"      # di CPU kuantisasi tidak membantu
            else:
                bebas_gb = torch.cuda.mem_get_info()[0] / 1e9
                if bebas_gb >= 8.6:
                    self.kuantisasi = "tidak"
                    lapor(f"      VRAM bebas {bebas_gb:.1f} GB -- cukup, tanpa kuantisasi")
                else:
                    self.kuantisasi = "4bit"
                    lapor(f"      VRAM bebas {bebas_gb:.1f} GB -- memakai kuantisasi 4-bit "
                          "supaya muat")

        if torch.cuda.is_available() and self.kuantisasi != "4bit":
            bebas_gb = torch.cuda.mem_get_info()[0] / 1e9
            if bebas_gb < 8.6:
                lapor(f"PERINGATAN: kuantisasi dimatikan paksa, tapi VRAM bebas cuma "
                      f"{bebas_gb:.1f} GB.")
                lapor("            Tanpa kuantisasi butuh ~8,6 GB. Kemungkinan besar gagal.")

        lapor(f"Memuat model bahasa: {self.nama_model}")
        lapor("      (kalau belum pernah dipakai, model diunduh dulu ke cache Hugging Face)")
        self._tok = AutoTokenizer.from_pretrained(self.nama_model)

        # Kuantisasi 4-bit: bobot disimpan 4 bit, bukan 16. Yang berkurang
        # bukan cuma VRAM, tapi juga LALU LINTAS MEMORI -- dan itu yang
        # sebenarnya membatasi kecepatan. Menghasilkan satu token berarti
        # membaca SELURUH bobot model dari VRAM; 4060 Laptop punya jalur memori
        # ~192 GB/detik, jadi model 6,2 GB mentok di ~31 token/detik secara
        # teori. Dengan 4-bit bobotnya jadi ~2 GB, dan batas itu naik tiga kali.
        #
        # nf4 + double quant adalah pengaturan lazim untuk model instruksi:
        # penurunan mutunya kecil, dan itu tetap harus DIUKUR dengan
        # eval/evaluate.py, bukan diasumsikan.
        cfg_kuantisasi = None
        if self.kuantisasi == "4bit":
            from transformers import BitsAndBytesConfig

            cfg_kuantisasi = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )

        def muat(peta):
            tambahan = {"quantization_config": cfg_kuantisasi} if cfg_kuantisasi else {}
            # transformers 5 memakai `dtype`, versi 4 memakai `torch_dtype`.
            try:
                return AutoModelForCausalLM.from_pretrained(
                    self.nama_model, dtype=torch.float16, device_map=peta, **tambahan)
            except TypeError:
                return AutoModelForCausalLM.from_pretrained(
                    self.nama_model, torch_dtype=torch.float16, device_map=peta, **tambahan)

        # Tiga tingkat, dari cepat ke paling aman. Seluruh model diusahakan
        # masuk GPU dulu; kalau VRAM tidak cukup, sebagian dilimpahkan ke CPU;
        # kalau masih gagal, seluruhnya di CPU.
        #
        # Kenapa perlu bertingkat: laptop dengan VRAM 8 GB bisa kehabisan memori
        # di tengah jalan, dan sistem yang langsung mati saat demo jauh lebih
        # buruk daripada sistem yang melambat. Lebih baik jawabannya datang
        # setelah satu menit daripada tidak datang sama sekali.
        catatan_kuantisasi = " (4-bit)" if self.kuantisasi == "4bit" else ""
        tingkat = [("cuda", f"seluruhnya di GPU{catatan_kuantisasi}"),
                   ("auto", "sebagian dilimpahkan ke CPU"),
                   ("cpu", "seluruhnya di CPU (lambat, tapi aman)")]
        if not torch.cuda.is_available():
            tingkat = tingkat[-1:]

        for peta, keterangan in tingkat:
            try:
                self._llm = muat(peta)
                lapor(f"      dimuat: {keterangan}")
                break
            except Exception as e:
                lapor(f"      gagal '{peta}' ({type(e).__name__}), mencoba cara berikutnya")
                if peta == "cuda":
                    torch.cuda.empty_cache()
        else:
            raise RuntimeError("Model tidak bisa dimuat, bahkan di CPU.")
        self._llm.eval()

    def _hasilkan(self, sistem: str, pengguna: str, maks_token: int | None = None) -> str:
        import torch

        self._muat_llm()
        maks_token = maks_token or MAKS_TOKEN_JAWABAN
        pesan = [{"role": "system", "content": sistem}, {"role": "user", "content": pengguna}]

        # return_dict=True: transformers 5 mengembalikan objek mirip kamus
        # (input_ids + attention_mask), sedangkan versi 4 mengembalikan tensor
        # polos. Meminta bentuk kamus secara eksplisit membuat kode ini jalan di
        # kedua versi, sekaligus mengirim attention_mask yang benar.
        masukan = self._tok.apply_chat_template(
            pesan, add_generation_prompt=True, return_tensors="pt", return_dict=True
        )
        masukan = {k: v.to(self._llm.device) for k, v in masukan.items()}
        panjang_masukan = masukan["input_ids"].shape[-1]

        # Kehabisan VRAM bisa terjadi saat MENJAWAB, bukan cuma saat memuat --
        # panjang jawaban tidak bisa ditebak di muka. Kalau itu terjadi, cache
        # GPU dibersihkan lalu dicoba sekali lagi dengan jawaban lebih pendek.
        try:
            return self._hasilkan_sekali(masukan, panjang_masukan, maks_token)
        except torch.cuda.OutOfMemoryError:
            lapor("      VRAM habis saat menjawab, dicoba ulang dengan jawaban lebih pendek")
            torch.cuda.empty_cache()
            return self._hasilkan_sekali(masukan, panjang_masukan, max(120, maks_token // 2))

    def _hasilkan_sekali(self, masukan: dict, panjang_masukan: int, maks_token: int) -> str:
        import torch

        with torch.no_grad():
            keluar = self._llm.generate(
                **masukan,
                max_new_tokens=maks_token,
                # do_sample=False: jawaban harus bisa diulang persis. Untuk sistem
                # yang dievaluasi, keluaran yang berubah-ubah tiap dijalankan
                # membuat angka evaluasinya tidak bisa dipercaya.
                do_sample=False,
                pad_token_id=self._tok.eos_token_id,
            )
        token_baru = keluar[0][panjang_masukan:]
        teks = self._tok.decode(token_baru, skip_special_tokens=True).strip()

        # Kalau jumlah token yang dihasilkan mentok di batas, berarti model
        # dihentikan paksa -- bukan karena sudah selesai bicara. Ditandai supaya
        # pembaca tahu ada bagian yang belum tersampaikan.
        if len(token_baru) >= maks_token:
            teks += CATATAN_TERPOTONG
        return teks

    def jawab(self, pertanyaan: str, sasaran: str = "nakes",
              rinci: bool | None = None) -> Jawaban:
        """Jawab satu pertanyaan.

        rinci=None  : deteksi sendiri dari kalimat pertanyaannya (bawaan)
        rinci=True  : paksa jawaban panjang dan lengkap
        rinci=False : paksa jawaban ringkas
        """
        if rinci is None:
            rinci = minta_rinci(pertanyaan)

        # --- Pertanyaan tentang sistem: dijawab dari daftar topik korpus,
        # tanpa sitasi, dan ditandai sebagai keterangan cakupan.
        if pertanyaan_tentang_sistem(pertanyaan):
            return Jawaban(teks=daftar_topik(self.pencari.potongan, sasaran),
                           alasan_tolak="tentang_sistem")

        # --- Guardrail 1: pertanyaan klinis ditolak sebelum apa pun dikerjakan
        if pertanyaan_klinis(pertanyaan):
            return Jawaban(teks=PENOLAKAN_KLINIS, ditolak=True, alasan_tolak="klinis")

        hasil = self.pencari.cari(pertanyaan, k=self.k, sasaran=sasaran,
                                  hibrida=self.hibrida)

        # --- Guardrail 2: tidak ada dokumen sama sekali
        if not hasil:
            return Jawaban(teks=PENOLAKAN_TIDAK_ADA, ditolak=True, alasan_tolak="tanpa_konteks")

        sumber = []
        for h in hasil:
            butir = {"nama": h.potongan["sumber_nama"], "url": h.potongan["sumber_url"]}
            if butir not in sumber:
                sumber.append(butir)

        if self.mesin == "ekstraktif":
            # Menyalin apa adanya: tidak mengarang satu kata pun, jadi mustahil
            # berhalusinasi. Konsekuensinya bahasanya tidak menyesuaikan pembaca.
            teratas = hasil[0]
            isi = re.sub(r"^#[^\n]*\n+", "", teratas.potongan["teks"]).strip()
            return Jawaban(
                teks=f"Dari dokumen \"{teratas.potongan['judul_dokumen']}\":\n\n{isi}",
                sumber=sumber[:1],
                hasil_cari=hasil,
            )

        sistem, pengguna = susun_prompt(pertanyaan, hasil, sasaran, rinci)
        # Jumlah potongan (k) SENGAJA tidak ikut dinaikkan di mode rinci.
        # k=8 sudah pernah diukur dan hasilnya LEBIH BURUK daripada k=4 (lihat
        # README, bagian "Yang sudah dicoba dan gagal"). Menaikkannya di sini
        # berarti mengulang percobaan yang sudah gagal, hanya karena terasa
        # masuk akal. Mode rinci menggali lebih dalam dari 4 potongan yang sama,
        # bukan menambah potongan.
        mentah = self._hasilkan(sistem, pengguna,
                                MAKS_TOKEN_RINCI if rinci else MAKS_TOKEN_JAWABAN)

        # --- Guardrail 3: LLM memberi sinyal bahwa konteksnya tidak menjawab.
        # Keputusannya tetap di kode, bukan pada kalimat karangan LLM.
        if SANDI_TIDAK_ADA in mentah.upper().replace(" ", "_"):
            return Jawaban(teks=PENOLAKAN_TIDAK_ADA, ditolak=True,
                           alasan_tolak="tidak_ada_di_dokumen", hasil_cari=hasil)

        # Dicatat SEBELUM dibersihkan: sesudahnya nomornya sudah jadi nama.
        dirujuk = dokumen_dirujuk(mentah, len(hasil))
        teks = bersihkan_keluaran(mentah, hasil)
        if not teks:
            return Jawaban(teks=PENOLAKAN_TIDAK_ADA, ditolak=True,
                           alasan_tolak="keluaran_kosong", hasil_cari=hasil)

        # --- Guardrail 4: jawaban harus berpijak pada dokumen yang disodorkan.
        # Ini pemeriksaan HASIL, bukan pemeriksaan niat: LLM boleh saja
        # melanggar perintah "jawab hanya dari konteks", tapi jawabannya tidak
        # akan lolos kalau tidak berbagi kata isi dengan satu pun potongan.
        if not berpijak_pada_dokumen(teks, hasil):
            return Jawaban(teks=PENOLAKAN_TIDAK_ADA, ditolak=True,
                           alasan_tolak="tidak_berpijak_pada_dokumen", hasil_cari=hasil)

        # --- Guardrail 5: sumber ditempel dari metadata, bukan dari LLM,
        # dan disaring supaya hanya dokumen yang benar-benar dipakai yang dikutip.
        terpakai = sumber_yang_dipakai(teks, hasil)
        # Dokumen yang namanya ikut ditulis di badan jawaban WAJIB ada di
        # daftar sumber, walau kemiripan katanya tidak lolos ambang. Lihat
        # dokumen_dirujuk(). Urutannya dijaga tetap mengikuti peringkat
        # pencarian, bukan urutan penyebutan.
        if dirujuk:
            wajib = {hasil[i].potongan["id"] for i in dirujuk}
            sudah = {h.potongan["id"] for h in terpakai}
            tambahan = [h for h in hasil
                        if h.potongan["id"] in wajib and h.potongan["id"] not in sudah]
            if tambahan:
                terpakai = [h for h in hasil if h in terpakai or h in tambahan]
        sumber_terpakai = []
        for h in terpakai:
            butir = {"nama": h.potongan["sumber_nama"], "url": h.potongan["sumber_url"]}
            if butir not in sumber_terpakai:
                sumber_terpakai.append(butir)
        return Jawaban(teks=teks, sumber=sumber_terpakai, hasil_cari=hasil)


def main() -> int:
    p = argparse.ArgumentParser(description="Jawab pertanyaan dari korpus CKG.")
    p.add_argument("pertanyaan")
    p.add_argument("--sasaran", default="nakes", choices=["nakes", "masyarakat", "sekolah"])
    p.add_argument("--mesin", default="llm", choices=["llm", "ekstraktif"])
    p.add_argument("--model", default=MODEL_BAWAAN)
    p.add_argument("-k", type=int, default=4, help="jumlah potongan yang dijadikan konteks")
    p.add_argument("--tanpa-hibrida", action="store_true",
                   help="pakai embedding saja, matikan penggabungan dengan BM25")
    p.add_argument("--4bit", dest="empat_bit", action="store_true",
                   help="paksa kuantisasi 4-bit (biasanya tidak perlu: dipilih otomatis)")
    p.add_argument("--tanpa-kuantisasi", dest="tanpa_kuant", action="store_true",
                   help="paksa TANPA kuantisasi, walau VRAM mungkin tidak cukup")
    args = p.parse_args()

    if args.mesin == "ekstraktif":
        lapor("PERINGATAN: mesin ekstraktif TIDAK BISA MENOLAK MENJAWAB.")
        lapor("            Ia selalu menyalin potongan paling mirip, walaupun jawabannya")
        lapor("            tidak ada di korpus. Mesin ini hanya titik acuan pembanding")
        lapor("            saat evaluasi. Untuk memakai sistem yang sebenarnya, hapus")
        lapor("            '--mesin ekstraktif' dari perintah.\n")

    penjawab = Penjawab(mesin=args.mesin, model=args.model, k=args.k,
                        hibrida=not args.tanpa_hibrida,
                        kuantisasi=("4bit" if args.empat_bit else
                                    "tidak" if args.tanpa_kuant else "otomatis"))
    j = penjawab.jawab(args.pertanyaan, sasaran=args.sasaran)

    lapor(f"Pertanyaan : {args.pertanyaan}")
    lapor(f"Sasaran    : {args.sasaran} | mesin: {args.mesin}")
    if j.ditolak:
        lapor(f"DITOLAK    : {j.alasan_tolak}")
    lapor("")
    print(j.cetak())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
