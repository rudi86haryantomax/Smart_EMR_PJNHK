"""
core/profesi.py
==========================================
Profesi pengguna dan alur yang menyertainya.

Ini BUKAN sistem autentikasi — tidak ada verifikasi identitas, kata
sandi, atau kewenangan. Fungsinya hanya memilih alur kerja mana yang
ditampilkan, karena perawat dan dokter memerlukan keluaran yang berbeda
dari data klinis yang sama:

    Perawat -> diagnosis keperawatan (SDKI/SLKI/SIKI), beberapa sekaligus,
               disusun berdasarkan prioritas
    Dokter  -> satu diagnosis kerja + panduan tatalaksana (PPK), dengan
               kandidat lain sebagai diagnosis banding

Kalau nanti perlu pembatasan akses sungguhan, tambahkan lapisan auth
terpisah — jangan menjadikan pilihan di sini sebagai pengaman.
"""

from __future__ import annotations

from typing import Any

PERAWAT = "perawat"
DOKTER = "dokter"

PROFESI: dict[str, dict[str, Any]] = {
    PERAWAT: {
        "nama": "Perawat",
        "ikon": "💉",
        "deskripsi": "Diagnosis keperawatan berbasis SDKI, luaran SLKI, dan intervensi SIKI",
        "halaman_awal": "asesmen",
        "menu": ["asesmen", "riwayat"],
    },
    DOKTER: {
        "nama": "Dokter",
        "ikon": "🩺",
        "deskripsi": "Panduan Praktik Klinis (PPK) — kriteria diagnosis dan tatalaksana",
        "halaman_awal": "tatalaksana",
        "menu": ["tatalaksana", "riwayat"],
    },
}

DEFAULT = PERAWAT


def normalize(kode: str | None) -> str:
    kode = str(kode or "").strip().lower()
    return kode if kode in PROFESI else DEFAULT


def info(kode: str | None) -> dict[str, Any]:
    return PROFESI[normalize(kode)]


def nama(kode: str | None) -> str:
    return info(kode)["nama"]


def ikon(kode: str | None) -> str:
    return info(kode)["ikon"]


def halaman_awal(kode: str | None) -> str:
    return info(kode)["halaman_awal"]


def menu_untuk(kode: str | None) -> list[str]:
    return list(info(kode)["menu"])


def boleh_akses(halaman: str, kode: str | None) -> bool:
    return halaman in menu_untuk(kode)


def semua() -> list[tuple[str, dict[str, Any]]]:
    return list(PROFESI.items())
