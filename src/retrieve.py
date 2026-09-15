"""Pencarian potongan korpus yang paling relevan dengan sebuah pertanyaan.

Masukan : data/processed/indeks.npz + korpus.jsonl
Keluaran: daftar potongan terurut beserta skor kemiripan dan sumbernya.

Dipakai dua cara:
  * dari terminal, untuk memeriksa mutu pencarian secara langsung
        python src/retrieve.py "peserta tidak punya NIK bagaimana?"
        python src/retrieve.py "syarat ikut CKG apa saja?" --sasaran masyarakat
  * sebagai pustaka, dipanggil answer.py nanti
        from retrieve import Pencari
        pencari = Pencari()
        hasil = pencari.cari("...", k=5, sasaran="nakes")

=== CARA KERJANYA ===

Semua vektor sudah dinormalkan saat pengindeksan, jadi kemiripan kosinus cukup
dihitung dengan satu perkalian matriks: skor = V @ q. Untuk ratusan potongan ini
selesai dalam milidetik, tanpa perlu pustaka pencarian vektor apa pun.

=== PENYARINGAN DILAKUKAN SEBELUM PERINGKAT, BUKAN SESUDAH ===

Kalau menyaring setelah mengambil 5 teratas, pertanyaan masyarakat bisa
mengambil 5 potongan nakes lalu tersaring habis dan menyisakan nol jawaban.
Menyaring dulu baru memeringkat menjamin k hasil teratas memang berasal dari
kelompok yang diminta. Inilah gunanya metadata `sasaran` (CLAUDE.md §4).

=== PENCARIAN HIBRIDA (BAWAAN) ===

Pencarian menggabungkan embedding dengan pencarian kata kunci BM25. Ini bukan
pilihan bawaan yang asal dipakai -- keduanya DIUKUR lebih dulu pada 63
pertanyaan uji:

    ukuran      embedding saja   hibrida
    Recall@1        66%            70%
    Recall@3        83%            91%
    Recall@10       94%            96%
    MRR            0,762          0,804
    gagal total      3              2

Naik di empat ukuran, turun di nol. Matikan dengan --tanpa-hibrida kalau ingin
membandingkan lagi.

=== SOAL PEMERINGKATAN ULANG (RERANKING) ===

Belum dipasang, dan itu disengaja. Reranking dengan cross-encoder menambah satu
model lagi dan memperlambat setiap pertanyaan. Hibrida sudah mengangkat
Recall@3 ke 91% tanpa model tambahan sama sekali, jadi manfaat reranking
sekarang lebih tipis. Kalau nanti tetap dicoba, harus diukur dengan cara yang
sama -- bukan diasumsikan lebih baik karena terdengar canggih.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from utils import (
    DATA_OLAHAN, baca_json, baca_jsonl, cocok_sasaran, daftar_sasaran, lapor,
    senyapkan_log_model, sha256_teks,
)


# ---------------------------------------------------------------------------
# Perluasan akronim pada pertanyaan -- SUDAH DIUKUR, HASILNYA MEMPERBURUK.
#
# Gagasan awalnya masuk akal: korpus menulis "Cek Kesehatan Gratis", pengguna
# mengetik "CKG", jadi kepanjangannya ditambahkan supaya cocok. Membandingkan
# dua pertanyaan berbeda pun sempat terlihat mendukung, karena skornya naik.
#
# Begitu diukur dengan benar (pertanyaan SAMA, dibandingkan dengan dan tanpa
# perluasan, lalu dilihat di peringkat berapa dokumen yang benar muncul),
# hasilnya justru terbalik:
#
#     pertanyaan                    peringkat dokumen benar
#                                   tanpa   ->  dengan perluasan
#     "syarat ikut CKG apa saja?"       9   ->  20
#     "Apa saja persyaratan CKG?"      14   ->  23
#     "Pemeriksaan CKG apa saja?"       7   ->  38
#
# Skornya memang naik (0,87 -> 0,91) tapi dokumen yang benar justru terlempar
# makin jauh. Sebabnya: mengulang "Cek Kesehatan Gratis" di pertanyaan menarik
# hasil ke potongan yang kebetulan sering menyebut frasa itu, bukan ke potongan
# yang benar-benar menjawab.
#
# PELAJARANNYA: skor kemiripan naik BUKAN berarti jawaban membaik. Fungsi ini
# sengaja dipertahankan dan bawaannya MATI, sebagai catatan hasil negatif yang
# bisa diulang -- supaya tidak ada yang menambahkannya lagi mengira ini bagus.
#
# BATAS DARI TEMUAN INI: pada eval/uji_retrieval.py angkanya nyaris tidak
# berubah (MRR 0,781 -> 0,792 di e5-small; tidak berubah sama sekali di
# e5-base). Sebabnya benih pertanyaan uji menuliskan "cek kesehatan gratis"
# lengkap, jadi fungsi ini hampir tidak pernah aktif di sana. Jadi tiga baris
# tabel di atas nyata, tapi belum terwakili di berkas uji. Kalau mau
# menyimpulkan dengan benar, tambahkan pertanyaan uji yang memakai singkatan.
# ---------------------------------------------------------------------------
AKRONIM = {
    "CKG": "Cek Kesehatan Gratis",
    "ASIK": "Aplikasi Sehat Indonesiaku",
    "PTM": "Penyakit Tidak Menular",
    "BNBA": "By Name By Address",
    "STR": "Surat Tanda Registrasi",
    "PKM": "Puskesmas",
    "NIK": "Nomor Induk Kependudukan",
    "NISN": "Nomor Induk Siswa Nasional",
    "KIA": "Kesehatan Ibu dan Anak",
}
_POLA_AKRONIM = re.compile(r"\b(" + "|".join(AKRONIM) + r")\b")


def perluas_akronim(teks: str) -> str:
    """Tulis singkatan beserta kepanjangannya: "CKG" -> "CKG (Cek Kesehatan Gratis)".

    Singkatannya tetap dipertahankan, tidak diganti, supaya pertanyaan yang
    memang mengandalkan bentuk singkat tidak jadi rugi.
    """
    return _POLA_AKRONIM.sub(lambda m: f"{m.group(1)} ({AKRONIM[m.group(1)]})", teks)


# ---------------------------------------------------------------------------
# PENCARIAN HIBRIDA: embedding + kata kunci
#
# Pencarian embedding kuat pada makna dan sinonim -- "ponsel" tetap menemukan
# dokumen yang menulis "Mobile". Tapi ia lemah pada ISTILAH LANGKA. Contoh
# nyata dari evaluasi: pertanyaan "Apakah ASIK sudah terhubung dengan SIPTM?"
# dokumennya TIDAK terambil sama sekali di 10 besar, padahal ada di korpus.
# "SIPTM" itu singkatan langka; embedding tidak punya pegangan kuat untuknya,
# sedangkan pencocokan kata harfiah menemukannya seketika.
#
# BM25 kebalikannya: kuat pada istilah persis (SIPTM, BNBA, NPSN), lemah pada
# sinonim. Dua-duanya dijalankan, lalu hasilnya digabung.
#
# CARA MENGGABUNG: Reciprocal Rank Fusion (RRF).
#     nilai = jumlah dari 1 / (K + peringkat) untuk tiap metode
#
# RRF dipilih karena bekerja pada PERINGKAT, bukan pada skor mentah. Skor
# kosinus (0,8-0,9) dan skor BM25 (0 sampai puluhan) tidak sebanding dan tidak
# bisa dijumlahkan begitu saja; menormalkannya butuh penyetelan bobot yang
# harus dicari-cari. RRF tidak butuh itu sama sekali: dokumen yang muncul di
# peringkat atas pada SALAH SATU metode akan terangkat.
#
# K=60 adalah nilai lazim dari makalah aslinya. Tidak disetel-setel supaya
# tidak ada tuduhan angkanya dipilih agar hasil evaluasi terlihat bagus.
RRF_K = 60


def tokenkan(teks: str) -> list[str]:
    """Pecah teks jadi kata untuk BM25.

    Sengaja sederhana: huruf dan angka, minimal dua karakter, huruf kecil
    semua. Tanpa stemming, karena pemenggal kata Bahasa Indonesia menambah
    satu dependensi lagi dan manfaatnya belum tentu ada di korpus sependek ini.
    Kalau nanti terbukti perlu, itu keputusan yang harus diukur tersendiri.
    """
    return re.findall(r"[a-z0-9]{2,}", teks.lower())


@dataclass
class Hasil:
    """Satu potongan hasil pencarian."""
    skor: float
    potongan: dict

    @property
    def rujukan(self) -> str:
        """Rujukan siap tampil: nama sumber + tautannya (CLAUDE.md §2 no. 1)."""
        return f"{self.potongan['sumber_nama']} — {self.potongan['sumber_url']}"


class Pencari:
    def __init__(
        self,
        korpus: Path | str = DATA_OLAHAN / "korpus.jsonl",
        indeks: Path | str = DATA_OLAHAN / "indeks.npz",
    ) -> None:
        korpus, indeks = Path(korpus), Path(indeks)
        for berkas, saran in ((korpus, "src/chunk.py"), (indeks, "src/index.py")):
            if not berkas.exists():
                raise FileNotFoundError(f"{berkas} belum ada. Jalankan {saran} dulu.")

        self.potongan = baca_jsonl(korpus)
        self.meta = baca_json(indeks.with_name(indeks.stem + "_meta.json"))
        with np.load(indeks, allow_pickle=False) as z:
            self.vektor = z["vektor"]

        # Indeks basi adalah kegagalan yang paling berbahaya: program tetap
        # jalan, tapi potongan nomor ke-n di korpus tidak lagi sama dengan
        # vektor nomor ke-n, sehingga jawaban dirujukkan ke sumber yang KELIRU.
        # Lebih baik berhenti dengan pesan jelas.
        sidik = sha256_teks("".join(x["id"] for x in self.potongan))
        if sidik != self.meta.get("sidik_korpus"):
            raise RuntimeError(
                "Indeks tidak cocok dengan korpus (korpus berubah setelah diindeks).\n"
                "Jalankan ulang: python src/index.py"
            )
        if len(self.potongan) != self.vektor.shape[0]:
            raise RuntimeError("Jumlah potongan dan jumlah vektor berbeda. Bangun ulang indeks.")

        self._model = None  # dimuat saat pertama dibutuhkan, supaya impor cepat
        self._bm25 = None   # dibangun saat pencarian hibrida pertama dipakai

    @property
    def model(self):
        if self._model is None:
            senyapkan_log_model()
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.meta["model"])
        return self._model

    @property
    def bm25(self):
        """Indeks kata kunci. Dibangun sekali, dari korpus yang sama.

        Tidak butuh GPU maupun unduhan model -- hanya menghitung kata, jadi
        selesai dalam hitungan detik untuk korpus ratusan potongan.
        """
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi([tokenkan(x["teks"]) for x in self.potongan])
        return self._bm25

    def _saring(self, sasaran: str | None, jenis: str | None, sumber: str | None) -> np.ndarray:
        """Kembalikan indeks potongan yang lolos penyaringan metadata."""
        lolos = np.ones(len(self.potongan), dtype=bool)
        # sasaran ditangani terpisah karena nilainya boleh berupa daftar:
        # satu dokumen bisa resmi menyasar dua kelompok sekaligus. Lihat
        # penjelasan di cocok_sasaran() pada src/utils.py.
        if sasaran:
            lolos &= np.array([cocok_sasaran(p, sasaran) for p in self.potongan])
        for kunci, nilai in (("jenis", jenis), ("sumber_id", sumber)):
            if nilai:
                lolos &= np.array([p.get(kunci) == nilai for p in self.potongan])
        return np.flatnonzero(lolos)

    def cari(
        self,
        pertanyaan: str,
        k: int = 5,
        *,
        sasaran: str | None = None,
        jenis: str | None = None,
        sumber: str | None = None,
        skor_minimum: float = 0.0,
        perluas: bool = False,
        hibrida: bool = True,
    ) -> list[Hasil]:
        """Cari k potongan paling mirip dengan pertanyaan.

        skor_minimum: ambang untuk membuang hasil yang terlalu jauh. Dibiarkan 0
        di sini karena ambang yang benar HARUS ditentukan dari data uji
        (eval/), bukan ditebak. Ini nanti jadi dasar kemampuan menolak menjawab
        di answer.py (CLAUDE.md §7 no. 3).
        """
        kandidat = self._saring(sasaran, jenis, sumber)
        if kandidat.size == 0:
            return []

        if perluas:
            pertanyaan = perluas_akronim(pertanyaan)

        # Awalan "query: " wajib untuk model E5 dan diambil dari meta indeks,
        # supaya pasangannya tidak mungkin tertukar dengan awalan dokumen.
        q = self.model.encode(
            [self.meta["awalan_pertanyaan"] + pertanyaan],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )[0].astype(np.float32)

        skor = self.vektor[kandidat] @ q  # vektor sudah dinormalkan -> ini kosinus
        k = min(k, kandidat.size)

        if not hibrida:
            # argpartition: ambil k teratas tanpa mengurutkan seluruh larik.
            teratas = np.argpartition(-skor, k - 1)[:k]
            teratas = teratas[np.argsort(-skor[teratas])]
            return [
                Hasil(skor=float(skor[i]), potongan=self.potongan[kandidat[i]])
                for i in teratas
                if skor[i] >= skor_minimum
            ]

        # --- Hibrida: gabungkan peringkat embedding dengan peringkat BM25 ---
        skor_kata = np.asarray(self.bm25.get_scores(tokenkan(pertanyaan)))[kandidat]

        nilai = np.zeros(kandidat.size, dtype=np.float64)
        for deret in (skor, skor_kata):
            # argsort dua kali = peringkat tiap kandidat (0 = paling atas).
            peringkat = np.empty(kandidat.size, dtype=np.int64)
            peringkat[np.argsort(-deret)] = np.arange(kandidat.size)
            nilai += 1.0 / (RRF_K + peringkat + 1)

        teratas = np.argsort(-nilai)[:k]
        # Skor yang dilaporkan adalah nilai RRF, BUKAN kemiripan kosinus.
        # Keduanya tidak sebanding, jadi jangan dibandingkan lintas mode.
        return [
            Hasil(skor=float(nilai[i]), potongan=self.potongan[kandidat[i]])
            for i in teratas
        ]


def main() -> int:
    p = argparse.ArgumentParser(description="Cari potongan korpus yang relevan.")
    p.add_argument("pertanyaan", help="pertanyaan yang dicari")
    p.add_argument("-k", type=int, default=5, help="jumlah hasil")
    p.add_argument("--sasaran", choices=["nakes", "masyarakat", "sekolah"])
    p.add_argument("--jenis", choices=["ketentuan_program", "cara_pakai_aplikasi", "penanganan_kendala"])
    p.add_argument("--sumber", help="mis. asik, faq_fasyankes, faq_umum")
    p.add_argument("--penuh", action="store_true", help="tampilkan teks potongan seutuhnya")
    p.add_argument("--perluas", action="store_true",
                   help="tulis kepanjangan akronim pada pertanyaan sebelum dicari")
    p.add_argument("--tanpa-hibrida", action="store_true",
                   help="pakai embedding saja, matikan penggabungan dengan BM25")
    args = p.parse_args()

    pencari = Pencari()
    hasil = pencari.cari(args.pertanyaan, k=args.k, sasaran=args.sasaran,
                         jenis=args.jenis, sumber=args.sumber, perluas=args.perluas,
                         hibrida=not args.tanpa_hibrida)

    saringan = [s for s in (args.sasaran, args.jenis, args.sumber) if s]
    lapor(f"Pertanyaan : {args.pertanyaan}")
    if args.perluas:
        lapor(f"Diperluas  : {perluas_akronim(args.pertanyaan)}")
    lapor(f"Saringan   : {', '.join(saringan) if saringan else '(tanpa saringan)'}")
    lapor(f"Kandidat   : {pencari._saring(args.sasaran, args.jenis, args.sumber).size}"
          f" dari {len(pencari.potongan)} potongan\n")

    if not hasil:
        lapor("Tidak ada hasil.")
        return 0

    for i, h in enumerate(hasil, 1):
        pot = h.potongan
        print(f"{i}. [{h.skor:.4f}] {pot['judul_dokumen']}")
        print(f"   {'+'.join(daftar_sasaran(pot))} | {pot['jenis']} | {pot['sumber_id']}"
              f" | potongan {pot['urutan'] + 1}/{pot['total_potongan']}")
        print(f"   {pot['sumber_url']}")
        isi = pot["teks"] if args.penuh else pot["teks"][:200].replace("\n", " ") + " ..."
        print(f"   {isi}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
