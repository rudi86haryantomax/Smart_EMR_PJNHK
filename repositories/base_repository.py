"""
repositories/base_repository.py
==========================================
Dasar seluruh repository: eksekusi SQL dan konversi hasil.

Semua repository menerima `conn` dari luar (bukan membuka sendiri),
supaya beberapa repository bisa berbagi SATU transaksi -- penting saat
menyimpan asesmen beserta daftar diagnosisnya secara atomik.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Sequence


class BaseRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --------------------------------------------------
    @staticmethod
    def now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.execute(sql, params).fetchall())

    def insert(self, table: str, payload: dict[str, Any]) -> int:
        cols = ", ".join(payload)
        holes = ", ".join("?" for _ in payload)
        cur = self.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({holes})", list(payload.values())
        )
        return int(cur.lastrowid)

    def update(self, table: str, payload: dict[str, Any], where: str, params: Sequence[Any]) -> int:
        sets = ", ".join(f"{k}=?" for k in payload)
        cur = self.execute(
            f"UPDATE {table} SET {sets} WHERE {where}",
            list(payload.values()) + list(params),
        )
        return cur.rowcount
