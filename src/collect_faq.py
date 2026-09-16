"""Pengumpul korpus: FAQ CKG di SATUSEHAT (bawaan: kategori Fasyankes).

Halaman FAQ SATUSEHAT bukan HTML biasa, melainkan aplikasi Nuxt. Jadi ada dua
jalan masuk, dan skrip ini memakai keduanya:

  1. DAFTAR TOPIK -> ada API JSON resmi yang dipakai aplikasinya sendiri:
         GET /mobile/api/faq/topic-list?categoryId=...
     Mengembalikan {id, label, categoryLabel} untuk seluruh topik satu kategori.

  2. ISI TIAP TOPIK -> tidak punya API. Tapi halaman topiknya dirender di server,
     dan datanya tertanam di <script id="__NUXT_DATA__"> dalam bentuk larik
     "devalue" (nilai disimpan datar, saling menunjuk lewat indeks). Di situ ada
     question, content (HTML), categoryLabel, dan lastUpdated.

Kenapa tidak mengikis HTML tampilannya saja: tata letak halaman bisa berubah
sewaktu-waktu, sedangkan struktur data ini lebih stabil dan sudah bersih dari
elemen tampilan. Kalau suatu saat tetap berubah, skrip akan gagal dengan pesan
jelas, bukan diam-diam menghasilkan teks kosong.

Contoh pakai:
    python src/collect_faq.py                       # FAQ CKG Fasyankes (nakes)
    python src/collect_faq.py --kategori umum       # FAQ CKG Umum (masyarakat)
    python src/collect_faq.py --limit 3             # uji coba
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from utils import DATA_MENTAH, ambil, hari_ini, lapor, sesi, sha256_teks, tulis_json

PANGKAL = "https://satusehat.kemkes.go.id/mobile"

# categoryId diambil dari alamat halaman FAQ SATUSEHAT Mobile. Nama pendek supaya tidak perlu menyalin UUID.
KATEGORI = {
    "fasyankes": {
        "id": "7cd776af-e9d8-496a-b7fd-1f149db25232",
        "nama": "FAQ CKG Fasyankes",
        "sasaran": "nakes",
    },
    "umum": {
        "id": "e4a5f2e3-343b-4648-90e5-0fa869b6c146",
        "nama": "FAQ CKG Umum",
        "sasaran": "masyarakat",
    },
    "sekolah": {
        "id": "617757c6-2104-4f71-9d6e-3b0bb0d19098",
        "nama": "FAQ CKG Sekolah",
        "sasaran": "sekolah",
    },

    # --- Prasyarat mengikuti CKG, bukan CKG itu sendiri ---
    #
    # Empat kategori berikut bukan tentang CKG, tapi tentang LANGKAH YANG HARUS
    # DILEWATI DULU sebelum bisa mendaftar: punya akun SATUSEHAT, berhasil
    # login, profilnya terverifikasi (KYC), dan bisa menambahkan profil anak
    # sebagai profil terhubung.
    #
    # Ditambahkan setelah menyadari sistem bisa menjawab "bagaimana cara daftar
    # CKG?" tetapi bungkam begitu penanya tersangkut di langkah pertama
    # ("saya tidak bisa login") -- padahal justru di situ orang paling butuh
    # bantuan. Kategori lain di SATUSEHAT (Resume Medis, Dashboard Kesehatan,
    # syarat & ketentuan) SENGAJA tidak diambil karena tidak menyentuh CKG.
    "kyc": {
        "id": "23b96d27-240a-49ba-acaf-75fafd7ec758",
        "nama": "FAQ Verifikasi Profil (KYC)",
        "sasaran": "masyarakat",
    },
    "akun": {
        "id": "66a03477-fecd-4a34-a181-e8c0bf580beb",
        "nama": "FAQ Akun dan Keamanan SATUSEHAT",
        "sasaran": "masyarakat",
    },
    "profil_terhubung": {
        "id": "19b8c7f7-b004-4525-8243-a79dda305af3",
        "nama": "FAQ Profil Terhubung",
        "sasaran": "masyarakat",
    },
    "login": {
        "id": "4d445909-f5f8-49c7-bdfa-128363974485",
        "nama": "FAQ Kendala Login SATUSEHAT Mobile",
        "sasaran": "masyarakat",
    },

    # --- Setelah CKG selesai: di mana hasilnya dilihat ---
    #
    # Ditambahkan setelah pengujian cakupan memperlihatkan pertanyaan "di mana
    # saya bisa melihat hasil CKG saya" jatuh ke artikel yang khusus membahas
    # hasil CKG tahun 2025, dan "apakah WNA bisa ikut" tidak punya dokumen
    # sama sekali. Keduanya terjawab di kategori Resume Medis, yang namanya
    # tidak menyebut CKG sehingga sempat terlewat.
    #
    # Hasil CKG memang mendarat di Resume Medis: Juknis CKG BAB IV menyatakan
    # "keseluruhan hasil CKG dikirimkan melalui WA dan dapat diakses melalui
    # akun SSM". Jadi ini bukan fitur lain, melainkan ujung dari alur CKG.
    "resume_medis": {
        "id": "bc5893fc-017c-431a-9161-66d152ec6aac",
        "nama": "FAQ Resume Medis SATUSEHAT",
        "sasaran": "masyarakat",
    },
}

# === KATEGORI YANG SENGAJA TIDAK DIAMBIL ===
#
# SATUSEHAT Mobile punya 26 kategori FAQ; yang diambil di atas hanya 8. Sisanya
# ditolak dengan alasan, bukan karena terlewat:
#
#   BUKAN CKG -- Vaksin Booster, Vaksin Non Indonesia, Data dan Sertifikat
#   Vaksinasi, Imunisasi Rutin, Telemedisin Isoman, Pengingat Minum Obat, Cari
#   Obat, Cari Rawat Inap, Cari Nakes, Dokter Praktik Mandiri, SATUSEHAT Health
#   Pass, Transplantasi Organ, Informasi Kesehatan. Semuanya fitur lain di
#   aplikasi yang sama. Memasukkannya menambah ratusan potongan yang bersaing
#   di setiap pencarian tanpa pernah menjawab satu pun pertanyaan CKG.
#
#   MELANGGAR BATAS KLINIS (prinsip wajib no. 3) -- ini yang perlu diwaspadai,
#   karena justru kategori terbesar yang belum diambil:
#     * Diari Kesehatan (29 topik) memuat "Apa yang dapat saya lakukan jika
#       kadar kolesterol saya tinggi?", "Apakah saya memiliki kanker paru?",
#       dan skrining kesehatan jiwa.
#     * Pertumbuhan Anak (7 topik) memuat "Apa yang perlu saya lakukan jika
#       hasil pengukuran anak kurang baik?".
#   Judul proyek yang melebar dari "pencatatan CKG" ke "tanya jawab CKG" TIDAK
#   melonggarkan batas ini. Yang melebar cakupan topiknya, bukan kewenangannya.
#
#   Dashboard Kesehatan (6 topik) ditahan karena isinya menafsirkan arti warna
#   indikator kesehatan seseorang -- perlu diputuskan pembimbing lebih dulu.

POLA_NUXT = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def urai_nuxt(html: str) -> dict | None:
    """Ambil data satu topik dari muatan __NUXT_DATA__ halaman topik.

    Formatnya larik datar: setiap nilai di dalam obyek bukan nilai aslinya,
    melainkan INDEKS ke elemen larik yang lain. Contoh:
        [ ..., {"id": 6, "question": 7, "content": 11}, "uuid...", "Judul...", ... ]
    Jadi cukup cari obyek yang punya kunci "question" dan "content", lalu
    tukar tiap indeks dengan nilai sebenarnya. Tidak perlu menerjemahkan
    seluruh larik -- hanya bagian yang kita butuhkan.
    """
    m = POLA_NUXT.search(html)
    if not m:
        return None
    try:
        larik = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

    for elemen in larik:
        if isinstance(elemen, dict) and "question" in elemen and "content" in elemen:
            hasil = {}
            for kunci, rujuk in elemen.items():
                nilai = larik[rujuk] if isinstance(rujuk, int) and 0 <= rujuk < len(larik) else rujuk
                hasil[kunci] = nilai if isinstance(nilai, (str, int, float, bool)) or nilai is None else None
            return hasil
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Unduh FAQ CKG dari SATUSEHAT.")
    p.add_argument("--kategori", default="fasyankes", choices=sorted(KATEGORI),
                   help="kategori FAQ yang diambil")
    p.add_argument("--keluaran", default=str(DATA_MENTAH / "faq"), help="folder tujuan")
    p.add_argument("--jeda", type=float, default=1.0, help="jeda antar permintaan (detik)")
    p.add_argument("--limit", type=int, default=0, help="batasi jumlah topik (0 = semua)")
    p.add_argument("--paksa", action="store_true", help="unduh ulang walau berkas sudah ada")
    args = p.parse_args()

    kat = KATEGORI[args.kategori]
    keluaran = Path(args.keluaran) / args.kategori
    keluaran.mkdir(parents=True, exist_ok=True)
    s = sesi()

    lapor(f"[1/2] Mengambil daftar topik: {kat['nama']}")
    r = ambil(f"{PANGKAL}/api/faq/topic-list?categoryId={kat['id']}", s)
    if r is None:
        lapor("GAGAL: daftar topik tidak bisa diambil. Berhenti.")
        return 1
    daftar = r.json().get("data", [])
    lapor(f"      {len(daftar)} topik terdaftar")
    if not daftar:
        lapor("GAGAL: daftar kosong. Kemungkinan categoryId berubah. Berhenti.")
        return 1

    if args.limit:
        daftar = daftar[: args.limit]
        lapor(f"      dibatasi ke {len(daftar)} topik")

    lapor(f"[2/2] Mengunduh isi tiap topik (jeda {args.jeda} dtk)")
    catatan: list[dict] = []
    berhasil = dilewati = gagal = 0

    for i, topik in enumerate(daftar, 1):
        tid = topik["id"]
        url_html = f"{PANGKAL}/faq/topic/{tid}"
        tujuan = keluaran / f"{tid}.json"

        if tujuan.exists() and not args.paksa:
            dilewati += 1
            catatan.append({"id": tid, "judul": topik["label"], "url": url_html,
                            "berkas": tujuan.name, "status": "dilewati"})
            continue

        lapor(f"  ({i}/{len(daftar)}) {topik['label'][:70]}")
        r = ambil(url_html, s)
        data = urai_nuxt(r.text) if r is not None else None

        if not data or not (data.get("content") or "").strip():
            alasan = "tidak bisa diunduh" if r is None else "struktur halaman tidak dikenali / isi kosong"
            lapor(f"    ! dilewati: {alasan}")
            catatan.append({"id": tid, "judul": topik["label"], "url": url_html,
                            "berkas": "", "status": "gagal", "alasan": alasan})
            gagal += 1
        else:
            rekam = {
                "id": tid,
                "pertanyaan": data.get("question") or topik["label"],
                "isi_html": data["content"],
                "kategori_label": data.get("categoryLabel") or topik.get("categoryLabel", ""),
                "kategori_kunci": args.kategori,
                "sasaran": kat["sasaran"],
                "sumber_nama": kat["nama"],
                "url": url_html,
                "tanggal_pembaruan": (data.get("lastUpdated") or "")[:10],
                "tanggal_ambil": hari_ini(),
            }
            tujuan.write_text(json.dumps(rekam, ensure_ascii=False, indent=2), encoding="utf-8")
            catatan.append({"id": tid, "judul": rekam["pertanyaan"], "url": url_html,
                            "berkas": tujuan.name, "status": "berhasil",
                            "sha256": sha256_teks(rekam["isi_html"])})
            berhasil += 1
        time.sleep(args.jeda)

    tulis_json(keluaran / "_manifest.json", {
        "sumber": kat["nama"],
        "kategori_id": kat["id"],
        "sasaran": kat["sasaran"],
        "tanggal_ambil": hari_ini(),
        "jumlah": len(catatan),
        "topik": catatan,
    })

    lapor(f"\nSelesai: {berhasil} diunduh, {dilewati} dilewati (sudah ada), {gagal} gagal")
    lapor(f"Mentahan  : {keluaran}")
    lapor(f"Manifest  : {keluaran / '_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
