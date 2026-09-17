"""Buka tunnel Cloudflare untuk antarmuka demo, lalu tampilkan QR alamatnya.

    python web/app_web.py            # terminal 1: server + model
    python web/tunnel_qr.py          # terminal 2: tunnel + QR

Sama saja dengan menjalankan `cloudflared tunnel --url http://localhost:5000`
sendiri, hanya saja alamat yang dicetak cloudflared langsung diubah menjadi QR
supaya penonton bisa memindai dari layar, tanpa ada yang perlu mengetik ulang
alamat acak sepanjang 40-an huruf.

=== KENAPA QR-NYA DIBUAT SAAT ITU JUGA, BUKAN DISIMPAN DI SLIDE ===

Quick tunnel Cloudflare memberi alamat ACAK yang baru setiap kali dijalankan
(https://<kata-acak>.trycloudflare.com). Jadi QR tidak mungkin dicetak di slide
lebih dulu: ia harus dibuat dari alamat yang baru saja terbit. Itu yang
dikerjakan berkas ini -- membaca keluaran cloudflared, menangkap alamatnya,
lalu menggambar QR di terminal sekaligus menyimpannya sebagai gambar.

=== YANG HARUS DISADARI SEBELUM DIPAKAI ===

Tunnel ini membuat purwarupa ini BISA DIBUKA SIAPA SAJA yang punya alamatnya,
tanpa kata sandi. Tiga hal yang perlu diingat:

1. Pengaman klinis masih bocor (lihat eval/README.md, bagian pengujian
   pengaman). Pengunjung yang mengetik pertanyaan klinis bisa mendapat jawaban
   karangan model yang tetap berhias sumber resmi.
2. Korpusnya diambil dari server staging Pusat Bantuan ASIK dan izin
   pemakaiannya belum dikonfirmasi. Tunnel menyajikan isi itu ke internet.
3. Jawaban diproses satu per satu di GPU laptop, sekitar 5 detik per pertanyaan.
   Sepuluh orang memindai bersamaan berarti antre, bukan sepuluh jawaban
   serentak.

Karena itu: nyalakan hanya selama demo berlangsung, dan tekan Ctrl+C setelah
selesai. Ini alat peragaan, bukan cara menerbitkan layanan.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Alamat quick tunnel selalu berbentuk https://<sesuatu>.trycloudflare.com.
POLA_ALAMAT = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# Lokasi pemasangan bawaan di Windows, dipakai kalau cloudflared tidak ada di PATH.
CADANGAN_CLOUDFLARED = [
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
]


def cari_cloudflared(diminta: str | None = None) -> str | None:
    if diminta:
        return diminta if Path(diminta).exists() or shutil.which(diminta) else None
    ada = shutil.which("cloudflared")
    if ada:
        return ada
    return next((c for c in CADANGAN_CLOUDFLARED if Path(c).exists()), None)


def gambar_qr(alamat: str, berkas: Path | None) -> str:
    """Kembalikan QR sebagai teks, dan simpan juga sebagai PNG kalau diminta."""
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(alamat)
    qr.make(fit=True)

    # invert=True: modul gelap digambar sebagai spasi berlatar terang. Terminal
    # umumnya berlatar gelap, dan pemindai ponsel butuh modul gelap di atas
    # latar terang -- bukan sebaliknya.
    penampung = io.StringIO()
    qr.print_ascii(out=penampung, invert=True)
    teks = penampung.getvalue()

    if berkas:
        qr.make_image(fill_color="black", back_color="white").save(berkas)

    # Konsol Windows bawaan memakai cp1252 dan tidak punya karakter balok yang
    # dipakai print_ascii. Kalau begitu, QR-nya digambar ulang dengan tanda
    # pagar supaya tetap terlihat, dan PNG-nya yang dipakai untuk dipindai.
    penyandian = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        teks.encode(penyandian)
    except UnicodeEncodeError:
        # get_matrix() sudah menyertakan bingkai kosong, jadi tinggal digambar:
        # modul gelap jadi '##', modul terang jadi dua spasi.
        teks = "\n".join(
            "".join("##" if modul else "  " for modul in deret)
            for deret in qr.get_matrix()
        )
    return teks


def buka_gambar(berkas: Path, diminta: bool) -> None:
    """Buka PNG QR dengan aplikasi bawaan, hanya kalau pengguna memintanya."""
    if not diminta:
        return
    try:
        import os
        os.startfile(berkas)        # hanya ada di Windows
    except Exception as e:          # noqa: BLE001 -- gagal membuka tidak boleh
        print(f"(gambar tidak bisa dibuka otomatis: {e})")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Jalankan cloudflared untuk antarmuka demo dan tampilkan QR alamatnya.")
    p.add_argument("--port", type=int, default=5000,
                   help="port antarmuka demo yang sudah berjalan (bawaan: 5000)")
    p.add_argument("--alamat", default=None,
                   help="lewati cloudflared, buat QR untuk alamat ini saja "
                        "(mis. alamat tunnel yang sudah telanjur berjalan)")
    p.add_argument("--gambar", default="qr-demo.png",
                   help="berkas PNG tempat QR disimpan; kosongkan dengan '' untuk tidak menyimpan")
    p.add_argument("--cloudflared", default=None, help="jalur ke cloudflared kalau tidak ada di PATH")
    p.add_argument("--buka", action="store_true",
                   help="buka gambar QR dengan aplikasi bawaan, supaya bisa ditampilkan layar penuh")
    args = p.parse_args()

    # Balok QR di terminal butuh UTF-8; konsol Windows bawaan memakai cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    berkas = Path(args.gambar).resolve() if args.gambar else None

    # Jalur pendek: alamat sudah diketahui, tidak perlu menjalankan apa pun.
    if args.alamat:
        print(gambar_qr(args.alamat, berkas))
        print(f"Alamat : {args.alamat}")
        if berkas:
            print(f"Gambar : {berkas}")
            buka_gambar(berkas, args.buka)
        return 0

    cf = cari_cloudflared(args.cloudflared)
    if not cf:
        print("cloudflared tidak ditemukan. Pasang dulu, atau sebutkan jalurnya "
              "dengan --cloudflared, atau pakai --alamat kalau tunnelnya sudah berjalan.")
        return 1

    sasaran = f"http://localhost:{args.port}"
    print(f"Menjalankan: cloudflared tunnel --url {sasaran}")
    print("Menunggu alamat tunnel terbit...\n")

    proses = subprocess.Popen(
        [cf, "tunnel", "--url", sasaran],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # cloudflared menulis lognya ke stderr
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    alamat = None
    try:
        for baris in proses.stdout:                      # type: ignore[union-attr]
            sys.stdout.write(baris)
            sys.stdout.flush()
            if alamat:
                continue
            m = POLA_ALAMAT.search(baris)
            if not m:
                continue
            alamat = m.group(0)
            print("\n" + gambar_qr(alamat, berkas))
            print(f"Alamat : {alamat}")
            if berkas:
                print(f"Gambar : {berkas}   (buka dan tampilkan layar penuh saat demo)")
                buka_gambar(berkas, args.buka)
            print("Ingat  : terbuka untuk siapa saja yang punya alamat ini. "
                  "Tekan Ctrl+C setelah demo selesai.\n")
        return proses.wait()
    except KeyboardInterrupt:
        print("\nMenutup tunnel...")
        proses.terminate()
        try:
            proses.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proses.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
