"""
core/lingkungan.py
==========================================
Deteksi lingkungan tempat aplikasi berjalan, dan peringatan yang menyertainya.

Kenapa ini perlu ada
--------------------
Aplikasi ini menyimpan catatan asuhan ke berkas SQLite di dalam
foldernya sendiri. Di komputer lokal itu aman. Di platform hosting
seperti Streamlit Community Cloud, tidak — dan bedanya tidak terlihat
sama sekali dari layar.

Dua kenyataan yang mudah luput:

1. **Filesystem hosting bersifat sementara.** Setiap redeploy, restart,
   atau bangun dari mode tidur, isi folder kembali ke keadaan di
   repositori git. Karena berkas `.db` sengaja tidak ikut di-commit
   (memang tidak boleh), seluruh asesmen yang tersimpan HILANG — tanpa
   pesan, tanpa jejak.

2. **Aplikasi ini tidak punya login.** Semua pengunjung berbagi satu
   basis data yang sama. Di URL publik, siapa pun yang membuka tautannya
   melihat seluruh catatan yang pernah disimpan, dan bisa menghapusnya.

Kedua hal itu tidak bisa diperbaiki hanya dengan kode di sisi ini —
keduanya keputusan penyebaran. Yang bisa dilakukan kode adalah membuatnya
TERLIHAT, bukan mendiamkannya. Kegagalan yang paling merugikan pada
aplikasi klinis adalah yang terjadi diam-diam.
"""

from __future__ import annotations

import os
from pathlib import Path


def _ada(*nama: str) -> bool:
    return any(os.environ.get(n) for n in nama)


def di_streamlit_cloud() -> bool:
    """
    Deteksi Streamlit Community Cloud.

    Tidak ada penanda resmi tunggal, jadi dipakai beberapa petunjuk yang
    lazim ada di sana. Kalau meleset, dampaknya hanya peringatan yang
    kurang atau berlebih — bukan kegagalan fungsi.
    """
    if _ada("STREAMLIT_SHARING_MODE", "STREAMLIT_CLOUD", "STREAMLIT_SERVER_ADDRESS"):
        return True
    if os.environ.get("HOSTNAME", "").startswith("streamlit"):
        return True
    return Path("/mount/src").exists()


def di_container() -> bool:
    """Deteksi container pada umumnya (Docker, Cloud Run, dsb)."""
    if Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text()
    except Exception:
        return False


def penyimpanan_sementara() -> bool:
    """
    True bila berkas SQLite kemungkinan besar TIDAK bertahan setelah restart.

    Bisa ditegaskan sendiri lewat ASUHAN_PENYIMPANAN=sementara|permanen
    kalau deteksi otomatis meleset di lingkungan Anda.
    """
    paksa = os.environ.get("ASUHAN_PENYIMPANAN", "").strip().lower()
    if paksa in {"sementara", "ephemeral"}:
        return True
    if paksa in {"permanen", "persistent"}:
        return False

    # Basis data di volume yang di-mount khusus dianggap permanen.
    dari_env = os.environ.get("ASUHAN_DB_PATH", "")
    if dari_env and any(
        str(dari_env).startswith(p) for p in ("/data", "/mnt", "/var/lib", "/persist")
    ):
        return False

    return di_streamlit_cloud() or di_container()


def bisa_diakses_publik() -> bool:
    """
    True bila aplikasi kemungkinan dapat dibuka siapa saja.

    Streamlit Community Cloud menerbitkan aplikasi secara publik pada
    paket gratis. Karena aplikasi ini tidak punya login, itu berarti
    seluruh catatan dapat dilihat dan dihapus oleh pengunjung mana pun.
    """
    paksa = os.environ.get("ASUHAN_AKSES", "").strip().lower()
    if paksa in {"publik", "public"}:
        return True
    if paksa in {"privat", "private", "internal"}:
        return False
    return di_streamlit_cloud()


def mode() -> str:
    """
    Mode pemakaian: 'pembelajaran' (bawaan) atau 'klinis'.

    Membedakan keduanya penting karena risikonya berbeda jauh. Pada mode
    pembelajaran, penyimpanan sementara hanyalah ketidaknyamanan kecil dan
    tidak ada data pasien yang dipertaruhkan — peringatan keras justru
    mengganggu dan lama-lama diabaikan, termasuk saat benar-benar penting.

    Set ASUHAN_MODE=klinis bila suatu saat dipakai dengan data nyata.
    """
    nilai = os.environ.get("ASUHAN_MODE", "").strip().lower()
    return "klinis" if nilai in {"klinis", "clinical", "produksi"} else "pembelajaran"


def mode_klinis() -> bool:
    return mode() == "klinis"


def ringkasan() -> dict[str, object]:
    return {
        "mode": mode(),
        "streamlit_cloud": di_streamlit_cloud(),
        "container": di_container(),
        "penyimpanan_sementara": penyimpanan_sementara(),
        "akses_publik": bisa_diakses_publik(),
    }


# --------------------------------------------------
# PESAN — mode pembelajaran (bawaan)
# --------------------------------------------------
# Nadanya sengaja tenang. Pada alat bantu belajar, kehilangan riwayat
# adalah ketidaknyamanan kecil, bukan musibah — dan peringatan bernada
# gawat yang muncul terus-menerus justru melatih orang mengabaikannya.

PESAN_SEMENTARA_BELAJAR = (
    "Riwayat asesmen di sini bersifat sementara dan dapat terhapus saat "
    "aplikasi di-restart. Unduh hasilnya (Markdown/CSV) bila ingin disimpan."
)

PESAN_PUBLIK_BELAJAR = (
    "Alat bantu pembelajaran — tanpa login, sehingga riwayat terlihat oleh "
    "semua pengguna. Gunakan data latihan, bukan data pasien sungguhan."
)

# --------------------------------------------------
# PESAN — mode klinis
# --------------------------------------------------
PESAN_SEMENTARA_KLINIS = (
    "**Catatan tidak tersimpan permanen di lingkungan ini.** "
    "Setiap kali aplikasi di-deploy ulang, di-restart, atau bangun dari mode "
    "tidur, seluruh asesmen yang tersimpan akan hilang. "
    "Unduh hasilnya (Markdown/CSV) bila ingin menyimpannya."
)

PESAN_PUBLIK_KLINIS = (
    "**Aplikasi ini dapat dibuka siapa saja dan tidak memiliki login.** "
    "Semua pengunjung berbagi satu daftar riwayat yang sama — catatan yang "
    "Anda simpan dapat dilihat dan dihapus orang lain. "
    "Jangan memasukkan nama, nomor rekam medis, atau data lain yang dapat "
    "mengidentifikasi pasien."
)


def pesan_sementara() -> str:
    return PESAN_SEMENTARA_KLINIS if mode_klinis() else PESAN_SEMENTARA_BELAJAR


def pesan_publik() -> str:
    return PESAN_PUBLIK_KLINIS if mode_klinis() else PESAN_PUBLIK_BELAJAR


# Nama lama dipertahankan agar pemanggil yang sudah ada tetap jalan.
PESAN_SEMENTARA = PESAN_SEMENTARA_BELAJAR
PESAN_PUBLIK = PESAN_PUBLIK_BELAJAR
