"""
models/asesmen.py
==========================================
Domain model: satu sesi asesmen dan diagnosis yang dipilih di dalamnya.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DiagnosisPilihan:
    """Satu diagnosis yang dipilih perawat, beserta urutan prioritasnya."""

    kode_diagnosis: str
    prioritas: int = 1
    intervensi_dipilih: list[str] = field(default_factory=list)
    id: int | None = None
    asesmen_id: int | None = None
    dibuat_pada: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "DiagnosisPilihan":
        data = dict(row)
        raw = data.get("intervensi_dipilih")
        try:
            terpilih = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            terpilih = []
        return cls(
            id=data.get("id"),
            asesmen_id=data.get("asesmen_id"),
            kode_diagnosis=data.get("kode_diagnosis", ""),
            prioritas=int(data.get("prioritas") or 1),
            intervensi_dipilih=terpilih,
            dibuat_pada=data.get("dibuat_pada"),
        )

    def intervensi_json(self) -> str:
        return json.dumps(self.intervensi_dipilih, ensure_ascii=False)


@dataclass
class Asesmen:
    """Satu sesi asesmen keperawatan (data S dan O)."""

    nomor: str = ""
    label: str = ""
    data_subjektif: str = ""
    data_objektif: str = ""
    sumber_input: str = "teks"
    catatan: str = ""
    id: int | None = None
    dibuat_pada: str | None = None
    diperbarui_pada: str | None = None
    diagnosis: list[DiagnosisPilihan] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: Any) -> "Asesmen":
        data = dict(row)
        return cls(
            id=data.get("id"),
            nomor=data.get("nomor", ""),
            label=data.get("label") or "",
            data_subjektif=data.get("data_subjektif") or "",
            data_objektif=data.get("data_objektif") or "",
            sumber_input=data.get("sumber_input") or "teks",
            catatan=data.get("catatan") or "",
            dibuat_pada=data.get("dibuat_pada"),
            diperbarui_pada=data.get("diperbarui_pada"),
        )

    @property
    def teks_gabungan(self) -> str:
        """S dan O digabung -- ini yang dianalisis untuk usulan diagnosis."""
        return f"{self.data_subjektif}\n{self.data_objektif}".strip()

    @property
    def ringkas(self) -> str:
        teks = self.teks_gabungan.replace("\n", " ")
        return teks[:80] + "..." if len(teks) > 80 else teks or "(kosong)"

    def is_valid(self) -> bool:
        return bool(self.teks_gabungan.strip())

    def touch(self) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.diperbarui_pada = now
        self.dibuat_pada = self.dibuat_pada or now
