"""
repositories/ppk_repository.py
==========================================
Akses Panduan Praktik Klinis (PPK) untuk alur dokter.

Polanya sengaja dibuat sama persis dengan `sdki_repository.py`: sumber
berupa berkas JSON yang bisa ditukar lewat environment variable, dengan
`validate()` untuk memeriksa integritas setelah disunting. Dengan begitu
tim medis memperbarui isinya tanpa menyentuh kode, sama seperti tim
keperawatan memperbarui master 3S.

    ASUHAN_PPK_JSON=/path/ke/ppk-rs-anda.json

PERINGATAN ISI: berkas bawaan adalah DRAF AWAL berbasis pedoman umum
(PERKI, ESC, AHA), bukan PPK resmi rumah sakit mana pun. Dosis obat
sengaja tidak dicantumkan karena harus mengikuti formularium setempat.
Ganti dengan PPK resmi Anda sebelum dipakai dalam pelayanan.
"""

from __future__ import annotations

import json
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import BASE_DIR
from core.exceptions import NotFoundError

_DEFAULT_JSON = BASE_DIR / "data" / "ppk_kardiovaskular.json"
_LOAD_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None

BAGIAN_KRITERIA = ("anamnesis", "pemeriksaan_fisik", "penunjang", "kriteria_diagnosis")
BAGIAN_TATALAKSANA = ("awal", "farmakologis", "non_farmakologis", "rujukan")

_STOPWORDS = {
    "yang", "dan", "atau", "pada", "dari", "dengan", "untuk", "tidak",
    "lebih", "kurang", "saat", "dalam", "akibat", "tampak", "dapat",
    "sering", "jika", "perlu", "tanda", "gejala", "kondisi", "pasien",
    "terkait", "secara", "bila", "adanya", "berat", "ringan", "sedang",
    "riwayat", "nilai", "cari", "sesuai", "setelah", "sebelum", "karena",
}

# Singkatan klinis pendek yang justru paling menentukan, tapi akan
# terbuang oleh filter panjang minimum kalau tidak didaftarkan.
_SINGKATAN = {
    "ekg", "ska", "ima", "gjk", "jvp", "crt", "agd", "ttv", "ivp",
    "vf", "vt", "pea", "ppk", "tee", "tte", "ikp", "bnp", "ldl", "af",
}

_SINONIM = {
    "sesak": "dispnea",
    "bengkak": "edema",
    "berdebar": "palpitasi",
    "pingsan": "sinkop",
    "kolaps": "sinkop",
    "lemas": "lelah",
    "biru": "sianosis",
    "nafas": "napas",
    "jantung": "jantung",
    "dada": "dada",
}


def _load_source() -> dict[str, Any]:
    override = os.environ.get("ASUHAN_PPK_JSON")
    kandidat = [Path(override)] if override else []
    kandidat.append(_DEFAULT_JSON)

    for path in kandidat:
        if path and path.exists():
            with path.open(encoding="utf-8") as handle:
                doc = json.load(handle)
            doc.setdefault("meta", {})["_sumber_file"] = str(path)
            return doc

    raise FileNotFoundError(
        f"Data PPK tidak ditemukan. Set ASUHAN_PPK_JSON atau pastikan {_DEFAULT_JSON} ada."
    )


def _get_document() -> dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        with _LOAD_LOCK:
            if _CACHE is None:
                _CACHE = _load_source()
    return _CACHE


def reload_data() -> None:
    """Buang cache supaya perubahan berkas terbaca tanpa restart proses."""
    global _CACHE
    with _LOAD_LOCK:
        _CACHE = None
    _tokenize.cache_clear()


@lru_cache(maxsize=2048)
def _tokenize(text: str) -> frozenset[str]:
    kata = re.findall(r"[a-z]+", str(text).lower())
    hasil: set[str] = set()
    for w in kata:
        if w in _STOPWORDS:
            continue
        if w in _SINGKATAN:
            hasil.add(w)
            continue
        if len(w) <= 3:
            continue
        hasil.add(_SINONIM.get(w, w))
    return frozenset(hasil)


class PpkRepository:
    """Repository read-only untuk Panduan Praktik Klinis."""

    def __init__(self, conn=None):
        self.conn = conn  # tidak dipakai; disamakan bentuknya dengan repo lain

    # =================================================
    # META
    # =================================================

    @property
    def meta(self) -> dict[str, Any]:
        return dict(_get_document().get("meta", {}))

    @property
    def peringatan(self) -> str:
        return str(self.meta.get("peringatan", ""))

    def _entries(self) -> list[dict[str, Any]]:
        return _get_document().get("ppk", [])

    def _index(self) -> dict[str, dict[str, Any]]:
        return {e["kode"]: e for e in self._entries()}

    # =================================================
    # FINDER
    # =================================================

    def find(self, kode: str) -> dict[str, Any] | None:
        if not kode:
            return None
        return self._index().get(str(kode).strip().upper())

    def get(self, kode: str) -> dict[str, Any]:
        entry = self.find(kode)
        if not entry:
            raise NotFoundError(f"PPK '{kode}' tidak ditemukan.")
        return entry

    def exists(self, kode: str) -> bool:
        return self.find(kode) is not None

    def all(self) -> list[dict[str, Any]]:
        return list(self._entries())

    def all_codes(self) -> list[str]:
        return [e["kode"] for e in self._entries()]

    def count(self) -> int:
        return len(self._entries())

    # =================================================
    # FILTER
    # =================================================

    def kategori_list(self) -> list[str]:
        return sorted({e.get("kategori", "Lainnya") for e in self._entries()})

    def by_kategori(self, kategori: str) -> list[dict[str, Any]]:
        target = str(kategori).strip().lower()
        return [e for e in self._entries()
                if str(e.get("kategori", "")).lower() == target]

    def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """Cari berdasarkan kode, nama, ICD-10, atau kategori."""
        term = str(keyword or "").strip().lower()
        if not term:
            return []

        tepat, sebagian = [], []
        for e in self._entries():
            ladang = " ".join([
                e.get("kode", ""), e.get("nama", ""),
                e.get("icd10", ""), e.get("kategori", ""),
            ]).lower()
            if term == e.get("kode", "").lower() or term == e.get("nama", "").lower():
                tepat.append(e)
            elif term in ladang:
                sebagian.append(e)
        return (tepat + sebagian)[:limit]

    # =================================================
    # BAGIAN SPESIFIK
    # =================================================

    def kriteria(self, kode: str, bagian: str | None = None) -> Any:
        entry = self.find(kode)
        if not entry:
            return [] if bagian else {}
        data = entry.get("kriteria", {})
        if bagian:
            return list(data.get(str(bagian).strip().lower(), []))
        return {k: list(v) for k, v in data.items()}

    def tatalaksana(self, kode: str, bagian: str | None = None) -> Any:
        entry = self.find(kode)
        if not entry:
            return [] if bagian else {}
        data = entry.get("tatalaksana", {})
        if bagian:
            return list(data.get(str(bagian).strip().lower(), []))
        return {k: list(v) for k, v in data.items()}

    def edukasi(self, kode: str) -> list[str]:
        entry = self.find(kode)
        return list(entry.get("edukasi", [])) if entry else []

    def komplikasi(self, kode: str) -> list[str]:
        entry = self.find(kode)
        return list(entry.get("komplikasi", [])) if entry else []

    def referensi(self, kode: str) -> str:
        entry = self.find(kode)
        return str(entry.get("referensi", "")) if entry else ""

    # =================================================
    # PENCOCOKAN
    # =================================================

    def suggest(self, text: str, limit: int = 5, min_score: float = 0.05) -> list[dict[str, Any]]:
        """
        Usulkan PPK berdasarkan kemiripan teks temuan klinis dengan
        kriteria diagnosis.

        PERINGATAN: pencocokan kata kunci, BUKAN penalaran diagnostik.
        Beberapa kondisi di sini berbagi gejala yang hampir sama (nyeri
        dada muncul pada SKA, diseksi aorta, perikarditis, emboli paru,
        dan miokarditis sekaligus) sehingga urutan skor TIDAK boleh
        dibaca sebagai kemungkinan diagnosis. Justru kondisi yang paling
        berbahaya sering bukan yang paling banyak kata cocoknya.
        """
        tokens = _tokenize(text)
        if not tokens:
            return []

        hasil = []
        for entry in self._entries():
            kantong: list[str] = [entry.get("nama", ""), entry.get("definisi", "")]
            kriteria = entry.get("kriteria", {})
            for bagian in BAGIAN_KRITERIA:
                kantong.extend(kriteria.get(bagian, []))

            token_entry = _tokenize(" ".join(kantong))
            if not token_entry:
                continue

            cocok = tokens & token_entry
            if not cocok:
                continue

            skor = len(cocok) / (len(token_entry) ** 0.5)
            if skor >= min_score:
                hasil.append({
                    "ppk": entry,
                    "kode": entry["kode"],
                    "nama": entry["nama"],
                    "kategori": entry.get("kategori", ""),
                    "icd10": entry.get("icd10", ""),
                    "skor": round(skor, 4),
                    "kata_cocok": sorted(cocok),
                })

        hasil.sort(key=lambda x: (-x["skor"], x["kode"]))
        return hasil[:limit]

    # =================================================
    # PEMELIHARAAN
    # =================================================

    def validate(self) -> dict[str, Any]:
        masalah: list[str] = []
        terlihat: set[str] = set()

        for entry in self._entries():
            kode = entry.get("kode", "")
            if not kode:
                masalah.append("Ada entri tanpa kode.")
                continue
            if kode in terlihat:
                masalah.append(f"{kode}: kode duplikat.")
            terlihat.add(kode)

            for wajib in ("nama", "kategori", "definisi", "referensi"):
                if not str(entry.get(wajib) or "").strip():
                    masalah.append(f"{kode}: '{wajib}' kosong.")

            kriteria = entry.get("kriteria", {})
            for bagian in BAGIAN_KRITERIA:
                if bagian not in kriteria:
                    masalah.append(f"{kode}: kriteria.{bagian} tidak ada.")
                elif not isinstance(kriteria.get(bagian), list):
                    masalah.append(f"{kode}: kriteria.{bagian} harus list.")
            if not kriteria.get("anamnesis"):
                masalah.append(f"{kode}: kriteria.anamnesis kosong.")

            tata = entry.get("tatalaksana", {})
            for bagian in BAGIAN_TATALAKSANA:
                if bagian not in tata:
                    masalah.append(f"{kode}: tatalaksana.{bagian} tidak ada.")
                elif not isinstance(tata.get(bagian), list):
                    masalah.append(f"{kode}: tatalaksana.{bagian} harus list.")
            if not tata.get("awal"):
                masalah.append(f"{kode}: tatalaksana.awal kosong.")

        return {"valid": not masalah, "jumlah": len(self._entries()), "masalah": masalah}


__all__ = ["PpkRepository", "reload_data", "BAGIAN_KRITERIA", "BAGIAN_TATALAKSANA"]
