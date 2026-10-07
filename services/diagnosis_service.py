"""
services/diagnosis_service.py
==========================================
Business rules penegakan diagnosis keperawatan.

TENTANG "KECERDASAN BUATAN" DI SINI
-----------------------------------
Usulan diagnosis dihasilkan dengan mencocokkan kata kunci pada data S dan
O terhadap kriteria diagnostik SDKI, bukan dengan penalaran klinis. Itu
perlu dinyatakan terus terang karena berpengaruh pada cara memakainya:

- Sistem ini **tidak menegakkan diagnosis**. Ia mempersempit 55
  kemungkinan menjadi beberapa kandidat agar perawat tidak perlu
  menyisir seluruh daftar.
- Kandidat bisa salah dua arah: memunculkan yang tidak relevan, dan
  MELEWATKAN yang relevan kalau perawat memakai istilah yang tidak ada
  di kriteria. Karena itu pencarian manual selalu tersedia berdampingan,
  bukan sebagai jalan cadangan.
- Setiap usulan disertai `kata_cocok` sebagai alasannya, supaya perawat
  bisa menilai apakah dasarnya masuk akal. Usulan tanpa alasan mendorong
  orang menerima begitu saja.

Skor yang ditampilkan berguna untuk mengurutkan kandidat, tetapi bukan
ukuran kepastian klinis dan sebaiknya tidak dibaca sebagai persentase.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from core.kategori import kategori_dari_luaran, urutkan_prioritas
from models.asesmen import Asesmen, DiagnosisPilihan
from repositories.sdki_repository import SdkiRepository
# Pencocok kriteria milik mesin itu sendiri, dipakai ulang HANYA untuk menandai
# kriteria mana yang terpenuhi (tampilan) -- supaya tanda ✓ selalu sama dengan
# pencocokan yang masuk ke skor, bukan pencocok kedua yang bisa berbeda.
from repositories.sdki_repository import _ekstrak_vital, _nilai_kelompok, _tokenize

_log = logging.getLogger(__name__)

_KELOMPOK_KRITERIA = ("mayor", "minor", "faktor_risiko")


def label_prioritas(skor: float) -> str:
    """
    Label prioritas dari skor relatif: CRITICAL >= 8, HIGH >= 4, MEDIUM >= 2,
    selebihnya LOW.

    Ambangnya SAMA PERSIS dengan `_prioritas()` di adapter smart_emr
    (modules/services/sdki_engine/adapter.py) supaya skor yang sama berlabel
    sama di kedua aplikasi. Bila diubah, ubah keduanya (dan teks KETERANGAN
    di components/kriteria_sdki.py).
    """
    if skor >= 8:
        return "CRITICAL"
    if skor >= 4:
        return "HIGH"
    if skor >= 2:
        return "MEDIUM"
    return "LOW"


class DiagnosisService:
    def __init__(self, repo: SdkiRepository | None = None):
        self.repo = repo or SdkiRepository()

    # =================================================
    # USULAN
    # =================================================

    def usulkan(
        self,
        data_subjektif: str,
        data_objektif: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """
        Usulkan diagnosis dari data S dan O.

        S dan O digabung karena kriteria SDKI memuat keduanya (mayor/minor
        subjektif dan objektif) tanpa memisahkan asalnya, sehingga
        mencocokkan terpisah justru menurunkan jumlah kecocokan.
        """
        teks = f"{data_subjektif or ''}\n{data_objektif or ''}".strip()
        if not teks:
            return []

        hasil = self.repo.suggest(teks, limit=limit)
        for item in hasil:
            entry = item["diagnosis"]
            item["jenis"] = entry.get("jenis", "")
            item["kategori"] = kategori_dari_luaran(entry.get("luaran", {}).get("kode"))
            item["luaran"] = entry.get("luaran", {})
            item["perlu_verifikasi"] = self.repo.perlu_verifikasi(entry["kode"])
            # Tampilan skor & kriteria (sama dengan ranking CDSS smart_emr).
            item["label_prioritas"] = label_prioritas(float(item.get("skor") or 0.0))
            item["kriteria_cek"] = self.cek_kriteria(teks, entry)
        return hasil

    def usulkan_untuk(self, asesmen: Asesmen, limit: int = 8) -> list[dict[str, Any]]:
        return self.usulkan(asesmen.data_subjektif, asesmen.data_objektif, limit)

    def cek_kriteria(self, teks: str, entry: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """
        Tandai tiap kriteria mayor / minor / faktor risiko: terpenuhi oleh
        data S/O atau belum.

        Memakai `_nilai_kelompok` milik mesin per kriteria, sehingga hasilnya
        identik dengan pencocokan yang dihitung ke skor (termasuk penjaga arah
        vital & anti-negasi). Murni tampilan: skor dan urutan tidak berubah.
        Kembar dengan `_cek_kriteria()` di adapter smart_emr.
        """
        kriteria = entry.get("kriteria") or {}
        hasil = {
            kunci: [{"teks": str(k), "cocok": False} for k in (kriteria.get(kunci) or [])]
            for kunci in _KELOMPOK_KRITERIA
        }
        try:
            tokens = _tokenize(teks)
            if tokens:
                vital = _ekstrak_vital(teks)
                bobot = self.repo._bobot_kata()
                bawaan = math.log(1 + len(self.repo._entries()))
                for daftar in hasil.values():
                    for k in daftar:
                        k["cocok"] = _nilai_kelompok(
                            [k["teks"]], tokens, vital, bobot, bawaan)[3] > 0
        except Exception:  # tampilan saja -- jangan gagalkan usulan
            _log.warning("Gagal menandai kriteria SDKI", exc_info=True)
        return hasil

    # =================================================
    # PRIORITAS
    # =================================================

    def usulkan_prioritas(self, kode_list: list[str]) -> list[str]:
        """
        Susun urutan prioritas awal untuk diagnosis yang dipilih perawat.

        Mengikuti kaidah ABC (respirasi -> sirkulasi -> keamanan -> ...),
        dengan diagnosis Aktual didahulukan atas Risiko. Ini titik awal
        untuk menghemat waktu; perawat dapat menyusun ulang sesuai kondisi
        pasien, dan urutan pilihannya yang disimpan.
        """
        entries = [e for e in (self.repo.find(k) for k in kode_list) if e]
        return [e["kode"] for e in urutkan_prioritas(entries)]

    # =================================================
    # PERAKITAN TABEL LENGKAP
    # =================================================

    def rakit_tabel(self, pilihan: list[DiagnosisPilihan]) -> list[dict[str, Any]]:
        """
        Rakit tabel asuhan lengkap: diagnosis + luaran SLKI + intervensi SIKI.

        Isinya diambil dari master 3S saat dipanggil, bukan dari salinan
        yang tersimpan di database. Jadi kalau redaksi master direvisi,
        catatan lama ikut menampilkan versi terbaru dan tidak ada dua
        sumber yang bisa berbeda.
        """
        baris = []
        for item in sorted(pilihan, key=lambda p: p.prioritas):
            entry = self.repo.find(item.kode_diagnosis)
            if not entry:
                # Kode tidak dikenal -- bisa terjadi kalau master diperbarui
                # dan sebuah kode dihapus. Ditampilkan apa adanya supaya
                # catatan lama tidak diam-diam kehilangan barisnya.
                baris.append({
                    "prioritas": item.prioritas,
                    "kode": item.kode_diagnosis,
                    "nama": f"[{item.kode_diagnosis}] tidak ada di master saat ini",
                    "jenis": "-", "kategori": "-",
                    "luaran": {}, "kriteria": {},
                    "intervensi": {}, "intervensi_dipilih": item.intervensi_dipilih,
                    "catatan": None, "indikator": [], "evaluasi_default": "",
                    "arah_luaran": "", "catatan_luaran": "",
                    "status_verifikasi": "",
                    "perlu_verifikasi": False, "hilang": True,
                })
                continue

            baris.append({
                "prioritas": item.prioritas,
                "kode": entry["kode"],
                "nama": entry["nama"],
                "jenis": entry.get("jenis", ""),
                "kategori": kategori_dari_luaran(entry.get("luaran", {}).get("kode")),
                "luaran": entry.get("luaran", {}),
                "kriteria": entry.get("kriteria", {}),
                "intervensi": entry.get("intervensi", {}),
                "intervensi_dipilih": item.intervensi_dipilih,
                "catatan": entry.get("catatan"),
                "indikator": self.repo.indikator(entry["kode"]),
                "evaluasi_default": self.repo.evaluasi_default(entry["kode"]),
                "arah_luaran": self.repo.arah_luaran(entry["kode"]),
                "catatan_luaran": self.repo.catatan_luaran(entry["kode"]),
                "status_verifikasi": entry.get("status_verifikasi", ""),
                "perlu_verifikasi": self.repo.perlu_verifikasi(entry["kode"]),
                "hilang": False,
            })
        return baris

    # =================================================
    # PENCARIAN MANUAL
    # =================================================

    def cari(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.repo.search(keyword, limit=limit)

    def semua(self) -> list[dict[str, Any]]:
        return self.repo.all()

    def detail(self, kode: str) -> dict[str, Any] | None:
        return self.repo.find(kode)

    def intervensi(self, kode: str) -> dict[str, list[str]]:
        return self.repo.get_intervensi(kode)

    def indikator(self, kode: str) -> list[dict[str, Any]]:
        """Indikator terukur untuk luaran diagnosis ini."""
        return self.repo.indikator(kode)

    def evaluasi_default(self, kode: str) -> str:
        return self.repo.evaluasi_default(kode)

    def arah_luaran(self, kode: str) -> str:
        return self.repo.arah_luaran(kode)

    def label(self, kode: str) -> str:
        entry = self.repo.find(kode)
        return f"{kode} — {entry['nama']}" if entry else kode
