"""Membuat indeks embedding dari korpus.

Masukan : data/processed/korpus.jsonl
Keluaran: data/processed/indeks.npz       (matriks embedding)
          data/processed/indeks_meta.json (keterangan indeks)

=== KENAPA TANPA VECTOR DATABASE ===

Korpus ini cuma ratusan potongan. Mencari yang paling mirip dari 250 vektor
adalah satu perkalian matriks NumPy yang selesai dalam hitungan milidetik.
Chroma/FAISS/Qdrant dirancang untuk jutaan vektor; memakainya di sini hanya
menambah satu dependensi, satu proses, dan satu hal baru untuk dijelaskan
tanpa membuat pencarian lebih cepat atau lebih tepat.

Kalau korpus nanti membengkak (mis. setelah Juknis CKG masuk) dan pencarian
mulai terasa lambat, barulah pindah. Ambang praktisnya kira-kira puluhan ribu
potongan -- masih sangat jauh.

=== KENAPA MODEL INI ===

Bawaan: intfloat/multilingual-e5-base
  * open-source dan jalan sepenuhnya lokal (prinsip wajib no. 5)
  * dilatih multibahasa termasuk Indonesia
  * jendela 512 token, sejalan dengan ambang potongan 1.600 huruf (~400 token)

Awalnya dipilih e5-small (~470 MB) karena lebih ringan. Diganti ke e5-base
(~1,1 GB) setelah keduanya DIUKUR dengan eval/uji_retrieval.py pada 16
pertanyaan uji berjawab:

    model      Recall@1  Recall@5  Recall@10   MRR
    e5-small       69%       88%        94%   0,781
    e5-base        75%       94%       100%   0,824

Recall@10 100% berarti untuk setiap pertanyaan uji, dokumen yang benar SELALU
ikut terambil di 10 besar. Itu penting karena recall adalah batas atas mutu
sistem: dokumen yang tidak pernah terambil mustahil dijadikan jawaban.

Biayanya: model 2x lebih besar dan pengindeksan lebih lambat. Untuk korpus
ratusan potongan, biaya itu tidak terasa. Kalau nanti perlu lebih ringan,
ganti dengan --model dan ukur lagi -- jangan ditebak.

=== AWALAN "passage:" DAN "query:" ===

Keluarga model E5 DILATIH dengan awalan: dokumen diberi "passage: " dan
pertanyaan diberi "query: ". Kalau awalan ini dilupakan, model tetap
menghasilkan angka dan program tetap jalan -- tapi mutu pencariannya turun
diam-diam tanpa error apa pun. Ini kesalahan yang sangat mudah terjadi, jadi
awalan yang dipakai ikut disimpan di indeks_meta.json supaya retrieve.py
memakai pasangan yang benar dan ketidakcocokan bisa ketahuan.

Contoh pakai:
    python src/index.py
    python src/index.py --model intfloat/multilingual-e5-base
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from utils import (
    DATA_OLAHAN, baca_jsonl, hari_ini, lapor, senyapkan_log_model, sha256_teks, tulis_json,
)

MODEL_BAWAAN = "intfloat/multilingual-e5-base"
AWALAN_DOKUMEN = "passage: "
AWALAN_PERTANYAAN = "query: "


def main() -> int:
    p = argparse.ArgumentParser(description="Bangun indeks embedding korpus CKG.")
    p.add_argument("--korpus", default=str(DATA_OLAHAN / "korpus.jsonl"))
    p.add_argument("--keluaran", default=str(DATA_OLAHAN / "indeks.npz"))
    p.add_argument("--model", default=MODEL_BAWAAN)
    p.add_argument("--batch", type=int, default=32, help="jumlah teks per proses")
    args = p.parse_args()

    korpus_path = Path(args.korpus)
    if not korpus_path.exists():
        lapor(f"GAGAL: {korpus_path} belum ada. Jalankan src/chunk.py dulu.")
        return 1

    potongan = baca_jsonl(korpus_path)
    lapor(f"[1/3] Korpus dimuat: {len(potongan)} potongan")

    lapor(f"[2/3] Memuat model: {args.model}")
    lapor("      (unduhan pertama kali bisa beberapa menit, setelah itu dari cache lokal)")
    senyapkan_log_model()
    from sentence_transformers import SentenceTransformer  # diimpor di sini supaya --help cepat

    model = SentenceTransformer(args.model)

    teks = [AWALAN_DOKUMEN + x["teks"] for x in potongan]
    lapor(f"[3/3] Menghitung embedding ({len(teks)} teks)")
    vektor = model.encode(
        teks,
        batch_size=args.batch,
        show_progress_bar=True,
        convert_to_numpy=True,
        # Vektor dinormalkan supaya kemiripan kosinus = perkalian titik biasa.
        # Satu operasi lebih sedikit saat mencari, dan skornya langsung berada
        # di rentang -1..1 sehingga mudah dijadikan ambang penolakan nanti.
        normalize_embeddings=True,
    ).astype(np.float32)

    keluaran = Path(args.keluaran)
    keluaran.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(keluaran, vektor=vektor, id=np.array([x["id"] for x in potongan]))

    # Nama meta diturunkan dari nama indeks, BUKAN dipatok "indeks_meta.json".
    # Kalau dipatok, membangun indeks pembanding (mis. --keluaran indeks_base.npz)
    # akan menimpa meta indeks utama dan retrieve.py membaca pasangan yang salah.
    meta_path = keluaran.with_name(keluaran.stem + "_meta.json")
    tulis_json(meta_path, {
        "model": args.model,
        "awalan_dokumen": AWALAN_DOKUMEN,
        "awalan_pertanyaan": AWALAN_PERTANYAAN,
        "dimensi": int(vektor.shape[1]),
        "jumlah_potongan": int(vektor.shape[0]),
        "dinormalkan": True,
        "korpus": str(korpus_path),
        # Sidik jari korpus: kalau korpus berubah tapi indeks tidak dibangun
        # ulang, retrieve.py bisa memperingatkan alih-alih diam-diam memakai
        # indeks basi yang urutannya sudah tidak cocok.
        "sidik_korpus": sha256_teks("".join(x["id"] for x in potongan)),
        "tanggal_indeks": hari_ini(),
    })

    lapor(f"\nIndeks   : {keluaran}  ({vektor.shape[0]} x {vektor.shape[1]})")
    lapor(f"Ukuran   : {keluaran.stat().st_size / 1024:.0f} KB")
    lapor(f"Meta     : {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
