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

from typing import Any

from core.kategori import kategori_dari_luaran, urutkan_prioritas
from models.asesmen import Asesmen, DiagnosisPilihan
from repositories.sdki_repository import SdkiRepository


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
        return hasil

    def usulkan_untuk(self, asesmen: Asesmen, limit: int = 8) -> list[dict[str, Any]]:
        return self.usulkan(asesmen.data_subjektif, asesmen.data_objektif, limit)

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
