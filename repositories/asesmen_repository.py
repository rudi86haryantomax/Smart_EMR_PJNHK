"""
repositories/asesmen_repository.py
==========================================
Penyimpanan sesi asesmen dan diagnosis yang dipilih perawat.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models.asesmen import Asesmen, DiagnosisPilihan
from repositories.base_repository import BaseRepository


class AsesmenRepository(BaseRepository):
    TABLE = "asesmen"
    TABLE_DX = "asesmen_diagnosis"

    # =================================================
    # NOMOR ASESMEN
    # =================================================

    def generate_nomor(self) -> str:
        """
        Nomor urut harian: ASM-YYYYMMDD-NNN.

        Nomor urutnya dihitung dari MAX yang sudah ada pada hari yang sama,
        bukan dari COUNT. Kalau memakai COUNT, menghapus satu asesmen akan
        membuat nomor berikutnya menabrak nomor yang masih terpakai.
        """
        stamp = datetime.now().strftime("%Y%m%d")
        prefix = f"ASM-{stamp}-"
        row = self.fetch_one(
            f"SELECT nomor FROM {self.TABLE} WHERE nomor LIKE ? "
            "ORDER BY nomor DESC LIMIT 1",
            (f"{prefix}%",),
        )
        urut = 1
        if row:
            try:
                urut = int(str(row["nomor"]).rsplit("-", 1)[-1]) + 1
            except (ValueError, IndexError):
                urut = 1
        return f"{prefix}{urut:03d}"

    # =================================================
    # SIMPAN
    # =================================================

    def create(self, asesmen: Asesmen) -> int:
        now = self.now()
        asesmen.nomor = asesmen.nomor or self.generate_nomor()
        return self.insert(self.TABLE, {
            "nomor": asesmen.nomor,
            "label": asesmen.label,
            "data_subjektif": asesmen.data_subjektif,
            "data_objektif": asesmen.data_objektif,
            "sumber_input": asesmen.sumber_input,
            "catatan": asesmen.catatan,
            "dibuat_pada": now,
            "diperbarui_pada": now,
        })

    def update_asesmen(self, asesmen: Asesmen) -> int:
        if not asesmen.id:
            raise ValueError("Asesmen belum punya id.")
        return self.update(self.TABLE, {
            "label": asesmen.label,
            "data_subjektif": asesmen.data_subjektif,
            "data_objektif": asesmen.data_objektif,
            "sumber_input": asesmen.sumber_input,
            "catatan": asesmen.catatan,
            "diperbarui_pada": self.now(),
        }, "id=?", (asesmen.id,))

    def set_diagnosis(self, asesmen_id: int, pilihan: list[DiagnosisPilihan]) -> int:
        """
        Ganti seluruh daftar diagnosis untuk satu asesmen.

        Hapus-lalu-sisipkan dipilih (bukan diff per baris) karena urutan
        prioritas bisa berubah menyeluruh saat perawat menyusun ulang, dan
        menyamakannya baris demi baris lebih rawan salah daripada menulis
        ulang daftar yang memang pendek.
        """
        self.execute(f"DELETE FROM {self.TABLE_DX} WHERE asesmen_id=?", (asesmen_id,))

        now = self.now()
        jumlah = 0
        for urutan, item in enumerate(pilihan, start=1):
            kode = str(item.kode_diagnosis or "").strip()
            if not kode:
                continue
            self.insert(self.TABLE_DX, {
                "asesmen_id": asesmen_id,
                "kode_diagnosis": kode,
                "prioritas": item.prioritas or urutan,
                "intervensi_dipilih": item.intervensi_json(),
                "dibuat_pada": now,
            })
            jumlah += 1

        self.update(self.TABLE, {"diperbarui_pada": now}, "id=?", (asesmen_id,))
        return jumlah

    # =================================================
    # BACA
    # =================================================

    def find(self, asesmen_id: int) -> Asesmen | None:
        row = self.fetch_one(f"SELECT * FROM {self.TABLE} WHERE id=?", (asesmen_id,))
        if not row:
            return None
        asesmen = Asesmen.from_row(row)
        asesmen.diagnosis = self.diagnosis_of(asesmen_id)
        return asesmen

    def find_by_nomor(self, nomor: str) -> Asesmen | None:
        row = self.fetch_one(f"SELECT * FROM {self.TABLE} WHERE nomor=?", (nomor,))
        if not row:
            return None
        asesmen = Asesmen.from_row(row)
        asesmen.diagnosis = self.diagnosis_of(asesmen.id)
        return asesmen

    def diagnosis_of(self, asesmen_id: int) -> list[DiagnosisPilihan]:
        rows = self.fetch_all(
            f"SELECT * FROM {self.TABLE_DX} WHERE asesmen_id=? ORDER BY prioritas ASC, id ASC",
            (asesmen_id,),
        )
        return [DiagnosisPilihan.from_row(r) for r in rows]

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Riwayat asesmen terbaru + jumlah diagnosisnya, untuk halaman riwayat."""
        rows = self.fetch_all(
            f"""
            SELECT a.*, COUNT(d.id) AS jumlah_diagnosis
            FROM {self.TABLE} a
            LEFT JOIN {self.TABLE_DX} d ON d.asesmen_id = a.id
            GROUP BY a.id
            ORDER BY a.dibuat_pada DESC, a.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        out = []
        for row in rows:
            data = dict(row)
            asesmen = Asesmen.from_row(row)
            out.append({
                "id": asesmen.id,
                "nomor": asesmen.nomor,
                "label": asesmen.label,
                "ringkas": asesmen.ringkas,
                "sumber_input": asesmen.sumber_input,
                "jumlah_diagnosis": int(data.get("jumlah_diagnosis") or 0),
                "dibuat_pada": asesmen.dibuat_pada,
            })
        return out

    def total(self) -> int:
        row = self.fetch_one(f"SELECT COUNT(*) AS n FROM {self.TABLE}")
        return int(row["n"]) if row else 0

    # =================================================
    # HAPUS
    # =================================================

    def delete(self, asesmen_id: int) -> int:
        # Baris diagnosis ikut terhapus lewat ON DELETE CASCADE -- lihat
        # database/connection.py, foreign key SQLite harus diaktifkan
        # eksplisit agar ini benar-benar berlaku.
        cur = self.execute(f"DELETE FROM {self.TABLE} WHERE id=?", (asesmen_id,))
        return cur.rowcount
