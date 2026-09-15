"""Antarmuka web untuk demo dan uji coba.

    python web/app_web.py
    lalu buka http://127.0.0.1:5000

=== KENAPA ADA, DAN APA YANG BUKAN ===

Ini ANTARMUKA DEMO LOKAL, bukan deployment. CLAUDE.md §8 menyebut deployment
di luar lingkup satu bulan, dan itu tidak diubah oleh berkas ini: tidak ada
server publik, tidak ada domain, tidak ada basis data, tidak ada autentikasi.
Yang ada hanya halaman lokal supaya sistem lebih enak dipakai saat uji coba
dan lebih jelas saat dipresentasikan ke pembimbing.

Bawaannya mengikat ke 127.0.0.1 -- hanya bisa dibuka dari komputer ini sendiri.
Untuk demo di ruang rapat lewat jaringan lokal, pakai --host 0.0.0.0, dan
sadari bahwa siapa pun di jaringan itu bisa membukanya.

=== TIDAK ADA LOGIKA BARU DI SINI ===

Berkas ini TIDAK menyalin satu pun aturan dari answer.py. Ia memanggil
Penjawab yang sama persis dengan yang dipakai app.py dan eval/. Kalau
antarmuka web punya salinan penapis klinis atau aturan sitasi sendiri, cepat
atau lambat keduanya akan berbeda -- dan yang didemokan bukan lagi sistem yang
diukur. Satu sumber kebenaran, dipakai bersama.

=== KENAPA FLASK, BUKAN GRADIO ===

Gradio lebih cepat dibuat tapi tampilannya seragam dan sulit diatur. Yang perlu
ditonjolkan di sistem ini justru hal yang tidak disediakan Gradio secara rapi:
daftar sumber beserta tautannya, dan tampilan BERBEDA saat sistem menolak
menjawab. Penolakan adalah fitur, bukan kegagalan, jadi ia harus terlihat
sebagai keputusan yang disengaja -- bukan seperti error.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AKAR / "src"))

from flask import Flask, jsonify, render_template, request  # noqa: E402

from answer import Penjawab  # noqa: E402
from utils import daftar_sasaran, lapor  # noqa: E402

app = Flask(__name__)

# Jinja menyimpan templat yang sudah dikompilasi di memori, dan karena server
# ini tidak berjalan dalam mode debug, penyimpanan itu TIDAK pernah kedaluwarsa.
# Akibatnya menyunting index.html tidak mengubah apa pun sampai server dimatikan
# dan dijalankan ulang -- yang memakan satu menit penuh karena model ikut dimuat
# ulang. Saat menyetel tampilan menjelang demo, itu menyesatkan: perubahan
# terasa "tidak berpengaruh" padahal sebenarnya belum pernah dibaca.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
PENJAWAB: Penjawab | None = None

# ---------------------------------------------------------------------------
# SATU THREAD PEKERJA YANG MEMILIKI MODEL
#
# Kenapa serumit ini, padahal cukup memanggil PENJAWAB.jawab() langsung?
#
# Karena cara sederhana itu SUDAH DICOBA DAN CRASH. Prosesnya mati dengan
# segmentation fault (exit code 139) begitu pertanyaan pertama dikirim --
# tanpa pesan error apa pun, jendelanya langsung menutup. Menyetel
# threaded=False pada Flask juga tidak menolong.
#
# Petunjuknya: generasi yang sama persis berjalan mulus 63 kali berturut-turut
# di eval/evaluate.py, yang cuma skrip biasa. Yang berbeda hanya satu -- di
# Flask, kode berjalan di thread yang dikelola server web.
#
# Rancangan ini menghilangkan variabel itu sepenuhnya: satu thread dibuat saat
# start-up, thread ITU yang memuat model, dan thread ITU JUGA satu-satunya yang
# pernah menyentuhnya. Permintaan web hanya menitipkan pertanyaan lewat antrean
# lalu menunggu jawabannya. Model tidak pernah disentuh dari thread lain.
#
# Efek sampingnya kebetulan kita inginkan juga: permintaan diproses satu per
# satu, jadi dua orang yang mengklik bersamaan saat demo tidak berebut GPU.
# ---------------------------------------------------------------------------
ANTREAN: "queue.Queue" = queue.Queue()
SIAP = threading.Event()
PEKERJA: "threading.Thread | None" = None


def pekerja_model(penjawab: Penjawab, mesin: str) -> None:
    """Berjalan seumur hidup program. Memuat model, lalu melayani antrean."""
    try:
        # Model PENCARIAN dimuat lebih dulu, sengaja. Sebelumnya ia dimuat
        # malas -- baru saat pertanyaan pertama masuk -- dan itu bencana:
        # model bahasa sudah menempati 95% VRAM, lalu model pencarian meminta
        # 1,1 GB lagi di tengah permintaan pengguna. Prosesnya mati saat demo
        # berlangsung, bukan saat start.
        #
        # Sekarang keduanya dimuat di depan. Kalau VRAM tidak cukup, gagalnya
        # terjadi sebelum server menerima satu pun pertanyaan -- jauh lebih
        # baik daripada gagal di depan pembimbing.
        lapor("Menyiapkan model pencarian...")
        penjawab.pencari.model
        if mesin == "llm":
            lapor("Menyiapkan model bahasa (sekitar satu menit)...")
            penjawab._muat_llm()
        else:
            lapor("PERINGATAN: mesin ekstraktif tidak bisa menolak menjawab. "
                  "Hanya untuk pembanding.")
    except Exception as e:  # noqa: BLE001
        lapor(f"GAGAL memuat model: {type(e).__name__}: {e}")
    finally:
        SIAP.set()

    while True:
        pertanyaan, sasaran, rinci, balasan = ANTREAN.get()
        try:
            balasan.put(("ok", penjawab.jawab(pertanyaan, sasaran=sasaran, rinci=rinci)))
        except Exception as e:  # noqa: BLE001
            balasan.put(("galat", f"{type(e).__name__}: {e}"))


# 90 detik, bukan 180. Jawaban normal selesai dalam 20-40 detik, jadi menunggu
# tiga menit hanya memperpanjang penderitaan sebelum orang tahu ada yang salah.
def minta_jawaban(pertanyaan: str, sasaran: str, rinci=None, batas_detik: int = 90):
    """Titipkan pertanyaan ke thread pekerja, tunggu jawabannya.

    Kegagalan di sini dibedakan dengan jelas, karena penyebabnya sangat
    berlainan dan penanganannya pun beda. Sebelumnya semuanya jatuh jadi satu
    pesan "Gagal menyusun jawaban:" tanpa keterangan apa pun -- yang berarti
    tidak memberi tahu apa-apa kepada orang yang harus memperbaikinya.
    """
    if PEKERJA is not None and not PEKERJA.is_alive():
        raise RuntimeError(
            "Thread pemroses model sudah mati. Ini biasanya berarti model "
            "kehabisan VRAM. Hentikan server, tutup aplikasi lain yang memakai "
            "GPU, lalu jalankan ulang -- atau pakai --4bit yang hanya butuh ~2 GB."
        )

    balasan: "queue.Queue" = queue.Queue()
    mulai = time.time()
    ANTREAN.put((pertanyaan, sasaran, rinci, balasan))
    try:
        status, isi = balasan.get(timeout=batas_detik)
    except queue.Empty:
        # Sarannya menyesuaikan mode yang SEDANG berjalan. Menyuruh orang
        # memakai --4bit padahal ia sudah memakainya cuma bikin bingung.
        if PENJAWAB is not None and PENJAWAB.kuantisasi == "4bit":
            saran = ("Mode 4-bit sudah aktif, jadi ini bukan soal VRAM. "
                     "Hentikan server (Ctrl+C), pastikan tidak ada proses Python "
                     "lain (tasklist | findstr python.exe), lalu jalankan ulang.")
        else:
            saran = ("Mode saat ini TANPA kuantisasi, dan itu butuh ~8,1 GB — "
                     "praktis tidak muat di GPU 8 GB. Hentikan server lalu "
                     "jalankan ulang dengan --4bit.")
        raise RuntimeError(
            f"Tidak ada jawaban setelah {batas_detik} detik. {saran}"
        ) from None
    if status == "galat":
        raise RuntimeError(isi)
    lapor(f"      dijawab dalam {time.time() - mulai:.1f} detik")
    return isi


def ringkasan_korpus(penjawab: Penjawab) -> dict:
    """Angka korpus untuk ditampilkan di halaman.

    Ditampilkan supaya penonton demo langsung tahu sistem menjawab dari
    berapa banyak dokumen, dan dari sumber apa saja -- bukan dari "AI yang
    tahu segalanya".
    """
    pot = penjawab.pencari.potongan
    per_sasaran: dict[str, int] = {}
    per_sumber: dict[str, int] = {}
    for x in pot:
        # Potongan yang menyasar dua kelompok dihitung di keduanya, jadi
        # jumlahnya bisa melebihi total potongan. Lihat daftar_sasaran().
        for s in daftar_sasaran(x):
            per_sasaran[s] = per_sasaran.get(s, 0) + 1
        per_sumber[x["sumber_id"]] = per_sumber.get(x["sumber_id"], 0) + 1
    return {
        "jumlah_potongan": len(pot),
        "jumlah_dokumen": len({x["sumber_url"] for x in pot}),
        "per_sasaran": dict(sorted(per_sasaran.items(), key=lambda x: -x[1])),
        "per_sumber": dict(sorted(per_sumber.items(), key=lambda x: -x[1])),
        "model_embedding": penjawab.pencari.meta["model"],
        "model_bahasa": penjawab.nama_model if penjawab.mesin == "llm" else "(tanpa LLM)",
        "mesin": penjawab.mesin,
    }


# Nama sumber diambil DARI KORPUS, bukan dipatok di sini.
#
# Tiap potongan sudah membawa `sumber_nama` berpola tetap
# "<nama sumber> — <judul>", jadi bagian sebelum tanda pisah itulah nama
# sumbernya. Daftar di bawah cuma untuk MEMENDEKKAN yang kepanjangan.
#
# Sebelumnya daftar ini yang jadi satu-satunya sumber nama, dan akibatnya
# 6 dari 12 sumber tampil sebagai kode mentah di panel demo -- "faq_kyc",
# "juknis_sekolah", "faq_resume_medis". Itu terjadi karena tiap kali korpus
# bertambah, daftar ini harus disunting manual, dan itu terlupa. Sekarang
# sumber baru tampil dengan nama yang benar tanpa menyentuh kode sama sekali.
NAMA_SUMBER_RINGKAS = {
    "juknis": "Juknis CKG (Kepmenkes 84/2026)",
    "juknis_sekolah": "Juknis CKG Sekolah (Kepmenkes 770/2025)",
}


def nama_tiap_sumber(potongan: list[dict]) -> dict[str, str]:
    """Peta sumber_id -> nama tampil, dibangun dari korpus."""
    nama: dict[str, str] = {}
    for x in potongan:
        sid = x.get("sumber_id", "")
        if sid and sid not in nama:
            nama[sid] = (x.get("sumber_nama") or "").split(" — ")[0].strip() or sid
    # Pemendekan manual menang atas nama bawaan korpus.
    nama.update(NAMA_SUMBER_RINGKAS)
    return nama

LABEL_SASARAN = {
    "nakes": "Tenaga kesehatan / kader",
    "masyarakat": "Masyarakat umum / peserta",
    "sekolah": "Pihak sekolah",
}


# Logo dipasang hanya kalau berkasnya benar-benar ada. Tidak diunduh otomatis:
# memasang lambang resmi sebuah kementerian pada prototipe adalah keputusan yang
# harus diambil pembimbing, bukan diputuskan oleh kode. Taruh berkasnya di
# web/static/logo.png kalau sudah disetujui.
BERKAS_LOGO = Path(__file__).resolve().parent / "static" / "logo.png"


@app.get("/")
def beranda():
    info = ringkasan_korpus(PENJAWAB)
    sasaran = [(s, LABEL_SASARAN.get(s, s), n) for s, n in info["per_sasaran"].items()]
    return render_template("index.html", info=info, sasaran=sasaran,
                           nama_sumber=nama_tiap_sumber(PENJAWAB.pencari.potongan),
                           ada_logo=BERKAS_LOGO.exists())


@app.post("/tanya")
def tanya():
    data = request.get_json(silent=True) or {}
    pertanyaan = (data.get("pertanyaan") or "").strip()
    sasaran = data.get("sasaran") or "nakes"
    # rinci: True = paksa panjang, False = paksa ringkas, None = deteksi
    # sendiri dari kalimat pertanyaannya. Halaman mengirim True hanya kalau
    # kotak "jawaban rinci" dicentang; kalau tidak dikirim sama sekali,
    # deteksi otomatis yang bekerja.
    rinci = data.get("rinci")
    if rinci is not None:
        rinci = bool(rinci) or None

    if not pertanyaan:
        return jsonify({"galat": "Pertanyaan kosong."}), 400

    try:
        j = minta_jawaban(pertanyaan, sasaran, rinci)
    except Exception as e:  # noqa: BLE001
        pesan = str(e) or f"{type(e).__name__} tanpa keterangan"
        # Dicatat juga di terminal, bukan cuma dikirim ke browser -- supaya
        # jejaknya tetap ada walau halamannya sudah ditutup.
        lapor(f"GAGAL menjawab: {pesan}")
        return jsonify({"galat": pesan}), 500

    return jsonify({
        "teks": j.teks,
        "ditolak": j.ditolak,
        "alasan_tolak": j.alasan_tolak,
        "sumber": j.sumber,
        # Potongan yang dipertimbangkan ikut dikirim supaya bisa ditelusuri saat
        # demo: kalau jawabannya terasa aneh, penonton bisa langsung melihat
        # apakah pencariannya yang meleset atau perangkumannya.
        "kandidat": [
            {
                "skor": round(h.skor, 4),
                "judul": h.potongan["judul_dokumen"],
                "bagian": h.potongan["judul_bagian"],
                "sumber_id": h.potongan["sumber_id"],
                "sumber_label": nama_tiap_sumber(PENJAWAB.pencari.potongan).get(
                    h.potongan["sumber_id"], h.potongan["sumber_id"]),
                "jenis": h.potongan["jenis"],
                "url": h.potongan["sumber_url"],
            }
            for h in j.hasil_cari
        ],
    })


def main() -> int:
    global PENJAWAB, PEKERJA
    p = argparse.ArgumentParser(description="Antarmuka web demo CKG.")
    p.add_argument("--host", default="127.0.0.1",
                   help="0.0.0.0 supaya bisa dibuka dari komputer lain di jaringan yang sama")
    p.add_argument("--port", type=int, default=5000)
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
    PENJAWAB = Penjawab(**kwargs)

    # Thread pekerja dinyalakan SEBELUM server web, dan ia yang memuat model.
    # daemon=True supaya Ctrl+C tetap menutup program dengan bersih.
    PEKERJA = threading.Thread(target=pekerja_model, args=(PENJAWAB, args.mesin),
                               daemon=True, name="model")
    PEKERJA.start()
    # Menunggu dengan tanda hidup. Tanpa ini layar diam sekitar satu menit dan
    # terlihat seperti macet -- lalu orang menekan Ctrl+C padahal semuanya
    # baik-baik saja. Titik yang bertambah tiap 3 detik menunjukkan prosesnya
    # masih berjalan.
    # Menunggu dengan tanda hidup. Tanpa ini layar diam sekitar satu menit
    # dan terlihat seperti macet -- lalu orang menekan Ctrl+C padahal semuanya
    # baik-baik saja.
    detik = 0
    while not SIAP.wait(timeout=10):
        detik += 10
        lapor(f"      masih memuat... {detik} detik")
    lapor("Model siap.")

    info = ringkasan_korpus(PENJAWAB)
    lapor(f"Korpus  : {info['jumlah_potongan']} potongan dari {info['jumlah_dokumen']} dokumen")
    kuant = PENJAWAB.kuantisasi
    lapor(f"Mode    : {args.mesin}"
          + (" + kuantisasi 4-bit" if kuant == "4bit" else " tanpa kuantisasi"))
    lapor(f"Buka    : http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}")
    if args.host == "0.0.0.0":
        lapor("CATATAN : terbuka untuk seluruh jaringan lokal. Jangan dipakai di jaringan publik.")
    lapor("Berhenti: tekan Ctrl+C\n")

    # ---------------------------------------------------------------------
    # Server bawaan Flask (Werkzeug) TIDAK dipakai di sini, dan itu bukan soal
    # selera. Dengan Werkzeug, proses mati dengan segmentation fault (exit code
    # 139) begitu jawaban mulai disusun -- tanpa pesan error, jendelanya
    # langsung menutup.
    #
    # Sudah dipersempit dengan tiga percobaan:
    #   1. threaded=False               -> tetap crash
    #   2. thread pekerja khusus model  -> tetap crash
    #   3. tanpa Flask (src/answer.py)  -> BERHASIL, exit code 0
    #
    # Jadi masalahnya di server bawaan Flask, bukan di kode penyusun jawaban
    # maupun di threading kita. Waitress adalah server WSGI murni Python yang
    # lazim dipakai di Windows, dan tidak memasang penanganan sinyal/soket yang
    # bentrok dengan CUDA.
    #
    # threads=1 dipertahankan: satu model, satu GPU, satu pertanyaan pada satu
    # waktu. Antrean di atas tetap berguna sebagai lapis kedua.
    # ---------------------------------------------------------------------
    try:
        from waitress import serve
    except ImportError:
        lapor("GAGAL: waitress belum terpasang. Jalankan:  pip install waitress")
        return 1

    serve(app, host=args.host, port=args.port, threads=1, _quiet=True)
    return 0


if __name__ == "__main__":
    # Ctrl+C adalah cara normal menghentikan server, bukan kesalahan. Tanpa
    # penanganan ini Python menumpahkan traceback panjang yang terlihat seperti
    # program rusak, padahal pengguna sendiri yang menghentikannya.
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        lapor("Dihentikan. Server berhenti dengan bersih.")
        raise SystemExit(0)
