# Arsitektur dan Keputusan Rancangan

Dokumen ini menjelaskan **mengapa** sistem dibangun seperti sekarang. Cara
memasang dan menjalankannya ada di [README](../README.md).

Setiap keputusan di sini diukur, bukan diasumsikan. Beberapa angka berasal dari
tahap pengembangan sebelumnya dengan berkas uji yang lebih kecil; tahap dan
ukuran berkasnya disebutkan di tempatnya.

## Daftar isi

- [Alur sistem](#alur-sistem)
- [Pengaman dikerjakan kode, bukan dititipkan ke model](#pengaman-dikerjakan-kode-bukan-dititipkan-ke-model)
- [Korpus](#korpus)
- [Pencarian](#pencarian)
- [Penyusunan jawaban](#penyusunan-jawaban)
- [Antarmuka](#antarmuka)
- [Yang sudah dicoba dan gagal](#yang-sudah-dicoba-dan-gagal)

---

## Alur sistem

```
pertanyaan
    │
    ├─ pertanyaan tentang sistem ("bisa tanya apa?") ─► daftar topik dari korpus, tanpa sumber
    │
    ├─ 1. penapis klinis ──────────────────────────► tolak, arahkan ke tenaga kesehatan
    │      (kode, sebelum apa pun dikerjakan)
    │
    ├─ 2. pencarian hibrida
    │      embedding (makna) + BM25 (kata harfiah), digabung RRF
    │      disaring dulu per sasaran, baru diperingkat
    │      └─ tidak ada hasil ─────────────────────► tolak
    │
    ├─ 3. LLM menyusun kalimat dari 4 potongan teratas
    │      └─ memberi sandi TIDAK_ADA_DI_DOKUMEN ───► tolak
    │
    ├─ 4. periksa jawaban berpijak pada dokumen ────► tolak kalau tidak
    │
    └─ 5. tempel sumber dari metadata + buang tautan karangan LLM
              │
              ▼
        jawaban + sumber
```

Semua komponen memanggil satu kelas yang sama, `Penjawab` di `src/answer.py`.
Antarmuka terminal, antarmuka web, dan skrip evaluasi tidak menyalin satu pun
aturan, sehingga yang didemokan adalah sistem yang diukur.

---

## Pengaman dikerjakan kode, bukan dititipkan ke model

Godaan terbesar saat membangun RAG adalah menulis perintah panjang ke model
("jangan mengarang, jangan beri saran medis, sertakan sumber") lalu menganggap
urusan selesai. Itu rapuh: model kecil sering melanggar perintahnya sendiri, dan
pelanggarannya baru ketahuan setelah dipakai orang.

| Dikerjakan **kode** | Dikerjakan **LLM** |
|---|---|
| menolak pertanyaan klinis | merangkai kalimat dari konteks |
| menolak saat tidak ada dokumen | memberi sandi kalau konteks tidak memuat jawaban |
| menolak jawaban yang tidak berpijak pada dokumen | |
| menempelkan sumber dari metadata | |
| membuang tautan karangan model | |

Dengan pembagian ini, model **tidak bisa mengarang alamat sumber**, karena
sumber tidak pernah ditulis olehnya.

**Batas yang terbukti (15 September 2026).** Pembagian ini *tidak* menjamin model
tidak menjawab pertanyaan klinis. Pada uji dengan 12 pertanyaan klinis baru, dua
pertanyaan lolos penapis lalu dijawab dengan informasi klinis karangan model,
dan jawabannya tetap ditempeli sumber resmi. Dua kelemahan penyebabnya:

1. Penapis berbasis pola hanya mengenali gaya kalimat yang terpikirkan saat
   menyusunnya. Singkatan ("sy"), akhiran ("darahku", "tensinya"), bahasa Inggris,
   dan perintah "abaikan semua aturan" lolos.
2. Pemeriksaan keberpijakan hanya menghitung liputan kata dengan ambang 0,15.
   Jawaban pendek mudah berbagi kata umum dengan potongan konteks meskipun isi
   pokoknya (angka, nama obat) dikarang. Kedua jawaban tadi berliputan 0,18 dan 0,38.

Rinciannya ada di [eval/README.md](../eval/README.md#pengujian-pengaman).

### Penapis klinis: bentuk yang paling wajar justru sempat lolos

Penapis versi awal mencari kata kerja klinis (*obat*, *dosis*, *terapi*) dan kata
penilaian (*normal*, *berbahaya*). Saat antarmuka web dicoba manual, pertanyaan
*"Tekanan darah saya 150/95, apakah saya hipertensi?"* tidak tertahan dan dijawab
dengan daftar jenis pemeriksaan CKG. Orang justru menyebut **nama penyakitnya
langsung**.

Perbaikannya ditulis sebagai fungsi `_melekat_pada_diri()`: nama penyakit atau
ukuran kesehatan dianggap klinis kalau dilekatkan pada diri penanya (*saya*,
*anak saya*, ...) tanpa kata kerja program (*periksa*, *daftar*, *catat*, ...) di
antaranya. Enam pertanyaan program yang menyebut nama penyakit yang sama
(*"Apakah anemia diperiksa di CKG?"*) ditambahkan ke berkas uji sebagai
pengimbang, supaya penapis tidak bisa diperketat sampai menolak segalanya.

Pelajarannya: berkas uji hanya menjamin kasus yang sudah terpikirkan. Setiap
perbaikan penapis perlu diuji dengan pertanyaan baru yang ditulis setelah
perbaikan selesai.

---

## Korpus

| Sumber | Potongan | Dokumen | Sasaran |
|---|---|---|---|
| Pusat Bantuan ASIK (staging) | 169 | 156 | tenaga kesehatan |
| Juknis CKG (Kepmenkes 84/2026) | 86 | 29 | tenaga kesehatan |
| FAQ Verifikasi Profil (KYC) | 56 | 17 | masyarakat |
| FAQ CKG Umum | 49 | 32 | masyarakat |
| Juknis CKG Sekolah (Kepmenkes 770/2025) | 47 | 17 | tenaga kesehatan, sekolah |
| FAQ CKG Fasyankes | 40 | 40 | tenaga kesehatan |
| FAQ CKG Sekolah | 15 | 15 | sekolah |
| FAQ Akun dan Keamanan SATUSEHAT | 15 | 15 | masyarakat |
| Ayo Sehat | 14 | 3 | masyarakat, sekolah |
| FAQ Resume Medis SATUSEHAT | 10 | 10 | masyarakat |
| FAQ Kendala Login SATUSEHAT Mobile | 9 | 7 | masyarakat |
| FAQ Profil Terhubung | 8 | 8 | masyarakat |
| **Jumlah** | **518** | **349** | |

Angka di atas sudah tanpa informasi chatbot WhatsApp; lihat
[Informasi chatbot WhatsApp dibuang](#informasi-chatbot-whatsapp-dibuang).

Empat kategori "prasyarat CKG" (KYC, akun, login, profil terhubung) bukan tentang
CKG langsung, melainkan langkah yang harus dilewati sebelum bisa mendaftar.
Kategori itu ditambahkan setelah menyadari sistem bisa menjawab *"bagaimana cara
daftar CKG?"* tetapi bungkam begitu penanya tersangkut di langkah pertama
(*"saya tidak bisa login"*), padahal justru di situ orang paling butuh bantuan.

Sebanyak 91 potongan (18%) Pusat Bantuan ASIK membahas program di luar CKG
(imunisasi, ibu hamil, bayi dan balita, remaja). Potongan ini dipertahankan karena
merupakan bagian dari aplikasi pencatatan yang sama, tetapi dapat bersaing dengan
dokumen CKG dalam pencarian.

### Satu halaman = satu potongan

Halaman Pusat Bantuan ASIK sudah merupakan satu topik utuh, biasanya satu
prosedur bernomor. Memotongnya di tengah membuat sistem bisa mengembalikan
setengah prosedur, dan jawabannya jadi **salah**, bukan sekadar kurang lengkap.

Potongan hanya dibelah kalau lebih dari 1.600 karakter (sekitar 400 token, di bawah
jendela 512 token model embedding), berurutan di batas heading, lalu paragraf,
lalu kalimat. Hasilnya 302 dari 349 dokumen (87%) muat utuh dalam satu potongan,
dengan median panjang potongan 641 karakter.

### Kenapa Juknis CKG Sekolah dicari sampai ketemu

Kepmenkes 84/2026 menyerahkan kelompok usia sekolah ke dokumen lain, dengan
kalimatnya sendiri di BAB II:

> "Pelaksanaan CKG pada usia sekolah dan remaja mengikuti Petunjuk Teknis CKG Sekolah."

Selama dokumen itu belum masuk, sasaran `sekolah` hanya punya 20 potongan, semuanya
FAQ dan halaman kampanye tanpa satu pun dasar ketentuan. Dokumennya adalah
**Kepmenkes HK.01.07/MENKES/770/2025**, ditemukan di JDIH Kemenkes. Setelah masuk,
sasaran `sekolah` naik ke 68 potongan (65 setelah informasi chatbot WhatsApp dibuang).

### Satu potongan boleh menyasar dua kelompok

Juknis CKG Sekolah menyebut sasarannya sendiri: dinas kesehatan dan puskesmas,
**dan** kepala sekolah/madrasah beserta tim pelaksana UKS. Memaksanya memilih satu
berarti menuliskan metadata yang tidak benar. Akibatnya juga nyata: `retrieve.py`
menyaring *sebelum* memeringkat, sehingga kelompok yang tidak terpilih tidak akan
pernah menemukan dokumen ini. Karena itu `sasaran` boleh berupa satu kata atau
daftar kata; lihat `cocok_sasaran()` di `src/utils.py`.

### Bab pedoman klinis Juknis sengaja dibuang

BAB V Juknis CKG (61 dari 122 halaman) berisi tabel *hasil pemeriksaan → tindak
lanjut klinis*. BAB IV dan Juknis CKG Sekolah memuat tabel serupa. Semuanya
disaring keluar: BAB V tidak diambil sama sekali (`collect_pdf.py`), tabel lainnya
dipangkas di tingkat teks bab sebelum pemotongan (`buang_tabel_klinis()`), dan
setiap potongan diperiksa lagi (`isi_klinis()`).

Pemangkasan di tingkat teks diperlukan karena tabel yang panjangnya beberapa
halaman terbelah menjadi banyak potongan, dan hanya potongan pertama yang membawa
judul kolomnya. Pada satu bagian saja, penyaringan per potongan meloloskan 5 dari
18 potongan tabel, semuanya potongan lanjutan.

Alasannya: penapis pertanyaan menyaring **cara bertanya**, bukan isi korpus.
Selama bahannya tidak ada, sistem tidak punya sumber untuk arahan klinis walaupun
penapisnya kebobolan. Uji 15 September 2026 menunjukkan model tetap bisa
mengarang dari pengetahuannya sendiri, jadi langkah ini perlu tetapi tidak cukup.

Bisa dibatalkan dengan `python src/collect_pdf.py --sertakan-bab-v`.

### Kategori FAQ yang sengaja TIDAK diambil

SATUSEHAT Mobile punya 26 kategori FAQ, yang diambil hanya 8.

- **Diari Kesehatan (29 topik)** berisi *"Apa yang dapat saya lakukan jika kadar
  kolesterol saya tinggi?"* dan skrining kesehatan jiwa. **Pertumbuhan Anak
  (7 topik)** berisi *"Apa yang perlu saya lakukan jika hasil pengukuran anak kurang
  baik?"*. Keduanya klinis.
- **Dashboard Kesehatan (6 topik)** menafsirkan arti warna indikator kesehatan
  seseorang, jadi ditahan sampai ada keputusan pembimbing.
- Sisanya (Vaksin Booster, Telemedisin Isoman, Cari Obat, Health Pass, dan lainnya)
  bukan CKG. Memasukkannya menambah ratusan pesaing di setiap pencarian tanpa
  pernah menjawab pertanyaan CKG.

Daftar lengkap beserta alasannya ada di kepala berkas `src/collect_faq.py`.

### Informasi chatbot WhatsApp dibuang

Pada 16 September 2026 pembimbing lapangan menyampaikan bahwa layanan chatbot
WhatsApp sudah dihentikan Kemenkes, yaitu WhatsApp Chatbot Kemenkes RI untuk
pendaftaran dan kuesioner CKG, serta ASIK WhatsApp Chatbot untuk kader dan nakes
mencatat data posyandu. Dokumen sumbernya belum diperbarui, sehingga sistem sempat
menjawab *"cara daftar CKG"* dengan langkah menghubungi nomor chatbot yang sudah tidak
aktif. Jawaban itu bersumber resmi, tetapi menyesatkan orang yang mengikutinya.

Penyaringannya dikerjakan di `src/chunk.py`, **bukan** dengan menyunting `data/raw/`.
Mentahan tetap salinan asli server, sementara keputusan ini tercatat, bisa diulang,
dan mudah dibatalkan kalau layanannya aktif kembali:

- 6 halaman Pusat Bantuan ASIK yang pokok bahasannya chatbot tidak diambil
  (`HALAMAN_CHATBOT_WA`).
- 15 dokumen atau bagian lain dipangkas hanya pada kalimat chatbot-nya
  (`ATURAN_CHATBOT_WA`), misalnya *"kuesioner mandiri pada aplikasi SSM atau WA
  Chatbot Kemenkes"* menjadi *"kuesioner mandiri pada aplikasi SSM"*, dan butir
  *"2. WhatsApp Chatbot Kemenkes RI"* di FAQ cara daftar CKG dibuang lalu butir
  sesudahnya dinomori ulang.
- Aturannya sengaja spesifik per kalimat, karena kata WhatsApp juga dipakai untuk hal
  yang masih berlaku: kode OTP, isian nomor WhatsApp, grup WhatsApp guru, serta
  notifikasi dan rapor hasil lewat WhatsApp. Semua itu tidak disentuh.
- `chunk.py` memeriksa sendiri bahwa tidak ada lagi kata "chatbot" atau nomor
  chatbot di korpus, dan mencetak peringatan kalau masih ada.

Korpus turun dari 533 menjadi 518 potongan (355 menjadi 349 dokumen). Pencarian
sedikit membaik, tetapi satu pertanyaan tanpa jawaban yang sebelumnya ditolak kini
dijawab keliru; lihat
[eval/README.md](../eval/README.md#dampak-pembuangan-informasi-chatbot-whatsapp).

### Bentuk satu baris korpus

```json
{
  "id": "asik:ptm-pencatatan-skrining-ptm:0",
  "teks": "# Pencatatan Skrining PTM\n\n...",
  "sasaran": "nakes",
  "jenis": "cara_pakai_aplikasi",
  "sumber_nama": "Pusat Bantuan ASIK — Pencatatan Skrining PTM",
  "sumber_url": "https://asiksupport-stg.dto.kemkes.go.id/...",
  "tanggal_ambil": "2026-08-24",
  "judul_bagian": "Pencatatan Skrining PTM",

  "sumber_id": "asik",
  "kategori": "ptm",
  "judul_dokumen": "Pencatatan Skrining PTM",
  "urutan": 0,
  "total_potongan": 2,
  "jumlah_karakter": 1204,
  "hash_isi": "…",
  "jenis_metode": "heuristik",
  "ada_gambar_di_sumber": false,
  "lingkungan": "staging"
}
```

Enam field setelah `teks` adalah metadata wajib (CLAUDE.md §4). Sisanya untuk
penelusuran dan evaluasi. `jenis_metode: heuristik` menandai bahwa label `jenis`
ditebak dari judul, belum diperiksa manual seluruhnya. Potongan dari dokumen yang
dipangkas informasi chatbot-nya membawa field tambahan `catatan_suntingan`, supaya
jelas teksnya tidak lagi sama persis dengan sumber aslinya.

---

## Pencarian

### Tanpa basis data vektor

Korpus ini ratusan potongan. Mencari yang paling mirip dari 518 vektor adalah satu
perkalian matriks NumPy yang selesai dalam milidetik. Chroma, FAISS, atau Qdrant
dirancang untuk jutaan vektor; memakainya di sini hanya menambah dependensi tanpa
membuat pencarian lebih cepat atau lebih tepat. Indeksnya hanya sekitar 1,4 MB.

Pertimbangkan lagi kalau korpus sudah puluhan ribu potongan.

`indeks_meta.json` menyimpan sidik jari korpus. Kalau korpus berubah tetapi indeks
belum dibangun ulang, `retrieve.py` berhenti dengan pesan jelas, bukan diam-diam
merujuk sumber yang keliru.

### Penyaringan sasaran sebelum peringkat

Kalau menyaring setelah mengambil lima teratas, pertanyaan masyarakat bisa
mengambil lima potongan untuk tenaga kesehatan yang lalu tersaring habis dan
menyisakan nol jawaban. Menyaring dulu baru memeringkat menjamin *k* hasil
teratas memang berasal dari kelompok yang diminta.

### Pencarian hibrida: embedding + kata kunci

Embedding kuat pada makna dan sinonim, tetapi lemah pada singkatan langka:

| Pertanyaan: *"Apakah ASIK sudah terhubung dengan SIPTM?"* | Peringkat dokumen benar |
|---|---|
| embedding saja | 14 |
| hibrida | **2** |

Penggabungannya memakai **Reciprocal Rank Fusion** (RRF), yang bekerja pada
peringkat, bukan skor mentah. Skor kosinus (0,8–0,9) dan skor BM25 (0 sampai
puluhan) tidak sebanding, dan menjumlahkannya butuh penyetelan bobot yang mudah
dicurigai dicari-cari agar hasilnya bagus. Konstanta `RRF_K = 60` diambil dari
makalah aslinya dan tidak disetel.

Diukur ulang pada 87 pertanyaan dan korpus 518 potongan (16 September 2026):

| Ukuran | Embedding saja | Hibrida |
|---|---|---|
| Recall@1 | 56,2% | 63,0% |
| Recall@3 | 76,7% | 83,6% |
| Recall@10 | 89,0% | 90,4% |
| MRR | 0,678 | 0,733 |

Recall@10 hanya naik satu pertanyaan, artinya BM25 terutama memperbaiki urutan
dokumen yang sudah terambil. Pada kelompok masyarakat Recall@3 keduanya sama
(12 dari 17), tetapi MRR hibrida lebih tinggi.

### Reranking belum dipasang

Reranking dengan *cross-encoder* menambah satu model lagi dan memperlambat setiap
pertanyaan. Kalau dicoba, harus diukur dengan cara yang sama, bukan diasumsikan
lebih baik karena terdengar canggih.

---

## Penyusunan jawaban

### Penolakan tidak memakai ambang skor

Rencana awalnya: kalau skor kemiripan di bawah ambang tertentu, tolak. Setelah
diukur, itu mustahil, karena rentang skornya bertumpang tindih.

| Mode pencarian | Skor terendah pertanyaan berjawab | Skor tertinggi pertanyaan tanpa jawaban |
|---|---|---|
| embedding saja (tahap awal) | 0,8267 | 0,8458 |
| hibrida (RRF, 87 pertanyaan) | 0,0300 | 0,0328 |

Ambang tunggal pasti salah pada salah satu arah. Yang dipakai sebagai gantinya:
model diminta menuliskan sandi `TIDAK_ADA_DI_DOKUMEN` kalau konteksnya tidak
menjawab, lalu **kode** yang mendeteksi sandi itu dan mengganti seluruh jawaban
dengan penolakan baku. Kode juga menolak jawaban yang kata isinya hampir tidak
muncul di potongan mana pun (`AMBANG_BERPIJAK = 0,15`). Ambang ini terbukti terlalu
longgar; lihat [bagian pengaman](#pengaman-dikerjakan-kode-bukan-dititipkan-ke-model).

**Sandi dari model ternyata rapuh.** Setelah informasi chatbot WhatsApp dibuang
(16 September 2026), pertanyaan *"berikan daftar puskesmas yang melayani CKG di
Bandung"* mendapat empat potongan konteks yang sama persis seperti sebelumnya; hanya
teks salah satunya yang berubah karena butir chatbot dihapus. Perubahan kecil itu
membuat model berhenti memberi sandi dan menyalin daftar fasyankes lokasi verifikasi
KYC sebagai jawaban. Keputusan menolak yang bergantung pada model bahasa kecil tidak
stabil terhadap perubahan konteks yang tampak tidak berkaitan, jadi setiap perubahan
korpus wajib diikuti evaluasi ulang.

### Sumber hanya dari potongan yang benar-benar dipakai

Pencarian mengambil empat potongan, tetapi jawaban biasanya hanya memakai satu.
Kalau keempatnya dicantumkan, pembaca mengira keempat dokumen mendukung jawaban.
Karena itu hanya potongan dengan liputan kata ≥ 0,35 yang dikutip; kalau tidak ada
yang memenuhi, potongan peringkat pertama tetap dicantumkan.

### Rujukan "Dokumen 1" ditukar nama dokumen, bukan dihapus

Konteks disusun sebagai `--- Dokumen 1 ---`, `--- Dokumen 2 ---`. Penomoran itu
tidak pernah terlihat pengguna, jadi jawaban yang berbunyi *"lihat Dokumen 2"*
tidak ada artinya.

Perlakuan lama mengganti `Dokumen \d+` dengan kata `dokumen`, dan cacatnya baru
ketahuan pada jawaban berisi daftar bernomor:

```
1. dokumen: Ini menjelaskan prosedur pelaksanaan CKG di tempat kerja...
2. dokumen: Dokumen ini menjelaskan sistem pencatatan dan pelaporan...
```

Pertanyaannya justru *"panduan apa yang perlu saya baca?"*, dan pembersihnya
menghapus persis bagian yang ditanyakan. Sekarang nomornya ditukar dengan nama
dokumen sebenarnya dari metadata (dipendekkan maksimal 62 huruf), dan dokumen yang
disebut di badan jawaban wajib ikut masuk daftar sumber. Nomor di luar jangkauan
tetap diturunkan jadi kata umum, bukan nama tebakan.

Batasnya: nama bercetak tebal yang ditulis model sendiri **bukan** hasil mekanisme
ini. Yang dijamin hanya nama di dalam tanda kutip dan daftar sumber.

### Panjang jawaban menyesuaikan permintaan

Jawaban bawaan sengaja ringkas. Tetapi begitu pengguna meminta penjelasan lengkap,
perintah sistem melawan permintaan pengguna, dan perintah sistem yang menang.

**Yang salah bukan atap tokennya.** Dengan atap 700 token (sekitar 450–500 kata),
jawaban terpanjang dari lima pertanyaan hanya 218 kata. Yang memendekkan adalah
kalimat perintahnya sendiri (`"Jawab ringkas dan runtut"`, `"Kalimat pendek"`).
Mode rinci mencabut kalimat itu, menambahkan instruksi tandingan, dan menaikkan
atap ke 1.400 token sebagai ruang:

| Pertanyaan | Ringkas | Rinci |
|---|---|---|
| Alur pencatatan dan pelaporan CKG | 97 kata | 385 kata |
| Tugas pihak sekolah dalam CKG | 98 kata | 199 kata |
| Cara mendaftar CKG | 106 kata | 324 kata |

Yang **tidak** berubah di mode rinci: aturan pengaman tetap sama, jumlah potongan
tetap 4, dan tidak ada pertanyaan uji yang memicu mode ini. Aktif otomatis dari
kalimat seperti *"jelaskan rinci…"* atau *"step by step…"*. Kata "lengkap" dan
"detail" sengaja tidak dihitung sebagai kata lepas (*"Imunisasi Dasar Lengkap"*
tidak boleh memicunya). Deteksinya diuji pada 22 kalimat, semuanya benar.

### Deterministik dan kuantisasi otomatis

- `do_sample=False`: jawaban bisa diulang persis. Pada 15 September 2026 evaluasi
  diulang dan menghasilkan angka yang sama persis dengan laporan 4 September.
- Kuantisasi 4-bit (NF4 + *double quantization*) dipilih otomatis kalau VRAM bebas
  < 8,6 GB. Tanpa kuantisasi model 3B butuh sekitar 8,1 GB termasuk model
  pencarian, praktis tidak muat di kartu 8 GB. Catatan pengembangan: VRAM model
  turun dari 6,17 GB ke 2,06 GB, kecepatan dari 21,6 ke 16,5 token/detik.
- Pemuatan bertingkat: GPU penuh → sebagian ke CPU → CPU penuh. Kalau VRAM habis
  saat menjawab, dicoba sekali lagi dengan atap token setengahnya.

---

## Antarmuka

### Server web memakai waitress, bukan bawaan Flask

Dengan server bawaan Flask (Werkzeug), proses mati dengan *segmentation fault*
begitu jawaban mulai disusun, tanpa pesan galat. Dipersempit dengan tiga percobaan:
`threaded=False` tetap crash, thread pekerja khusus tetap crash, tanpa Flask
berhasil. Waitress menyelesaikannya.

Model tetap dimuat dan dipakai oleh **satu thread pekerja** yang menerima
pertanyaan lewat antrean. Model pencarian dan model bahasa dimuat di depan, supaya
kegagalan VRAM terjadi sebelum server menerima pertanyaan, bukan saat demo.

### Tampilan

- Layar pembuka (lambang Kemenkes dan SATUSEHAT) hilang sendiri sekitar 2,6 detik
  lewat animasi CSS, bisa dilewati dengan klik, dan dilewati sama sekali pada
  `prefers-reduced-motion`. Kalau JavaScript gagal dimuat, halaman tetap bisa dipakai.
- Logo SATUSEHAT digambar ulang sebagai SVG dari ukuran dan warna berkas resmi
  (teal `#01B3AC`, limau `#D1DD27`) supaya tajam di ukuran berapa pun.
- Gerak antarmuka memakai CSS dan Web Animations API tanpa pustaka JavaScript.
  **Catatan:** font Plus Jakarta Sans dan IBM Plex Mono diambil dari Google Fonts;
  tanpa internet, peramban memakai font cadangan dan halaman tetap berfungsi.
- Penolakan ditampilkan dengan plakat berbeda ("Tidak dijawab · klinis"), karena
  penolakan adalah fitur, bukan galat.
- Panel "Lihat 4 potongan yang dipertimbangkan" memperlihatkan nilai RRF tiap
  potongan, supaya jelas apakah kekeliruan berasal dari pencarian atau perangkuman.

Dua jebakan saat membuat SVG yang layak dicatat: komentar XML tidak boleh memuat
`--`, dan animasi CSS `transform` menimpa atribut `transform` pada elemen SVG
(bungkus dengan `<g>` dan animasikan pembungkusnya).

---

## Yang sudah dicoba dan gagal

Semua gagasan di bawah ini terdengar masuk akal sebelum diuji. Dicatat supaya tidak
diulang.

### Memperbaiki penolakan keliru pada kalimat perintah: tiga cara, semua gagal

Isi sama, konteks sama, hanya bentuk kalimatnya berbeda:

```
"berikan panduan dan pedoman pencatatan data pasien"   -> DITOLAK
"Bagaimana panduan pencatatan data pasien?"            -> dijawab
```

Pencariannya benar. Model menafsirkan *"berikan panduan"* secara harfiah sebagai
*"serahkan dokumen panduannya"*. Diuji pada 6 kalimat perintah yang jawabannya ada
dan 15 pertanyaan pembanding yang jawabannya tidak ada:

| Konfigurasi | Terjawab | Mengarang |
|---|---|---|
| **apa adanya, tanpa perubahan** | **4/6** | 0 |
| perintah sistem diperjelas | 3/6 | 0 |
| perintah diperjelas + coba-ulang bercatatan | 3/6 | 0 |
| apa adanya + coba-ulang bercatatan | 4/6 | 0 |
| perintah diperjelas + pertanyaan ditulis ulang | 5/6 | **1** |

Satu-satunya yang menaikkan angka justru mengarang. Menulis ulang *"berikan DAFTAR
puskesmas yang melayani CKG di Bandung"* (senarai) menjadi *"Bagaimana daftar
puskesmas…"* (mendaftar) membuat model menjawab pertanyaan yang berbeda. Seluruh
perubahan dikembalikan.

Pelajarannya: memperjelas perintah sistem tidak selalu memperbaiki (4/6 turun ke
3/6 pada model 3B 4-bit), dan kelompok pembanding wajib ada. Pengarangan itu hanya
ketahuan karena ada pertanyaan yang sengaja tidak memiliki jawaban.

### Menuliskan kepanjangan akronim: memperburuk

Korpus menulis *"Cek Kesehatan Gratis"*, pengguna mengetik *"CKG"*, jadi
kepanjangannya ditambahkan. Skornya naik dari 0,87 ke 0,91, tetapi dokumen yang
benar justru terlempar makin jauh:

| Pertanyaan | Tanpa | Dengan |
|---|---|---|
| "syarat ikut CKG apa saja?" | 9 | 20 |
| "Apa saja persyaratan CKG?" | 14 | 23 |
| "Pemeriksaan CKG apa saja?" | 7 | **38** |

**Skor naik bukan berarti jawaban membaik.** Fungsinya masih ada di
`src/retrieve.py` (`perluas_akronim`, bawaan mati) sebagai catatan hasil negatif.

### Memperbanyak konteks jadi 8 potongan: tidak menolong

Ketepatan sumber turun dari 75% ke 69%, satu pertanyaan yang tadinya benar jadi
meleset, dan waktunya dua kali lipat. Tetap `k=4`.

### Model bahasa 1,5 miliar parameter: mengarang

Model 3B terasa berat dan berlisensi riset, jadi `Qwen2.5-1.5B-Instruct` diuji
sebagai pengganti (berkas uji 63 pertanyaan). Kriteria kelulusan ditetapkan
**sebelum** angkanya keluar, dan model ini gagal di dua arah sekaligus:

| Ukuran | Syarat | 1.5B |
|---|---|---|
| Mengarang | 0 | **3** |
| Menolak dengan benar | ≥ 8/10 | 7/10 |
| Ditolak padahal ada | ≤ 5/53 | **10** |

Model yang lebih kecil tidak sekadar "lebih lemah": kepatuhannya pada aturan tidak konsisten.
