"""Pengumpul korpus: halaman web publik seputar CKG (Ayo Sehat).

    python src/collect_web.py

Melengkapi tiga pengumpul lain:
    collect_asik.py  -> Pusat Bantuan ASIK (Markdown)
    collect_faq.py   -> FAQ SATUSEHAT (JSON di balik aplikasi Nuxt)
    collect_pdf.py   -> Juknis CKG (PDF)
    collect_web.py   -> halaman HTML biasa                      <- berkas ini

Halaman Ayo Sehat memakai judul-judul berbentuk pertanyaan ("Siapa Aja yang
Mendapatkan Cek Kesehatan Gratis Ini?", "Jenis Pemeriksaannya Apa Saja?"), dan
bahasanya sudah ditulis untuk orang awam. Karena itu judulnya DIPERTAHANKAN
sebagai heading Markdown: strukturnya jadi titik potong yang rapi, dan teks
pertanyaannya ikut masuk ke potongan -- persis alasan yang membuat FAQ bekerja
baik saat dicari.

Daftar halaman sengaja ditulis eksplisit, bukan menjelajah seluruh situs.
Ayo Sehat memuat ribuan artikel kesehatan umum; mengambil semuanya akan
mengulangi kesalahan yang sudah terbukti pada Pusat Bantuan ASIK -- korpus
melebar jauh dari CKG dan mengotori hasil pencarian.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from utils import DATA_MENTAH, ambil, hari_ini, lapor, sesi, sha256_teks, slug, tulis_json

# Halaman yang diambil. Ditambah manual dan sadar, bukan hasil penjelajahan.
HALAMAN = [
    {
        "url": "https://ayosehat.kemkes.go.id/diet-sehat/cek-kesehatan-gratis",
        "nama": "Ayo Sehat — Cek Kesehatan Gratis",
        "sasaran": "masyarakat",
    },
    {
        "url": "https://ayosehat.kemkes.go.id/agenda-kegiatan/cek-kesehatan-gratis-ckg-sekolah",
        "nama": "Ayo Sehat — Cek Kesehatan Gratis (CKG) Sekolah",
        "sasaran": "sekolah",
    },
    # Dua halaman berikut ditambahkan belakangan, setelah pengujian cakupan
    # menunjukkan sisi masyarakat terlalu tipis: satu artikel FAQ yang sama
    # ("Bagaimana cara mendaftar CKG Umum?") menjadi hasil teratas untuk lima
    # pertanyaan yang berbeda jauh -- tanda tidak ada dokumen yang lebih
    # spesifik untuk direbut.
    {
        "url": "https://ayosehat.kemkes.go.id/cek-kesehatan-gratis",
        "nama": "Ayo Sehat — Cek Kesehatan Gratis: Manfaat dan Jenis Pemeriksaan",
        "sasaran": "masyarakat",
    },
]

# TIDAK diambil, walau muncul di hasil pencarian:
#
#   https://ayosehat.kemkes.go.id/diet-sehat/pemeriksaan-kesehatan-gratis
#
# Isinya artikel yang PERSIS SAMA dengan /diet-sehat/cek-kesehatan-gratis --
# Ayo Sehat menerbitkannya di dua alamat. Sudah diperiksa potongan per
# potongan: kelimanya beda tepat 8 huruf, dan seluruh selisihnya berasal dari
# jejak judul yang ditambahkan chunk.py, bukan dari isi.
#
# Ini luput dari penyaring ganda di chunk.py karena penyaring itu membandingkan
# hash teks LENGKAP, sedangkan jejak judul sudah menempel di depan. Dibuang di
# sini saja: menambah pendeteksi nyaris-sama demi satu kasus lebih rumit
# daripada manfaatnya. Kalau nanti duplikat semacam ini muncul lagi berkali-kali,
# barulah penyaringnya diubah supaya membandingkan isi tanpa jejak judul.

# Elemen tata letak yang tidak pernah membawa isi.
BUANG = ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "iframe"]

# Baris menu situs yang ikut terbawa dan harus dibuang.
POLA_MENU = re.compile(r"^(beranda|profile|profil|program|siklus hidup|kontak|"
                       r"dilihat \d+ kali|bagikan|share)$", re.I)


# ---------------------------------------------------------------------------
# Pemotong blok "artikel lainnya" di ekor halaman
#
# Halaman Ayo Sehat menutup artikelnya dengan daftar artikel lain: "Gejala Happy
# Hypoxia", "Check Up Jantung Yang Bagus Apa Saja?", "Jangan Mendiagnosa Diri
# Sendiri Depresi". Semuanya di luar CKG, dan yang terakhir bahkan soal
# mendiagnosa diri sendiri -- persis isi yang dilarang masuk korpus ini.
#
# Cirinya khas dan bisa diandalkan: tiap blok itu cuma CUPLIKAN, jadi
# paragrafnya terpotong dan diakhiri elipsis. Jadi aturannya: potong mulai dari
# bagian pertama yang paragrafnya berakhir elipsis, DAN diikuti setidaknya satu
# bagian lain yang juga begitu. Syarat kedua penting supaya satu kalimat sah
# yang kebetulan berakhir "..." tidak memotong artikel di tengah.
# ---------------------------------------------------------------------------
POLA_CUPLIKAN = re.compile(r"(\.\.\.|…)\s*$")


def _bagian_cuplikan(blok: str) -> bool:
    isi = blok.split("\n", 1)[1].strip() if "\n" in blok else ""
    return bool(isi) and bool(POLA_CUPLIKAN.search(isi))


def buang_artikel_lain(teks: str) -> tuple[str, int]:
    """Kembalikan (teks tanpa ekor artikel lain, jumlah bagian yang dibuang)."""
    potong = re.split(r"(?m)^(?=## )", teks)
    if len(potong) < 2:
        return teks, 0

    for i in range(1, len(potong)):
        if _bagian_cuplikan(potong[i]) and any(
            _bagian_cuplikan(potong[j]) for j in range(i + 1, len(potong))
        ):
            return "".join(potong[:i]).strip(), len(potong) - i
    return teks, 0


def ke_markdown(html: str) -> str:
    """Ubah halaman jadi teks berjudul, judul dipertahankan sebagai heading.

    Judul dipertahankan karena di halaman ini judulnya justru berbentuk
    pertanyaan pengguna -- itu bahan paling berharga untuk pencarian.
    """
    sup = BeautifulSoup(html, "html.parser")
    for t in sup(BUANG):
        t.decompose()

    baris: list[str] = []
    for el in sup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        teks = el.get_text(" ", strip=True)
        if not teks or POLA_MENU.match(teks):
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            baris.append("")
            baris.append(("# " if el.name == "h1" else "## ") + teks)
            baris.append("")
        elif el.name == "li":
            baris.append("- " + teks)
        else:
            baris.append(teks)

    keluar, sebelum = [], None
    for b in baris:
        if b and b == sebelum:  # halaman ini kerap mengulang judul dua kali
            continue
        keluar.append(b)
        sebelum = b
    teks = re.sub(r"\n{3,}", "\n\n", "\n".join(keluar)).strip()
    teks, dibuang = buang_artikel_lain(teks)
    if dibuang:
        lapor(f"      {dibuang} bagian 'artikel lainnya' di ekor halaman dibuang")
    return teks


def main() -> int:
    p = argparse.ArgumentParser(description="Unduh halaman web publik seputar CKG.")
    p.add_argument("--keluaran", default=str(DATA_MENTAH / "web"))
    p.add_argument("--jeda", type=float, default=1.0)
    p.add_argument("--paksa", action="store_true")
    args = p.parse_args()

    keluaran = Path(args.keluaran)
    keluaran.mkdir(parents=True, exist_ok=True)
    s = sesi()

    catatan, berhasil, gagal = [], 0, 0
    for i, h in enumerate(HALAMAN, 1):
        # Nama berkas diambil dari SELURUH jalur URL, bukan ruas terakhirnya.
        # Ayo Sehat memuat dua halaman berbeda yang ruas terakhirnya sama:
        #   /cek-kesehatan-gratis            (artikel manfaat & jenis pemeriksaan)
        #   /diet-sehat/cek-kesehatan-gratis (halaman program)
        # Dengan ruas terakhir saja keduanya menjadi cek-kesehatan-gratis.md,
        # dan yang kedua dilewati diam-diam sebagai "sudah ada" -- hilang tanpa
        # pesan gagal satu pun.
        jalur = h["url"].split("://", 1)[-1].split("/", 1)[-1]
        berkas = keluaran / (slug(jalur) + ".md")
        if berkas.exists() and not args.paksa:
            lapor(f"  ({i}/{len(HALAMAN)}) dilewati, sudah ada: {berkas.name}")
            isi = berkas.read_text(encoding="utf-8")
        else:
            lapor(f"  ({i}/{len(HALAMAN)}) {h['nama']}")
            r = ambil(h["url"], s)
            if r is None:
                gagal += 1
                catatan.append({**h, "berkas": "", "status": "gagal"})
                continue
            isi = ke_markdown(r.text)
            berkas.write_text(isi, encoding="utf-8")
            berhasil += 1
            time.sleep(args.jeda)

        catatan.append({
            **h,
            "berkas": berkas.name,
            "status": "berhasil",
            "ukuran": len(isi),
            "sha256": sha256_teks(isi),
            "tanggal_ambil": hari_ini(),
        })

    tulis_json(keluaran / "_manifest.json", {
        "sumber": "Halaman web publik CKG",
        "lingkungan": "produksi",
        "tanggal_ambil": hari_ini(),
        "jumlah": len(catatan),
        "halaman": catatan,
    })

    lapor(f"\nSelesai: {berhasil} diunduh, {gagal} gagal")
    lapor(f"Mentahan : {keluaran}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
