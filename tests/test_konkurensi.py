"""
tests/test_konkurensi.py
==========================================
Test pemakaian oleh banyak orang sekaligus.

LATAR BELAKANG
--------------
Versi awal aplikasi ini punya race condition yang serius. Pembuatan nomor
asesmen membaca nomor terakhir lalu menyisipkan baris baru, tanpa kunci
tulis sejak awal transaksi. Akibatnya dua orang yang menyimpan bersamaan
membaca nomor terakhir yang sama, lalu keduanya menyisipkan nomor yang
sama.

Diuji sebelum perbaikan: **dari 10 penyimpanan serentak, 8 GAGAL** dengan
"UNIQUE constraint failed". Data tidak rusak — constraint bekerja
sebagaimana mestinya — tetapi delapan orang kehilangan hasil kerjanya.

Perbaikannya ada di `database/connection.py`: WAL, busy_timeout, dan
`BEGIN IMMEDIATE` untuk transaksi tulis.

Test ini menjaga agar bug itu tidak kembali tanpa disadari. Bug
konkurensi jarang muncul saat pengembangan (satu orang, satu klik pada
satu waktu) dan baru terlihat justru ketika aplikasi ramai dipakai.

Jalankan:
    cd tests && python test_konkurensi.py
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ASUHAN_DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="konkurensi_test_"), "test.db"
)

_st = types.ModuleType("streamlit")
_st.secrets = {}
sys.modules.setdefault("streamlit", _st)

from database.connection import (  # noqa: E402
    baca_saja,
    init_database,
    reset_database,
    unit_of_work,
)
from models.asesmen import Asesmen, DiagnosisPilihan  # noqa: E402
from repositories.asesmen_repository import AsesmenRepository  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {extra}")


def _simpan(label: str, kode_diagnosis: list[str]) -> int:
    with unit_of_work() as conn:
        repo = AsesmenRepository(conn)
        asesmen_id = repo.create(Asesmen(
            label=label, data_subjektif="sesak napas", data_objektif="edema tungkai"
        ))
        repo.set_diagnosis(asesmen_id, [
            DiagnosisPilihan(k, i + 1, []) for i, k in enumerate(kode_diagnosis)
        ])
        return asesmen_id


def jalankan_serentak(jumlah: int, kerja) -> tuple[list, list]:
    """Jalankan `kerja(n)` pada `jumlah` thread yang mulai BERSAMAAN."""
    berhasil: list = []
    galat: list = []
    gerbang = threading.Barrier(jumlah)

    def bungkus(n: int) -> None:
        gerbang.wait()  # tanpa ini thread jalan bergantian dan race tidak terpicu
        try:
            kerja(n)
            berhasil.append(n)
        except Exception as exc:
            galat.append((n, type(exc).__name__, str(exc)[:90]))

    utas = [threading.Thread(target=bungkus, args=(i,)) for i in range(jumlah)]
    for u in utas:
        u.start()
    for u in utas:
        u.join()
    return berhasil, galat


def main() -> int:
    init_database()

    print("=" * 62)
    print("TEST 1 -- 10 orang menyimpan BERSAMAAN")
    print("=" * 62)
    reset_database()
    berhasil, galat = jalankan_serentak(
        10, lambda n: _simpan(f"Perawat-{n}", ["D.0077"])
    )
    check("Semua 10 penyimpanan berhasil", len(berhasil) == 10, f"{len(berhasil)}/10")
    check("Tidak ada kegagalan", not galat, galat[:3])

    with baca_saja() as conn:
        baris = AsesmenRepository(conn).list_recent(limit=100)
    nomor = [b["nomor"] for b in baris]
    check("10 asesmen tersimpan", len(baris) == 10, len(baris))
    check("Semua nomor asesmen unik", len(set(nomor)) == len(nomor),
          f"{len(set(nomor))} unik dari {len(nomor)}")

    print("\n" + "=" * 62)
    print("TEST 2 -- 25 orang menyimpan BERSAMAAN (beban lebih berat)")
    print("=" * 62)
    reset_database()
    berhasil, galat = jalankan_serentak(
        25, lambda n: _simpan(f"P-{n}", ["D.0008", "D.0022"])
    )
    check("Semua 25 penyimpanan berhasil", len(berhasil) == 25, f"{len(berhasil)}/25")
    check("Tidak ada kegagalan", not galat, galat[:3])

    with baca_saja() as conn:
        baris = AsesmenRepository(conn).list_recent(limit=200)
    check("25 asesmen tersimpan", len(baris) == 25, len(baris))
    check("Nomor tetap unik", len({b["nomor"] for b in baris}) == 25)
    check("Setiap asesmen punya 2 diagnosis (tidak ada tulisan separuh jadi)",
          all(b["jumlah_diagnosis"] == 2 for b in baris),
          [b["jumlah_diagnosis"] for b in baris[:5]])

    print("\n" + "=" * 62)
    print("TEST 3 -- Campuran baca & tulis serentak (skenario kelas)")
    print("=" * 62)
    reset_database()
    hitung = {"tulis": 0, "baca": 0}
    kunci = threading.Lock()

    def peserta(n: int) -> None:
        for putaran in range(3):
            if random.random() < 0.5:
                _simpan(f"P{n}-{putaran}", ["D.0001"])
                with kunci:
                    hitung["tulis"] += 1
            else:
                with baca_saja() as conn:
                    AsesmenRepository(conn).list_recent(limit=50)
                with kunci:
                    hitung["baca"] += 1
            time.sleep(random.uniform(0, 0.01))

    mulai = time.time()
    berhasil, galat = jalankan_serentak(30, peserta)
    durasi = time.time() - mulai

    check("Semua 30 peserta selesai tanpa error", len(berhasil) == 30, galat[:3])
    check("Tidak ada operasi yang gagal", not galat, galat[:3])
    check(f"Selesai wajar cepat ({durasi:.2f} detik)", durasi < 15, f"{durasi:.2f}s")

    with baca_saja() as conn:
        baris = AsesmenRepository(conn).list_recent(limit=500)
    check("Jumlah tersimpan cocok dengan jumlah operasi tulis",
          len(baris) == hitung["tulis"], f"{len(baris)} vs {hitung['tulis']}")
    check("Nomor tetap unik di bawah beban campuran",
          len({b["nomor"] for b in baris}) == len(baris))
    check("Tidak ada asesmen tanpa diagnosis",
          all(b["jumlah_diagnosis"] > 0 for b in baris))

    print("\n" + "=" * 62)
    print("TEST 4 -- Menghapus sambil membaca")
    print("=" * 62)
    reset_database()
    ids = [_simpan(f"Hapus-{i}", ["D.0077"]) for i in range(12)]

    def kerja_campur(n: int) -> None:
        if n < 6:
            with unit_of_work() as conn:
                AsesmenRepository(conn).delete(ids[n])
        else:
            with baca_saja() as conn:
                AsesmenRepository(conn).list_recent(limit=50)

    berhasil, galat = jalankan_serentak(12, kerja_campur)
    check("Hapus & baca serentak tanpa error", not galat, galat[:3])

    with baca_saja() as conn:
        repo = AsesmenRepository(conn)
        sisa = repo.total()
        yatim = repo.fetch_all(
            "SELECT COUNT(*) AS n FROM asesmen_diagnosis d "
            "LEFT JOIN asesmen a ON a.id = d.asesmen_id WHERE a.id IS NULL"
        )
    check("6 asesmen tersisa", sisa == 6, sisa)
    check("Tidak ada baris diagnosis yatim (cascade bekerja)",
          int(yatim[0]["n"]) == 0, yatim[0]["n"])

    print("\n" + "=" * 62)
    print("TEST 5 -- Pengaturan SQLite untuk konkurensi terpasang")
    print("=" * 62)
    with baca_saja() as conn:
        mode_jurnal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    check("journal_mode = WAL (pembaca tidak diblokir penulis)",
          str(mode_jurnal).lower() == "wal", mode_jurnal)
    check("foreign_keys aktif (ON DELETE CASCADE berfungsi)", fk == 1, fk)
    check("busy_timeout > 0 (menunggu, bukan langsung gagal)", busy > 0, busy)

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
