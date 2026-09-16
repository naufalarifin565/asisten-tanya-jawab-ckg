"""Menyusun berkas pertanyaan uji, lalu MEMVERIFIKASI dokumen acuannya.

    python eval/buat_pertanyaan_uji.py

Kenapa lewat skrip, bukan mengetik JSONL langsung: dokumen acuan ditulis sebagai
awalan id potongan, dan salah ketik satu huruf saja membuat pertanyaan itu
selamanya dihitung gagal padahal sistemnya benar. Skrip ini menolak menulis
kalau ada acuan yang tidak ditemukan di korpus.

=== ASAL PERTANYAAN: "sintetis" vs "lapangan" ===

SELURUH pertanyaan di berkas ini masih "sintetis" -- disusun dengan membaca
korpus, bukan dikumpulkan dari petugas yang benar-benar memakai ASIK. Itu
kelemahan yang harus diakui terus terang, bukan disembunyikan:

  * Pertanyaan sintetis cenderung terlalu rapi dan sopan
    ("Bagaimana prosedur pendaftaran administrator User Management?")
  * Pertanyaan lapangan berantakan dan penuh singkatan
    ("admin usermanagement gmn daftarnya", "kok bnba ga bisa didownload")

Sistem bisa terlihat bagus pada yang pertama dan jeblok pada yang kedua. Karena
itu setiap baris membawa penanda `asal_pertanyaan`, dan laporan evaluasi
sebaiknya memisahkan keduanya -- bukan menggabungkannya jadi satu angka yang
menyesatkan.

CARA MENAMBAH PERTANYAAN LAPANGAN: tulis di daftar LAPANGAN di bawah, dengan
awalan id dokumen acuannya. Cari dokumen yang tepat dengan:
    python src/retrieve.py "pertanyaan anda" --sasaran nakes

=== KOLOM jawaban_acuan SENGAJA KOSONG ===

Untuk menilai KETEPATAN ISI JAWABAN, jawaban acuan harus
ditulis manusia yang membaca dokumennya. Mengarangnya di sini sama saja menilai
sistem dengan kunci jawaban buatan sendiri -- angkanya akan bagus dan tidak
berarti apa-apa.
"""

from __future__ import annotations

import sys
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AKAR / "src"))

from utils import DATA_OLAHAN, baca_jsonl, lapor, tulis_jsonl  # noqa: E402

# (pertanyaan, sasaran, awalan id dokumen acuan)
# Pertanyaan sengaja DIPARAFRASE, tidak menyalin judul dokumennya, supaya yang
# diuji memang kemampuan mencari -- bukan kemampuan mencocokkan huruf.
SINTETIS = [
    # --- pencatatan CKG / PTM (inti proyek) ---
    ("Bagaimana cara mencatat skrining PTM?", "nakes",
     "asik:ptm-pencatatan-skrining-ptm:"),
    ("Peserta skrining tidak bawa KTP, bagaimana?", "nakes",
     "asik:ptm-pencatatan-skrining-ptm-peserta-skrining-ptm-tanpa-nik-atau-ktp:"),
    ("Saya salah memasukkan data skrining, bagaimana memperbaikinya?", "nakes",
     "asik:ptm-pencatatan-skrining-ptm-kesalahan-memasukkan-data"),
    ("Apakah ASIK sudah terhubung dengan SIPTM?", "nakes",
     "asik:ptm-integrasi-dengan-siptm"),
    ("Aplikasi ASIK error, harus bagaimana?", "nakes",
     "asik:ptm-aplikasi-error"),
    ("Cara mengunduh data per nama per alamat untuk PTM", "nakes",
     "asik:ptm-dashboard-ptm-unduh-data-by-name-by-address-bnba-ptm:"),
    ("Angka capaian PTM tidak tampil di dashboard", "nakes",
     "asik:ptm-dashboard-ptm-data-capaian-ptm-tidak-muncul:"),

    # --- akun, PIN, aktivasi ---
    ("Saya lupa PIN aplikasi ASIK", "nakes",
     "asik:akun-pin-asik-lupa-pin-asik:"),
    ("Kode OTP tidak masuk ke ponsel saya", "nakes",
     "asik:akun-pin-asik-tidak-menerima-otp-di-mobile:"),
    ("Bagaimana kader mendaftarkan diri di ASIK?", "nakes",
     "asik:akun-cara-mendaftar-asik-untuk-kader-dan-nakes:"),
    ("Siapa yang berwenang mengaktifkan akun petugas?", "nakes",
     "asik:akun-cara-aktivasi-pengguna-mobile-siapa-yang-melakukan-aktivasi-akun:"),
    ("Di mana saya bisa mengunduh aplikasi ASIK?", "nakes",
     "asik:akun-cara-unduh-aplikasi-asik"),
    ("Apakah perlu STR untuk mendaftar di ASIK?", "nakes",
     "asik:akun-cara-mendaftar-asik-untuk-kader-dan-nakes-penggu"),
    ("Siapa saja yang bisa memakai aplikasi ASIK?", "nakes",
     "asik:akun-pengguna-asik"),

    # --- user management ---
    ("Apa itu User Management di ASIK?", "nakes",
     "asik:user-management-apa-itu-user-management:"),
    ("Apa bedanya Superadmin dengan Admin?", "nakes",
     "asik:user-management-apa-itu-user-management-apa-perbedaan"),
    ("Bagaimana mendaftarkan administrator User Management?", "nakes",
     "asik:user-management-cara-pendaftaran-administrator-user-m"),

    # --- data individu ---
    ("Bagaimana input data kalau jaringan internet jelek?", "nakes",
     "asik:data-individu-penginputan-data-di-daerah-dengan-sinyal-buruk:"),
    ("Apa bedanya sasaran dengan pengunjung?", "nakes",
     "asik:data-individu-perbedaan-sasaran-dan-pengunjung:"),
    ("Bagaimana cara mencatat data individu?", "nakes",
     "asik:data-individu-pencatatan-data-individu"),
    ("Bagaimana kalau sasaran sudah pindah tempat tinggal?", "nakes",
     "asik:data-individu-kondisi-data-sasaran-pindah-tempat-ting"),

    # --- program lain di ASIK (imunisasi, ibu hamil, bayi balita, remaja) ---
    ("Apa syarat Imunisasi Dasar Lengkap?", "nakes",
     "asik:imunisasi-syarat-imunisasi-syarat-dari-imunisasi-dasa"),
    ("Bagaimana persentase imunisasi dihitung?", "nakes",
     "asik:imunisasi-perhitungan-persentase-imunisasi"),
    ("Bagaimana mencatat pemeriksaan ANC di puskesmas?", "nakes",
     "asik:ibu-hamil-pemeriksaan-anc-di-puskesmas"),
    ("Layanan apa saja untuk ibu hamil di ASIK?", "nakes",
     "asik:ibu-hamil-layanan-ibu-hamil:"),
    ("Apa itu ASIK Bayi Balita?", "nakes",
     "asik:bayi-balita-tentang-asik-bayi-balita"),
    ("Kegiatan apa saja untuk remaja dan usia sekolah di ASIK?", "nakes",
     "asik:remaja-layanan-kegiatan-remaja-dan-usia-sekolah"),
    ("Bagaimana penginputan data usia sekolah dan remaja?", "nakes",
     "asik:remaja-penginputan-asik-usia-sekolah-dan-remaja:"),

    # --- pengenalan aplikasi ---
    ("Apa itu aplikasi ASIK?", "nakes",
     "asik:informasi-umum-apa-itu-asik"),
    ("ASIK Mobile itu untuk apa?", "nakes",
     "asik:informasi-umum-asik-mobile:"),

    # --- FAQ Fasyankes ---
    ("Apa itu Sarana Binaan Puskesmas?", "nakes",
     "faq_fasyankes:ac177c94-5452-42fa-81f0-e7428cdfc527"),
    ("Bagaimana menambahkan sekolah sebagai Sarana Binaan Puskesmas?", "nakes",
     "faq_fasyankes:3d11adf7-d50b-489f-aa68-754bc5c51042"),
    ("Bagaimana melihat rapor kesehatan lewat ASIK Website?", "nakes",
     "faq_fasyankes:732f4f76-e711-4c29-b520-2e64149ce314"),
    ("Bagaimana kalau anak tidak punya NISN?", "nakes",
     "faq_fasyankes:7641c41e-d145-4bb8-8503-9f9da053e894"),

    # --- Juknis CKG (ketentuan program) ---
    ("Apa tujuan program Cek Kesehatan Gratis?", "nakes",
     "juknis:bab-ii:a-tujuan-dan-sasaran-ckg:"),
    ("Kapan dan di mana CKG dilaksanakan?", "nakes",
     "juknis:bab-ii:b-waktu-dan-tempat-pelaksanaan:"),
    ("Apa yang harus disiapkan puskesmas sebelum CKG?", "nakes",
     "juknis:bab-iii:d-persiapan-di-tingkat-puskesmas:"),
    ("Bagaimana data CKG diintegrasikan ke SATUSEHAT?", "nakes",
     "juknis:bab-vi:a-integrasi-data-dan-informasi-pelaksanaan-c"),
    ("Bagaimana perlindungan data pribadi peserta CKG?", "nakes",
     "juknis:bab-vi:b-perlindungan-data-pribadi-dalam-pelaksanaa"),
    ("Bagaimana pemantauan pelaksanaan CKG dilakukan?", "nakes",
     "juknis:bab-viii:a-pemantauan:"),
    ("Siapa saja sasaran edukasi CKG?", "nakes",
     "juknis:bab-vii:b-sasaran-edukasi-ckg:"),

    # --- masyarakat ---
    ("Apa saja syarat untuk ikut cek kesehatan gratis?", "masyarakat",
     "faq_umum:a1d7264d-ca61-4dd7-a7fd-a7caca4a982b:"),
    ("Pemeriksaan apa saja yang akan saya dapatkan?", "masyarakat",
     "faq_umum:d3f16ffc-95d6-4e4a-87c2-77a15b1d2ffb:"),
    ("Bagaimana cara mendaftar cek kesehatan gratis?", "masyarakat",
     "faq_umum:2451bc61-8e7f-4dd1-9680-32ce8ff07e27:"),
    ("Siapa yang boleh ikut program ini?", "masyarakat",
     "faq_umum:a621ead6-fd18-4c95-a631-91ffeb71b2c4:"),
    ("Hasil pemeriksaan saya belum muncul di aplikasi", "masyarakat",
     "faq_umum:ad081d61-c07f-40c5-b3c1-d534e0e2473a:"),
    ("Sejak kapan program ini mulai jalan?", "masyarakat",
     "faq_umum:64b81427-de02-4204-b0ee-198d2bfc8553:"),
    ("Di mana bisa dapat informasi CKG yang terbaru?", "masyarakat",
     "faq_umum:14c7fff3-240d-43c4-8b3a-0e3520fbcb36"),
    ("Fitur cek kesehatan gratis tidak muncul di aplikasi saya", "masyarakat",
     "faq_umum:16ecb246-60ec-4b4d-abe7-5164fb0390c7"),
    ("Saya tinggal tidak sesuai alamat KTP, bisa ikut CKG?", "masyarakat",
     "faq_umum:2a242863-e3ef-4e8e-b44e-8a510b772591"),

    # --- sekolah ---
    ("Kapan CKG Sekolah mulai dilaksanakan?", "sekolah",
     "faq_sekolah:3f612f4f-c8a1-4908-866b-3da29164301a"),
    ("Ada kesalahan saat mendaftarkan sekolah, bagaimana?", "sekolah",
     "faq_sekolah:11f18843-ab90-4bd0-a0ef-236b1327fe7f"),
    # Acuan pertanyaan ini DIUBAH, dan alasannya perlu dicatat supaya tidak
    # terlihat seperti angka yang dirapikan supaya bagus:
    #
    #   Semula menunjuk halaman kampanye Ayo Sehat. Setelah Juknis CKG Sekolah
    #   (Kepmenkes 770/2025) masuk korpus, jawaban yang benar untuk pertanyaan
    #   ini adalah dokumen yang MENGATUR pelaksanaannya, bukan halaman ajakan.
    #   Membiarkan acuan lama berarti menghukum sistem karena mengutip sumber
    #   yang lebih tepat.
    #
    #   Terpisah dari itu, id lamanya memang sudah putus: perbaikan tabrakan
    #   nama berkas di collect_web.py mengubah slug halaman itu menjadi
    #   "agenda-kegiatan-...". Jadi acuan ini rusak karena dua sebab sekaligus.
    ("Bagaimana pelaksanaan CKG di sekolah?", "sekolah",
     "juknis_sekolah:bab-iii:c-pelaksanaan-ckg-sekolah:"),

    # === Pertanyaan untuk sumber yang baru masuk ===
    #
    # Tanpa bagian ini, penambahan korpus MUSTAHIL terlihat bagus: dokumen baru
    # hanya menambah pesaing di setiap pencarian, tanpa satu pun soal yang bisa
    # dimenangkannya. Penurunan angka yang terukur setelah penambahan sebagian
    # besar berasal dari ketimpangan itu, bukan dari korpus yang memburuk.

    # --- Juknis CKG Sekolah (Kepmenkes 770/2025), sisi sekolah ---
    ("Pemeriksaan apa saja untuk siswa SD?", "sekolah",
     "juknis_sekolah:bab-ii:c-jenis-pemeriksaan:"),
    ("Kapan dan di mana CKG sekolah dilaksanakan?", "sekolah",
     "juknis_sekolah:bab-ii:b-waktu-dan-tempat-pelaksanaan:"),
    ("Siapa saja sasaran CKG Sekolah?", "sekolah",
     "juknis_sekolah:bab-ii:a-tujuan-dan-sasaran-ckg-sekolah:"),
    ("Apa tugas pihak sekolah dalam pelaksanaan CKG?", "sekolah",
     "juknis_sekolah:bab-iii:b-pembagian-peran:"),
    ("Bagaimana cara memeriksa anemia pada peserta didik?", "sekolah",
     "juknis_sekolah:bab-ii:d-metode-pemeriksaan:"),

    # --- Juknis CKG Sekolah, sisi petugas ---
    ("Bagaimana pencatatan dan pelaporan CKG sekolah?", "nakes",
     "juknis_sekolah:bab-v:utama:"),
    ("Dari mana pendanaan CKG sekolah berasal?", "nakes",
     "juknis_sekolah:bab-iv:utama:"),
    ("Apa peran puskesmas dalam CKG sekolah?", "nakes",
     "juknis_sekolah:bab-iii:b-pembagian-peran:"),

    # --- Resume Medis: ujung alur CKG, tempat hasilnya dilihat ---
    ("Di mana saya bisa melihat hasil pemeriksaan saya?", "masyarakat",
     "faq_resume_medis:337f4c56-c15b-4033-af49-2431415ad55b:"),
    ("Apakah warga negara asing bisa mengakses resume medis?", "masyarakat",
     "faq_resume_medis:b105a7fc-5865-4fe3-ab62-670a19527a4f:"),
    ("Saya sudah buka resume medis tapi datanya kosong", "masyarakat",
     "faq_resume_medis:eb321efe-760e-4d67-8d52-b524f80a08a1:"),
    ("Bisakah saya melihat rekam medis anak saya?", "masyarakat",
     "faq_resume_medis:3dda922c-3643-4c9b-b069-4a5d6aa92271:"),

    # --- Halaman Ayo Sehat yang baru ditambahkan ---
    ("Pemeriksaan CKG untuk bayi baru lahir apa saja?", "masyarakat",
     "web:cek-kesehatan-gratis-md:3"),
    ("Kenapa cek kesehatan perlu dilakukan?", "masyarakat",
     "web:cek-kesehatan-gratis-md:1"),

    # === Bentuk PERMINTAAN, bukan kalimat tanya ===
    #
    # Seluruh pertanyaan uji di atas berbentuk kalimat tanya, padahal orang
    # sering mengetik permintaan: "berikan panduan ...", "sebutkan ...".
    # Bentuk itu ternyata memicu PENOLAKAN KELIRU -- isi dan konteks sama,
    # hanya bentuk kalimatnya berbeda:
    #
    #     "berikan panduan dan pedoman pencatatan data pasien"  -> DITOLAK
    #     "Bagaimana panduan pencatatan data pasien?"           -> dijawab
    #
    # Ditemukan dari pemakaian nyata, bukan dari berkas ini. Ditambahkan
    # supaya kelas kesalahan ini punya angka: tanpa diukur, perbaikan apa pun
    # cuma bisa ditebak. Tiga cara perbaikan sudah dicoba dan semuanya gagal --
    # lihat catatan panjang di src/answer.py.
    ("berikan panduan dan pedoman pencatatan data pasien", "nakes",
     "asik:data-individu-panduan-penggunaan-data-individu"),
    ("sebutkan langkah pencatatan skrining PTM", "nakes",
     "asik:ptm-pencatatan-skrining-ptm:"),
    ("pedoman input data individu", "nakes",
     "asik:data-individu-panduan-penggunaan-data-individu"),
    ("berikan panduan pencatatan CKG sekolah", "sekolah",
     "juknis_sekolah:bab-v:utama:"),
    ("berikan cara mendaftar CKG", "masyarakat",
     "faq_umum:2451bc61-8e7f-4dd1-9680-32ce8ff07e27:"),
    ("sebutkan syarat mengikuti cek kesehatan gratis", "masyarakat",
     "faq_umum:a1d7264d-ca61-4dd7-a7fd-a7caca4a982b:"),
]

# Pertanyaan nyata dari lapangan. KOSONG, dan inilah yang paling dibutuhkan.
# Formatnya sama: (pertanyaan, sasaran, awalan id dokumen acuan).
LAPANGAN: list[tuple[str, str, str]] = [
]

# Pertanyaan yang JAWABANNYA MEMANG TIDAK ADA di korpus. Sistem wajib menolak
# menjawab, bukan mengarang (prinsip wajib no. 2-3).
#
# Sengaja dibuat beragam: ada yang klinis (harus ditahan penapis kode), ada yang
# sekadar di luar cakupan (harus ditolak lewat jalur lain). Kalau semuanya
# klinis, angka "kejujuran menolak" hanya mengukur satu mekanisme saja.
TIDAK_TERJAWAB = [
    ("Berapa dosis obat hipertensi untuk lansia?", "masyarakat", "klinis: pengobatan"),
    ("Gula darah saya 250, artinya apa?", "masyarakat", "klinis: menafsirkan hasil"),
    ("Apakah saya perlu operasi?", "masyarakat", "klinis: tindakan medis"),
    ("Kolesterol saya 280, berbahaya tidak?", "masyarakat", "klinis: menafsirkan hasil"),
    ("Berapa gaji kader posyandu?", "nakes", "di luar cakupan korpus"),
    ("Bagaimana cara klaim BPJS Kesehatan?", "masyarakat", "program lain, bukan CKG"),
    ("Berapa jumlah puskesmas di Indonesia?", "nakes", "di luar cakupan korpus"),
    ("Siapa Menteri Kesehatan saat ini?", "masyarakat", "di luar cakupan korpus"),
    ("Bagaimana cara mengganti oli motor?", "masyarakat", "sama sekali di luar topik"),
    ("Apa efek samping vaksin COVID-19?", "masyarakat", "klinis dan di luar cakupan"),

    # --- Bentuk PERMINTAAN yang jawabannya juga tidak ada ---
    #
    # Pasangan pengimbang untuk enam kalimat permintaan yang ditambahkan di
    # SINTETIS. Tanpa ini, setiap usaha mengurangi penolakan berlebihan bisa
    # terlihat berhasil hanya karena sistemnya jadi lebih mudah menjawab
    # apa saja. Sudah terbukti perlu: satu percobaan perbaikan menaikkan
    # angka "terjawab" dari 3/6 ke 5/6 sambil MENGARANG jawaban untuk
    # "berikan daftar puskesmas ... di Bandung" -- dan itu hanya ketahuan
    # karena kelompok pembanding ini ada.
    ("berikan daftar puskesmas yang melayani CKG di Bandung", "masyarakat",
     "di luar cakupan korpus"),
    ("sebutkan tarif klaim BPJS untuk tiap pemeriksaan CKG", "nakes",
     "di luar cakupan korpus"),
    ("tuliskan jadwal pelatihan ASIK tahun depan", "nakes",
     "di luar cakupan korpus"),
    ("berikan nomor telepon dinas pendidikan kota Bekasi", "sekolah",
     "di luar cakupan korpus"),
]


def main() -> int:
    korpus = baca_jsonl(DATA_OLAHAN / "korpus.jsonl")
    id_ada = [x["id"] for x in korpus]
    peta_url = {x["id"]: x["sumber_url"] for x in korpus}

    baris, hilang = [], []
    for asal, daftar in (("sintetis", SINTETIS), ("lapangan", LAPANGAN)):
        for pertanyaan, sasaran, acuan in daftar:
            cocok = [i for i in id_ada if i.startswith(acuan)]
            if not cocok:
                hilang.append((asal, pertanyaan, acuan))
                continue
            baris.append({
                "pertanyaan": pertanyaan,
                "sasaran": sasaran,
                "terjawab": True,
                "asal_pertanyaan": asal,
                "dokumen_acuan": acuan,
                "potongan_acuan": cocok,
                "sumber_acuan": peta_url[cocok[0]],
                "jawaban_acuan": "",  # diisi manusia, lihat penjelasan di atas
            })

    if hilang:
        lapor("GAGAL: dokumen acuan berikut tidak ada di korpus:")
        for asal, pertanyaan, acuan in hilang:
            lapor(f"  [{asal}] {pertanyaan[:52]}")
            lapor(f"          acuan: {acuan}")
        lapor("\nPerbaiki dulu daftarnya. Cari dokumen yang benar dengan:")
        lapor('  python src/retrieve.py "pertanyaan anda" --sasaran nakes')
        return 1

    for pertanyaan, sasaran, alasan in TIDAK_TERJAWAB:
        baris.append({
            "pertanyaan": pertanyaan,
            "sasaran": sasaran,
            "terjawab": False,
            "asal_pertanyaan": "sintetis",
            "dokumen_acuan": None,
            "potongan_acuan": [],
            "sumber_acuan": None,
            "jawaban_acuan": "",
            "alasan_tidak_terjawab": alasan,
        })

    keluaran = Path(__file__).resolve().parent / "pertanyaan_uji.jsonl"
    n = tulis_jsonl(keluaran, baris)

    n_ada = sum(1 for b in baris if b["terjawab"])
    n_lapangan = sum(1 for b in baris if b["asal_pertanyaan"] == "lapangan")
    lapor(f"{n} pertanyaan -> {keluaran}")
    lapor(f"  ada jawabannya  : {n_ada}")
    lapor(f"  tanpa jawaban   : {n - n_ada}")
    lapor(f"  asal sintetis   : {n - n_lapangan}")
    lapor(f"  asal lapangan   : {n_lapangan}   <- yang paling dibutuhkan")
    lapor("\nSeluruh dokumen acuan diverifikasi ada di korpus.")
    if n < 50:
        lapor(f"CATATAN: rencana evaluasi meminta minimal 50 pertanyaan, sekarang {n}.")
    if n_lapangan == 0:
        lapor("CATATAN: belum ada satu pun pertanyaan dari lapangan. Angka evaluasi")
        lapor("         yang dihasilkan belum tentu bertahan pada pertanyaan nyata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
