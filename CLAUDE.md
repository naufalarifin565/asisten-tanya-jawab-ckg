# Proyek: Asisten Tanya-Jawab Pencatatan CKG (RAG)

Berkas ini adalah konteks proyek. Baca seluruhnya sebelum mulai bekerja.

---

## 1. Konteks

Proyek magang di **Divisi Community Health, Digital Transformation Office (DTO), Kementerian Kesehatan RI**.
Durasi magang: **sekitar 4 minggu**. Peran: AI Engineer.

Yang dibangun: **sistem tanya-jawab berbasis RAG** (Retrieval-Augmented Generation) untuk membantu
pertanyaan seputar **pencatatan program Cek Kesehatan Gratis (CKG)**.

Dua sasaran pengguna:

| Sasaran | Contoh pertanyaan |
|---|---|
| **Tenaga kesehatan (nakes)** | "Bagaimana mencatat skrining PTM di ASIK?", "Peserta tidak punya NIK, bagaimana?", "Kenapa data tidak muncul di dashboard?" |
| **Masyarakat / peserta** | "Bagaimana cara daftar CKG?", "Syaratnya apa?", "Pemeriksaannya apa saja?" |

Pertanyaan yang sama bisa berbeda jawabannya tergantung siapa yang bertanya, dan bahasa untuk
masyarakat harus jauh lebih sederhana. **Setiap potongan pengetahuan wajib diberi penanda sasaran pengguna.**

---

## 2. Prinsip Wajib (tidak boleh dilanggar)

1. **Setiap jawaban wajib menyertakan sumber** — nama dokumen/halaman dan tautannya.
2. **Dilarang menjawab di luar dokumen.** Kalau informasi tidak ada di korpus, sistem harus
   menyatakannya terus terang dan mengarahkan ke jalur bantuan resmi. Jangan mengarang.
3. **Bukan alat klinis.** Sistem tidak menafsirkan hasil pemeriksaan seseorang, tidak memberi
   saran pengobatan, tidak menegakkan diagnosis. Kalau pertanyaannya klinis, arahkan ke nakes.
4. **Tidak menyentuh data pribadi.** Seluruh korpus adalah dokumen publik. Tidak ada data pasien.
5. **Model open-source, dijalankan lokal.** Alasan: kedaulatan data dan kebijakan internal.
   Untuk pengembangan awal boleh pakai model kecil supaya cepat iterasi.

---

## 3. Sumber Korpus (semua resmi & publik)

### Prioritas 1 — kerjakan duluan

**a. Pusat Bantuan ASIK** — ratusan artikel bantuan penggunaan aplikasi pencatatan.

```
Indeks halaman : https://asiksupport-stg.dto.kemkes.go.id/llms.txt
Versi Markdown : tambahkan .md di akhir URL halaman
```

Kategori paling relevan untuk pencatatan CKG:
- **PTM (Penyakit Tidak Menular)** — di sinilah skrining CKG dicatat
- **Data Individu** — sasaran vs pengunjung, input data, kondisi sinyal buruk
- **Manajemen Akun & Pengguna** — pendaftaran kader/nakes, aktivasi, STR
- **PIN & Keamanan**
- **User Management** — peran & izin akses

> **PENTING**: URL di atas mengandung `-stg` (staging). Alamat produksi masih perlu
> dikonfirmasi ke pembimbing. Jangan bangun korpus final dari lingkungan uji coba.

**b. FAQ CKG untuk Fasyankes** — ±47 topik, disusun dari pertanyaan nyata petugas.

```
https://satusehat.kemkes.go.id/mobile/faq/topic?categoryId=7cd776af-e9d8-496a-b7fd-1f149db25232
```

### Prioritas 2

**c. Petunjuk Teknis CKG** — Kepmenkes HK.01.07/MENKES/84/2026 (PDF).
Rujukan untuk pertanyaan yang bersifat ketentuan program, bukan teknis aplikasi.

```
https://kesprimkom.kemkes.go.id/assets/uploads/contents/others/2026kepmenkes084.pdf
```

### Prioritas 3 — sisi masyarakat

**d. FAQ CKG Umum**
```
https://satusehat.kemkes.go.id/mobile/faq/topic?categoryId=e4a5f2e3-343b-4648-90e5-0fa869b6c146
```

**e. Halaman program CKG (Ayo Sehat)**
```
https://ayosehat.kemkes.go.id/diet-sehat/cek-kesehatan-gratis
```

**f. FAQ CKG Sekolah** (opsional, kalau cakupan sampai sekolah)
```
https://satusehat.kemkes.go.id/mobile/faq/topic?categoryId=617757c6-2104-4f71-9d6e-3b0bb0d19098
```

### JANGAN dulu

Pedoman klinis (hipertensi/diabetes/Buku KIA). Begitu sistem menyentuh makna hasil
pemeriksaan, pembatasannya jadi jauh lebih ketat. Tahan dulu sampai yang dasar beres.

---

## 4. Metadata Wajib per Chunk

Setiap potongan teks yang masuk vector store harus membawa:

```json
{
  "sasaran": "nakes | masyarakat | sekolah",
  "jenis": "ketentuan_program | cara_pakai_aplikasi | penanganan_kendala",
  "sumber_nama": "Pusat Bantuan ASIK — Pencatatan Skrining PTM",
  "sumber_url": "https://...",
  "tanggal_ambil": "2026-08-19",
  "judul_bagian": "..."
}
```

Alasan: metadata inilah yang memungkinkan penyaringan jawaban per sasaran pengguna
dan penyertaan rujukan yang benar.

---

## 5. Struktur Proyek yang Diharapkan

```
rag-ckg/
├── CLAUDE.md
├── requirements.txt
├── data/
│   ├── raw/              # hasil unduhan mentah
│   └── processed/        # sudah bersih + bermetadata (jsonl)
├── src/
│   ├── collect_asik.py   # ambil dari llms.txt + versi .md
│   ├── collect_faq.py    # ambil FAQ SATUSEHAT
│   ├── collect_pdf.py    # ekstrak Juknis CKG
│   ├── chunk.py          # pemotongan + pemberian metadata
│   ├── index.py          # embedding + vector store
│   ├── retrieve.py       # pencarian + reranking
│   ├── answer.py         # susun jawaban + wajib sitasi + guardrails
│   └── app.py            # antarmuka chat sederhana
├── eval/
│   ├── pertanyaan_uji.jsonl   # pertanyaan + jawaban acuan + sumber acuan
│   └── evaluate.py
└── README.md
```

---

## 6. Rencana 4 Minggu

| Minggu | Fokus | Hasil |
|---|---|---|
| 1 | Korpus | Data terkumpul, bersih, bermetadata, tersimpan sebagai jsonl |
| 2 | Pipeline RAG | Chunking, embedding, vector store, retrieval berjalan |
| 3 | Jawaban + guardrails + antarmuka | Chat jalan, wajib sitasi, bisa menolak menjawab |
| 4 | Evaluasi + laporan | Angka evaluasi, perbaikan, dokumentasi, bahan presentasi |

**Minggu 4 jangan dikorbankan.** Untuk proyek RAG, evaluasi adalah nilai utamanya.
Chatbot yang "kelihatan jalan" saat didemokan tapi tidak pernah diukur itu tidak bisa
dipertanggungjawabkan.

---

## 7. Cara Evaluasi

Buat minimal **50 pertanyaan uji** berlabel, campuran: pertanyaan nakes, pertanyaan
masyarakat, dan **pertanyaan yang sengaja tidak ada jawabannya di korpus**.

Yang diukur:

1. **Ketepatan jawaban** — sesuai dokumen acuan atau tidak
2. **Ketepatan sumber** — dokumen yang dikutip benar atau tidak
3. **Kejujuran menolak** — untuk pertanyaan tanpa jawaban di korpus, sistem harus
   menolak menjawab. Ini metrik penting dan sering dilupakan.
4. **Kesesuaian bahasa** — jawaban untuk masyarakat harus lebih sederhana

---

## 8. Definisi Selesai (minimum)

- Korpus prioritas 1 terkumpul dan bermetadata
- Pipeline RAG berjalan dari pertanyaan sampai jawaban bersumber
- Antarmuka chat sederhana bisa dipakai
- Laporan evaluasi dengan angka, termasuk kelemahannya
- README yang menjelaskan cara menjalankan ulang

Fitur di luar ini (integrasi WhatsApp, deployment, dsb) **di luar lingkup 1 bulan.**

---

## 9. Belum Dikonfirmasi ke Pembimbing

Catat ini, jangan diasumsikan sendiri:

1. **ASIK sudah punya WhatsApp chatbot** untuk input data. Perlu ditegaskan bedanya:
   chatbot yang ada = memasukkan data (transaksional). Yang dibangun = menjawab
   pertanyaan cara mencatat (bantuan). Konfirmasi supaya tidak duplikat.
2. Alamat **produksi** Pusat Bantuan ASIK (yang ditemukan masih staging).
3. Apakah boleh mengambil seluruh isi pusat bantuan sebagai korpus.
4. Cakupan: nakes dulu, atau langsung dua sasaran sekaligus.
5. Apakah pertanyaan soal makna hasil pemeriksaan masuk cakupan.

---

## 10. Gaya Kerja yang Diharapkan

- Jelaskan alasan keputusan teknis, jangan hanya menuliskan kode. Saya harus bisa
  menjelaskan ulang setiap bagian ke pembimbing tanpa bantuan.
- Kalau ada pendekatan sederhana yang sudah cukup, katakan — jangan memaksakan
  yang rumit supaya terlihat canggih.
- Kalau hasil evaluasi jelek, laporkan apa adanya. Temuan jujur lebih bernilai
  daripada angka yang dipoles.
