# Panduan Deploy

## Kesimpulan: siap di-deploy

Aplikasi ini ditujukan untuk **pembelajaran dan alat bantu**, bukan
pemakaian dengan data pasien. Untuk tujuan itu, **silakan langsung
deploy ke Streamlit Community Cloud** — tidak ada penghalang.

| Aspek | Status |
|---|---|
| Aplikasi start tanpa error | ✅ terverifikasi (HTTP 200, log bersih) |
| `requirements.txt` minimal & benar | ✅ 3 paket |
| Tidak ada path absolut / rahasia ter-commit | ✅ |
| `.gitignore` melindungi `secrets.toml` & `.env` | ✅ |
| 398 test lolos | ✅ |
| **Aman dipakai banyak orang sekaligus** | ✅ diuji 30 pengguna serentak |
| Penyimpanan sementara | ℹ️ wajar untuk alat pembelajaran |
| Tanpa login | ℹ️ wajar untuk alat pembelajaran |

Dua hal terakhir tetap perlu diketahui, tapi untuk konteks pembelajaran
keduanya bukan penghalang — hanya keterbatasan yang perlu disampaikan ke
pengguna. Aplikasi sudah menampilkannya sendiri di layar.

---

## Pemakaian banyak orang sekaligus

Sudah diuji dan aman. **30 pengguna melakukan 90 operasi serentak
(campuran simpan dan baca): 0 gagal, selesai dalam 0,14 detik.**

Ini sempat bermasalah. Versi awal punya race condition: pembuatan nomor
asesmen membaca nomor terakhir lalu menyisipkan baris baru tanpa kunci
tulis, sehingga dua orang yang menyimpan bersamaan menghasilkan nomor
yang sama. Diuji saat itu, **8 dari 10 penyimpanan serentak gagal** —
data tidak rusak, tapi delapan orang kehilangan hasil kerjanya.

Diperbaiki di `database/connection.py` dengan tiga hal:

| Pengaturan | Gunanya |
|---|---|
| `journal_mode = WAL` | Pembaca tidak diblokir saat ada yang menulis |
| `busy_timeout` | Menunggu giliran alih-alih langsung gagal |
| `BEGIN IMMEDIATE` | Kunci tulis diambil sejak awal transaksi — ini yang menghapus race condition |

Operasi yang hanya membaca memakai `baca_saja()` agar tidak ikut
mengantre di kunci tulis.

`tests/test_konkurensi.py` menjaga agar bug ini tidak kembali. Bug
konkurensi jarang terlihat saat pengembangan (satu orang, satu klik) dan
baru muncul justru ketika aplikasi ramai dipakai — persis saat paling
merugikan.

**Batas wajar:** SQLite cocok sampai puluhan pengguna aktif bersamaan —
lebih dari cukup untuk satu kelas atau pelatihan. Bila suatu saat dipakai
ratusan orang sekaligus, barulah perlu pindah ke Postgres.

---

## Dua keterbatasan yang perlu diketahui pengguna

### Riwayat bersifat sementara

Filesystem Streamlit Cloud kembali ke keadaan repositori setiap kali
aplikasi di-deploy ulang, restart, atau bangun dari mode tidur. Riwayat
asesmen akan kosong lagi.

Untuk pembelajaran ini umumnya tidak masalah — hasil tiap latihan bisa
diunduh sebagai Markdown atau CSV. Aplikasi menampilkan pengingatnya di
halaman Riwayat.

### Tidak ada login

Semua pengguna berbagi satu daftar riwayat. Pada kelas atau pelatihan,
ini justru kadang berguna — peserta dapat melihat hasil kerja satu sama
lain sebagai bahan diskusi.

Yang tetap perlu ditekankan: **gunakan data latihan, bukan data pasien
sungguhan.** Aplikasi menampilkan pesan ini di layar pertama.

---

## Bila suatu saat dipakai dengan data nyata

Aplikasi mengenali dua mode. Bawaannya `pembelajaran`, dengan pesan
bernada informasi biasa. Untuk pemakaian klinis:

```bash
export ASUHAN_MODE=klinis
```

Mode klinis menaikkan pesan menjadi peringatan yang tegas. Tapi mode itu
sendiri bukan pengaman — untuk data pasien sungguhan, yang diperlukan
adalah penyimpanan permanen dan pembatasan akses:

```bash
export ASUHAN_DB_PATH=/data/asuhan.db      # volume permanen
export ASUHAN_PENYIMPANAN=permanen
export ASUHAN_AKSES=privat                 # bila akses sudah dibatasi
```

Untuk itu, hosting internal di jaringan rumah sakit adalah pilihan yang
paling sesuai — data tidak keluar, dan pembatasan akses mengikuti
kebijakan yang sudah berlaku.

---

## Langkah deploy ke Streamlit Community Cloud

### 1. Siapkan repositori GitHub

```bash
git init
git add .
git commit -m "Aplikasi asuhan keperawatan SDKI/SLKI/SIKI"
git branch -M main
git remote add origin https://github.com/<akun>/<repo>.git
git push -u origin main
```

Pastikan `.streamlit/secrets.toml` **tidak** ikut ter-commit:

```bash
git status --ignored | grep secrets    # harus muncul di daftar ignored
```

### 2. Buat aplikasi di Streamlit Cloud

1. Buka https://share.streamlit.io
2. **New app** → pilih repositori dan branch `main`
3. **Main file path**: `app.py`
4. **Deploy**

### 3. Isi secrets

Di dasbor aplikasi → **Settings → Secrets**, tempelkan:

```toml
GOOGLE_STT_API_KEY = "isi-api-key-anda"
```

Kosongkan saja bila belum memakai voice-to-text — aplikasi tetap berjalan
dengan input teks, dan fitur suara otomatis dinonaktifkan.

### 4. Verifikasi setelah deploy

- Halaman pertama memunculkan pemilihan profesi
- Peringatan penyimpanan sementara & akses publik muncul (memang benar)
- Alur perawat: isi S/O → usulan diagnosis muncul
- Alur dokter: isi temuan → usulan PPK muncul
- Sidebar menampilkan jumlah master data (55 diagnosis / 15 PPK)

---

## Berkas yang berperan saat deploy

| Berkas | Kegunaan |
|---|---|
| `requirements.txt` | Dependensi aplikasi — **sengaja minimal** (3 paket) |
| `requirements-tools.txt` | Dependensi alat admin (Excel/PDF), **tidak** dipasang di server |
| `runtime.txt` | Versi Python (3.11) |
| `.streamlit/config.toml` | Tema dan batas unggahan |
| `.streamlit/secrets.toml` | Rahasia — **jangan di-commit** |

`openpyxl` dan `pdfplumber` sengaja dipisah ke `requirements-tools.txt`.
Keduanya hanya dipakai alat konversi yang dijalankan di komputer lokal;
memasangnya di server memperlambat build tanpa manfaat — `pdfplumber`
menarik pdfminer.six, pillow, dan cryptography sekaligus.

---

## Alternatif hosting

| Platform | Penyimpanan permanen | Bisa privat | Catatan |
|---|---|---|---|
| Streamlit Community Cloud | ❌ | Berbayar | Paling mudah, cocok untuk uji coba |
| Railway / Render | ✅ (volume) | ✅ | Perlu sedikit konfigurasi |
| Fly.io | ✅ (volume) | ✅ | Cocok bila ingin dekat dengan pengguna |
| VPS / server rumah sakit | ✅ | ✅ | Paling sesuai untuk data klinis nyata |

Untuk pemakaian klinis sungguhan di RSJPDHK, hosting internal adalah yang
paling tepat — data tidak keluar jaringan rumah sakit, dan pembatasan
akses mengikuti kebijakan yang sudah ada.
