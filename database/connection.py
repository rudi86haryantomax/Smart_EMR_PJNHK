"""
database/connection.py
==========================================
Koneksi SQLite terpusat, disiapkan untuk pemakaian banyak orang sekaligus.

Empat pengaturan di bawah gampang terlewat pada SQLite, dan ketiadaannya
baru terasa saat beberapa orang memakai aplikasi bersamaan:

1. `PRAGMA foreign_keys = ON`
   SQLite MEMATIKAN foreign key secara bawaan. Tanpa ini, `ON DELETE
   CASCADE` tidak jalan dan menghapus asesmen meninggalkan baris
   diagnosis yatim.

2. `PRAGMA journal_mode = WAL`
   Mode bawaan mengunci seluruh berkas saat menulis, sehingga pembaca
   ikut terblokir. Dengan WAL, pembaca dan penulis bisa jalan bersamaan —
   penting karena aplikasi ini jauh lebih sering membaca (memuat riwayat,
   memuat asesmen) daripada menulis.

3. `PRAGMA busy_timeout`
   Bawaannya nol: begitu berkas terkunci, SQLite langsung melempar error.
   Dengan timeout, koneksi menunggu giliran alih-alih gagal.

4. `BEGIN IMMEDIATE` untuk transaksi tulis
   Ini yang memperbaiki bug sesungguhnya. Pembuatan nomor asesmen membaca
   nomor terakhir lalu menyisipkan baris baru. Tanpa kunci tulis sejak
   awal transaksi, dua orang yang menyimpan bersamaan sama-sama membaca
   nomor terakhir yang sama, lalu keduanya mencoba menyisipkan nomor yang
   sama — dan salah satunya ditolak.

   Diuji sebelum perbaikan: dari 10 penyimpanan serentak, 8 GAGAL dengan
   "UNIQUE constraint failed". Data tidak rusak, tetapi delapan orang
   kehilangan hasil kerjanya.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.config import BASE_DIR, db_path

_SCHEMA_FILE = BASE_DIR / "database" / "schema.sql"

# Berapa lama menunggu bila berkas sedang dikunci proses lain.
_BUSY_TIMEOUT_MS = 10_000

# Percobaan ulang bila kunci tetap tidak didapat dalam batas waktu.
_MAKS_PERCOBAAN = 4
_JEDA_AWAL = 0.12


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path(),
        check_same_thread=False,  # Streamlit berganti thread antar-rerun
        timeout=_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,     # transaksi dikelola manual, lihat unit_of_work
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        # NORMAL cukup aman berpasangan dengan WAL dan jauh lebih cepat
        # daripada FULL untuk beban tulis kecil seperti aplikasi ini.
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        # Sebagian filesystem (mis. berbagi lewat jaringan) tidak mendukung
        # WAL. Mode jurnal bawaan tetap berfungsi, hanya kurang paralel.
        pass
    return conn


def _kunci_sementara(exc: Exception) -> bool:
    pesan = str(exc).lower()
    return "locked" in pesan or "busy" in pesan


@contextmanager
def unit_of_work(menulis: bool = True) -> Iterator[sqlite3.Connection]:
    """
    Transaksi: commit bila blok selesai tanpa error, rollback bila ada.

    `menulis=True` (bawaan) memakai BEGIN IMMEDIATE sehingga kunci tulis
    diambil sejak awal — inilah yang membuat dua orang tidak bisa membaca
    keadaan yang sama lalu menulis bertabrakan.

    Pakai `menulis=False` untuk operasi yang murni membaca, supaya tidak
    ikut mengantre di kunci tulis.
    """
    percobaan = 0
    while True:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE" if menulis else "BEGIN")
            yield conn
            conn.execute("COMMIT")
            return
        except sqlite3.OperationalError as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            percobaan += 1
            if not _kunci_sementara(exc) or percobaan >= _MAKS_PERCOBAAN:
                raise
            # Jeda bertambah tiap percobaan agar pengantre tidak serempak
            # mencoba lagi pada saat yang sama.
            time.sleep(_JEDA_AWAL * (2 ** (percobaan - 1)))
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


@contextmanager
def baca_saja() -> Iterator[sqlite3.Connection]:
    """Pintasan untuk operasi yang hanya membaca."""
    with unit_of_work(menulis=False) as conn:
        yield conn


def init_database() -> None:
    """
    Buat tabel bila belum ada. Aman dipanggil setiap start.

    Tidak memakai unit_of_work karena `executescript()` melakukan COMMIT
    implisit, yang bertabrakan dengan transaksi yang dikelola manual.
    """
    schema = Path(_SCHEMA_FILE).read_text(encoding="utf-8")
    conn = get_connection()
    try:
        conn.executescript(schema)
    finally:
        conn.close()


def reset_database() -> None:
    """Kosongkan seluruh tabel. Dipakai test dan tombol reset."""
    with unit_of_work() as conn:
        conn.execute("DELETE FROM asesmen_diagnosis")
        conn.execute("DELETE FROM asesmen")
