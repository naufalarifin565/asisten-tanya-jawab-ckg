"""Mengukur mutu PENCARIAN (belum jawabannya).

    python eval/uji_retrieval.py
    python eval/uji_retrieval.py --indeks data/processed/indeks_base.npz
    python eval/uji_retrieval.py --perluas

Yang diukur:

1. Recall@k -- dari k potongan teratas, apakah dokumen acuan ikut terambil?
   Kalau dokumen yang benar tidak pernah terambil, sebagus apa pun LLM-nya
   jawabannya tetap tidak bisa benar. Jadi angka ini batas atas mutu sistem.

2. MRR (Mean Reciprocal Rank) -- rata-rata 1/peringkat dokumen benar.
   Recall@5 hanya bilang "masuk atau tidak"; MRR membedakan yang nangkring di
   peringkat 1 dengan yang nyangkut di peringkat 5.

3. Pemisahan skor -- skor terendah pertanyaan yang ADA jawabannya dibandingkan
   skor tertinggi pertanyaan yang TIDAK ada jawabannya. Kalau kedua rentang itu
   bertumpang tindih, ambang skor tunggal TIDAK BISA dipakai untuk menolak
   menjawab, dan penolakan harus ditangani dengan cara lain di answer.py.
   Ini yang paling sering dilupakan orang saat membangun RAG.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AKAR / "src"))

from retrieve import Pencari  # noqa: E402
from utils import DATA_OLAHAN, baca_jsonl, lapor  # noqa: E402

K_DIUKUR = (1, 3, 5, 10)


def peringkat_acuan(hasil, acuan: list[str]) -> int | None:
    """Peringkat (mulai 1) potongan acuan pertama yang ditemukan; None kalau tidak ada."""
    kumpulan = set(acuan)
    return next((i for i, h in enumerate(hasil, 1) if h.potongan["id"] in kumpulan), None)


def main() -> int:
    p = argparse.ArgumentParser(description="Ukur mutu pencarian terhadap pertanyaan uji.")
    p.add_argument("--indeks", default=str(DATA_OLAHAN / "indeks.npz"))
    p.add_argument("--uji", default=str(Path(__file__).resolve().parent / "pertanyaan_uji.jsonl"))
    p.add_argument("--perluas", action="store_true", help="perluas akronim pada pertanyaan")
    p.add_argument("--rinci", action="store_true", help="tampilkan hasil tiap pertanyaan")
    p.add_argument("--tanpa-saringan", action="store_true",
                   help="abaikan metadata sasaran (untuk melihat pengaruh penyaringan)")
    args = p.parse_args()

    uji = baca_jsonl(Path(args.uji))
    pencari = Pencari(indeks=args.indeks)
    maks_k = max(K_DIUKUR)

    lapor(f"Model    : {pencari.meta['model']}")
    lapor(f"Indeks   : {args.indeks}  ({pencari.meta['jumlah_potongan']} potongan)")
    lapor(f"Perluas  : {'ya' if args.perluas else 'tidak'}")
    lapor(f"Saringan : {'tanpa saringan sasaran' if args.tanpa_saringan else 'menurut sasaran'}\n")

    peringkat: list[int | None] = []
    skor_terjawab: list[float] = []
    skor_tak_terjawab: list[float] = []
    rinci = []

    for u in uji:
        hasil = pencari.cari(
            u["pertanyaan"],
            k=maks_k,
            sasaran=None if args.tanpa_saringan else u["sasaran"],
            perluas=args.perluas,
        )
        skor1 = hasil[0].skor if hasil else 0.0
        if u["terjawab"]:
            r = peringkat_acuan(hasil, u["potongan_acuan"])
            peringkat.append(r)
            skor_terjawab.append(skor1)
            rinci.append((u["pertanyaan"], r, skor1))
        else:
            skor_tak_terjawab.append(skor1)
            rinci.append((u["pertanyaan"], "-", skor1))

    n = len(peringkat)
    if not n:
        lapor("Tidak ada pertanyaan berjawab di berkas uji.")
        return 1

    lapor(f"=== PERTANYAAN YANG ADA JAWABANNYA ({n}) ===")
    for k in K_DIUKUR:
        kena = sum(1 for r in peringkat if r is not None and r <= k)
        lapor(f"  Recall@{k:<3}: {kena}/{n}  ({kena / n:.0%})")
    mrr = sum(1 / r for r in peringkat if r) / n
    lapor(f"  MRR      : {mrr:.3f}")
    tak_kena = [i for i, r in enumerate(peringkat) if r is None]
    if tak_kena:
        lapor(f"  Tidak masuk {maks_k} besar sama sekali: {len(tak_kena)} pertanyaan")

    if skor_tak_terjawab:
        lapor(f"\n=== PERTANYAAN TANPA JAWABAN DI KORPUS ({len(skor_tak_terjawab)}) ===")
        lapor(f"  skor tertinggi   : {max(skor_tak_terjawab):.4f}")
        lapor(f"  skor terendah    : {min(skor_tak_terjawab):.4f}")
        lapor(f"\n=== BISAKAH DIPAKAI AMBANG SKOR? ===")
        batas_bawah = min(skor_terjawab)
        batas_atas = max(skor_tak_terjawab)
        lapor(f"  skor TERENDAH pertanyaan berjawab   : {batas_bawah:.4f}")
        lapor(f"  skor TERTINGGI pertanyaan tak dijawab: {batas_atas:.4f}")
        if batas_atas < batas_bawah:
            lapor(f"  -> Terpisah. Ambang di antara keduanya (mis. {(batas_atas + batas_bawah) / 2:.4f}) bisa dipakai.")
        else:
            lapor("  -> BERTUMPANG TINDIH. Ambang skor tunggal TIDAK BISA memisahkan.")
            lapor("     Penolakan menjawab harus ditangani dengan cara lain di answer.py,")
            lapor("     mis. LLM diminta menilai sendiri apakah konteksnya benar-benar menjawab.")

    if args.rinci:
        lapor("\n=== RINCIAN ===")
        for pertanyaan, r, s in rinci:
            tanda = "  " if r in ("-",) else ("OK" if r and r <= 3 else "!!")
            lapor(f"  {tanda} peringkat {str(r):>4}  skor {s:.4f}  {pertanyaan[:58]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
