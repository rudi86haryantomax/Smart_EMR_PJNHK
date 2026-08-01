"""
components/tabel_asuhan.py
==========================================
Tampilan tabel asuhan lengkap: diagnosis + luaran (SLKI) + intervensi (SIKI).

Dipakai halaman asesmen (setelah simpan) dan halaman riwayat, supaya
bentuk tampilannya sama di kedua tempat.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from models.asesmen import Asesmen
from services import export_service

_KATEGORI_SIKI = ("observasi", "terapeutik", "edukasi", "kolaborasi")


def _header(asesmen: Asesmen) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("No. Asesmen", asesmen.nomor or "-")
    col2.metric("Jumlah Diagnosis", len(asesmen.diagnosis))
    col3.metric("Sumber Input", (asesmen.sumber_input or "teks").capitalize())

    if asesmen.label:
        st.caption(f"Penanda: {asesmen.label}")

    with st.expander("Data asesmen (S & O)", expanded=False):
        st.markdown("**S — Subjektif**")
        st.write(asesmen.data_subjektif or "-")
        st.markdown("**O — Objektif**")
        st.write(asesmen.data_objektif or "-")
        if asesmen.catatan:
            st.markdown("**Catatan**")
            st.write(asesmen.catatan)


def _ringkasan(tabel: list[dict[str, Any]]) -> None:
    """Ringkasan satu baris per diagnosis, untuk melihat urutan sekilas."""
    df = pd.DataFrame([
        {
            "Prioritas": item["prioritas"],
            "Kode": item["kode"],
            "Diagnosis": item["nama"],
            "Jenis": item.get("jenis") or "-",
            "Kategori": item.get("kategori") or "-",
            "Luaran (SLKI)": (item.get("luaran") or {}).get("nama", "-"),
        }
        for item in tabel
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)


def _detail(item: dict[str, Any]) -> None:
    luaran = item.get("luaran") or {}
    intervensi = item.get("intervensi") or {}
    dipilih = set(item.get("intervensi_dipilih") or [])

    if item.get("hilang"):
        st.warning(
            f"Kode **{item['kode']}** tidak ada lagi di master 3S saat ini. "
            "Catatan tetap ditampilkan apa adanya."
        )
        return

    st.markdown(
        f"**Luaran (SLKI):** {luaran.get('kode', '-')} — {luaran.get('nama', '-')}"
    )

    indikator = item.get("indikator") or []
    if indikator:
        evaluasi = item.get("evaluasi_default") or "24 jam"
        st.caption(f"Evaluasi dijadwalkan: **{evaluasi}** setelah intervensi dimulai")

        # Baseline sengaja dibiarkan kosong untuk diisi perawat saat
        # penilaian awal. Mengisinya otomatis akan menjadi tebakan, dan
        # baseline yang salah membuat evaluasi kemajuan ikut salah.
        df = pd.DataFrame([
            {
                "Indikator": i["nama"],
                "Baseline": "",
                "Target": i.get("target", ""),
                "Satuan": i.get("satuan", "") or ("skala 1-5" if i["jenis"] == "skala5" else ""),
            }
            for i in indikator
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

        if item.get("catatan_luaran"):
            st.warning(item["catatan_luaran"])
    if item.get("perlu_verifikasi"):
        st.warning(
            "Diagnosis tambahan internal — belum diverifikasi terhadap SDKI resmi."
        )
    if item.get("catatan"):
        st.info(item["catatan"])

    kriteria = item.get("kriteria") or {}
    with st.expander("Kriteria diagnostik", expanded=False):
        for label, kunci in (("Mayor", "mayor"), ("Minor", "minor"),
                             ("Faktor risiko", "faktor_risiko")):
            isi = kriteria.get(kunci) or []
            if isi:
                st.markdown(f"*{label}*")
                for k in isi:
                    st.markdown(f"- {k}")

    st.markdown("**Intervensi (SIKI)**")
    # Kalau perawat tidak mencentang apa pun, seluruh intervensi ditampilkan
    # sebagai acuan -- lebih berguna daripada tabel kosong.
    tampilkan_semua = not dipilih

    for kategori in _KATEGORI_SIKI:
        tindakan = intervensi.get(kategori) or []
        if not tindakan:
            continue
        st.markdown(f"*{kategori.capitalize()}*")
        for t in tindakan:
            if tampilkan_semua:
                st.markdown(f"- {t}")
            elif t in dipilih:
                st.markdown(f"- ✅ {t}")
            else:
                st.markdown(
                    f"- <span style='color:#999'>{t}</span>",
                    unsafe_allow_html=True,
                )

    if tampilkan_semua:
        st.caption(
            "Tidak ada intervensi yang dicentang, seluruh rencana ditampilkan "
            "sebagai acuan."
        )


def _unduh(asesmen: Asesmen, tabel: list[dict[str, Any]]) -> None:
    """
    Tombol unduh. Word ditaruh paling kiri karena itu yang paling sering
    dipakai — tabelnya siap disalin langsung ke dokumen asuhan.
    """
    st.caption(
        "Word dan Excel berisi tabel askep siap pakai "
        "(No · Diagnosis · Luaran · Intervensi), sudah diatur lanskap dan siap cetak."
    )

    kolom = st.columns(3)

    with kolom[0]:
        if export_service.docx_tersedia():
            st.download_button(
                "⬇️ Word (.docx)",
                data=export_service.ke_docx(asesmen, tabel),
                file_name=export_service.nama_berkas(asesmen, "docx"),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
                type="primary",
            )
        else:
            st.button("Word (.docx)", disabled=True, use_container_width=True,
                      help="Butuh python-docx: pip install python-docx")

    with kolom[1]:
        if export_service.xlsx_tersedia():
            st.download_button(
                "⬇️ Excel (.xlsx)",
                data=export_service.ke_xlsx(asesmen, tabel),
                file_name=export_service.nama_berkas(asesmen, "xlsx"),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("Excel (.xlsx)", disabled=True, use_container_width=True,
                      help="Butuh openpyxl: pip install openpyxl")

    with kolom[2]:
        st.download_button(
            "⬇️ Markdown (.md)",
            data=export_service.ke_markdown(asesmen, tabel),
            file_name=export_service.nama_berkas(asesmen, "md"),
            mime="text/markdown",
            use_container_width=True,
        )


def render(asesmen: Asesmen, tabel: list[dict[str, Any]]) -> None:
    _header(asesmen)

    if not tabel:
        st.info("Belum ada diagnosis pada asesmen ini.")
        return

    st.divider()
    st.subheader("Ringkasan")
    _ringkasan(tabel)

    st.divider()
    st.subheader("Rincian Asuhan")
    for item in tabel:
        with st.expander(
            f"{item['prioritas']}. {item['kode']} — {item['nama']}",
            expanded=item["prioritas"] == 1,
        ):
            _detail(item)

    st.divider()
    _unduh(asesmen, tabel)
