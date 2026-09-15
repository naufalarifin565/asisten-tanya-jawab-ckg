"""Fungsi bantu yang dipakai bersama oleh skrip pengumpul korpus.

Ditaruh terpisah supaya logika "sopan mengunduh" (jeda, coba ulang, User-Agent
yang jujur) hanya ditulis sekali dan berlaku sama untuk semua sumber.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import requests

AKAR = Path(__file__).resolve().parent.parent
DATA_MENTAH = AKAR / "data" / "raw"
DATA_OLAHAN = AKAR / "data" / "processed"

# Identitas yang jujur: pemilik server berhak tahu siapa yang mengunduh dan untuk apa.
# Isi alamat kontak sebelum pengumpulan skala penuh (lihat README).
KONTAK = "isi-kontak-anda"
USER_AGENT = (
    "RAG-CKG-Magang/0.1 (proyek magang DTO Kemenkes RI; "
    f"pengumpulan dokumen publik untuk sistem tanya-jawab; kontak: {KONTAK})"
)


def lapor(pesan: str) -> None:
    """Cetak ke stderr supaya keluaran data (stdout) tidak tercampur log."""
    print(pesan, file=sys.stderr, flush=True)


def sesi() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "id-ID,id;q=0.9"})
    return s


def ambil(
    url: str,
    s: requests.Session,
    *,
    percobaan: int = 3,
    jeda_mundur: float = 2.0,
    timeout: int = 30,
) -> requests.Response | None:
    """Unduh satu URL. Kembalikan None kalau tetap gagal setelah dicoba ulang.

    Kegagalan sengaja tidak dilempar sebagai exception: satu halaman rusak tidak
    boleh menggagalkan pengumpulan 161 halaman lainnya. Yang gagal dicatat di
    manifest supaya bisa diulang belakangan.
    """
    for ke in range(1, percobaan + 1):
        try:
            r = s.get(url, timeout=timeout)
        except requests.RequestException as e:
            lapor(f"    ! galat jaringan ({ke}/{percobaan}): {e}")
        else:
            if r.status_code == 200:
                return r
            # 429 = terlalu sering, 5xx = server bermasalah. Keduanya layak dicoba ulang.
            if r.status_code == 429 or r.status_code >= 500:
                tunggu = float(r.headers.get("Retry-After", jeda_mundur * ke))
                lapor(f"    ! HTTP {r.status_code}, tunggu {tunggu:.0f} dtk ({ke}/{percobaan})")
                time.sleep(tunggu)
                continue
            # 404 dan sejenisnya: percuma diulang.
            lapor(f"    ! HTTP {r.status_code} (tidak diulang): {url}")
            return None
        time.sleep(jeda_mundur * ke)
    return None


def sha256_teks(teks: str) -> str:
    return hashlib.sha256(teks.encode("utf-8")).hexdigest()


def hari_ini() -> str:
    return date.today().isoformat()


def slug(teks: str, panjang_maks: int = 120) -> str:
    """Ubah teks/URL jadi nama berkas yang aman di Windows maupun Linux."""
    teks = re.sub(r"[^A-Za-z0-9]+", "-", teks).strip("-").lower()
    return teks[:panjang_maks] or "tanpa-nama"


def rapikan_spasi(teks: str) -> str:
    """Normalisasi spasi tak-putus dan baris kosong berlebih."""
    teks = teks.replace("\u00a0", " ").replace("\u200b", "")
    teks = re.sub(r"[ \t]+\n", "\n", teks)
    teks = re.sub(r"\n{3,}", "\n\n", teks)
    return teks.strip()


def perkiraan_token(teks: str) -> int:
    """Perkiraan kasar: ~4 karakter per token untuk teks Bahasa Indonesia.

    Dipakai hanya untuk memilih ambang pemotongan, bukan untuk penagihan apa pun,
    jadi ketelitiannya cukup. Kalau nanti model embedding-nya sudah dipilih,
    ganti dengan tokenizer aslinya.
    """
    return max(1, round(len(teks) / 4))


def tulis_json(path: Path, obyek: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obyek, ensure_ascii=False, indent=2), encoding="utf-8")


def baca_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def tulis_jsonl(path: Path, baris: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for b in baris:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
            n += 1
    return n


def baca_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(b) for b in f if b.strip()]


def senyapkan_log_model() -> None:
    """Redam laporan pemuatan model dari transformers.

    Laporan itu berguna saat menelusuri masalah, tapi muncul di setiap
    pemanggilan dan menenggelamkan hasil pencarian yang justru ingin dibaca.
    """
    import warnings

    warnings.filterwarnings("ignore", category=FutureWarning)
    try:
        from transformers import logging as log_hf

        log_hf.set_verbosity_error()
    except ImportError:
        pass


# --- Sasaran yang boleh lebih dari satu -------------------------------------
#
# Sebagian besar potongan hanya menyasar satu kelompok pembaca, dan untuk itu
# `sasaran` cukup berupa satu kata. Tapi ada dokumen yang MENYEBUT SENDIRI dua
# kelompok sekaligus. Juknis CKG Sekolah (Kepmenkes 770/2025) hal. 8 mendaftar
# sasarannya: dinas kesehatan, kepala puskesmas, DAN kepala sekolah/madrasah
# serta tim pelaksana UKS.
#
# Memaksa dokumen itu memilih satu nilai berarti menuliskan metadata yang tidak
# benar, dan akibatnya bukan sekadar kurang rapi: retrieve.py menyaring SEBELUM
# memeringkat, jadi kelompok yang tidak terpilih tidak akan pernah menemukan
# dokumen ini sama sekali -- berapa pun bagusnya embedding.
#
# Karena itu `sasaran` boleh berupa satu kata ATAU daftar kata. Dua penolong di
# bawah ini dipakai di semua tempat yang membaca nilai tersebut, supaya
# perbedaan bentuk cukup ditangani di satu tempat.

def daftar_sasaran(potongan: dict) -> list[str]:
    """Kembalikan sasaran sebuah potongan selalu sebagai daftar."""
    nilai = potongan.get("sasaran", "")
    if isinstance(nilai, str):
        return [nilai] if nilai else []
    return [str(x) for x in nilai if x]


def cocok_sasaran(potongan: dict, diminta: str | None) -> bool:
    """Apakah potongan ini ditujukan untuk kelompok pembaca yang diminta?"""
    if not diminta:
        return True
    return diminta in daftar_sasaran(potongan)
