"""Menjalankan SELURUH evaluasi sekaligus, lalu menulis laporannya.

    python eval/evaluate.py                    # mesin ekstraktif (cepat, tanpa GPU)
    python eval/evaluate.py --mesin llm        # lengkap, perlu GPU ~12 menit

Keluaran:
    layar                        ringkasan
    eval/laporan_evaluasi.md     laporan tertulis siap dilampirkan

Berkas ini dan laporannya memenuhi definisi selesai proyek
("laporan evaluasi dengan angka, termasuk kelemahannya").

=== KENAPA TIDAK MENGHITUNG SENDIRI ===

Definisi tiap ukuran diambil dari ketiga skrip uji, bukan ditulis ulang di sini:

    uji_retrieval.py  -> peringkat_acuan(), K_DIUKUR
    uji_jawaban.py    -> ukur_bahasa()
    uji_guardrail.py  -> KLINIS, SAH, KELUARAN_NAKAL

Kalau rumusnya disalin, cepat atau lambat angka di laporan akan berbeda dengan
angka yang keluar saat skrip ujinya dijalankan sendiri -- dan tidak ada yang
tahu mana yang benar. Satu definisi, dipakai bersama.

=== LAPORANNYA MENCANTUMKAN KELEMAHAN, BUKAN CUMA ANGKA ===

Definisi selesai proyek meminta laporan "termasuk kelemahannya", dan hasil
jelek harus dilaporkan apa adanya. Jadi laporan ini selalu memuat bagian keterbatasan,
dan angka yang buruk tidak disembunyikan atau dihaluskan.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(AKAR / "src"))
sys.path.insert(0, str(HERE))

from answer import (  # noqa: E402
    Penjawab, bersihkan_keluaran, liputan_potongan, pertanyaan_klinis,
)
from retrieve import Pencari  # noqa: E402
from uji_guardrail import KELUARAN_NAKAL, KLINIS, SAH  # noqa: E402
from uji_jawaban import ukur_bahasa  # noqa: E402
from uji_retrieval import K_DIUKUR, peringkat_acuan  # noqa: E402
from utils import DATA_OLAHAN, baca_json, baca_jsonl, hari_ini, lapor  # noqa: E402


def ukur_korpus() -> dict:
    pot = baca_jsonl(DATA_OLAHAN / "korpus.jsonl")
    meta = baca_json(DATA_OLAHAN / "indeks_meta.json")

    def hitung(kunci):
        # Nilai yang berupa DAFTAR dihitung pada tiap anggotanya, bukan
        # distringkan jadi satu kelompok semu. Tanpa ini laporan mencantumkan
        # kelompok bernama "['nakes', 'sekolah']" -- yang tidak pernah dipilih
        # siapa pun di antarmuka, dan bikin bingung siapa pun yang membacanya.
        d = {}
        for x in pot:
            nilai = x.get(kunci, "")
            for v in (nilai if isinstance(nilai, list) else [nilai]):
                d[str(v)] = d.get(str(v), 0) + 1
        return dict(sorted(d.items(), key=lambda x: -x[1]))

    panjang = sorted(x["jumlah_karakter"] for x in pot)
    return {
        "jumlah_potongan": len(pot),
        "jumlah_dokumen": len({x["sumber_url"] for x in pot}),
        "per_sumber": hitung("sumber_id"),
        "per_sasaran": hitung("sasaran"),
        "per_jenis": hitung("jenis"),
        "median_karakter": panjang[len(panjang) // 2],
        "maks_karakter": panjang[-1],
        "model_embedding": meta["model"],
        "lingkungan": hitung("lingkungan"),
    }


def ukur_pencarian(pencari: Pencari, uji: list[dict], hibrida: bool = False) -> dict:
    maks_k = max(K_DIUKUR)
    peringkat, skor_ada, skor_tiada, meleset = [], [], [], []

    for u in uji:
        hasil = pencari.cari(u["pertanyaan"], k=maks_k, sasaran=u["sasaran"],
                             hibrida=hibrida)
        skor1 = hasil[0].skor if hasil else 0.0
        if u["terjawab"]:
            r = peringkat_acuan(hasil, u["potongan_acuan"])
            peringkat.append(r)
            skor_ada.append(skor1)
            if r is None:
                meleset.append(u["pertanyaan"])
        else:
            skor_tiada.append(skor1)

    n = len(peringkat)
    return {
        "jumlah_berjawab": n,
        "recall": {k: sum(1 for r in peringkat if r and r <= k) / n for k in K_DIUKUR},
        "mrr": sum(1 / r for r in peringkat if r) / n,
        "tidak_terambil": meleset,
        "skor_terendah_berjawab": min(skor_ada) if skor_ada else 0.0,
        "skor_tertinggi_tak_dijawab": max(skor_tiada) if skor_tiada else 0.0,
    }


def ukur_guardrail() -> dict:
    lolos = [q for q in KLINIS if not pertanyaan_klinis(q)]
    salah_tahan = [q for q in SAH if pertanyaan_klinis(q)]
    tautan_lolos = [t for t in KELUARAN_NAKAL
                    if "http" in bersihkan_keluaran(t) or "www." in bersihkan_keluaran(t)]
    return {
        "klinis_tertahan": (len(KLINIS) - len(lolos), len(KLINIS)),
        "klinis_lolos": lolos,
        "sah_lolos": (len(SAH) - len(salah_tahan), len(SAH)),
        "sah_salah_tahan": salah_tahan,
        "tautan_dibuang": (len(KELUARAN_NAKAL) - len(tautan_lolos), len(KELUARAN_NAKAL)),
    }


def ukur_jawaban(penjawab: Penjawab, uji: list[dict], tampilkan: bool = False) -> dict:
    """Ukur mutu jawaban atas seluruh pertanyaan uji.

    `tampilkan` menyalakan laporan kemajuan per pertanyaan. Wajib dinyalakan
    untuk mesin llm: bagian ini memakan belasan menit, dan tanpa tanda apa pun
    di layar tidak ada cara membedakan "sedang bekerja" dari "macet". Itu bukan
    dugaan -- pernah terjadi, dan yang terjadi berikutnya adalah prosesnya
    dimatikan padahal sebenarnya baik-baik saja.
    """
    tepat1 = tepat = meleset = tertolak_padahal_ada = 0
    menolak_benar = mengarang = 0
    mengarang_apa, bahasa = [], []
    # Liputan = bagian kata isi jawaban yang benar-benar ada di potongan.
    # Dicatat terpisah untuk jawaban yang SEHARUSNYA diberikan dan yang
    # SEHARUSNYA ditolak. Kalau kedua sebaran ini terpisah, ambang penolakan
    # berbasis liputan bisa dipakai; kalau bertumpang tindih, tidak bisa --
    # sama seperti pelajaran dari ambang skor kemiripan.
    liputan_ada, liputan_tiada = [], []

    mulai = time.time()
    for nomor, u in enumerate(uji, 1):
        j = penjawab.jawab(u["pertanyaan"], sasaran=u["sasaran"])

        if tampilkan:
            # Sisa waktu diperkirakan dari rata-rata yang SUDAH terjadi, bukan
            # dari angka tetap: kecepatan tiap mesin dan tiap kartu grafis beda.
            lewat = time.time() - mulai
            sisa = lewat / nomor * (len(uji) - nomor)
            # DUA kolom, bukan satu label gabungan. Versi sebelumnya menulis
            # "tolak ok" untuk penolakan yang benar, dan itu terbaca ambigu --
            # "ketolak, atau oke?". Padahal yang perlu diketahui memang dua hal
            # berbeda: APA yang dilakukan sistem, dan APAKAH itu benar.
            lakuan = "menolak " if j.ditolak else "menjawab"
            if u["terjawab"]:
                benar = j.ditolak is False
            else:
                benar = j.ditolak is True
            # Mengarang diberi nama sendiri, bukan sekadar "SALAH menjawab":
            # ini satu-satunya kegagalan yang menyesatkan pembaca, sedangkan
            # menolak berlebihan cuma mengecewakan.
            if not benar and not j.ditolak and not u["terjawab"]:
                nilai, lakuan = "MENGARANG", "        "
            else:
                nilai = "benar" if benar else "SALAH"
            lapor(f"      {nomor:3}/{len(uji)}  {nilai:9} {lakuan}  "
                  f"sisa ~{sisa/60:4.1f} mnt  {u['pertanyaan'][:44]}")

        if u["terjawab"]:
            if j.ditolak:
                tertolak_padahal_ada += 1
            else:
                dikutip = [s["url"] for s in j.sumber]
                if dikutip and dikutip[0] == u["sumber_acuan"]:
                    tepat1 += 1
                if u["sumber_acuan"] in dikutip:
                    tepat += 1
                else:
                    meleset += 1
        else:
            if j.ditolak:
                menolak_benar += 1
            else:
                mengarang += 1
                mengarang_apa.append(u["pertanyaan"])
        if u["sasaran"] == "masyarakat" and not j.ditolak:
            bahasa.append(ukur_bahasa(j.teks))
        if not j.ditolak and j.hasil_cari:
            lip = max(liputan_potongan(j.teks, h) for h in j.hasil_cari)
            (liputan_ada if u["terjawab"] else liputan_tiada).append(lip)

    n_ada = sum(1 for u in uji if u["terjawab"])
    n_tiada = len(uji) - n_ada
    return {
        "n_ada": n_ada, "n_tiada": n_tiada,
        "sumber_pertama_tepat": tepat1, "sumber_ikut_dikutip": tepat,
        "sumber_meleset": meleset, "tertolak_padahal_ada": tertolak_padahal_ada,
        "menolak_benar": menolak_benar, "mengarang": mengarang,
        "mengarang_apa": mengarang_apa,
        "kata_per_kalimat": sum(a for a, _ in bahasa) / len(bahasa) if bahasa else 0,
        "istilah_teknis": sum(b for _, b in bahasa) / len(bahasa) if bahasa else 0,
        "jumlah_jawaban_masyarakat": len(bahasa),
        "liputan_ada": sorted(liputan_ada),
        "liputan_tiada": sorted(liputan_tiada),
    }


def tulis_laporan(path: Path, korpus, cari, guard, jawab, uji, mesin, model) -> None:
    n_sintetis = sum(1 for u in uji if u.get("asal_pertanyaan") != "lapangan")
    n_lapangan = len(uji) - n_sintetis
    b = []
    A = b.append

    A(f"# Laporan Evaluasi — Asisten Tanya-Jawab CKG\n")
    A(f"Dibuat otomatis oleh `eval/evaluate.py` pada {hari_ini()}.")
    A(f"Mesin jawaban: **{mesin}**" + (f" (`{model}`)" if mesin == "llm" else "") + "\n")

    A("## 1. Korpus\n")
    A("| | |")
    A("|---|---|")
    A(f"| Potongan | **{korpus['jumlah_potongan']}** |")
    A(f"| Dokumen | {korpus['jumlah_dokumen']} |")
    A(f"| Per sumber | {' · '.join(f'{k} {v}' for k, v in korpus['per_sumber'].items())} |")
    A(f"| Per sasaran | {' · '.join(f'{k} {v}' for k, v in korpus['per_sasaran'].items())} |")
    A(f"| Per jenis | {' · '.join(f'{k} {v}' for k, v in korpus['per_jenis'].items())} |")
    A(f"| Panjang potongan | median {korpus['median_karakter']} · maks {korpus['maks_karakter']} huruf |")
    A(f"| Model pencarian | `{korpus['model_embedding']}` |\n")

    A("## 2. Berkas uji\n")
    A(f"- Total **{len(uji)}** pertanyaan: {cari['jumlah_berjawab']} ada jawabannya, "
      f"{len(uji) - cari['jumlah_berjawab']} sengaja tanpa jawaban di korpus")
    A(f"- Asal: {n_sintetis} sintetis, **{n_lapangan} dari lapangan**")
    if n_lapangan == 0:
        A("\n> **Peringatan.** Belum ada satu pun pertanyaan dari petugas sungguhan. "
          "Pertanyaan sintetis cenderung lebih rapi daripada pertanyaan nyata, "
          "sehingga angka di bawah ini berpotensi lebih baik daripada kenyataannya.")
    A("")

    A("## 3. Mutu pencarian\n")
    A("Recall@k = dari k potongan teratas, apakah dokumen acuan ikut terambil. "
      "Ini batas atas mutu sistem: dokumen yang tidak pernah terambil mustahil jadi jawaban.\n")
    A("| Ukuran | Nilai |")
    A("|---|---|")
    for k in K_DIUKUR:
        A(f"| Recall@{k} | {cari['recall'][k]:.0%} |")
    A(f"| MRR | {cari['mrr']:.3f} |\n")
    if cari["tidak_terambil"]:
        A(f"**{len(cari['tidak_terambil'])} pertanyaan yang dokumennya tidak terambil sama sekali:**\n")
        for q in cari["tidak_terambil"]:
            A(f"- {q}")
        A("")

    A("## 4. Bisakah penolakan memakai ambang skor?\n")
    A(f"- Skor **terendah** pertanyaan yang ada jawabannya: `{cari['skor_terendah_berjawab']:.4f}`")
    A(f"- Skor **tertinggi** pertanyaan yang tidak ada jawabannya: `{cari['skor_tertinggi_tak_dijawab']:.4f}`\n")
    if cari["skor_tertinggi_tak_dijawab"] < cari["skor_terendah_berjawab"]:
        A("Kedua rentang **terpisah**, jadi ambang skor tunggal bisa dipakai.\n")
    else:
        A("Kedua rentang **bertumpang tindih**, jadi aturan \"kalau skor < X maka tolak\" "
          "pasti salah — entah menolak pertanyaan sah, atau menjawab yang seharusnya "
          "ditolak. Karena itu penolakan ditangani dengan cara lain di `answer.py`.\n")

    A("## 5. Guardrail (dikerjakan kode, bukan LLM)\n")
    A("| Uji | Hasil |")
    A("|---|---|")
    A(f"| Pertanyaan klinis tertahan | {guard['klinis_tertahan'][0]}/{guard['klinis_tertahan'][1]} |")
    A(f"| Pertanyaan pencatatan tetap lolos | {guard['sah_lolos'][0]}/{guard['sah_lolos'][1]} |")
    A(f"| Tautan karangan LLM dibuang | {guard['tautan_dibuang'][0]}/{guard['tautan_dibuang'][1]} |")
    A("")
    for q in guard["klinis_lolos"]:
        A(f"- **BAHAYA**, pertanyaan klinis lolos: {q}")
    for q in guard["sah_salah_tahan"]:
        A(f"- Pertanyaan sah salah tertahan: {q}")
    A("")

    A("## 6. Mutu jawaban\n")
    A("| Ukuran | Nilai |")
    A("|---|---|")
    A(f"| Sumber pertama sudah tepat | {jawab['sumber_pertama_tepat']}/{jawab['n_ada']} "
      f"({jawab['sumber_pertama_tepat'] / jawab['n_ada']:.0%}) |")
    A(f"| Sumber acuan ikut dikutip | {jawab['sumber_ikut_dikutip']}/{jawab['n_ada']} "
      f"({jawab['sumber_ikut_dikutip'] / jawab['n_ada']:.0%}) |")
    A(f"| Ditolak padahal ada jawabannya | {jawab['tertolak_padahal_ada']} |")
    A(f"| **Menolak dengan benar** | {jawab['menolak_benar']}/{jawab['n_tiada']} "
      f"({jawab['menolak_benar'] / jawab['n_tiada']:.0%}) |")
    A(f"| **Mengarang** | {jawab['mengarang']} |")
    A(f"| Kata per kalimat (masyarakat) | {jawab['kata_per_kalimat']:.1f} |")
    A(f"| Istilah teknis (masyarakat) | {jawab['istilah_teknis']:.1f} |\n")
    if jawab["mengarang_apa"]:
        A("**Pertanyaan yang dikarang padahal jawabannya tidak ada di korpus:**\n")
        for q in jawab["mengarang_apa"]:
            A(f"- {q}")
        A("")

    # Hanya bermakna untuk mesin llm. Mesin ekstraktif menyalin potongan apa
    # adanya, jadi liputannya selalu mendekati 1 dan tidak memberi tahu apa pun.
    la, lt = jawab["liputan_ada"], jawab["liputan_tiada"]
    if mesin == "llm" and la and lt:
        A("## 6b. Bisakah penolakan memakai ambang LIPUTAN KATA?\n")
        A("Liputan = bagian kata isi jawaban yang benar-benar muncul di potongan "
          "yang disodorkan. Jawaban yang dikarang dari pengetahuan model, bukan "
          "dari dokumen, seharusnya berliputan rendah.\n")
        A(f"- Jawaban yang memang ada dokumennya: terendah `{min(la):.2f}` · "
          f"median `{la[len(la) // 2]:.2f}`")
        A(f"- Jawaban yang seharusnya ditolak: tertinggi `{max(lt):.2f}` · "
          f"median `{lt[len(lt) // 2]:.2f}`\n")
        if max(lt) < min(la):
            A(f"Kedua sebaran **terpisah**. Ambang di sekitar "
              f"`{(max(lt) + min(la)) / 2:.2f}` bisa dipakai untuk menolak jawaban "
              "yang tidak berpijak pada dokumen — dan itu berlaku untuk model apa pun.\n")
        else:
            A("Kedua sebaran **bertumpang tindih**, jadi ambang liputan tunggal juga "
              "tidak bisa memisahkan — persis seperti pelajaran dari ambang skor "
              "kemiripan. Perlu sinyal lain.\n")

    A("## 7. Keterbatasan yang diketahui\n")
    A("Bagian ini sengaja selalu ada. Laporan evaluasi tanpa daftar kelemahan "
      "tidak bisa dipertanggungjawabkan.\n")
    if n_lapangan == 0:
        A("1. **Seluruh pertanyaan uji masih sintetis.** Belum diuji dengan pertanyaan "
          "nyata petugas, yang biasanya lebih singkat, penuh singkatan, dan salah ketik.")
    A("2. **Ketepatan ISI jawaban belum diukur.** Kolom `jawaban_acuan` masih kosong; "
       "mengisinya butuh manusia yang membaca dokumennya. Yang terukur di atas baru "
       "ketepatan SUMBER.")
    A("3. **Satu dokumen acuan per pertanyaan.** Kalau beberapa dokumen sama-sama "
       "menjawab, jawaban benar dari dokumen lain tetap dihitung meleset.")
    A("4. **Label `jenis` masih heuristik**, ditandai `jenis_metode` di tiap potongan.")
    stg = korpus["lingkungan"].get("staging", 0)
    if stg:
        A(f"5. **{stg} potongan berasal dari lingkungan staging** (Pusat Bantuan ASIK), "
          "bukan produksi. Alamat produksi belum dikonfirmasi.")
    if mesin == "ekstraktif":
        A("6. **Diukur dengan mesin ekstraktif**, yang tidak bisa menolak menjawab. "
          "Angka 'menolak dengan benar' di atas hanya mencerminkan penapis klinis. "
          "Jalankan ulang dengan `--mesin llm` untuk angka sistem yang sebenarnya.")
    A("")

    path.write_text("\n".join(b), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Jalankan seluruh evaluasi dan tulis laporan.")
    p.add_argument("--mesin", default="ekstraktif", choices=["llm", "ekstraktif"])
    p.add_argument("--uji", default=str(HERE / "pertanyaan_uji.jsonl"))
    p.add_argument("--keluaran", default=str(HERE / "laporan_evaluasi.md"))
    p.add_argument("-k", type=int, default=4)
    p.add_argument("--tanpa-hibrida", action="store_true",
                   help="pakai embedding saja, untuk membandingkan dengan hibrida")
    p.add_argument("--model", default=None,
                   help="model bahasa lain, mis. Qwen/Qwen2.5-1.5B-Instruct. "
                        "Dipakai untuk MEMBANDINGKAN model, bukan menebak mana yang terbaik.")
    args = p.parse_args()

    uji = baca_jsonl(Path(args.uji))
    lapor(f"[1/4] Korpus")
    korpus = ukur_korpus()
    lapor(f"      {korpus['jumlah_potongan']} potongan, {korpus['jumlah_dokumen']} dokumen")

    lapor("[2/4] Guardrail (tanpa LLM)")
    guard = ukur_guardrail()
    lapor(f"      klinis tertahan {guard['klinis_tertahan'][0]}/{guard['klinis_tertahan'][1]}, "
          f"sah lolos {guard['sah_lolos'][0]}/{guard['sah_lolos'][1]}")

    lapor(f"[3/4] Pencarian ({len(uji)} pertanyaan)"
          + ("  [embedding saja]" if args.tanpa_hibrida else "  [hibrida: embedding + kata kunci]"))
    kwargs = {"mesin": args.mesin, "k": args.k}
    if args.model:
        kwargs["model"] = args.model
    penjawab = Penjawab(**kwargs)
    cari = ukur_pencarian(penjawab.pencari, uji, hibrida=not args.tanpa_hibrida)
    lapor(f"      Recall@1 {cari['recall'][1]:.0%}, Recall@5 {cari['recall'][5]:.0%}, "
          f"MRR {cari['mrr']:.3f}")

    lapor(f"[4/4] Jawaban (mesin {args.mesin})")
    if args.mesin == "llm":
        lapor("      memuat model bahasa, lalu menjawab satu per satu — ini lama")
    jawab = ukur_jawaban(penjawab, uji, tampilkan=(args.mesin == "llm"))
    lapor(f"      sumber pertama tepat {jawab['sumber_pertama_tepat']}/{jawab['n_ada']}, "
          f"menolak benar {jawab['menolak_benar']}/{jawab['n_tiada']}, "
          f"mengarang {jawab['mengarang']}")

    keluaran = Path(args.keluaran)
    tulis_laporan(keluaran, korpus, cari, guard, jawab, uji,
                  args.mesin, penjawab.nama_model)
    lapor(f"\nLaporan: {keluaran}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
