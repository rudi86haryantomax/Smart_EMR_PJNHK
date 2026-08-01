"""
tools/_konversi.py
==========================================
Helper bersama untuk konversi Excel <-> JSON.

KEPUTUSAN DESAIN: SATU SEL = SATU DAFTAR
----------------------------------------
Kriteria dan intervensi berupa daftar (beberapa butir). Di Excel, daftar
itu ditulis dalam SATU sel dengan tiap butir pada baris tersendiri
(tekan Alt+Enter untuk baris baru di dalam sel).

Alternatifnya adalah satu baris Excel per butir, tetapi itu membuat satu
diagnosis tersebar di puluhan baris dan sangat mudah rusak saat
disortir — satu kali sortir kolom bisa mengacak butir antar-diagnosis
tanpa disadari. Dengan satu sel satu daftar, satu baris Excel selalu
berarti satu diagnosis utuh dan aman disortir.

Selain baris baru, tanda pipa `|` juga diterima saat membaca. Titik koma
TIDAK dipakai sebagai pemisah karena lazim muncul di dalam kalimat
klinis -- lihat catatan pada _POLA_PISAH.
"""

from __future__ import annotations

import re
from typing import Any

# Pemisah yang diterima saat MEMBACA sel: baris baru, atau tanda pipa.
#
# Titik koma SENGAJA TIDAK dipakai sebagai pemisah. Dalam teks klinis,
# `;` lazim muncul DI DALAM satu kalimat -- mis. "Kendali laju:
# beta-blocker atau penyekat kanal kalsium; digoksin pada gagal jantung".
# Menjadikannya pemisah akan memotong satu butir menjadi dua secara diam-
# diam, dan kalimat yang terpotong itu berubah makna klinisnya.
_POLA_PISAH = re.compile(r"[\n\r]+|\s*\|\s*")

# Pemisah yang dipakai saat MENULIS ke Excel.
PEMISAH_TULIS = "\n"


# =====================================================
# DEFINISI KOLOM
# =====================================================
# (nama_kolom_excel, jalur_di_json, apakah_daftar)
# Jalur memakai titik untuk objek bersarang, mis. "luaran.kode".

KOLOM_SDKI: list[tuple[str, str, bool]] = [
    ("Kode",              "kode",                       False),
    ("Nama Diagnosis",    "nama",                       False),
    ("Jenis",             "jenis",                      False),
    ("SDKI Resmi",        "is_sdki",                    False),
    ("Kriteria Mayor",    "kriteria.mayor",             True),
    ("Kriteria Minor",    "kriteria.minor",             True),
    ("Faktor Risiko",     "kriteria.faktor_risiko",     True),
    ("Kode Luaran",       "luaran.kode",                False),
    ("Nama Luaran",       "luaran.nama",                False),
    ("Intervensi Observasi",   "intervensi.observasi",  True),
    ("Intervensi Terapeutik",  "intervensi.terapeutik", True),
    ("Intervensi Edukasi",     "intervensi.edukasi",    True),
    ("Intervensi Kolaborasi",  "intervensi.kolaborasi", True),
    ("Status Verifikasi", "status_verifikasi",          False),
    ("Catatan",           "catatan",                    False),
    ("Terkait",           "terkait",                    True),
]

KOLOM_PPK: list[tuple[str, str, bool]] = [
    ("Kode",                "kode",                          False),
    ("ICD-10",              "icd10",                         False),
    ("Nama Diagnosis",      "nama",                          False),
    ("Kategori",            "kategori",                      False),
    ("Definisi",            "definisi",                      False),
    ("Anamnesis",           "kriteria.anamnesis",            True),
    ("Pemeriksaan Fisik",   "kriteria.pemeriksaan_fisik",    True),
    ("Pemeriksaan Penunjang", "kriteria.penunjang",          True),
    ("Kriteria Diagnosis",  "kriteria.kriteria_diagnosis",   True),
    ("Tatalaksana Awal",    "tatalaksana.awal",              True),
    ("Farmakologis",        "tatalaksana.farmakologis",      True),
    ("Non-Farmakologis",    "tatalaksana.non_farmakologis",  True),
    ("Rawat & Rujukan",     "tatalaksana.rujukan",           True),
    ("Edukasi",             "edukasi",                       True),
    ("Komplikasi",          "komplikasi",                    True),
    ("Referensi",           "referensi",                     False),
]

FORMAT = {
    "sdki": {
        "kolom": KOLOM_SDKI,
        "kunci_daftar": "diagnosis",
        "sheet": "SDKI",
        "berkas_default": "sdki_slki_siki.json",
    },
    "ppk": {
        "kolom": KOLOM_PPK,
        "kunci_daftar": "ppk",
        "sheet": "PPK",
        "berkas_default": "ppk_kardiovaskular.json",
    },
}


def deteksi_format_json(doc: dict) -> str | None:
    """Tebak format dari kunci daftar utamanya."""
    if isinstance(doc.get("diagnosis"), list):
        return "sdki"
    if isinstance(doc.get("ppk"), list):
        return "ppk"
    return None


def deteksi_format_kolom(header: list[str]) -> str | None:
    """
    Tebak format dari judul kolom. Memakai kolom penanda yang hanya ada
    di salah satu format, bukan sekadar mencocokkan semua kolom — supaya
    berkas yang kolomnya belum lengkap tetap terdeteksi.
    """
    bersih = {str(h or "").strip().lower() for h in header}
    if {"nama luaran", "intervensi observasi"} & bersih:
        return "sdki"
    if {"tatalaksana awal", "farmakologis", "icd-10"} & bersih:
        return "ppk"
    return None


# =====================================================
# SEL <-> NILAI
# =====================================================

def pecah_sel(nilai: Any) -> list[str]:
    """Ubah isi satu sel menjadi daftar butir."""
    if nilai is None:
        return []
    if isinstance(nilai, list):
        return [str(v).strip() for v in nilai if str(v).strip()]

    teks = str(nilai).strip()
    if not teks:
        return []

    butir = []
    for potong in _POLA_PISAH.split(teks):
        potong = potong.strip()
        # Buang penanda daftar yang ikut tersalin dari dokumen lain.
        potong = re.sub(r"^[-•*\u2022]\s*", "", potong)
        potong = re.sub(r"^\d+[.)]\s*", "", potong)
        if potong:
            butir.append(potong)
    return butir


def gabung_sel(butir: Any) -> str:
    if not butir:
        return ""
    if isinstance(butir, str):
        return butir
    return PEMISAH_TULIS.join(str(b) for b in butir)


def ke_bool(nilai: Any) -> bool:
    if isinstance(nilai, bool):
        return nilai
    teks = str(nilai or "").strip().lower()
    return teks in {"true", "ya", "y", "1", "yes", "benar", "sdki"}


# =====================================================
# JALUR BERSARANG
# =====================================================

def ambil(obj: dict, jalur: str) -> Any:
    kini: Any = obj
    for bagian in jalur.split("."):
        if not isinstance(kini, dict):
            return None
        kini = kini.get(bagian)
    return kini


def taruh(obj: dict, jalur: str, nilai: Any) -> None:
    bagian = jalur.split(".")
    kini = obj
    for b in bagian[:-1]:
        kini = kini.setdefault(b, {})
    kini[bagian[-1]] = nilai
