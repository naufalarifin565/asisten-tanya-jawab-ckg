"""Pembersihan, pemotongan, dan pemberian metadata korpus CKG.

Masukan : data/raw/asik/*.md  +  data/raw/faq/<kategori>/*.json
          + data/raw/juknis/*.txt (Juknis CKG, dokumen resmi berstruktur BAB)
Keluaran: data/processed/korpus.jsonl  (satu potongan per baris)
          data/processed/statistik.json

=== STRATEGI PEMOTONGAN (kenapa begini) ===

Aturan utama: SATU HALAMAN = SATU POTONGAN. Potong hanya kalau kepanjangan.

Alasannya bentuk datanya sendiri:
  * Satu halaman Pusat Bantuan ASIK sudah merupakan satu topik utuh -- biasanya
    satu prosedur bernomor. Kalau prosedur 6 langkah dipotong di tengah, sistem
    bisa mengembalikan setengah prosedur dan jawabannya jadi SALAH, bukan
    sekadar kurang lengkap. Ini risiko nyata untuk panduan pencatatan.
  * Ukuran halamannya memang sudah pas: mayoritas 700-1.500 karakter
    (~200-400 token), muat utuh di jendela model embedding yang lazim (512 token).

Ambang:
  POTONG_MAKS = 1600 karakter (~400 token). Diambil dari batas jendela model
                embedding, bukan angka karangan.
  POTONG_MIN  = 200 karakter. Potongan lebih pendek dari ini digabung ke
                tetangganya -- potongan kerdil hanya mengotori hasil pencarian.
  TUMPANG     = 150 karakter, HANYA untuk halaman yang benar-benar terpotong.
                Halaman yang muat utuh tidak diberi tumpang tindih: percuma dan
                membengkakkan indeks.

Urutan titik potong (dari yang paling menghormati struktur):
  1. batas heading (## / ###)
  2. batas paragraf
  3. batas kalimat
  Tidak pernah memotong di tengah kalimat kecuali satu kalimat sendirian sudah
  melebihi ambang (praktis tidak terjadi di korpus ini).

FAQ diperlakukan khusus: satu topik = satu potongan, dan TEKS PERTANYAANNYA ikut
disimpan di dalam potongan. Sebabnya, pertanyaan pengguna nanti bentuknya mirip
pertanyaan FAQ, jadi kemiripan embedding paling tinggi kalau pertanyaannya ada
di dalam potongan itu sendiri. Kalau jawabannya terlalu panjang dan terpaksa
dipotong, pertanyaannya diulang di setiap bagian supaya pasangan tanya-jawab
tidak pernah terputus.

Setiap potongan diawali JEJAK JUDUL ("# Judul Halaman" / "## Sub-judul") supaya
potongan yang berdiri sendiri tetap punya konteks -- membantu embedding maupun
LLM saat menyusun jawaban.

Contoh pakai:
    python src/chunk.py
    python src/chunk.py --maks 1200        # coba ambang lain
"""

from __future__ import annotations

import argparse
import itertools
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from utils import (
    DATA_MENTAH,
    DATA_OLAHAN,
    baca_json,
    daftar_sasaran,
    hari_ini,
    lapor,
    perkiraan_token,
    rapikan_spasi,
    sha256_teks,
    slug,
    tulis_json,
    tulis_jsonl,
)

POTONG_MAKS = 1600
POTONG_MIN = 200
TUMPANG = 150

# ---------------------------------------------------------------------------
# Penentuan metadata "jenis" (metadata wajib)
#
# JUJUR: ini heuristik kata kunci, bukan kebenaran. Dipakai supaya 200+ potongan
# tidak perlu dilabeli tangan satu per satu di minggu pertama. Setiap potongan
# diberi penanda jenis_metode="heuristik" dan skrip ini mencetak distribusinya,
# supaya labelnya bisa diperiksa dan dikoreksi manual sebelum dipakai untuk
# penyaringan jawaban.
# ---------------------------------------------------------------------------
KATA_KENDALA = [
    "tidak bisa", "tidak dapat", "tidak muncul", "tidak sesuai", "tidak menerima",
    "tidak terdaftar", "tidak ditemukan", "tidak valid", "tidak aktif", "belum",
    "gagal", "error", "kenapa", "mengapa", "lupa", "salah", "kesalahan",
    "kendala", "terkunci", "duplikat", "ganda", "bermasalah", "sudah terdaftar",
    "hilang", "keluhan",
]

# Jenis bawaan per sumber. Ini pengganti daftar kata kunci "ketentuan_program"
# yang sempat dipakai lalu dibuang, karena pendekatan kata kunci salah kerangka:
#   * "gratis" cocok dengan "Cek Kesehatan Gratis" di hampir setiap judul FAQ,
#     sehingga separuh FAQ salah label.
#   * sebaliknya "persyaratan" TIDAK cocok dengan pola \bsyarat\b, sehingga
#     "Apa saja persyaratan CKG?" justru terlewat.
# Yang sebenarnya menentukan bukan kata di judul, melainkan sifat sumbernya:
#   - Pusat Bantuan ASIK dan FAQ Fasyankes = dokumentasi memakai aplikasi.
#   - FAQ Umum ditujukan ke masyarakat dan hampir seluruhnya membahas program
#     itu sendiri: persyaratan, alur, siapa yang berhak, jenis pemeriksaan.
JENIS_BAWAAN = {
    "asik": "cara_pakai_aplikasi",
    "faq_fasyankes": "cara_pakai_aplikasi",
    "faq_umum": "ketentuan_program",
    "faq_sekolah": "ketentuan_program",
    # Prasyarat CKG: semuanya tentang cara memakai aplikasi SATUSEHAT Mobile,
    # bukan ketentuan program. Judul yang mengandung kata kendala tetap
    # dilabeli penanganan_kendala oleh tebak_jenis().
    "faq_kyc": "cara_pakai_aplikasi",
    "faq_akun": "cara_pakai_aplikasi",
    "faq_profil_terhubung": "cara_pakai_aplikasi",
    "faq_login": "cara_pakai_aplikasi",
    "juknis": "ketentuan_program",
    "web": "ketentuan_program",
}


def _pola(kata_kata: list[str]) -> re.Pattern:
    """Susun pola dengan BATAS KATA.

    Wajib pakai \\b: pencocokan substring biasa membuat "masalah" mengandung
    "salah", sehingga halaman "Tujuan ASIK" sempat terlabeli penanganan_kendala
    hanya karena badannya memuat kata "masalah". Kesalahan seperti ini sulit
    terlihat kalau labelnya tidak pernah diperiksa satu per satu.
    """
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kata_kata) + r")\b", re.I)


POLA_KENDALA = _pola(KATA_KENDALA)


def tebak_jenis(judul: str, sumber_id: str) -> str:
    """Tebak jenis potongan: kata kunci kendala di judul, sisanya dari sumbernya.

    Dua keputusan yang perlu bisa dijelaskan ke pembimbing:

    1. Untuk mendeteksi kendala, hanya JUDUL yang dipakai, bukan badan teks.
       Judul di korpus ini sangat deskriptif ("Data Capaian PTM Tidak Muncul",
       "Lupa PIN ASIK"), sedangkan badan teks penuh kata umum yang memicu salah
       label.

    2. Selebihnya ditentukan sumbernya, bukan kata kunci (lihat JENIS_BAWAAN).
       Pembedaan yang lebih halus dari ini butuh pelabelan manual, dan itu
       lebih jujur daripada menambah kata kunci sampai angkanya kelihatan bagus.
    """
    if POLA_KENDALA.search(judul):
        return "penanganan_kendala"
    return JENIS_BAWAAN.get(sumber_id, "cara_pakai_aplikasi")


# ---------------------------------------------------------------------------
# Pembersihan Markdown ASIK (sintaks GitBook)
# ---------------------------------------------------------------------------
POLA_BOILERPLATE = re.compile(r"^>\s*For the complete documentation index.*?$", re.M)
POLA_HINT_BUKA = re.compile(r'\{%\s*hint\s+style="(\w+)"\s*%\}')
POLA_HINT_TUTUP = re.compile(r"\{%\s*endhint\s*%\}")
POLA_EMBED = re.compile(r'\{%\s*(?:embed|file)\s+(?:url|src)="([^"]+)"[^%]*%\}')
POLA_TAG_SISA = re.compile(r"\{%[^%]*%\}")
POLA_FIGURE = re.compile(r"<figure>(.*?)</figure>", re.S)
POLA_IMG_MD = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
POLA_DETAILS = re.compile(r"<details>\s*<summary>(.*?)</summary>(.*?)</details>", re.S)
POLA_STEPPER = re.compile(r"\{%\s*stepper\s*%\}(.*?)\{%\s*endstepper\s*%\}", re.S)
POLA_STEP = re.compile(r"\{%\s*step\s*%\}(.*?)\{%\s*endstep\s*%\}", re.S)
POLA_TABEL = re.compile(r"<table.*?</table>", re.S)
POLA_A_HTML = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
POLA_TAG_HTML = re.compile(
    r"</?(?:mark|kbd|em|strong|span|div|br|p|code|figcaption|img|a|"
    r"table|thead|tbody|tr|td|th)[^>]*>"
)

LABEL_HINT = {
    "info": "Catatan", "success": "Catatan", "tip": "Tips",
    "warning": "Perhatian", "danger": "Perhatian",
}


def _langkah_bernomor(blok: str) -> str:
    """Ubah blok {% step %}...{% endstep %} jadi daftar bernomor biasa.

    WAJIB, bukan hiasan: kalau tag {% step %} sekadar dibuang, langkah-langkah
    prosedur berubah jadi paragraf lepas tanpa nomor. Petugas yang bertanya
    "bagaimana cara login" lalu menerima langkah tanpa urutan sama saja dengan
    menerima jawaban salah.
    """
    langkah = POLA_STEP.findall(blok)
    if not langkah:
        return blok
    hasil = []
    for i, isi in enumerate(langkah, 1):
        isi = isi.strip()
        # Baris lanjutan diberi indentasi supaya tetap terbaca sebagai satu butir.
        hasil.append(f"{i}. " + re.sub(r"\n\s*\n", "\n   ", isi))
    return "\n\n" + "\n".join(hasil) + "\n\n"


def _tabel_ke_teks(m: re.Match) -> str:
    """Ubah tabel HTML jadi baris "sel | sel | sel".

    Baris pertama (header) ikut disimpan supaya arti tiap kolom tidak hilang
    saat potongan berdiri sendiri.
    """
    sup = BeautifulSoup(m.group(0), "html.parser")
    baris = []
    for tr in sup.find_all("tr"):
        sel = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if any(sel):
            baris.append(" | ".join(sel))
    return "\n\n" + "\n".join(baris) + "\n\n" if baris else "\n"


def bersihkan_asik(teks: str) -> tuple[str, str, bool]:
    """Bersihkan satu halaman Markdown ASIK.

    Kembalikan (judul_dokumen, isi_bersih, ada_gambar).

    ada_gambar penting dicatat: banyak artikel bantuan mengandalkan tangkapan
    layar. Gambarnya TIDAK masuk korpus, jadi potongan yang aslinya bergambar
    berpotensi kehilangan informasi. Ini keterbatasan yang harus dilaporkan,
    bukan disembunyikan.
    """
    teks = POLA_BOILERPLATE.sub("", teks)

    ada_gambar = bool(POLA_FIGURE.search(teks) or POLA_IMG_MD.search(teks))

    def ganti_figure(m: re.Match) -> str:
        blok = m.group(1)
        alt = re.search(r'alt="([^"]*)"', blok)
        cap = re.search(r"<figcaption>(.*?)</figcaption>", blok, re.S)
        label = (cap.group(1) if cap else "") or (alt.group(1) if alt else "")
        label = re.sub(r"<[^>]+>", "", label).strip()
        # Gambar tanpa keterangan tidak menambah apa pun untuk pencarian teks.
        return f"\n[gambar: {label}]\n" if label else "\n"

    teks = POLA_FIGURE.sub(ganti_figure, teks)
    teks = POLA_IMG_MD.sub(lambda m: f"[gambar: {m.group(1)}]" if m.group(1).strip() else "", teks)

    # Blok tanya-jawab bawaan GitBook -> ditulis eksplisit sebagai Tanya/Jawab.
    teks = POLA_DETAILS.sub(
        lambda m: f"\n\n**Tanya:** {m.group(1).strip()}\n\n**Jawab:** {m.group(2).strip()}\n\n",
        teks,
    )

    # Daftar langkah GitBook -> daftar bernomor. Dilakukan sebelum tag sisa
    # dibuang, kalau tidak nomor langkahnya hilang.
    teks = POLA_STEPPER.sub(lambda m: _langkah_bernomor(m.group(1)), teks)
    if POLA_STEP.search(teks):
        # {% step %} yang berdiri tanpa pembungkus {% stepper %}: nomori di
        # tempat, jangan merakit ulang halaman (teks di sekitarnya harus utuh).
        nomor = itertools.count(1)
        teks = POLA_STEP.sub(
            lambda m: f"\n\n{next(nomor)}. " + re.sub(r"\n\s*\n", "\n   ", m.group(1).strip()) + "\n",
            teks,
        )

    teks = POLA_TABEL.sub(_tabel_ke_teks, teks)

    teks = POLA_HINT_BUKA.sub(lambda m: f"\n**{LABEL_HINT.get(m.group(1), 'Catatan')}:**\n", teks)
    teks = POLA_HINT_TUTUP.sub("\n", teks)
    teks = POLA_EMBED.sub(lambda m: f"[tautan: {m.group(1)}]", teks)
    teks = POLA_TAG_SISA.sub("", teks)
    teks = POLA_A_HTML.sub(
        lambda m: (lambda label, url: f"{label} ({url})" if label and url not in label else (label or url))(
            re.sub(r"<[^>]+>", "", m.group(2)).strip(), m.group(1)
        ),
        teks,
    )
    teks = POLA_TAG_HTML.sub("", teks)
    teks = re.sub(r"\\\n", "\n", teks)  # baris-baru keras GitBook (backslash di ujung baris)

    # Judul dokumen = heading H1 pertama, lalu dibuang dari isi supaya tidak dobel
    # (jejak judul ditambahkan lagi belakangan di setiap potongan).
    judul = ""
    m = re.search(r"^#\s+(.+?)\s*$", teks, re.M)
    if m:
        judul = m.group(1).strip()
        teks = teks[: m.start()] + teks[m.end():]

    return judul, rapikan_spasi(teks), ada_gambar


# ---------------------------------------------------------------------------
# Pembersihan HTML jawaban FAQ
# ---------------------------------------------------------------------------
def html_ke_teks(html: str) -> tuple[str, bool]:
    """Ubah jawaban FAQ (HTML) jadi teks datar, penomoran daftar dipertahankan.

    Ditulis tangan (bukan pakai pustaka pengubah otomatis) karena perilakunya
    harus bisa ditebak: daftar bernomor pada FAQ adalah LANGKAH BERURUTAN dan
    nomornya wajib ikut. Pengubah otomatis sering meratakannya jadi paragraf.
    """
    sup = BeautifulSoup(html, "html.parser")
    ada_gambar = bool(sup.find("img"))
    baris: list[str] = []

    def teks_inline(node: Tag) -> str:
        bagian = []
        for anak in node.children:
            if isinstance(anak, NavigableString):
                bagian.append(str(anak))
            elif isinstance(anak, Tag):
                if anak.name == "br":
                    bagian.append("\n")
                elif anak.name == "a":
                    tautan = anak.get("href", "")
                    label = anak.get_text(" ", strip=True)
                    bagian.append(f"{label} ({tautan})" if tautan and tautan not in label else label)
                elif anak.name == "img":
                    alt = (anak.get("alt") or "").strip()
                    bagian.append(f"[gambar: {alt}]" if alt else "")
                else:
                    bagian.append(teks_inline(anak))
        return re.sub(r"[ \t]+", " ", "".join(bagian)).strip()

    def telusuri(node: Tag) -> None:
        for anak in node.children:
            if isinstance(anak, NavigableString):
                s = str(anak).strip()
                if s:
                    baris.append(s)
            elif isinstance(anak, Tag):
                if anak.name in ("ol", "ul"):
                    butir = anak.find_all("li", recursive=False)
                    for i, li in enumerate(butir, 1):
                        awalan = f"{i}. " if anak.name == "ol" else "- "
                        baris.append(awalan + teks_inline(li))
                    baris.append("")
                elif anak.name in ("h1", "h2", "h3", "h4", "h5"):
                    baris.append("")
                    baris.append("## " + teks_inline(anak))
                    baris.append("")
                elif anak.name == "table":
                    for tr in anak.find_all("tr"):
                        sel = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                        if any(sel):
                            baris.append(" | ".join(sel))
                    baris.append("")
                elif anak.name in ("p", "div", "blockquote"):
                    t = teks_inline(anak)
                    if t:
                        baris.append(t)
                        baris.append("")
                else:
                    telusuri(anak)

    telusuri(sup)
    return rapikan_spasi("\n".join(baris)), ada_gambar


# ---------------------------------------------------------------------------
# Pemotongan
# ---------------------------------------------------------------------------
def pecah_per_heading(teks: str) -> list[tuple[str, str]]:
    """Pecah teks jadi [(judul_bagian, isi)] berdasarkan heading ## / ###."""
    bagian: list[tuple[str, str]] = []
    judul_kini = ""
    kumpul: list[str] = []
    for baris in teks.splitlines():
        m = re.match(r"^(#{2,4})\s+(.+?)\s*$", baris)
        if m:
            isi = "\n".join(kumpul).strip()
            if isi:
                bagian.append((judul_kini, isi))
            judul_kini = m.group(2).strip()
            kumpul = []
        else:
            kumpul.append(baris)
    isi = "\n".join(kumpul).strip()
    if isi:
        bagian.append((judul_kini, isi))
    return bagian or [("", teks)]


def _mirip_tabel(par: str) -> bool:
    """Apakah paragraf ini sebenarnya tabel (Markdown maupun hasil ubahan HTML)?"""
    baris = [b for b in par.splitlines() if b.strip()]
    return len(baris) >= 3 and sum(1 for b in baris if "|" in b) >= len(baris) * 0.7


def _pecah_tabel(par: str, maks: int) -> list[str]:
    """Pecah tabel panjang per baris, BARIS JUDUL DIULANG di tiap bagian.

    Tabel adalah satu paragraf tanpa baris kosong dan sering tanpa tanda titik,
    jadi pemotong paragraf maupun kalimat tidak punya titik potong sama sekali
    -- akibatnya tabel besar lolos melewati ambang dan nanti terpangkas diam-diam
    oleh model embedding. Dipotong per baris jauh lebih aman.

    Baris judul diulang karena tanpa itu potongan kedua hanya berisi deretan sel
    tanpa keterangan kolom, dan artinya hilang.
    """
    baris = [b for b in par.splitlines() if b.strip()]
    kepala = [baris[0]]
    sisa = baris[1:]
    # Baris pemisah tabel Markdown (|---|---|) ikut jadi bagian judul.
    if sisa and re.fullmatch(r"[\s|:\-]+", sisa[0]):
        kepala.append(sisa[0])
        sisa = sisa[1:]

    judul_teks = "\n".join(kepala)
    hasil: list[str] = []
    penampung: list[str] = []
    for b in sisa:
        panjang = len(judul_teks) + sum(len(x) + 1 for x in penampung) + len(b) + 1
        if penampung and panjang > maks:
            hasil.append("\n".join(kepala + penampung))
            penampung = []
        penampung.append(b)
    if penampung:
        hasil.append("\n".join(kepala + penampung))
    return hasil or [par]


def pecah_per_paragraf(isi: str, maks: int, tumpang: int) -> list[str]:
    """Pecah isi panjang di batas paragraf; kalau satu paragraf pun kepanjangan,
    baru turun ke batas kalimat."""
    paragraf = [p.strip() for p in re.split(r"\n\s*\n", isi) if p.strip()]

    # Pasangan Tanya/Jawab digabung jadi SATU satuan yang tak terpisahkan.
    # Tanpa ini, potongan bisa berakhir dengan pertanyaan yatim yang jawabannya
    # ada di potongan lain -- lalu sistem menjawab pertanyaan pengguna dengan
    # sebuah pertanyaan. Sudah terbukti terjadi pada halaman "Pencatatan
    # Skrining PTM" sebelum penanganan ini ditambahkan.
    gabungan: list[str] = []
    lewati = False
    for i, par in enumerate(paragraf):
        if lewati:
            lewati = False
            continue
        berikut = paragraf[i + 1] if i + 1 < len(paragraf) else ""
        if par.startswith("**Tanya:**") and berikut.startswith("**Jawab:**"):
            gabungan.append(f"{par}\n\n{berikut}")
            lewati = True
        else:
            gabungan.append(par)

    satuan: list[str] = []
    for par in gabungan:
        if len(par) <= maks:
            satuan.append(par)
            continue
        if _mirip_tabel(par):
            satuan.extend(_pecah_tabel(par, maks))
            continue
        penampung = ""
        for k in re.split(r"(?<=[.!?])\s+", par):
            if penampung and len(penampung) + len(k) + 1 > maks:
                satuan.append(penampung.strip())
                penampung = k
            else:
                penampung = f"{penampung} {k}".strip()
        if penampung:
            satuan.append(penampung.strip())

    hasil: list[str] = []
    penampung = ""
    for s in satuan:
        if penampung and len(penampung) + len(s) + 2 > maks:
            hasil.append(penampung.strip())
            # Tumpang tindih: bawa ekor potongan sebelumnya supaya kalimat di
            # batas potongan tidak kehilangan konteks.
            ekor = ""
            # Tumpang tindih dilewati untuk tabel: ekor sebuah tabel hanyalah
            # potongan baris tanpa keterangan kolom -- tidak menambah konteks,
            # malah jadi sampah di awal potongan.
            if tumpang and not _mirip_tabel(penampung) and not _mirip_tabel(s):
                ekor = penampung[-tumpang:]
                # Mulai tumpang tindih dari awal kalimat, bukan dari tengah
                # kalimat -- potongan yang diawali penggalan kalimat justru
                # membingungkan model saat menyusun jawaban.
                m = re.search(r"(?<=[.!?])\s+", ekor)
                ekor = ekor[m.end():] if m else (ekor[ekor.index(" ") + 1:] if " " in ekor else "")
                if len(re.sub(r"[^0-9A-Za-z]", "", ekor)) < 20:
                    ekor = ""  # sisa terlalu pendek untuk berguna sebagai konteks
            penampung = f"{ekor}\n\n{s}".strip() if ekor else s
        else:
            penampung = f"{penampung}\n\n{s}".strip()
    if penampung.strip():
        hasil.append(penampung.strip())
    return hasil


def potong(judul_dokumen: str, teks: str, maks: int, minimum: int, tumpang: int) -> list[tuple[str, str]]:
    """Kembalikan [(judul_bagian, teks_potongan_berjejak_judul)]."""
    kasar: list[tuple[str, str]] = []
    for judul_bagian, isi in pecah_per_heading(teks):
        if len(isi) <= maks:
            kasar.append((judul_bagian, isi))
        else:
            kasar.extend((judul_bagian, bagian) for bagian in pecah_per_paragraf(isi, maks, tumpang))

    # Gabungkan potongan kerdil: coba ke tetangga SEBELUMNYA dulu, kalau tidak
    # muat coba ke tetangga BERIKUTNYA. Tanpa percobaan ke depan, kalimat
    # pengantar sebuah tabel bisa tertinggal sendirian jadi potongan 72 huruf
    # ("Berikut adalah perbedaan eKohort dan ASIK:") yang tidak berguna dicari
    # maupun dijadikan rujukan.
    rapat: list[tuple[str, str]] = []
    tunda: tuple[str, str] | None = None
    for judul_bagian, isi in kasar:
        if tunda is not None:
            tunda_judul, tunda_isi = tunda
            if len(tunda_isi) + len(isi) + 2 <= maks:
                judul_bagian = tunda_judul or judul_bagian
                isi = f"{tunda_isi}\n\n{isi}"
            else:
                rapat.append(tunda)
            tunda = None

        if len(isi) < minimum:
            if rapat and len(rapat[-1][1]) + len(isi) + 2 <= maks:
                sebelum_judul, sebelum_isi = rapat[-1]
                rapat[-1] = (sebelum_judul or judul_bagian, f"{sebelum_isi}\n\n{isi}")
            else:
                tunda = (judul_bagian, isi)
            continue

        rapat.append((judul_bagian, isi))

    if tunda is not None:
        rapat.append(tunda)

    # Jejak judul di awal tiap potongan.
    hasil = []
    for judul_bagian, isi in rapat:
        jejak = f"# {judul_dokumen}" if judul_dokumen else ""
        if judul_bagian and judul_bagian != judul_dokumen:
            jejak = f"{jejak}\n## {judul_bagian}" if jejak else f"# {judul_bagian}"
        hasil.append((judul_bagian or judul_dokumen, f"{jejak}\n\n{isi}".strip()))
    return hasil


# ---------------------------------------------------------------------------
# Informasi chatbot WhatsApp dibuang: layanannya sudah dihentikan
#
# Pada 16 September 2026 pembimbing lapangan menyampaikan bahwa layanan chatbot
# WhatsApp sudah dihapus Kemenkes, yaitu WhatsApp Chatbot Kemenkes RI untuk
# pendaftaran dan kuesioner CKG (0811 10 500 567 / 0812-7887-8812) dan ASIK
# WhatsApp Chatbot untuk kader/nakes mencatat data posyandu.
#
# Dokumen sumbernya belum diperbarui, sehingga sistem sempat menjawab "cara daftar
# CKG" dengan langkah menghubungi chatbot yang sudah tidak ada. Jawaban itu memang
# bersumber resmi, tetapi tetap menyesatkan orang yang mengikutinya.
#
# Mentahan di data/raw/ SENGAJA tidak diubah karena merupakan salinan apa adanya
# dari server. Penyaringan dilakukan di sini supaya tercatat, bisa diulang, dan
# mudah dibatalkan kalau layanannya diaktifkan kembali.
#
# Yang TIDAK dibuang karena bukan chatbot: kode OTP lewat WhatsApp, isian nomor
# WhatsApp, grup WhatsApp guru, serta notifikasi dan rapor hasil lewat WhatsApp.
#
# Dua cara:
#   1. Halaman Pusat Bantuan ASIK yang pokok bahasannya chatbot tidak diambil.
#      Halaman induk "ASIK Whatsapp" ikut, karena isinya campuran chatbot dan
#      notifikasi, sedangkan notifikasi sudah dibahas lengkap di halaman
#      "Notifikasi WhatsApp ASIK" yang tetap diambil.
#   2. Dokumen lain yang hanya menyinggung chatbot dipangkas bagian itu saja.
#      Aturannya sengaja spesifik per kalimat, bukan "hapus semua yang menyebut
#      WhatsApp", karena kata itu juga dipakai untuk hal yang masih berlaku.
#      Huruf butir di Juknis tidak disusun ulang, jadi bisa muncul loncatan
#      seperti "b." langsung ke "d.".
# ---------------------------------------------------------------------------
HALAMAN_CHATBOT_WA = {
    "informasi-umum-asik-whatsapp",
    "informasi-umum-asik-whatsapp-asik-whatsapp-chatbot",
    "informasi-umum-asik-whatsapp-registrasi-asik-whatsapp",
    "bayi-balita-asik-chatbot-bayi-balita",
    "bayi-balita-asik-chatbot-bayi-balita-registrasi-chatbot-asik",
    "bayi-balita-asik-chatbot-bayi-balita-alur-pencatatan-chatbot-asik",
}

ATURAN_CHATBOT_WA: list[tuple[re.Pattern, str]] = [
    # --- Pusat Bantuan ASIK: chatbot sebagai kanal/platform dalam daftar dan tabel
    (re.compile(r"\n[ \t]*\d+\.\s*ASIK WhatsApp Chatbot:[^\n]*"), ""),
    (re.compile(r"ASIK memiliki tiga kanal utama"), "ASIK memiliki dua kanal utama"),
    (re.compile(r"\n[ \t]*\d+\.\s*\*\*ASIK Chatbot\*\*:[^\n]*"), ""),
    (re.compile(r"menggunakan tiga platform dengan fungsi"), "menggunakan dua platform dengan fungsi"),
    (re.compile(r"\n[^\n|]*\|\s*Whatsapp\s*\|[^\n]*", re.I), ""),
    (re.compile(r"\n[ \t]*\*\s*\[Panduan pencatatan melalui Whatsapp Chatbot\]\([^)]*\)", re.I), ""),
    (re.compile(r"\nASIK Chatbot \|[^\n]*"), ""),
    (re.compile(r"melalui WhatsApp atau ASIK Mobile"), "melalui ASIK Mobile"),
    (re.compile(r"melalui ASIK Mobile atau WhatsApp"), "melalui ASIK Mobile"),
    # --- FAQ SATUSEHAT
    (re.compile(r"melalui aplikasi SATUSEHAT Mobile, WhatsApp Kemenkes RI 0811 10 500 567, atau"),
     "melalui aplikasi SATUSEHAT Mobile atau"),
    # --- Juknis CKG (Kepmenkes 84/2026); teks PDF, jadi pergantian baris bisa di mana saja
    (re.compile(r"c\.\s+Jika mengalami kesulitan untuk mendaftar melalui SSM,\s+pendaftaran CKG dapat"
                r"\s+dilakukan melalui WA Chatbot\s+Kementerian Kesehatan di nomor\s+\(0812-7887-8812\);\s*"), ""),
    (re.compile(r"melalui aplikasi SSM atau melalui\s+WA Chatbot\s+Kementerian Kesehatan\s+\(0812-7887-8812\)"),
     "melalui aplikasi SSM"),
    (re.compile(r"\bSSM\s+atau\s+(?:atau\s+)?(?:Whatsapp\s+\(WA\)|WA)\s+Chatbot\s+Kemenkes", re.I), "SSM"),
    (re.compile(r"\bSSM/WA\s+chatbot\s+Kemenkes", re.I), "SSM"),
    (re.compile(r"Tiket pendaftaran di SSM/WA\b"), "Tiket pendaftaran di SSM"),
    # --- Juknis CKG Sekolah (Kepmenkes 770/2025)
    (re.compile(r"melalui SATUSEHAT Mobile atau\s+WhatsApp Chatbot Kemenkes RI"), "melalui SATUSEHAT Mobile"),
    (re.compile(r"b\.\s+Pendaftaran melalui WhatsApp Chatbot\n.*?(?=\nc\.\s)", re.S), ""),
    (re.compile(r"melalui akun SSM atau WhatsApp(\s+masing-masing)"), r"melalui akun SSM\1"),
]

# Dipakai main() untuk memastikan tidak ada yang tersisa setelah penyaringan.
POLA_SISA_CHATBOT_WA = re.compile(r"chat\s?bot|0812-7887-8812|0811\s?10\s?500\s?567", re.I)

DIBUANG_CHATBOT_WA: list[str] = []        # judul halaman yang tidak diambil
DISUNTING_CHATBOT_WA: set[str] = set()    # awalan id dokumen yang dipangkas


def _hapus_butir_daftar_wa(teks: str) -> tuple[str, int]:
    """FAQ cara daftar CKG: buang butir "2. WhatsApp Chatbot Kemenkes RI" beserta
    sub-langkahnya, lalu geser butir sesudahnya dari "3." menjadi "2."."""
    m = re.search(r"\n2\.\s*WhatsApp Chatbot Kemenkes RI.*?(?=\n3\.|\Z)", teks, re.S)
    if not m:
        return teks, 0
    sisa = re.sub(r"^\n3\.", "\n2.", teks[m.end():], count=1)
    return teks[:m.start()] + sisa, 1


def buang_info_chatbot_wa(teks: str) -> tuple[str, int]:
    """Pangkas penyebutan chatbot WhatsApp dari teks satu dokumen.

    Kembalikan (teks, jumlah_penggantian). Teks dirapikan ulang hanya kalau ada
    yang diganti, supaya dokumen lain tidak berubah sedikit pun.
    """
    teks, n = _hapus_butir_daftar_wa(teks)
    for pola, ganti in ATURAN_CHATBOT_WA:
        teks, k = pola.subn(ganti, teks)
        n += k
    return (rapikan_spasi(teks) if n else teks), n


# ---------------------------------------------------------------------------
# Perakitan potongan per sumber
# ---------------------------------------------------------------------------
def olah_asik(folder: Path, maks: int, minimum: int, tumpang: int) -> list[dict]:
    berkas_manifest = folder / "_manifest.json"
    if not berkas_manifest.exists():
        lapor(f"  (lewat) {berkas_manifest} belum ada -- jalankan collect_asik.py dulu")
        return []

    manifest = baca_json(berkas_manifest)
    potongan: list[dict] = []

    for hal in manifest["halaman"]:
        if hal["status"] == "gagal":
            continue
        berkas = folder / hal["berkas"]
        if not berkas.exists():
            continue

        judul, isi, ada_gambar = bersihkan_asik(berkas.read_text(encoding="utf-8"))
        judul = judul or hal["judul"]
        # Layanan chatbot WhatsApp sudah dihentikan; lihat HALAMAN_CHATBOT_WA.
        if slug(hal["jalur"]) in HALAMAN_CHATBOT_WA:
            DIBUANG_CHATBOT_WA.append(judul)
            continue
        isi, n_wa = buang_info_chatbot_wa(isi)
        if n_wa:
            DISUNTING_CHATBOT_WA.add(f"asik:{slug(hal['jalur'])}")
        if not isi:
            lapor(f"  (kosong setelah dibersihkan) {hal['judul']}")
            continue

        bagian = potong(judul, isi, maks, minimum, tumpang)
        for urut, (judul_bagian, teks) in enumerate(bagian):
            potongan.append({
                "id": f"asik:{slug(hal['jalur'])}:{urut}",
                "teks": teks,
                # --- metadata wajib (docs/arsitektur-dan-keputusan.md, Prinsip wajib) ---
                "sasaran": "nakes",
                "jenis": tebak_jenis(judul_bagian or judul, "asik"),
                "sumber_nama": f"Pusat Bantuan ASIK — {judul}",
                "sumber_url": hal["url_html"],
                "tanggal_ambil": hal.get("tanggal_ambil") or hari_ini(),
                "judul_bagian": judul_bagian or judul,
                # --- tambahan untuk penelusuran & evaluasi ---
                "sumber_id": "asik",
                "sumber_url_md": hal["url_md"],
                "kategori": hal["kategori"],
                "judul_dokumen": judul,
                "urutan": urut,
                "total_potongan": len(bagian),
                "jumlah_karakter": len(teks),
                "perkiraan_token": perkiraan_token(teks),
                "hash_isi": sha256_teks(teks),
                "jenis_metode": "heuristik",
                "ada_gambar_di_sumber": ada_gambar,
                "lingkungan": hal.get("lingkungan", "tidak diketahui"),
            })
    return potongan


def olah_faq(folder: Path, maks: int, minimum: int, tumpang: int) -> list[dict]:
    potongan: list[dict] = []
    if not folder.exists():
        return potongan

    for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
        if not (sub / "_manifest.json").exists():
            continue
        for berkas in sorted(sub.glob("*.json")):
            if berkas.name.startswith("_"):
                continue
            rekam = baca_json(berkas)
            jawaban, ada_gambar = html_ke_teks(rekam["isi_html"])
            if not jawaban:
                lapor(f"  (kosong) {rekam['pertanyaan'][:60]}")
                continue

            jawaban, n_wa = buang_info_chatbot_wa(jawaban)
            if n_wa:
                DISUNTING_CHATBOT_WA.add(f"faq_{rekam['kategori_kunci']}:{rekam['id']}")

            pertanyaan = rekam["pertanyaan"].strip()
            # Satu topik = satu potongan. Dipotong HANYA kalau jawabannya panjang,
            # dan pertanyaannya diulang di tiap bagian supaya pasangan
            # tanya-jawab tidak pernah terputus.
            if len(jawaban) + len(pertanyaan) <= maks:
                bagian = [(pertanyaan, f"# {pertanyaan}\n\n{jawaban}")]
            else:
                bagian = potong(pertanyaan, jawaban, maks, minimum, tumpang)

            for urut, (judul_bagian, teks) in enumerate(bagian):
                potongan.append({
                    "id": f"faq_{rekam['kategori_kunci']}:{rekam['id']}:{urut}",
                    "teks": teks,
                    "sasaran": rekam["sasaran"],
                    "jenis": tebak_jenis(pertanyaan, f"faq_{rekam['kategori_kunci']}"),
                    "sumber_nama": f"{rekam['sumber_nama']} — {pertanyaan}",
                    "sumber_url": rekam["url"],
                    "tanggal_ambil": rekam.get("tanggal_ambil") or hari_ini(),
                    "judul_bagian": judul_bagian or pertanyaan,
                    "sumber_id": f"faq_{rekam['kategori_kunci']}",
                    "sumber_url_md": "",
                    "kategori": rekam.get("kategori_label", ""),
                    "judul_dokumen": pertanyaan,
                    "urutan": urut,
                    "total_potongan": len(bagian),
                    "jumlah_karakter": len(teks),
                    "perkiraan_token": perkiraan_token(teks),
                    "hash_isi": sha256_teks(teks),
                    "jenis_metode": "heuristik",
                    "ada_gambar_di_sumber": ada_gambar,
                    "lingkungan": "produksi",
                    "tanggal_pembaruan_sumber": rekam.get("tanggal_pembaruan", ""),
                })
    return potongan


# ---------------------------------------------------------------------------
# Halaman web publik (Ayo Sehat)
# ---------------------------------------------------------------------------
def olah_web(folder: Path, maks: int, minimum: int, tumpang: int) -> list[dict]:
    berkas_manifest = folder / "_manifest.json"
    if not berkas_manifest.exists():
        return []

    manifest = baca_json(berkas_manifest)
    potongan: list[dict] = []

    for hal in manifest["halaman"]:
        if hal.get("status") != "berhasil" or not hal.get("berkas"):
            continue
        berkas = folder / hal["berkas"]
        if not berkas.exists():
            continue

        isi = rapikan_spasi(berkas.read_text(encoding="utf-8"))
        # Judul halaman diambil dari H1, lalu dibuang dari badan supaya tidak
        # dobel dengan jejak judul yang ditambahkan potong().
        judul = hal["nama"]
        m = re.match(r"^#\s+(.+?)\s*$", isi, re.M)
        if m:
            judul_h1 = m.group(1).strip()
            isi = isi[m.end():].strip()
            judul = f"{hal['nama']} — {judul_h1}" if judul_h1.lower() not in hal["nama"].lower() else hal["nama"]
        isi, n_wa = buang_info_chatbot_wa(isi)
        if n_wa:
            DISUNTING_CHATBOT_WA.add(f"web:{slug(hal['berkas'])}")
        if not isi:
            continue

        bagian = potong(judul, isi, maks, minimum, tumpang)
        for urut, (judul_bagian, teks) in enumerate(bagian):
            potongan.append({
                "id": f"web:{slug(hal['berkas'])}:{urut}",
                "teks": teks,
                "sasaran": hal["sasaran"],
                "jenis": tebak_jenis(judul_bagian or judul, "web"),
                "sumber_nama": f"{hal['nama']} — {judul_bagian}" if judul_bagian else hal["nama"],
                "sumber_url": hal["url"],
                "tanggal_ambil": hal.get("tanggal_ambil") or hari_ini(),
                "judul_bagian": judul_bagian or judul,
                "sumber_id": "web",
                "sumber_url_md": "",
                "kategori": "ayosehat",
                "judul_dokumen": judul,
                "urutan": urut,
                "total_potongan": len(bagian),
                "jumlah_karakter": len(teks),
                "perkiraan_token": perkiraan_token(teks),
                "hash_isi": sha256_teks(teks),
                "jenis_metode": "bawaan_sumber",
                "ada_gambar_di_sumber": False,
                "lingkungan": "produksi",
            })
    return potongan


# ---------------------------------------------------------------------------
# Juknis CKG (PDF dokumen resmi)
# ---------------------------------------------------------------------------
POLA_PENANDA_HAL = re.compile(r"\[\[hal:(\d+)\]\]")
# Judul sub-bagian dokumen resmi: "A. Integrasi Data ...". SENGAJA hanya huruf,
# tidak angka: "1." "2." di dokumen ini adalah butir daftar, dan memotong di
# situ akan mencacah daftar langkah jadi potongan-potongan lepas.
POLA_SUBBAGIAN = re.compile(r"^([A-Z])\.\s+(\S.*)$", re.M)


def _pisah_halaman(teks: str) -> tuple[str, list[int]]:
    """Kembalikan (teks tanpa penanda, daftar nomor halaman yang muncul)."""
    hal = [int(h) for h in POLA_PENANDA_HAL.findall(teks)]
    return rapikan_spasi(POLA_PENANDA_HAL.sub("", teks)), hal


# ---------------------------------------------------------------------------
# Penyaring isi klinis pada Juknis
#
# collect_pdf.py sudah melewati BAB V karena isinya pedoman klinis. Ternyata
# ITU TIDAK CUKUP: BAB IV memuat "Tabel 4.1 Tindak Lanjut Cek Kesehatan Gratis"
# yang isinya sama persis sifatnya --
#
#     Hasil pemeriksaan menunjukkan kondisi Prehipertensi, Prediabetes, Obesitas
#     -> Berikan tata laksana Prehipertensi dan Prediabetes sesuai standar
#
# Jadi penyaringan per-BAB terlalu kasar. Penyaring ini bekerja di tingkat
# potongan: sebuah potongan dibuang kalau memetakan HASIL PEMERIKSAAN ke
# TINDAKAN MEDIS. Yang dibuang dicatat dan dihitung, tidak dibuang diam-diam.
#
# Ini menjaga prinsip wajib no. 3 (bukan alat klinis) di sumbernya. Penapis pertanyaan di answer.py
# tetap ada, tapi ia lapis kedua -- lapis pertama adalah tidak menyimpan bahan
# yang tidak boleh dikeluarkan.
# ---------------------------------------------------------------------------
POLA_HASIL = re.compile(r"\bhasil pemeriksaan\b|\bhasil skrining\b", re.I)
POLA_TINDAK = re.compile(r"\btindak lanjut\b|\btata laksana\b|\btatalaksana\b", re.I)
POLA_AKSI_MEDIS = re.compile(
    r"\b(tata laksana|tatalaksana|rujuk|terapi|pengobatan|obat|konseling|"
    r"prehipertensi|prediabetes|hipertensi|diabetes|anemia|stunting|"
    r"tuberkulosis|\bTB\b|kemoterapi|dosis)\b", re.I)


# Halaman LANJUTAN sebuah tabel tindak lanjut mengulang judul kolomnya, dan di
# situ frasa "hasil pemeriksaan" tidak pernah muncul utuh -- yang muncul hanya
# nama-nama kolom yang berdempetan saat PDF diekstrak:
#
#     "Layanan Skrining Hasil Tindak lanjut Tekanan Darah Normal (<120/80
#      mmHg) - Edukasi gaya hidup sehat ... Hipertensi tingkat 2 (>=140/90)
#      Dirujuk ke Puskesmas ..."
#
# Akibatnya POLA_HASIL tidak kena dan halaman selanjutan tabel klinis LOLOS,
# padahal isinya justru bagian paling klinis dari seluruh dokumen. Ini
# ketahuan saat menguji Juknis CKG Sekolah: dari 9 halaman tabel tindak lanjut,
# 2 lolos -- keduanya halaman lanjutan. Judul kolom itu sendiri sudah cukup
# menjadi bukti, jadi diperlakukan sebagai penanda tersendiri.
POLA_TABEL_TINDAK_LANJUT = re.compile(
    r"\b(layanan\s+skrining|pemeriksaan|jenis\s+pemeriksaan)\s+hasil\s+tindak\s*lanjut\b",
    re.I)


# Awal sebuah tabel tindak lanjut klinis, dan penanda struktur yang mengakhirinya.
# Nomor tabel/gambar ditulis BERBEDA di dua Juknis, dan pola yang cuma
# menangani salah satunya meninggalkan cacat yang halus:
#
#     Kepmenkes 770/2025 (Sekolah) : "Tabel 2. Tindak Lanjut ..."
#     Kepmenkes 84/2026            : "Tabel 4.1. Tindak Lanjut ..."   <- bab.nomor
#
# Versi pertama pola ini hanya mengenal bentuk pertama, sehingga di Juknis
# 84/2026 isi tabelnya memang terbuang (oleh isi_klinis per potongan) TAPI
# JUDULNYA TERTINGGAL, menggantung di ujung potongan tanpa apa-apa sesudahnya:
#
#     "...5. Tindak lanjut hasil pemeriksaan dilakukan sesuai Tabel 4.1.
#      Tabel 4.1. Tindak Lanjut Cek Kesehatan Gratis"     <- potongan habis
#
# Akibatnya model menjawab "Tabel ini menunjukkan tindak lanjut hasil
# pemeriksaan" lalu berhenti -- terbaca seperti jawaban terpotong, padahal
# memang tidak ada lagi yang bisa dikatakannya. Ketahuan dari jawaban nyata,
# bukan dari berkas uji.
#
# Kalimat yang MERUJUK tabelnya ("tindak lanjut dilakukan sesuai Tabel 4.1")
# sengaja DIBIARKAN: itu ketentuan prosedural yang sah, dan mengarahkan
# pembaca ke dokumen aslinya justru perilaku yang benar. Yang dibuang cuma
# judul tabel yang menjanjikan isi yang tidak kita punya.
NOMOR_TABEL = r"\d+(?:\.\d+)*\.?"
POLA_AWAL_TABEL_KLINIS = re.compile(
    rf"^[ \t]*Tabel\s+{NOMOR_TABEL}\s*Tindak\s+Lanjut\b.*$", re.I | re.M)
POLA_AKHIR_WILAYAH = re.compile(
    rf"^[ \t]*(?:BAB\s+[IVXL]+|Gambar\s+{NOMOR_TABEL}|Tabel\s+{NOMOR_TABEL}|"
    r"[A-Z]\.\s+[A-Z]).*$", re.M)


def buang_tabel_klinis(teks: str) -> tuple[str, int]:
    """Buang wilayah tabel "Tindak Lanjut Hasil Pemeriksaan" dari teks satu bab.

    KENAPA TIDAK CUKUP MENGANDALKAN isi_klinis() SAJA:

    isi_klinis() bekerja per POTONGAN, sesudah pemotongan. Tabel tindak lanjut
    di Juknis CKG Sekolah panjangnya beberapa halaman, jadi ia terbelah menjadi
    banyak potongan -- dan hanya potongan PERTAMA yang membawa judul kolomnya.
    Potongan lanjutan tinggal berisi barisnya saja:

        "... Prehipertensi (120/<80 mmHg s.d 129/<80 mmHg) Dirujuk ke
         Puskesmas/fasilitas kesehatan lainnya Hipertensi tingkat 1 ..."

    Isinya jelas pemetaan hasil ke tindakan medis, tapi tidak satu pun kata
    kunci penanda tersisa di situ. Diukur pada korpus: dari satu bagian saja,
    13 potongan tertahan dan 5 LOLOS -- semuanya potongan lanjutan.

    Karena itu pemotongan dilakukan di tingkat TEKS BAB, sebelum dipotong-potong:
    dari baris "Tabel N. Tindak Lanjut ..." sampai penanda struktur berikutnya
    (Gambar/Tabel/sub-bagian/BAB). Batas ini bukan tebakan -- struktur itu
    dipakai konsisten di seluruh dokumen.

    Kembalikan (teks_bersih, jumlah_wilayah_dibuang).
    """
    potong: list[tuple[int, int]] = []
    for m in POLA_AWAL_TABEL_KLINIS.finditer(teks):
        akhir = len(teks)
        for n in POLA_AKHIR_WILAYAH.finditer(teks, m.end()):
            akhir = n.start()
            break
        potong.append((m.start(), akhir))

    if not potong:
        return teks, 0

    sisa, batas_akhir = [], 0
    for awal, akhir in potong:
        sisa.append(teks[batas_akhir:awal])
        batas_akhir = akhir
    sisa.append(teks[batas_akhir:])
    return "".join(sisa), len(potong)


def isi_klinis(teks: str) -> bool:
    """Apakah potongan ini memetakan hasil pemeriksaan ke tindakan medis?

    Dua jalan menuju True:

    1. Judul kolom tabel tindak lanjut terdeteksi -- ini sudah cukup sendirian,
       karena hanya tabel semacam itu yang menyusun kata sedemikian rupa.
    2. Ketiga syarat lama terpenuhi sekaligus. Harus ketiganya supaya paragraf
       yang sekadar MENYEBUT "tindak lanjut" -- misalnya bab pencatatan yang
       menerangkan bahwa hasil tindak lanjut wajib diinput ke aplikasi -- tidak
       ikut terbuang.
    """
    if POLA_TABEL_TINDAK_LANJUT.search(teks):
        return True
    return bool(POLA_HASIL.search(teks) and POLA_TINDAK.search(teks)
                and POLA_AKSI_MEDIS.search(teks))


DIBUANG_KLINIS: list[str] = []
DIBUANG_TABEL_KLINIS: list[str] = []


def olah_juknis(folder: Path, maks: int, minimum: int, tumpang: int,
                sumber_id: str = "juknis", sasaran: str | list[str] = "nakes",
                nama_pendek: str = "Juknis CKG (Kepmenkes HK.01.07/MENKES/84/2026)") -> list[dict]:
    """Ubah satu folder Juknis hasil collect_pdf.py menjadi potongan bermetadata.

    Dipakai untuk DUA dokumen yang bentuknya sama tapi identitasnya berbeda:

        folder            sumber_id       sasaran
        juknis/           juknis          nakes       Kepmenkes 84/2026
        juknis_sekolah/   juknis_sekolah  sekolah     Kepmenkes 770/2025

    Identitas dijadikan parameter, bukan dipatok di dalam badan fungsi, supaya
    dokumen kedua tidak perlu menyalin ulang seluruh logika pemotongan --
    termasuk penomoran halaman dan penyaringan isi klinis yang justru bagian
    paling mudah salah kalau digandakan.
    """
    berkas_manifest = folder / "_manifest.json"
    if not berkas_manifest.exists():
        return []

    manifest = baca_json(berkas_manifest)
    potongan: list[dict] = []
    hal_terakhir = 0

    for bab in manifest["bab"]:
        if not bab.get("diambil") or not bab.get("berkas"):
            continue
        berkas = folder / bab["berkas"]
        if not berkas.exists():
            continue

        isi = berkas.read_text(encoding="utf-8")

        # Wilayah tabel tindak lanjut dibuang DULU, selagi strukturnya masih
        # utuh. Sesudah dipotong-potong, penanda batasnya sudah hilang dan
        # bagian klinisnya tidak bisa dikenali lagi. Lihat buang_tabel_klinis().
        isi, n_tabel = buang_tabel_klinis(isi)
        if n_tabel:
            DIBUANG_TABEL_KLINIS.append(f"{sumber_id}:BAB {bab['angka']} ({n_tabel} tabel)")

        # Judul bab muncul lagi di badan teks (baris "BAB VI" lalu
        # "PENCATATAN DAN PELAPORAN"). Dibuang supaya tidak dobel dengan jejak
        # judul yang nanti ditambahkan oleh potong().
        pola_judul_ulang = (rf"^\s*BAB\s+{bab['angka']}\s*\n"
                            rf"\s*{re.escape(bab['judul'].strip())}\s*\n")
        isi = re.sub(pola_judul_ulang, "", isi, count=1, flags=re.I | re.M)
        judul_bab = f"BAB {bab['angka']} {bab['judul'].strip().capitalize()}"

        # Pecah per sub-bagian A./B./C.; kalau bab tidak punya sub-bagian,
        # seluruh bab diperlakukan sebagai satu bagian.
        titik = [(m.start(), f"{m.group(1)}. {m.group(2).strip()}")
                 for m in POLA_SUBBAGIAN.finditer(isi)]
        if titik:
            bagian = []
            if titik[0][0] > 0:
                bagian.append(("", isi[:titik[0][0]]))
            for i, (pos, judul) in enumerate(titik):
                akhir = titik[i + 1][0] if i + 1 < len(titik) else len(isi)
                bagian.append((judul, isi[pos:akhir]))
        else:
            bagian = [("", isi)]

        for judul_bagian, blok in bagian:
            bersih, halaman = _pisah_halaman(blok)
            bersih, n_wa = buang_info_chatbot_wa(bersih)
            if n_wa:
                DISUNTING_CHATBOT_WA.add(
                    f"{sumber_id}:bab-{bab['angka'].lower()}:{slug(judul_bagian or 'utama')}")
            if len(bersih) < 60:  # sisa pemotongan yang tidak bermakna
                continue

            jejak = judul_bab if not judul_bagian else f"{judul_bab} > {judul_bagian}"
            for urut, (_, teks) in enumerate(potong(jejak, bersih, maks, minimum, tumpang)):
                # Isi = teks tanpa jejak judul. Potongan yang isinya tinggal
                # beberapa kata (mis. "BAB IX PENUTUP") tidak berguna dicari.
                isi_saja = teks[len(jejak) + 2:].strip() if teks.startswith(jejak) else teks
                if len(isi_saja) < 80:
                    continue
                if isi_klinis(teks):
                    DIBUANG_KLINIS.append(
                        f"{sumber_id}:{bab['angka']}/{judul_bagian or 'utama'}:{urut}")
                    continue
                # Halaman diambil dari penanda di blok ini. Kalau satu blok
                # tidak memuat penanda (mis. lanjutan halaman sebelumnya),
                # dipakai halaman terakhir yang diketahui -- lebih baik
                # menunjuk halaman yang berdekatan daripada tidak sama sekali.
                if halaman:
                    hal_awal, hal_akhir = min(halaman), max(halaman)
                    hal_terakhir = hal_akhir
                else:
                    hal_awal = hal_akhir = hal_terakhir

                label_hal = f"hal. {hal_awal}" if hal_awal == hal_akhir else f"hal. {hal_awal}-{hal_akhir}"
                potongan.append({
                    "id": f"{sumber_id}:bab-{bab['angka'].lower()}:"
                          f"{slug(judul_bagian or 'utama')}:{urut}",
                    "teks": teks,
                    "sasaran": sasaran,
                    "jenis": tebak_jenis(judul_bagian or judul_bab, "juknis"),
                    "sumber_nama": f"{nama_pendek} — {jejak}, {label_hal}",
                    # #page= dipakai supaya pembaca mendarat langsung di halaman
                    # yang dirujuk, bukan di halaman 1 dari 122.
                    "sumber_url": f"{manifest['url']}#page={hal_awal}",
                    "tanggal_ambil": manifest.get("tanggal_ambil") or hari_ini(),
                    "judul_bagian": judul_bagian or judul_bab,
                    "sumber_id": sumber_id,
                    "sumber_url_md": "",
                    "kategori": f"bab-{bab['angka'].lower()}",
                    "judul_dokumen": judul_bab,
                    "urutan": urut,
                    "total_potongan": 0,  # diisi setelah semua potongan bab jadi
                    "jumlah_karakter": len(teks),
                    "perkiraan_token": perkiraan_token(teks),
                    "hash_isi": sha256_teks(teks),
                    "jenis_metode": "bawaan_sumber",
                    "ada_gambar_di_sumber": False,
                    "lingkungan": "produksi",
                    "halaman_awal": hal_awal,
                    "halaman_akhir": hal_akhir,
                })

    # total_potongan dihitung per sub-bagian, bukan per bab, supaya artinya
    # sama dengan sumber lain: "potongan ke-n dari m untuk bagian ini".
    per_bagian: dict[str, int] = {}
    for x in potongan:
        kunci = x["id"].rsplit(":", 1)[0]
        per_bagian[kunci] = per_bagian.get(kunci, 0) + 1
    for x in potongan:
        x["total_potongan"] = per_bagian[x["id"].rsplit(":", 1)[0]]

    return potongan


# ---------------------------------------------------------------------------
def ringkas(potongan: list[dict]) -> dict:
    def hitung(kunci: str) -> dict:
        d: dict[str, int] = {}
        for p in potongan:
            nilai = str(p.get(kunci, ""))
            d[nilai] = d.get(nilai, 0) + 1
        return dict(sorted(d.items(), key=lambda x: -x[1]))

    def hitung_sasaran() -> dict:
        """Hitung per kelompok pembaca, bukan per kombinasi.

        Potongan dwi-sasaran dihitung di KEDUA kelompoknya, karena angka ini
        menjawab "berapa banyak yang bisa ditemukan kelompok ini", bukan
        "bagaimana korpus dibagi". Akibatnya jumlahnya melebihi total potongan,
        dan itu memang benar. Kalau dihitung per kombinasi, akan muncul
        kelompok semu bernama "['nakes', 'sekolah']" yang tidak pernah dipilih
        siapa pun di antarmuka.
        """
        d: dict[str, int] = {}
        for p in potongan:
            for s in daftar_sasaran(p):
                d[s] = d.get(s, 0) + 1
        return dict(sorted(d.items(), key=lambda x: -x[1]))

    panjang = sorted(p["jumlah_karakter"] for p in potongan) or [0]
    n = len(panjang)
    return {
        "tanggal_olah": hari_ini(),
        "jumlah_potongan": n,
        "jumlah_dokumen": len({p["sumber_url"] for p in potongan}),
        "karakter": {
            "min": panjang[0],
            "median": panjang[n // 2],
            "maks": panjang[-1],
            "rata2": round(sum(panjang) / n) if n else 0,
        },
        "perkiraan_token_maks": perkiraan_token("x" * panjang[-1]),
        "per_sumber": hitung("sumber_id"),
        "per_sasaran": hitung_sasaran(),
        "per_jenis": hitung("jenis"),
        "per_kategori": hitung("kategori"),
        "dokumen_terpotong": len({p["sumber_url"] for p in potongan if p["total_potongan"] > 1}),
        "potongan_dengan_gambar_di_sumber": sum(1 for p in potongan if p["ada_gambar_di_sumber"]),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Potong korpus dan bubuhkan metadata.")
    p.add_argument("--maks", type=int, default=POTONG_MAKS, help="panjang maksimum potongan (karakter)")
    p.add_argument("--min", dest="minimum", type=int, default=POTONG_MIN, help="panjang minimum potongan")
    p.add_argument("--tumpang", type=int, default=TUMPANG, help="tumpang tindih antar potongan (karakter)")
    p.add_argument("--keluaran", default=str(DATA_OLAHAN / "korpus.jsonl"))
    args = p.parse_args()

    lapor(f"Ambang: maks={args.maks} min={args.minimum} tumpang={args.tumpang} karakter")

    lapor("[1/5] Mengolah Pusat Bantuan ASIK")
    potongan = olah_asik(DATA_MENTAH / "asik", args.maks, args.minimum, args.tumpang)
    lapor(f"      {len(potongan)} potongan")

    lapor("[2/5] Mengolah FAQ SATUSEHAT")
    faq = olah_faq(DATA_MENTAH / "faq", args.maks, args.minimum, args.tumpang)
    lapor(f"      {len(faq)} potongan")
    potongan.extend(faq)

    lapor("[3/5] Mengolah Juknis CKG")
    juknis = olah_juknis(DATA_MENTAH / "juknis", args.maks, args.minimum, args.tumpang)
    lapor(f"      {len(juknis)} potongan  (Kepmenkes 84/2026)")

    # Dokumen kedua: Juknis CKG Sekolah. Kepmenkes 84/2026 BAB II hal. 9
    # menyatakan sendiri bahwa "Pelaksanaan CKG pada usia sekolah dan remaja
    # mengikuti Petunjuk Teknis CKG Sekolah", jadi tanpa dokumen ini korpus
    # tidak punya dasar ketentuan apa pun untuk kelompok sekolah -- yang ada
    # cuma FAQ dan halaman kampanye.
    #
    # sasaran-nya dua, mengikuti daftar sasaran yang ditulis dokumen itu
    # sendiri di hal. 8: dinas kesehatan dan puskesmas (nakes) DAN kepala
    # sekolah/madrasah beserta tim pelaksana UKS (sekolah).
    juknis_sekolah = olah_juknis(
        DATA_MENTAH / "juknis_sekolah", args.maks, args.minimum, args.tumpang,
        sumber_id="juknis_sekolah",
        sasaran=["nakes", "sekolah"],
        nama_pendek="Juknis CKG Sekolah (Kepmenkes HK.01.07/MENKES/770/2025)",
    )
    lapor(f"      {len(juknis_sekolah)} potongan  (Kepmenkes 770/2025, CKG Sekolah)")
    juknis.extend(juknis_sekolah)
    if DIBUANG_TABEL_KLINIS:
        lapor(f"      {len(DIBUANG_TABEL_KLINIS)} bab dipangkas tabel tindak lanjutnya"
              " sebelum dipotong")
        lapor(f"      ({', '.join(DIBUANG_TABEL_KLINIS)})")
    if DIBUANG_KLINIS:
        lapor(f"      {len(DIBUANG_KLINIS)} potongan DIBUANG karena memetakan hasil"
              " pemeriksaan ke tindakan medis")
        lapor(f"      (bab/bagian: {', '.join(DIBUANG_KLINIS[:6])}"
              f"{' ...' if len(DIBUANG_KLINIS) > 6 else ''})")
    potongan.extend(juknis)

    lapor("[4/5] Mengolah halaman web publik")
    web = olah_web(DATA_MENTAH / "web", args.maks, args.minimum, args.tumpang)
    lapor(f"      {len(web)} potongan")
    potongan.extend(web)

    if not potongan:
        lapor("Tidak ada yang bisa diolah. Jalankan skrip pengumpul dulu.")
        return 1

    # Potongan dari dokumen yang dipangkas info chatbot-nya diberi catatan, supaya
    # jelas teksnya tidak lagi sama persis dengan sumber aslinya.
    for pot in potongan:
        if pot["id"].rsplit(":", 1)[0] in DISUNTING_CHATBOT_WA:
            pot["catatan_suntingan"] = "informasi chatbot WhatsApp dihapus (layanan dihentikan)"
    if DIBUANG_CHATBOT_WA or DISUNTING_CHATBOT_WA:
        lapor(f"      Chatbot WhatsApp: {len(DIBUANG_CHATBOT_WA)} halaman ASIK tidak diambil, "
              f"{len(DISUNTING_CHATBOT_WA)} dokumen/bagian dipangkas")
    sisa_wa = [p["id"] for p in potongan if POLA_SISA_CHATBOT_WA.search(p["teks"])]
    if sisa_wa:
        lapor(f"      PERINGATAN: {len(sisa_wa)} potongan masih menyebut chatbot WhatsApp: "
              f"{', '.join(sisa_wa[:5])}")

    # Buang potongan berisi teks identik (mis. halaman pengantar yang terduplikasi).
    # HARUS setelah SEMUA sumber dikumpulkan: kalau ada sumber yang ditambahkan
    # sesudah baris ini, potongannya tidak akan pernah ikut tertulis ke berkas.
    unik, terlihat = [], set()
    for pot in potongan:
        if pot["hash_isi"] in terlihat:
            continue
        terlihat.add(pot["hash_isi"])
        unik.append(pot)
    buang = len(potongan) - len(unik)
    if buang:
        lapor(f"      {buang} potongan kembar dibuang")

    lapor("[5/5] Menulis keluaran")
    keluaran = Path(args.keluaran)
    n = tulis_jsonl(keluaran, unik)
    stat = ringkas(unik)
    tulis_json(keluaran.with_name("statistik.json"), stat)

    k = stat["karakter"]
    lapor(f"\n{n} potongan -> {keluaran}")
    lapor(f"Dokumen        : {stat['jumlah_dokumen']} ({stat['dokumen_terpotong']} perlu lebih dari satu potongan)")
    lapor(f"Panjang (huruf): min {k['min']} | median {k['median']} | rata2 {k['rata2']} | maks {k['maks']}")
    lapor(f"Per sumber     : {stat['per_sumber']}")
    lapor(f"Per sasaran    : {stat['per_sasaran']}"
          "   <- dwi-sasaran dihitung dua kali, jadi jumlahnya > total")
    lapor(f"Per jenis      : {stat['per_jenis']}   <- label heuristik, PERIKSA MANUAL")
    lapor(f"Potongan yang sumbernya bergambar: {stat['potongan_dengan_gambar_di_sumber']}"
          " (tangkapan layar tidak ikut masuk korpus)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
