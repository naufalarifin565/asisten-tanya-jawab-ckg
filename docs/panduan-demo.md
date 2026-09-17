# Panduan Demo

Urutan yang sudah diuji, beserta apa yang harus ditunjukkan di tiap langkah.
Tujuannya bukan membuat sistem terlihat sempurna, tetapi membuat **batas-batasnya
terlihat jelas**. Itu yang membedakan purwarupa yang bisa dipertanggungjawabkan
dari demo yang cuma mengesankan.

---

## A. Persiapan (10 menit sebelum mulai)

```bash
# 1. Uji cepat pengaman: harus "SEMUA UJI LULUS"
python eval/uji_guardrail.py

# 2. Nyalakan antarmuka web dengan mesin llm, tunggu model selesai dimuat (±60 detik)
python web/app_web.py
```

Korpus dan indeks sudah ada di `data/processed/`, jadi `chunk.py` dan `index.py`
**tidak perlu** dijalankan sebelum demo.

Kalau memakai `jalankan_web.bat`, tambahkan `--mesin llm`. Bawaan berkas itu
adalah mesin ekstraktif, yang tidak bisa menolak menjawab.

Buka `http://127.0.0.1:5000` dan **coba satu pertanyaan lebih dulu** sebelum
penonton masuk. Pertanyaan pertama selalu paling lambat.

Kalau demo lewat proyektor dan perlu dibuka dari laptop lain:
`python web/app_web.py --host 0.0.0.0`

**Siapkan juga** [eval/README.md](../eval/README.md) di jendela lain.
Kalau ditanya angka, tunjukkan berkasnya, jangan mengandalkan ingatan.

---

## B. Alur demo (±10 menit)

### 1. Mulai dari halaman depan, jangan langsung bertanya

Tunjukkan tiga angka di atas: **518 potongan · 349 dokumen · 12 sumber resmi**.

> "Sistem ini tidak menjawab dari pengetahuan umum model. Ia hanya menjawab dari
> dokumen resmi ini: Pusat Bantuan ASIK, FAQ SATUSEHAT Mobile, Juknis CKG, Juknis CKG
> Sekolah, dan Ayo Sehat."

Buka panel "Rincian sumber & model" dan tunjukkan bahwa semuanya berjalan lokal.

### 2. Pertanyaan yang berhasil: tunjukkan sumbernya, bukan cuma jawabannya

Pilih sasaran **Tenaga kesehatan**, lalu:

```
peserta tidak punya NIK, bagaimana?
```

Yang ditunjukkan: jawaban berupa langkah bernomor **dan** blok "Sumber" dengan
tautan yang bisa diklik.

> "Setiap jawaban wajib menyertakan sumber. Tautannya bisa dibuka dan diperiksa."

Klik tautannya sekali untuk membuktikan itu bukan hiasan.

### 3. Buka panel penelusuran: ini pembeda utamanya

Klik **"Lihat 4 potongan yang dipertimbangkan"**.

> "Kalau jawabannya terasa aneh, kita bisa langsung melihat penyebabnya: pencariannya
> yang meleset, atau perangkumannya."

### 4. Ganti sasaran: pertanyaan mirip, jawaban berbeda

Ganti ke **Masyarakat umum**:

```
bagaimana cara daftar Cek Kesehatan Gratis?
```

> "Metadata sasaran di setiap potongan membuat jawaban untuk warga tidak diambil dari
> dokumen teknis petugas."

### 5. Tunjukkan penolakan: ini fitur, bukan kegagalan

```
gula darah saya 250 artinya apa?
```

Jawaban ditolak dengan plakat **"Tidak dijawab · klinis"**.

> "Sistem ini bukan alat klinis. Penapisnya dikerjakan kode, bukan dititipkan ke
> model bahasa."

Lalu satu yang di luar korpus:

```
bagaimana cara mengganti oli motor?
```

> "Ditolak juga, tapi alasannya berbeda: tidak ada di dokumen. Sistem tidak menebak."

### 6. Tutup dengan angka evaluasi, termasuk yang jelek

Buka [eval/README.md](../eval/README.md).

> "87 pertanyaan uji, 14 di antaranya sengaja tidak ada jawabannya. Recall@3 84%.
> Satu pertanyaan tanpa jawaban masih dijawab keliru, 9 pertanyaan yang ada
> jawabannya ditolak, dan pada uji tambahan dua pertanyaan klinis yang lolos
> penapis dijawab dengan informasi karangan model."

---

## C. Kalau penonton ingin mencoba sendiri dari ponsel

Dua cara, dan keduanya hanya dinyalakan selama demo berlangsung.

**Jaringan lokal (paling aman).** Jalankan server dengan `python web/app_web.py --host 0.0.0.0`,
lalu buat QR alamatnya:

```bash
python web/tunnel_qr.py --alamat http://192.168.1.10:5000   # ganti dengan IP laptop
```

Syaratnya penonton berada di Wi-Fi yang sama, dan Wi-Fi itu tidak memblokir
komunikasi antarperangkat.

**Tunnel Cloudflare (bisa diakses dari mana saja).** Server tetap berjalan di terminal
pertama, lalu di terminal kedua:

```bash
python web/tunnel_qr.py            # menjalankan cloudflared sekaligus membuat QR
python web/tunnel_qr.py --buka     # sekalian membuka gambar QR untuk ditampilkan
```

Alamat quick tunnel berbeda setiap kali dijalankan, jadi QR-nya dibuat saat itu juga
dari alamat yang baru terbit, dan disimpan sebagai `qr-demo.png`.

Tiga hal yang perlu diingat sebelum memakai tunnel:

- Siapa pun yang punya alamatnya bisa membuka sistem ini, tanpa kata sandi.
  **Pengaman klinis masih bocor**, jadi pengunjung bisa saja mendapat jawaban klinis
  karangan yang tetap berhias sumber resmi.
- Korpusnya berasal dari server staging Pusat Bantuan ASIK dan izinnya belum
  dikonfirmasi. Tunnel menyajikan isi itu ke internet.
- Jawaban diproses satu per satu di GPU laptop, sekitar 5 detik per pertanyaan.
  Sepuluh orang memindai bersamaan berarti mengantre.

Tekan Ctrl+C di terminal tunnel begitu demo selesai.

---

## D. Kalau ditanya, ini jawabannya

**"Apa bedanya dengan ChatGPT?"**
> Sistem ini hanya menjawab dari 12 sumber resmi, selalu menyertakan sumber, dan
> menolak kalau jawabannya tidak ada. Semuanya berjalan lokal; pertanyaan tidak
> pernah keluar dari komputer ini.

**"Datanya aman? Dikirim ke mana?"**
> Tidak ke mana-mana. Model dijalankan lokal dan sudah diuji dengan akses internet
> diblokir. Korpusnya dokumen publik, tanpa data pasien.

**"Berapa akurasinya?"**
> Untuk ketepatan **sumber**, angkanya ada di laporan. Ketepatan **isi jawaban** belum
> diukur karena butuh kunci jawaban yang ditulis manusia. Saya tidak mau mengklaim
> angka yang belum saya ukur.

**"Apakah benar tidak bisa menjawab pertanyaan klinis?"**
> Tidak sepenuhnya. Pada berkas uji semua tertahan, tetapi uji tambahan dengan
> pertanyaan bergaya informal menemukan dua yang lolos dan dijawab. Itu kelemahan
> terbesar yang harus diperbaiki sebelum sistem dipakai.

**"Bisa langsung dipakai?"**
> Belum. Pengaman klinis masih bocor, korpus ASIK masih dari server staging, model
> bahasanya berlisensi riset, dan pertanyaan ujinya belum ada yang dari lapangan.

---

## E. Yang sebaiknya TIDAK dilakukan

- **Jangan pakai `--mesin ekstraktif` saat demo.** Mesin itu tidak bisa menolak
  menjawab.
- **Jangan mencoba pertanyaan klinis bergaya bebas di depan penonton** tanpa
  menjelaskan temuan uji tambahan terlebih dahulu.
- **Jangan menyembunyikan pertanyaan yang gagal.** Daftar kelemahan sudah ada di
  laporan; pakai itu.
- **Jangan mengklaim angka ketepatan jawaban.** Yang terukur baru ketepatan sumber.
- **Jangan menjalankan `chunk.py` atau `index.py` saat server web berjalan.**

---

## F. Kalau ada yang salah saat demo

| Gejala | Sebabnya | Tindakan |
|---|---|---|
| Halaman tidak terbuka | server belum selesai memuat model | tunggu sampai muncul "Buka : http://..." |
| Jawaban lama sekali | ada permintaan lain sedang diproses | wajar, permintaan diantre satu per satu |
| "Tidak ada jawaban setelah 90 detik" | VRAM penuh | tutup proses Python lain, jalankan ulang server |
| "Indeks tidak cocok dengan korpus" | `chunk.py` dijalankan tanpa `index.py` | `python src/index.py`, lalu nyalakan ulang |
| Jawaban meleset | kelemahan pencarian yang memang ada | buka panel penelusuran, jelaskan apa adanya |

Kalau sistemnya benar-benar mati saat demo, buka [eval/README.md](../eval/README.md).
Angka dan temuannya tetap bisa dipresentasikan tanpa sistem yang berjalan.
