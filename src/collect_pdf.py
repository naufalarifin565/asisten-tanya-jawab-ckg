"""Pengumpul korpus: Petunjuk Teknis CKG (Kepmenkes HK.01.07/MENKES/84/2026).

    python src/collect_pdf.py                    # BAB I-IV dan VI-IX
    python src/collect_pdf.py --sertakan-bab-v   # termasuk bab klinis (baca dulu!)

Alur: unduh PDF -> ekstrak teks per halaman -> kelompokkan per BAB -> simpan
satu berkas teks per BAB + manifest berisi rentang halamannya.

=== KENAPA BAB V DILEWATI (BAWAAN) ===

Dokumen ini 122 halaman, dan BAB V "Alur dan Tindak Lanjut" memakan 61 halaman
di antaranya -- separuh dokumen. Isinya tabel tiga kolom:

    Pemeriksaan | Hasil | Tindak lanjut
    ...         | Gizi Kurang | 1. Edukasi gizi ... 2. Lakukan uji kulit
                              | tuberkulin (mantoux) ...

Itu pedoman klinis: memetakan hasil pemeriksaan ke tindakan medis. Rencana awal proyek
meminta pedoman klinis DITAHAN DULU, dan prinsip wajib no. 3 melarang sistem menafsirkan
hasil pemeriksaan atau memberi saran pengobatan.

Penapis pertanyaan klinis di answer.py menyaring CARA BERTANYA, bukan isi
korpus. Selama bahannya tidak ada di korpus, sistem tidak mungkin memberi
arahan klinis walaupun penapisnya kebobolan. Begitu bahannya dimasukkan,
keamanan sistem bergantung sepenuhnya pada penapis yang tidak sempurna --
pertukaran yang tidak sepadan untuk sistem kesehatan.

Keputusan ini sengaja dibuat bisa dibatalkan dengan --sertakan-bab-v, supaya
terlihat dan bisa diubah kalau pembimbing memutuskan lain. Bukan disembunyikan.

=== KENAPA NOMOR HALAMAN IKUT DISIMPAN ===

Ini dokumen hukum 122 halaman. Menjawab "menurut Juknis CKG..." tanpa menyebut
halaman membuat rujukannya mustahil diperiksa orang lain. Setiap potongan
membawa nomor halaman, dan URL-nya diberi akhiran #page=NNN supaya pembaca
langsung mendarat di halaman yang dimaksud.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from utils import DATA_MENTAH, ambil, hari_ini, lapor, sesi, sha256_teks, slug, tulis_json

# === DUA DOKUMEN, BUKAN SATU ===
#
# Kepmenkes 84/2026 sendiri menyerahkan kelompok usia sekolah ke dokumen lain:
#
#     "Pelaksanaan CKG pada usia sekolah dan remaja mengikuti Petunjuk
#      Teknis CKG Sekolah."          -- Kepmenkes 84/2026, BAB II, hal. 9
#
# Dokumen yang dimaksud adalah Kepmenkes 770/2025. Selama dokumen itu belum
# masuk, korpus tidak punya dasar hukum apa pun untuk pertanyaan CKG Sekolah
# -- yang tersedia hanya FAQ dan halaman kampanye.
#
# Kedua dokumen dipisah ke folder mentah masing-masing supaya nomor halaman,
# manifest, dan sitasinya tidak tercampur. Nomor BAB keduanya BERBEDA ARTI:
# BAB V di 84/2026 adalah pedoman klinis (dibuang), sedangkan BAB V di 770/2025
# adalah Pencatatan dan Pelaporan (justru inti proyek ini). Karena itu bab yang
# dilewati ditentukan per dokumen, bukan satu daftar global.
DOKUMEN = {
    "ckg": {
        "url": "https://kesprimkom.kemkes.go.id/assets/uploads/contents/others/2026kepmenkes084.pdf",
        "nama": "Petunjuk Teknis CKG (Kepmenkes HK.01.07/MENKES/84/2026)",
        "berkas_pdf": "kepmenkes-084-2026.pdf",
        "folder": "juknis",
        # BAB V "Alur dan Tindak Lanjut" = 61 dari 122 halaman, isinya tabel
        # hasil-pemeriksaan -> tindakan medis. Lihat penjelasan di atas.
        "bab_klinis": {"V"},
    },
    "ckg_sekolah": {
        "url": "https://jdih.kemkes.go.id/storage/documents/pdfs/2025kepmenkes770.pdf",
        "nama": "Petunjuk Teknis CKG Sekolah (Kepmenkes HK.01.07/MENKES/770/2025)",
        "berkas_pdf": "kepmenkes-770-2025-ckg-sekolah.pdf",
        "folder": "juknis_sekolah",
        # Tidak ada satu bab pun yang seluruhnya klinis di dokumen ini. Bagian
        # klinisnya berupa Tabel 2-5 "Tindak Lanjut Hasil Pemeriksaan" yang
        # menyelip DI DALAM BAB III, berdampingan dengan bagian yang justru
        # paling berguna (pembagian peran, alur pelaksanaan). Membuang seluruh
        # BAB III berarti membuang bayi bersama air mandinya, jadi penyaringan
        # untuk dokumen ini dikerjakan per potongan oleh isi_klinis() di
        # chunk.py, bukan per bab di sini.
        "bab_klinis": set(),
    },
}

# Nama lama dipertahankan supaya pemanggilan --url ... yang sudah ada tetap jalan.
URL_JUKNIS = DOKUMEN["ckg"]["url"]
NAMA_DOKUMEN = DOKUMEN["ckg"]["nama"]
BAB_KLINIS = DOKUMEN["ckg"]["bab_klinis"]

POLA_BAB = re.compile(r"^\s*(BAB\s+([IVXL]+))\s*$", re.M)
# Header berjalan tiap halaman: "- 45 -". Harus dibuang, kalau tidak angkanya
# ikut masuk teks dan mengacaukan potongan.
POLA_NOMOR_HALAMAN = re.compile(r"^\s*-\s*\d+\s*-\s*$", re.M)

# Kop/kaki berjalan dokumen JDIH, tercetak di SETIAP halaman:
#     KEMENTERIAN KESEHATAN
#     jdih.kemkes.go.id
# Kalau dibiarkan, dua baris ini ikut ke dalam potongan dan bisa muncul di
# tengah jawaban yang dikutip ke pengguna -- terlihat seperti kalimat dokumen
# padahal cuma cap halaman. Sudah terlihat nyata pada jawaban uji.
POLA_KAKI_JDIH = re.compile(
    r"^\s*(?:KEMENTERIAN\s+KESEHATAN|jdih\.kemkes\.go\.id|"
    r"www\.jdih\.kemkes\.go\.id)\s*$", re.I | re.M)

# Penanda halaman internal, dibuang lagi oleh chunk.py setelah dipakai.
PENANDA_HALAMAN = "[[hal:%d]]"


def rapikan_halaman(teks: str) -> str:
    teks = POLA_NOMOR_HALAMAN.sub("", teks)
    teks = POLA_KAKI_JDIH.sub("", teks)
    teks = teks.replace(" ", " ")
    # PDF sering menyisakan spasi ganda dan baris pecah di tengah kalimat.
    teks = re.sub(r"[ \t]{2,}", " ", teks)
    teks = re.sub(r"\n{3,}", "\n\n", teks)
    return teks.strip()


def petakan_bab(halaman: list[str]) -> list[dict]:
    """Kembalikan daftar bab beserta halaman awal, judul, dan isinya.

    Judul bab berada di baris SETELAH penanda "BAB VI", jadi diambil dari
    potongan teks tepat sesudahnya.
    """
    penanda = []
    for i, t in enumerate(halaman, 1):
        for m in POLA_BAB.finditer(t):
            sisa = t[m.end():].strip().split("\n")
            judul = next((s.strip() for s in sisa if s.strip()), "")
            penanda.append({"halaman": i, "angka": m.group(2), "judul": judul})

    bab = []
    for k, p in enumerate(penanda):
        awal = p["halaman"]
        akhir = penanda[k + 1]["halaman"] - 1 if k + 1 < len(penanda) else len(halaman)
        isi = "\n".join(halaman[awal - 1:akhir])
        bab.append({
            "angka": p["angka"],
            "judul": p["judul"],
            "halaman_awal": awal,
            "halaman_akhir": akhir,
            "jumlah_halaman": akhir - awal + 1,
            "teks": isi,
        })
    return bab


def main() -> int:
    p = argparse.ArgumentParser(description="Ekstrak Juknis CKG dari PDF.")
    p.add_argument("--dokumen", default="ckg", choices=sorted(DOKUMEN),
                   help="ckg = Kepmenkes 84/2026; ckg_sekolah = Kepmenkes 770/2025")
    p.add_argument("--semua", action="store_true", help="ambil kedua dokumen sekaligus")
    p.add_argument("--url", default=None, help="menimpa URL dokumen terpilih")
    p.add_argument("--keluaran", default=None, help="menimpa folder keluaran")
    p.add_argument("--sertakan-bab-v", action="store_true",
                   help="ikut mengambil bab pedoman klinis (baca penjelasan di kepala berkas ini)")
    p.add_argument("--paksa", action="store_true", help="unduh ulang PDF walau sudah ada")
    args = p.parse_args()

    if args.semua:
        if args.url or args.keluaran:
            lapor("GAGAL: --semua tidak bisa digabung dengan --url atau --keluaran.")
            return 1
        kode = 0
        for nama in sorted(DOKUMEN):
            lapor(f"\n########## {DOKUMEN[nama]['nama']} ##########")
            kode |= ambil_satu(nama, args)
        return kode
    return ambil_satu(args.dokumen, args)


def ambil_satu(nama_dokumen: str, args) -> int:
    dok = DOKUMEN[nama_dokumen]
    url = args.url or dok["url"]
    keluaran = Path(args.keluaran) if args.keluaran else DATA_MENTAH / dok["folder"]
    bab_klinis = dok["bab_klinis"]

    keluaran.mkdir(parents=True, exist_ok=True)
    berkas_pdf = keluaran / dok["berkas_pdf"]

    if berkas_pdf.exists() and not args.paksa:
        lapor(f"[1/3] PDF sudah ada, tidak diunduh ulang: {berkas_pdf}")
    else:
        lapor(f"[1/3] Mengunduh: {url}")
        r = ambil(url, sesi(), timeout=120)
        if r is None:
            lapor("GAGAL: PDF tidak bisa diunduh.")
            return 1
        berkas_pdf.write_bytes(r.content)
        lapor(f"      {len(r.content):,} byte")

    lapor("[2/3] Mengekstrak teks")
    try:
        from pypdf import PdfReader
    except ImportError:
        lapor("GAGAL: pypdf belum terpasang. Jalankan: pip install pypdf")
        return 1

    pembaca = PdfReader(str(berkas_pdf))
    # Penanda halaman disisipkan supaya chunk.py bisa menyebutkan nomor halaman
    # di setiap sitasi. Untuk dokumen hukum 122 halaman, rujukan tanpa nomor
    # halaman praktis tidak bisa diperiksa orang lain.
    halaman = [
        (PENANDA_HALAMAN % (i + 1)) + "\n" + rapikan_halaman(h.extract_text() or "")
        for i, h in enumerate(pembaca.pages)
    ]
    lapor(f"      {len(halaman)} halaman")

    bab = petakan_bab(halaman)
    if not bab:
        lapor("GAGAL: tidak ada penanda BAB yang terdeteksi. Struktur PDF mungkin berubah.")
        return 1

    lapor("[3/3] Menyimpan per bab")
    catatan = []
    for b in bab:
        klinis = b["angka"] in bab_klinis
        diambil = args.sertakan_bab_v or not klinis

        nama = f"bab-{b['angka'].lower()}-{slug(b['judul'])}.txt"
        if diambil:
            (keluaran / nama).write_text(b["teks"], encoding="utf-8")

        tanda = "" if diambil else "   <- DILEWATI (pedoman klinis)"
        lapor(f"  BAB {b['angka']:<5} hal {b['halaman_awal']:>3}-{b['halaman_akhir']:<3}"
              f" {b['judul'][:44]:46}{tanda}")

        catatan.append({
            "angka": b["angka"],
            "judul": b["judul"],
            "halaman_awal": b["halaman_awal"],
            "halaman_akhir": b["halaman_akhir"],
            "jumlah_halaman": b["jumlah_halaman"],
            "berkas": nama if diambil else "",
            "diambil": diambil,
            "alasan_dilewati": "pedoman klinis (CLAUDE.md §2 no. 3 dan §3)" if not diambil else "",
            "ukuran": len(b["teks"]) if diambil else 0,
            "sha256": sha256_teks(b["teks"]) if diambil else "",
        })

    tulis_json(keluaran / "_manifest.json", {
        "sumber": dok["nama"],
        "dokumen": nama_dokumen,
        "url": url,
        "lingkungan": "produksi",
        "tanggal_ambil": hari_ini(),
        "jumlah_halaman": len(halaman),
        "sertakan_bab_klinis": args.sertakan_bab_v,
        "bab": catatan,
    })

    diambil = sum(1 for c in catatan if c["diambil"])
    hal_diambil = sum(c["jumlah_halaman"] for c in catatan if c["diambil"])
    lapor(f"\n{diambil} dari {len(catatan)} bab diambil ({hal_diambil} dari {len(halaman)} halaman)")
    lapor(f"Mentahan : {keluaran}")
    lapor(f"Manifest : {keluaran / '_manifest.json'}")
    if bab_klinis and not args.sertakan_bab_v:
        lapor("\nCATATAN: bab pedoman klinis sengaja dilewati. Lihat penjelasan di")
        lapor("         kepala src/collect_pdf.py sebelum memakai --sertakan-bab-v.")
    elif not bab_klinis:
        lapor("\nCATATAN: dokumen ini tidak punya bab yang seluruhnya klinis. Bagian")
        lapor("         klinisnya (tabel Tindak Lanjut di dalam BAB III) disaring per")
        lapor("         potongan oleh isi_klinis() di src/chunk.py, bukan di sini.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
