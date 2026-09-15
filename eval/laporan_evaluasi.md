# Laporan Evaluasi — Asisten Tanya-Jawab CKG

Dibuat otomatis oleh `eval/evaluate.py` pada 2026-09-04.
Mesin jawaban: **llm** (`Qwen/Qwen2.5-3B-Instruct`)

## 1. Korpus

| | |
|---|---|
| Potongan | **533** |
| Dokumen | 355 |
| Per sumber | asik 178 · juknis 88 · faq_kyc 56 · faq_umum 50 · juknis_sekolah 48 · faq_fasyankes 40 · faq_sekolah 17 · faq_akun 15 · web 14 · faq_resume_medis 10 · faq_login 9 · faq_profil_terhubung 8 |
| Per sasaran | nakes 354 · masyarakat 159 · sekolah 68 |
| Per jenis | cara_pakai_aplikasi 249 · ketentuan_program 204 · penanganan_kendala 80 |
| Panjang potongan | median 631 · maks 1815 huruf |
| Model pencarian | `intfloat/multilingual-e5-base` |

## 2. Berkas uji

- Total **87** pertanyaan: 73 ada jawabannya, 14 sengaja tanpa jawaban di korpus
- Asal: 87 sintetis, **0 dari lapangan**

> **Peringatan.** Belum ada satu pun pertanyaan dari petugas sungguhan. Pertanyaan sintetis cenderung lebih rapi daripada pertanyaan nyata, sehingga angka di bawah ini berpotensi lebih baik daripada kenyataannya.

## 3. Mutu pencarian

Recall@k = dari k potongan teratas, apakah dokumen acuan ikut terambil. Ini batas atas mutu sistem: dokumen yang tidak pernah terambil mustahil jadi jawaban.

| Ukuran | Nilai |
|---|---|
| Recall@1 | 63% |
| Recall@3 | 82% |
| Recall@5 | 86% |
| Recall@10 | 89% |
| MRR | 0.729 |

**8 pertanyaan yang dokumennya tidak terambil sama sekali:**

- Bagaimana kader mendaftarkan diri di ASIK?
- Siapa saja yang bisa memakai aplikasi ASIK?
- Di mana bisa dapat informasi CKG yang terbaru?
- Bagaimana pelaksanaan CKG di sekolah?
- Pemeriksaan apa saja untuk siswa SD?
- Di mana saya bisa melihat hasil pemeriksaan saya?
- berikan panduan pencatatan CKG sekolah
- sebutkan syarat mengikuti cek kesehatan gratis

## 4. Bisakah penolakan memakai ambang skor?

- Skor **terendah** pertanyaan yang ada jawabannya: `0.0300`
- Skor **tertinggi** pertanyaan yang tidak ada jawabannya: `0.0328`

Kedua rentang **bertumpang tindih**, jadi aturan "kalau skor < X maka tolak" pasti salah — entah menolak pertanyaan sah, atau menjawab yang seharusnya ditolak. Karena itu penolakan ditangani dengan cara lain di `answer.py`.

## 5. Guardrail (dikerjakan kode, bukan LLM)

| Uji | Hasil |
|---|---|
| Pertanyaan klinis tertahan | 17/17 |
| Pertanyaan pencatatan tetap lolos | 20/20 |
| Tautan karangan LLM dibuang | 3/3 |


## 6. Mutu jawaban

| Ukuran | Nilai |
|---|---|
| Sumber pertama sudah tepat | 41/73 (56%) |
| Sumber acuan ikut dikutip | 46/73 (63%) |
| Ditolak padahal ada jawabannya | 9 |
| **Menolak dengan benar** | 14/14 (100%) |
| **Mengarang** | 0 |
| Kata per kalimat (masyarakat) | 16.4 |
| Istilah teknis (masyarakat) | 0.3 |

## 7. Keterbatasan yang diketahui

Bagian ini sengaja selalu ada. Laporan evaluasi tanpa daftar kelemahan tidak bisa dipertanggungjawabkan.

1. **Seluruh pertanyaan uji masih sintetis.** Belum diuji dengan pertanyaan nyata petugas, yang biasanya lebih singkat, penuh singkatan, dan salah ketik.
2. **Ketepatan ISI jawaban belum diukur.** Kolom `jawaban_acuan` masih kosong; mengisinya butuh manusia yang membaca dokumennya. Yang terukur di atas baru ketepatan SUMBER.
3. **Satu dokumen acuan per pertanyaan.** Kalau beberapa dokumen sama-sama menjawab, jawaban benar dari dokumen lain tetap dihitung meleset.
4. **Label `jenis` masih heuristik**, ditandai `jenis_metode` di tiap potongan.
5. **178 potongan berasal dari lingkungan staging** (Pusat Bantuan ASIK), bukan produksi. Alamat produksi belum dikonfirmasi.
