"""Antarmuka percakapan sederhana di terminal.

    python src/app.py                      # menanyakan dulu Anda nakes atau masyarakat
    python src/app.py --sasaran masyarakat # lewati pertanyaan pembuka
    python src/app.py --mesin ekstraktif   # tanpa LLM (pembanding, tidak bisa menolak)
    python src/app.py --model Qwen/Qwen2.5-1.5B-Instruct   # model lebih ringan

PENTING: jangan menjalankan dua proses yang memuat model bahasa secara
bersamaan. VRAM 8 GB tidak cukup untuk dua model sekaligus, dan yang terjadi
bukan pesan error yang rapi melainkan macet. Pastikan evaluasi sudah selesai
sebelum menyalakan antarmuka ini.

Kenapa ini ada: menjalankan `python src/answer.py "..."` berulang kali berarti
memuat ulang model 6 GB setiap pertanyaan -- sekitar 60 detik terbuang per
pertanyaan, padahal menjawabnya sendiri cuma 10 detik. Di sini model dimuat
SEKALI lalu dipakai terus selama sesi berjalan.

Sengaja dibuat di terminal, bukan web. Definisi selesai proyek meminta "antarmuka chat
sederhana yang bisa dipakai", dan yang perlu dibuktikan adalah mutu jawabannya,
bukan tampilannya. Menambah kerangka web berarti menambah dependensi dan hal
baru yang harus dijelaskan, tanpa mengubah satu pun angka evaluasi.

Perintah yang bisa diketik saat sesi berjalan:
    /nakes /masyarakat /sekolah   ganti sasaran pembaca
    /rinci /ringkas /otomatis     kunci panjang jawaban (bawaan: /otomatis)
    /sumber                       tampilkan potongan yang dipakai jawaban terakhir
    /keluar                       selesai

Bawaannya /otomatis: panjang jawaban ditentukan dari kalimat pertanyaannya.
Menulis "jelaskan rinci ..." atau "uraikan lengkap ..." sudah cukup, tidak
perlu perintah khusus. /rinci hanya berguna kalau ingin menguncinya untuk
beberapa pertanyaan berturut-turut.
"""

from __future__ import annotations

import argparse

from answer import Penjawab
from utils import daftar_sasaran, lapor

SAMBUTAN = """
================================================================
  Asisten Tanya-Jawab CKG
================================================================
  Menjawab HANYA berdasarkan dokumen resmi: Pusat Bantuan ASIK,
  FAQ Cek Kesehatan Gratis di SATUSEHAT, Juknis CKG (Kepmenkes
  84/2026), dan Juknis CKG Sekolah (Kepmenkes 770/2025).
  Setiap jawaban disertai sumber.

  BUKAN alat klinis: tidak menafsirkan hasil pemeriksaan, tidak
  memberi saran pengobatan, tidak menegakkan diagnosis.
================================================================
"""

# Keterangan tiap kelompok pembaca. Yang ditampilkan hanya kelompok yang
# potongannya benar-benar ada di korpus -- lihat pilih_sasaran().
KETERANGAN_SASARAN = {
    "nakes": ("Tenaga kesehatan / kader",
              "petugas yang mencatat data di aplikasi ASIK"),
    "masyarakat": ("Masyarakat umum / peserta",
                   "ingin ikut atau sudah ikut Cek Kesehatan Gratis"),
    "sekolah": ("Pihak sekolah",
                "guru atau petugas sekolah terkait CKG Sekolah"),
}


def pilih_sasaran(potongan: list[dict]) -> str:
    """Tanyakan pembaca termasuk kelompok mana sebelum sesi dimulai.

    Pilihannya dibangun dari korpus, bukan dipatok di kode. Sasaran yang tidak
    punya satu pun potongan tidak ditawarkan -- menawarkannya hanya membuat
    pengguna memilih sesuatu yang pasti berujung jawaban kosong. Saat ini
    korpus berisi 218 potongan nakes dan 50 masyarakat, sedangkan sekolah
    masih nol, jadi pilihan sekolah otomatis tidak muncul.
    """
    tersedia: dict[str, int] = {}
    for x in potongan:
        # Satu potongan bisa menyasar lebih dari satu kelompok, jadi ia dihitung
        # pada masing-masing kelompok. Akibatnya jumlah seluruh angka di menu
        # bisa melebihi jumlah potongan -- itu memang seharusnya, karena yang
        # ditanyakan adalah "berapa yang tersedia untuk saya", bukan pembagian.
        for s in daftar_sasaran(x):
            tersedia[s] = tersedia.get(s, 0) + 1

    urut = [s for s in ("nakes", "masyarakat", "sekolah") if s in tersedia]
    urut += [s for s in tersedia if s not in urut]

    if len(urut) == 1:
        return urut[0]

    print("Sebelum mulai, siapa Anda?\n")
    for i, s in enumerate(urut, 1):
        judul, jelas = KETERANGAN_SASARAN.get(s, (s, ""))
        print(f"  {i}. {judul}")
        print(f"     ({jelas}) — {tersedia[s]} potongan dokumen tersedia\n")

    while True:
        try:
            jawab = input(f"Pilih [1-{len(urut)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return urut[0]
        if jawab.isdigit() and 1 <= int(jawab) <= len(urut):
            dipilih = urut[int(jawab) - 1]
            judul = KETERANGAN_SASARAN.get(dipilih, (dipilih, ""))[0]
            print(f"\n  Baik, jawaban akan disesuaikan untuk: {judul}")
            print("  (bisa diganti kapan saja dengan /nakes atau /masyarakat)\n")
            return dipilih
        print(f"  Ketik angka 1 sampai {len(urut)}.")


def main() -> int:
    p = argparse.ArgumentParser(description="Antarmuka percakapan CKG.")
    p.add_argument("--sasaran", default=None, choices=["nakes", "masyarakat", "sekolah"],
                   help="lewati pertanyaan pembuka dan langsung pakai sasaran ini")
    p.add_argument("--mesin", default="llm", choices=["llm", "ekstraktif"])
    p.add_argument("-k", type=int, default=4)
    p.add_argument("--model", default=None,
                   help="model bahasa lain, mis. Qwen/Qwen2.5-1.5B-Instruct (lebih ringan)")
    p.add_argument("--tanpa-hibrida", action="store_true",
                   help="pakai embedding saja, matikan penggabungan dengan BM25")
    p.add_argument("--4bit", dest="empat_bit", action="store_true",
                   help="paksa kuantisasi 4-bit (biasanya tidak perlu: dipilih otomatis)")
    p.add_argument("--tanpa-kuantisasi", dest="tanpa_kuant", action="store_true",
                   help="paksa TANPA kuantisasi, walau VRAM mungkin tidak cukup")
    args = p.parse_args()

    kwargs = {"mesin": args.mesin, "k": args.k, "hibrida": not args.tanpa_hibrida,
              "kuantisasi": ("4bit" if args.empat_bit else
                             "tidak" if args.tanpa_kuant else "otomatis")}
    if args.model:
        kwargs["model"] = args.model
    penjawab = Penjawab(**kwargs)

    print(SAMBUTAN)
    # Pembaca memilih dirinya sendiri sebelum sesi dimulai. Ini menggantikan
    # bawaan "nakes" yang diam-diam dipakai sebelumnya -- diam-diam memilihkan
    # sasaran untuk pengguna adalah cara paling mudah menghasilkan jawaban yang
    # terasa ngawur, karena dokumen yang benar bisa berada di kelompok lain.
    sasaran = args.sasaran or pilih_sasaran(penjawab.pencari.potongan)
    if args.mesin == "ekstraktif":
        print("PERINGATAN: mesin ekstraktif tidak bisa menolak menjawab. Hanya untuk pembanding.\n")
    else:
        # Model dimuat di depan supaya jeda panjangnya terjadi sekali di awal,
        # bukan mengejutkan pengguna saat pertanyaan pertama diketik.
        lapor("Menyiapkan model, mohon tunggu (sekitar satu menit)...")
        penjawab._muat_llm()
        lapor("Siap.\n")

    terakhir = None
    # None = panjang jawaban ditentukan dari kalimat pertanyaannya sendiri.
    # /rinci menguncinya supaya tidak perlu menulis "jelaskan rinci" tiap kali.
    rinci = None
    while True:
        tanda = "" if rinci is None else (" rinci" if rinci else " ringkas")
        try:
            masukan = input(f"[{sasaran}{tanda}] Pertanyaan: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSelesai.")
            return 0

        if not masukan:
            continue

        if masukan.lower() in ("/keluar", "/exit", "/quit"):
            print("Selesai.")
            return 0

        if masukan.lower().lstrip("/") in ("nakes", "masyarakat", "sekolah"):
            sasaran = masukan.lower().lstrip("/")
            print(f"  -> sasaran pembaca diganti ke: {sasaran}\n")
            continue

        if masukan.lower() in ("/rinci", "/ringkas", "/otomatis"):
            rinci = {"/rinci": True, "/ringkas": False, "/otomatis": None}[masukan.lower()]
            keterangan = {True: "selalu rinci", False: "selalu ringkas",
                          None: "menyesuaikan pertanyaan"}[rinci]
            print(f"  -> panjang jawaban: {keterangan}\n")
            continue

        if masukan.lower() == "/sumber":
            if not terakhir or not terakhir.hasil_cari:
                print("  Belum ada jawaban yang punya sumber.\n")
                continue
            print("\n  Potongan yang dipertimbangkan (urut dari paling mirip):")
            for i, h in enumerate(terakhir.hasil_cari, 1):
                pot = h.potongan
                print(f"    {i}. [{h.skor:.4f}] {pot['judul_dokumen'][:62]}")
                print(f"       {'+'.join(daftar_sasaran(pot))} | {pot['jenis']}"
                      f" | {pot['sumber_id']}")
            print()
            continue

        terakhir = penjawab.jawab(masukan, sasaran=sasaran, rinci=rinci)
        print()
        if terakhir.ditolak:
            print(f"  [ditolak: {terakhir.alasan_tolak}]")
        print(terakhir.cetak())
        print()


if __name__ == "__main__":
    raise SystemExit(main())
