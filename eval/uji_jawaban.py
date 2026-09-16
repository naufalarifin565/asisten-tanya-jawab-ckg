"""Mengukur mutu JAWABAN, bukan cuma pencarian.

    python eval/uji_jawaban.py --mesin ekstraktif     # cepat, tanpa LLM
    python eval/uji_jawaban.py --mesin llm            # perlu model bahasa

Tiga dari empat ukuran evaluasi proyek diukur di sini:

  2. KETEPATAN SUMBER   -- apakah dokumen yang dikutip memang dokumen acuannya?
                           Dilaporkan DUA angka, dan ini penting: mesin
                           ekstraktif mengutip 1 sumber sedangkan mesin llm
                           mengutip 4, jadi "ikut dikutip" otomatis lebih mudah
                           dipenuhi mesin yang mengutip lebih banyak. Karena itu
                           ditambahkan "sumber PERTAMA sudah tepat", yang tidak
                           bisa dicurangi dengan memperbanyak kutipan.
  3. KEJUJURAN MENOLAK  -- untuk pertanyaan yang jawabannya TIDAK ADA di korpus,
                           apakah sistem menolak, bukan mengarang?
  4. KESESUAIAN BAHASA  -- diukur kasar lewat panjang kalimat dan jumlah istilah
                           teknis pada jawaban untuk masyarakat.

Yang BELUM diukur di sini: ketepatan isi jawaban (§7 no. 1). Itu butuh
`jawaban_acuan` yang ditulis manusia; menilainya dengan kunci jawaban karangan
sendiri tidak ada artinya.

Dua kesalahan menolak dihitung terpisah, karena akibatnya berbeda:

  MENGARANG (paling buruk)  -- pertanyaan tanpa jawaban di korpus tetap dijawab.
  MENOLAK BERLEBIHAN        -- pertanyaan yang ada jawabannya malah ditolak.

Yang pertama merusak kepercayaan, yang kedua sekadar mengecewakan.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AKAR / "src"))

from answer import Penjawab  # noqa: E402
from utils import baca_jsonl, lapor  # noqa: E402

# Istilah yang wajar dipakai ke petugas, tapi tidak wajar ke masyarakat umum.
ISTILAH_TEKNIS = [
    "dashboard", "BNBA", "regpus", "NIK", "NISN", "STR", "user management",
    "admin", "administrator", "input", "entri", "sinkronisasi", "API",
    "by name by address", "fasyankes", "puskesmas", "kader",
]


def ukur_bahasa(teks: str) -> tuple[float, int]:
    """Kembalikan (rata-rata kata per kalimat, jumlah istilah teknis)."""
    kalimat = [k for k in re.split(r"[.!?]\s+", teks) if k.strip()]
    kata = len(teks.split())
    rerata = kata / len(kalimat) if kalimat else float(kata)
    n_teknis = sum(1 for t in ISTILAH_TEKNIS if re.search(rf"\b{re.escape(t)}\b", teks, re.I))
    return rerata, n_teknis


def main() -> int:
    p = argparse.ArgumentParser(description="Ukur mutu jawaban terhadap pertanyaan uji.")
    p.add_argument("--mesin", default="ekstraktif", choices=["llm", "ekstraktif"])
    p.add_argument("--model", default=None)
    p.add_argument("--uji", default=str(Path(__file__).resolve().parent / "pertanyaan_uji.jsonl"))
    p.add_argument("-k", type=int, default=4)
    p.add_argument("--rinci", action="store_true")
    args = p.parse_args()

    uji = baca_jsonl(Path(args.uji))
    kwargs = {"mesin": args.mesin, "k": args.k}
    if args.model:
        kwargs["model"] = args.model
    penjawab = Penjawab(**kwargs)

    lapor(f"Mesin : {args.mesin}")
    lapor(f"Soal  : {len(uji)} pertanyaan\n")

    sumber_tepat = sumber_meleset = tertolak_padahal_ada = sumber_tepat_1 = 0
    menolak_benar = mengarang = 0
    bahasa_masyarakat: list[tuple[float, int]] = []
    rinci = []

    for u in uji:
        j = penjawab.jawab(u["pertanyaan"], sasaran=u["sasaran"])

        if u["terjawab"]:
            if j.ditolak:
                tertolak_padahal_ada += 1
                status = "TERTOLAK padahal ada"
            else:
                dikutip = [s["url"] for s in j.sumber]
                if dikutip and dikutip[0] == u["sumber_acuan"]:
                    sumber_tepat_1 += 1
                if u["sumber_acuan"] in dikutip:
                    sumber_tepat += 1
                    status = "sumber tepat"
                else:
                    sumber_meleset += 1
                    status = "SUMBER MELESET"
        else:
            if j.ditolak:
                menolak_benar += 1
                status = f"menolak benar ({j.alasan_tolak})"
            else:
                mengarang += 1
                status = "MENGARANG"

        if u["sasaran"] == "masyarakat" and not j.ditolak:
            bahasa_masyarakat.append(ukur_bahasa(j.teks))

        rinci.append((status, u["pertanyaan"]))

    n_ada = sum(1 for u in uji if u["terjawab"])
    n_tiada = len(uji) - n_ada

    lapor(f"=== KETEPATAN SUMBER ({n_ada} pertanyaan yang ada jawabannya) ===")
    lapor(f"  sumber acuan ikut dikutip : {sumber_tepat}/{n_ada}  ({sumber_tepat / n_ada:.0%})")
    lapor(f"  sumber PERTAMA sudah tepat: {sumber_tepat_1}/{n_ada}  ({sumber_tepat_1 / n_ada:.0%})"
          "   <- ukuran yang adil antar mesin")
    lapor(f"  sumber meleset            : {sumber_meleset}")
    lapor(f"  ditolak padahal ada       : {tertolak_padahal_ada}   <- menolak berlebihan")

    lapor(f"\n=== KEJUJURAN MENOLAK ({n_tiada} pertanyaan tanpa jawaban di korpus) ===")
    lapor(f"  menolak dengan benar      : {menolak_benar}/{n_tiada}  ({menolak_benar / n_tiada:.0%})")
    lapor(f"  MENGARANG                 : {mengarang}   <- paling berbahaya")

    if bahasa_masyarakat:
        rerata = sum(x for x, _ in bahasa_masyarakat) / len(bahasa_masyarakat)
        teknis = sum(y for _, y in bahasa_masyarakat) / len(bahasa_masyarakat)
        lapor(f"\n=== KESESUAIAN BAHASA (jawaban untuk masyarakat, {len(bahasa_masyarakat)} jawaban) ===")
        lapor(f"  rata-rata kata per kalimat : {rerata:.1f}   (makin pendek makin mudah dibaca)")
        lapor(f"  rata-rata istilah teknis   : {teknis:.1f}   (idealnya mendekati 0)")
        lapor("  CATATAN: ini ukuran kasar. Penilaian sebenarnya tetap perlu dibaca manusia.")

    if args.rinci:
        lapor("\n=== RINCIAN ===")
        for status, pertanyaan in rinci:
            tanda = "  " if status.islower() or status.startswith(("sumber tepat", "menolak")) else "!!"
            lapor(f"  {tanda} {status:24} {pertanyaan[:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
