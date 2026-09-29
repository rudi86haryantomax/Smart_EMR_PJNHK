# Daftar Perubahan

Untuk memperbarui salinan di GitHub tanpa mengganti semuanya.

---

## Putaran terakhir — integrasi perbaikan Anda + bug halaman dokter

### ⚠️ WAJIB diperbarui (4 berkas)

| Berkas | Yang berubah |
|---|---|
| **`pages/tatalaksana/__init__.py`** | **Perbaikan utama.** Bug session_state pada rekam suara dokter, rekam satu tombol, tombol terjemahan |
| **`app.py`** | Hanya di `_reset_profesi()`: kunci `dok_temuan` → `dok_data` + `dok_versi` |
| `tests/test_form_state.py` | Test baru untuk `reset_asesmen()` dan halaman dokter |
| `tests/test_ui_intervensi.py` | Test disesuaikan: centang di-set `False`, bukan dihapus |

### ✅ Tidak perlu diubah

`pages/asesmen/__init__.py` — **versi Anda dipakai apa adanya**, tidak
saya sentuh sama sekali.

### Tambahan opsional

| Berkas | |
|---|---|
| `tools/cek_versi.py` | Alat verifikasi — lihat bagian bawah |
| `DEPLOY.md` | Hanya angka jumlah test |
| `PERUBAHAN.md` | Berkas ini |

### Perubahan persis pada `app.py`

Satu blok saja, di dalam `_reset_profesi()`:

```python
# SEBELUM
for kunci in ("profesi", "halaman", "dok_ppk_dipilih", "dok_temuan",
              "asesmen_tersimpan", "riwayat_dibuka"):

# SESUDAH
for kunci in ("profesi", "halaman", "dok_ppk_dipilih",
              "dok_data", "dok_temuan", "dok_versi",
              "asesmen_tersimpan", "riwayat_dibuka"):
```

Sisanya — kredit developer, link Saran & Masukan, `_mulai_asesmen_baru()` —
adalah milik Anda dan saya pertahankan utuh.

---

## Kalau repo Anda tertinggal lebih jauh

Beberapa putaran sebelumnya juga mengubah berkas berikut. Periksa apakah
sudah ada di repo Anda:

### Indikator luaran SLKI

| Berkas | Status |
|---|---|
| `data/indikator_slki.json` | **BARU** — 41 luaran, 225 indikator |
| `repositories/sdki_repository.py` | + method `indikator()`, `evaluasi_default()`, `arah_luaran()` |
| `services/diagnosis_service.py` | + indikator ke `rakit_tabel()` |
| `services/export_service.py` | Luaran + indikator jadi satu kalimat askep |
| `components/tabel_asuhan.py` | Tampilan menyatu, bukan tabel terpisah |
| `tools/validasi_indikator.py` | **BARU** |
| `tests/test_indikator.py` | **BARU** |

### PPK Kardiovaskular (63 → 65)

| Berkas | Status |
|---|---|
| `data/ppk_kardiovaskular.json` | 65 PPK |
| `services/ppk_service.py` | `KODE_KRITIS` jadi 14 kondisi |
| `tests/test_ppk.py` | |
| `CATATAN_PPK.md` | |

---

## Cara memastikan tanpa menebak

Daftar manual selalu berisiko: berkas yang lupa disebut tidak akan
ketahuan sampai aplikasinya bermasalah. Jalankan ini di kedua tempat lalu
bandingkan:

```bash
python tools/cek_versi.py
```

Keluarannya berupa sidik jari tiap berkas:

```
sidik           baris  berkas
cb3e274f95ce      273  app.py
7a1f9e2b4c88      356  pages/tatalaksana/__init__.py
...
57 berkas, 18947 baris
```

Baris yang **sidiknya berbeda** adalah berkas yang perlu diperbarui.
Berkas yang **tidak muncul** di salinan Anda berarti belum ada.

Untuk membandingkan langsung:

```bash
python tools/cek_versi.py > /tmp/versi-baru.txt
# di repo Anda:
python tools/cek_versi.py > /tmp/versi-lama.txt
diff /tmp/versi-lama.txt /tmp/versi-baru.txt
```

---

## Setelah memperbarui

```bash
cd tests
python test_form_state.py    # bug session_state kedua halaman
python test_ui_intervensi.py # centang intervensi
```

Atau seluruhnya:

```bash
for f in tests/test_*.py; do python "$f" | tail -1; done
```

Semuanya harus **0 FAIL**. Total saat ini: **416 assertion**.
