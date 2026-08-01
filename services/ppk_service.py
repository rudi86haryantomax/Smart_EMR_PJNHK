"""
services/ppk_service.py
==========================================
Business rules alur dokter: dari temuan klinis menuju panduan tatalaksana.

Perbedaan penting dengan alur perawat
-------------------------------------
Alur perawat menghasilkan DAFTAR diagnosis berprioritas — beberapa
diagnosis keperawatan memang berjalan bersamaan pada satu pasien.

Alur dokter berbeda: yang dicari adalah SATU diagnosis kerja, dan
beberapa kandidat yang muncul justru merupakan diagnosis banding yang
harus disingkirkan. Karena itu di sini tidak ada penyusunan prioritas;
yang ada adalah pemilihan satu PPK, dengan kandidat lain tetap
ditampilkan sebagai pengingat diagnosis banding.
"""

from __future__ import annotations

from typing import Any

from repositories.ppk_repository import (
    BAGIAN_KRITERIA,
    BAGIAN_TATALAKSANA,
    PpkRepository,
)

# Kondisi yang mengancam jiwa dalam hitungan menit sampai jam. Ditandai
# agar tetap terlihat meski skor kecocokan katanya rendah -- pada nyeri
# dada, diseksi aorta sering kalah skor dari SKA padahal justru itu yang
# paling fatal bila terlewat, dan tatalaksananya berlawanan (antikoagulan
# menyelamatkan pada SKA, membahayakan pada diseksi).
KODE_KRITIS = {
    "PPK.CV.001",  # STEMI
    "PPK.CV.006",  # Syok kardiogenik
    "PPK.CV.007",  # Edema paru akut
    "PPK.CV.010",  # Emboli paru
    "PPK.CV.012",  # Diseksi aorta
    "PPK.CV.013",  # Henti jantung
    # --- Aritmia yang mengancam jiwa ---
    "PPK.CV.022",  # Takikardia ventrikel
    "PPK.CV.023",  # Fibrilasi ventrikel
    "PPK.CV.024",  # Torsades de pointes
    "PPK.CV.028",  # Blok AV total
    # WPW ikut ditandai bukan karena selalu gawat, melainkan karena
    # tatalaksananya mudah keliru: memberikan penghambat nodus AV pada
    # fibrilasi atrium dengan preeksitasi dapat berakibat fatal.
    "PPK.CV.018",  # AVRT / WPW
    # Tamponade: kegawatan yang menuntut drainase segera, dan mudah
    # diperburuk oleh naluri memberi diuretik pada "jantung membesar".
    "PPK.CV.049",  # Tamponade jantung
    # Sindrom aorta akut: hematoma intramural ditangani dengan kaidah yang
    # sama dengan diseksi — antikoagulan dan trombolitik berbahaya.
    "PPK.CV.057",  # Hematoma intramural aorta
    "PPK.CV.062",  # Badai listrik
}


class PpkService:
    def __init__(self, repo: PpkRepository | None = None):
        self.repo = repo or PpkRepository()

    # =================================================
    # USULAN
    # =================================================

    def usulkan(self, temuan: str, limit: int = 6) -> list[dict[str, Any]]:
        """Usulkan PPK dari temuan klinis (anamnesis + pemeriksaan + penunjang)."""
        if not str(temuan or "").strip():
            return []

        hasil = self.repo.suggest(temuan, limit=limit)
        for item in hasil:
            item["kritis"] = item["kode"] in KODE_KRITIS
        return hasil

    def kandidat_kritis_terlewat(self, temuan: str, sudah_muncul: list[str]) -> list[dict[str, Any]]:
        """
        Kondisi kritis yang punya kecocokan tetapi tidak masuk daftar teratas.

        Dipisahkan supaya bisa ditampilkan sebagai pengingat tersendiri.
        Menampilkannya bercampur dalam daftar berperingkat justru menyamarkan
        maksudnya: ini bukan "kandidat yang lebih lemah", melainkan
        "jangan sampai terlewat".
        """
        semua = self.repo.suggest(temuan, limit=len(self.repo.all()), min_score=0.02)
        muncul = set(sudah_muncul)
        return [
            {**item, "kritis": True}
            for item in semua
            if item["kode"] in KODE_KRITIS and item["kode"] not in muncul
        ]

    # =================================================
    # DETAIL
    # =================================================

    def detail(self, kode: str) -> dict[str, Any] | None:
        return self.repo.find(kode)

    def cari(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.repo.cari(keyword, limit) if hasattr(self.repo, "cari") \
            else self.repo.search(keyword, limit)

    def semua(self) -> list[dict[str, Any]]:
        return self.repo.all()

    def kategori_list(self) -> list[str]:
        return self.repo.kategori_list()

    def by_kategori(self, kategori: str) -> list[dict[str, Any]]:
        return self.repo.by_kategori(kategori)

    def label(self, kode: str) -> str:
        entry = self.repo.find(kode)
        return f"{kode} — {entry['nama']}" if entry else kode

    def is_kritis(self, kode: str) -> bool:
        return kode in KODE_KRITIS

    # =================================================
    # PERAKITAN
    # =================================================

    def rakit(self, kode: str) -> dict[str, Any] | None:
        """Susun PPK lengkap dalam bentuk siap tampil."""
        entry = self.repo.find(kode)
        if not entry:
            return None

        kriteria = entry.get("kriteria", {})
        tata = entry.get("tatalaksana", {})

        return {
            "kode": entry["kode"],
            "nama": entry["nama"],
            "icd10": entry.get("icd10", ""),
            "kategori": entry.get("kategori", ""),
            "definisi": entry.get("definisi", ""),
            "kritis": kode in KODE_KRITIS,
            "kriteria": [
                (bagian, list(kriteria.get(bagian, []))) for bagian in BAGIAN_KRITERIA
            ],
            "tatalaksana": [
                (bagian, list(tata.get(bagian, []))) for bagian in BAGIAN_TATALAKSANA
            ],
            "edukasi": list(entry.get("edukasi", [])),
            "komplikasi": list(entry.get("komplikasi", [])),
            "referensi": entry.get("referensi", ""),
        }

    @property
    def peringatan_sumber(self) -> str:
        return self.repo.peringatan
