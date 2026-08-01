"""
pages/riwayat
==========================================
Daftar asesmen yang pernah dibuat, dan tampilan tabel asuhan lengkapnya.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import tabel_asuhan
from core import lingkungan
from database.connection import baca_saja, unit_of_work
from repositories.asesmen_repository import AsesmenRepository
from services.diagnosis_service import DiagnosisService

_service = DiagnosisService()


def _daftar() -> None:
    with baca_saja() as conn:
        rows = AsesmenRepository(conn).list_recent(limit=100)

    if not rows:
        st.info("Belum ada asesmen tersimpan.")
        return

    st.caption(f"{len(rows)} asesmen terakhir.")

    df = pd.DataFrame([
        {
            "No. Asesmen": r["nomor"],
            "Penanda": r["label"] or "-",
            "Ringkasan": r["ringkas"],
            "Diagnosis": r["jumlah_diagnosis"],
            "Sumber": (r["sumber_input"] or "teks").capitalize(),
            "Dibuat": r["dibuat_pada"],
        }
        for r in rows
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    pilihan = {r["nomor"]: r["id"] for r in rows}
    nomor = st.selectbox(
        "Buka asesmen",
        options=list(pilihan.keys()),
        format_func=lambda n: f"{n} — {next((r['ringkas'] for r in rows if r['nomor'] == n), '')}",
        key="riwayat_pilih",
    )

    if nomor:
        st.session_state["riwayat_dibuka"] = pilihan[nomor]


def _tampilkan(asesmen_id: int) -> None:
    with baca_saja() as conn:
        asesmen = AsesmenRepository(conn).find(asesmen_id)

    if not asesmen:
        st.error("Asesmen tidak ditemukan.")
        st.session_state.pop("riwayat_dibuka", None)
        return

    tabel = _service.rakit_tabel(asesmen.diagnosis)
    tabel_asuhan.render(asesmen, tabel)

    st.divider()
    col1, col2 = st.columns([3, 1])
    with col1:
        if st.button("← Kembali ke daftar", use_container_width=True):
            st.session_state.pop("riwayat_dibuka", None)
            st.rerun()
    with col2:
        # Konfirmasi dipisah dari tombol hapus supaya satu kali klik tidak
        # langsung menghapus catatan asuhan.
        konfirmasi = st.checkbox("Konfirmasi hapus", key=f"konf_{asesmen_id}")
        if st.button("🗑️ Hapus", disabled=not konfirmasi, use_container_width=True):
            with unit_of_work() as conn:
                AsesmenRepository(conn).delete(asesmen_id)
            st.session_state.pop("riwayat_dibuka", None)
            st.success(f"{asesmen.nomor} dihapus.")
            st.rerun()


def render() -> None:
    st.title("📚 Riwayat Asesmen")

    # Justru di halaman inilah konsekuensinya paling nyata: daftar di bawah
    # berisi catatan SEMUA pengguna, dan bisa lenyap saat aplikasi restart.
    klinis = lingkungan.mode_klinis()
    if lingkungan.bisa_diakses_publik():
        (st.error if klinis else st.info)(lingkungan.pesan_publik())
    if lingkungan.penyimpanan_sementara():
        (st.warning if klinis else st.caption)(lingkungan.pesan_sementara())

    dibuka = st.session_state.get("riwayat_dibuka")
    if dibuka:
        _tampilkan(dibuka)
    else:
        _daftar()
