"""
tools/cek_versi.py
==========================================
Cetak sidik jari (hash) setiap berkas proyek.

    python tools/cek_versi.py

Gunanya: memastikan salinan Anda — mis. di GitHub — sudah sama dengan
versi terbaru. Jalankan di kedua tempat lalu bandingkan keluarannya;
baris yang berbeda menandakan berkas yang perlu diperbarui.

Ini melengkapi daftar perubahan manual, yang selalu punya risiko sama:
berkas yang lupa disebut tidak akan pernah ketahuan sampai aplikasinya
bermasalah.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Berkas yang isinya bergantung lingkungan, bukan bagian dari kode.
ABAIKAN_POLA = (
    "__pycache__", ".pyc", ".db", ".db-wal", ".db-shm",
    "secrets.toml", ".env", ".xlsx", "backup-",
)


def diabaikan(path: Path) -> bool:
    teks = str(path)
    return any(p in teks for p in ABAIKAN_POLA)


def sidik(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main() -> int:
    berkas = sorted(
        p for p in ROOT.rglob("*")
        if p.is_file() and not diabaikan(p) and not p.name.startswith(".git")
    )

    print(f"{'sidik':<14} {'baris':>6}  berkas")
    print("-" * 70)
    total_baris = 0
    for p in berkas:
        rel = p.relative_to(ROOT)
        try:
            baris = len(p.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            baris = 0
        total_baris += baris
        print(f"{sidik(p):<14} {baris:>6}  {rel}")

    print("-" * 70)
    print(f"{len(berkas)} berkas, {total_baris} baris")
    return 0


if __name__ == "__main__":
    sys.exit(main())
