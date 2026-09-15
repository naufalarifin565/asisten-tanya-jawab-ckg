"""Pengumpul korpus: Pusat Bantuan ASIK.

Alur:
  1. Unduh indeks `llms.txt` (daftar seluruh halaman bantuan).
  2. Unduh versi Markdown tiap halaman, satu per satu, dengan jeda.
  3. Simpan mentahan apa adanya ke data/raw/asik/ (belum dibersihkan sama sekali).
  4. Tulis _manifest.json: catatan judul, URL, status, dan sidik jari isi.

Kenapa mentahan disimpan apa adanya: supaya pembersihan di chunk.py bisa
diperbaiki dan dijalankan ulang berkali-kali tanpa perlu mengunduh lagi.
Server tidak dibebani ulang hanya karena aturan pembersihan kita berubah.

PERINGATAN: alamat bawaan masih lingkungan STAGING (lihat CLAUDE.md §9 no. 2).
Setiap berkas ditandai lingkungannya di manifest supaya tidak tertukar dengan
korpus produksi nanti.

Contoh pakai:
    python src/collect_asik.py --limit 5      # uji coba dulu, 5 halaman
    python src/collect_asik.py                # seluruh halaman
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from utils import (
    DATA_MENTAH,
    ambil,
    hari_ini,
    lapor,
    sesi,
    sha256_teks,
    slug,
    tulis_json,
)

INDEKS_BAWAAN = "https://asiksupport-stg.dto.kemkes.go.id/llms.txt"

# Baris indeks berbentuk:  - [Judul](https://...md): deskripsi opsional
POLA_BARIS = re.compile(r"^\s*-\s*\[(?P<judul>[^\]]+)\]\((?P<url>[^)\s]+)\)\s*(?::\s*(?P<deskripsi>.*))?$")
POLA_HEADING = re.compile(r"^##\s+(?P<judul>.+?)\s*$")


def urai_indeks(teks: str) -> list[dict]:
    """Ubah isi llms.txt jadi daftar halaman beserta kategorinya.

    Kategori diambil dari segmen pertama path URL (mis. .../asiksupport-stg/ptm/...
    -> "ptm"), bukan dari heading, karena indeks ini hanya punya satu heading
    untuk seluruh 162 halaman sehingga tidak membedakan apa pun.
    """
    halaman: list[dict] = []
    bagian = ""
    for baris in teks.splitlines():
        if m := POLA_HEADING.match(baris):
            bagian = m.group("judul")
            continue
        m = POLA_BARIS.match(baris)
        if not m:
            continue
        url = m.group("url")
        # Versi Markdown = URL halaman + ".md". Di indeks ini sudah berakhiran .md,
        # tapi tetap ditangani dua-duanya kalau format indeks berubah.
        url_md = url if url.endswith(".md") else url + ".md"
        url_html = url[:-3] if url.endswith(".md") else url

        potongan = [p for p in urlparse(url).path.split("/") if p]
        # potongan[0] = prefiks aplikasi ("asiksupport-stg"), potongan[1] = kategori
        kategori = potongan[1] if len(potongan) > 2 else "lainnya"
        jalur_relatif = "/".join(potongan[1:]).removesuffix(".md")

        halaman.append(
            {
                "judul": m.group("judul").strip(),
                "deskripsi": (m.group("deskripsi") or "").strip(),
                "bagian_indeks": bagian,
                "kategori": kategori,
                "jalur": jalur_relatif,
                "url_md": url_md,
                "url_html": url_html,
                "berkas": slug(jalur_relatif) + ".md",
            }
        )
    return halaman


def main() -> int:
    p = argparse.ArgumentParser(description="Unduh korpus Pusat Bantuan ASIK.")
    p.add_argument("--indeks", default=INDEKS_BAWAAN, help="URL llms.txt")
    p.add_argument("--keluaran", default=str(DATA_MENTAH / "asik"), help="folder tujuan")
    p.add_argument("--jeda", type=float, default=1.0, help="jeda antar permintaan (detik)")
    p.add_argument("--limit", type=int, default=0, help="batasi jumlah halaman (0 = semua)")
    p.add_argument("--kategori", default="", help="ambil kategori tertentu saja, dipisah koma (mis. ptm,data-individu)")
    p.add_argument("--paksa", action="store_true", help="unduh ulang walau berkas sudah ada")
    args = p.parse_args()

    keluaran = Path(args.keluaran)
    keluaran.mkdir(parents=True, exist_ok=True)
    s = sesi()

    lapor(f"[1/2] Mengambil indeks: {args.indeks}")
    r = ambil(args.indeks, s)
    if r is None:
        lapor("GAGAL: indeks tidak bisa diambil. Berhenti.")
        return 1
    (keluaran / "_llms.txt").write_text(r.text, encoding="utf-8")

    halaman = urai_indeks(r.text)
    lapor(f"      {len(halaman)} halaman terdaftar di indeks")

    if args.kategori:
        pilih = {k.strip() for k in args.kategori.split(",") if k.strip()}
        halaman = [h for h in halaman if h["kategori"] in pilih]
        lapor(f"      disaring ke kategori {sorted(pilih)}: {len(halaman)} halaman")
    if args.limit:
        halaman = halaman[: args.limit]
        lapor(f"      dibatasi ke {len(halaman)} halaman")

    lingkungan = "staging" if "-stg" in args.indeks else "produksi"
    if lingkungan == "staging":
        lapor("      CATATAN: sumber masih STAGING. Jangan dipakai sebagai korpus final.")

    lapor(f"[2/2] Mengunduh halaman (jeda {args.jeda} dtk antar permintaan)")
    catatan: list[dict] = []
    berhasil = dilewati = gagal = 0

    for i, h in enumerate(halaman, 1):
        tujuan = keluaran / h["berkas"]
        if tujuan.exists() and not args.paksa:
            isi = tujuan.read_text(encoding="utf-8")
            catatan.append({**h, "status": "dilewati", "ukuran": len(isi),
                            "sha256": sha256_teks(isi), "tanggal_ambil": hari_ini(),
                            "lingkungan": lingkungan})
            dilewati += 1
            continue

        lapor(f"  ({i}/{len(halaman)}) {h['judul']}")
        r = ambil(h["url_md"], s)
        if r is None:
            catatan.append({**h, "status": "gagal", "ukuran": 0, "sha256": "",
                            "tanggal_ambil": hari_ini(), "lingkungan": lingkungan})
            gagal += 1
        else:
            isi = r.text
            tujuan.write_text(isi, encoding="utf-8")
            catatan.append({**h, "status": "berhasil", "ukuran": len(isi),
                            "sha256": sha256_teks(isi), "tanggal_ambil": hari_ini(),
                            "lingkungan": lingkungan})
            berhasil += 1
        # Jeda hanya setelah permintaan yang benar-benar dikirim ke server.
        time.sleep(args.jeda)

    tulis_json(keluaran / "_manifest.json", {
        "sumber": "Pusat Bantuan ASIK",
        "indeks": args.indeks,
        "lingkungan": lingkungan,
        "tanggal_ambil": hari_ini(),
        "jumlah": len(catatan),
        "halaman": catatan,
    })

    lapor(f"\nSelesai: {berhasil} diunduh, {dilewati} dilewati (sudah ada), {gagal} gagal")
    lapor(f"Mentahan  : {keluaran}")
    lapor(f"Manifest  : {keluaran / '_manifest.json'}")
    if gagal:
        lapor("Jalankan ulang perintah yang sama untuk mencoba lagi yang gagal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
