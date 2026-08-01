"""
pages/tatalaksana
==========================================
Alur dokter: temuan klinis -> diagnosis kerja -> panduan tatalaksana (PPK).

Berbeda dari alur perawat yang menyusun beberapa diagnosis berprioritas,
alur ini mengarah ke SATU diagnosis kerja. Kandidat lain tetap
ditampilkan, tetapi diposisikan sebagai diagnosis banding yang perlu
disingkirkan — bukan daftar yang semuanya dipakai.
"""

from __future__ import annotations

import streamlit as st

from services import speech_service
from services.ppk_service import PpkService

_service = PpkService()

_LABEL_KRITERIA = {
    "anamnesis": "Anamnesis",
    "pemeriksaan_fisik": "Pemeriksaan Fisik",
    "penunjang": "Pemeriksaan Penunjang",
    "kriteria_diagnosis": "Kriteria Diagnosis",
}

_LABEL_TATALAKSANA = {
    "awal": "Tatalaksana Awal",
    "farmakologis": "Farmakologis",
    "non_farmakologis": "Non-Farmakologis",
    "rujukan": "Rawat & Rujukan",
}


def _init_state() -> None:
    st.session_state.setdefault("dok_temuan", "")
    st.session_state.setdefault("dok_ppk_dipilih", None)


# =====================================================
# INPUT
# =====================================================

def _rekam_suara() -> None:
    if not speech_service.is_available():
        return

    with st.expander("🎤 Rekam temuan klinis", expanded=False):
        st.caption("Maksimal sekitar 60 detik per rekaman.")
        audio = st.audio_input("Rekam", key="audio_dok")
        if audio is not None and st.button(
            "Transkripsi", key="btn_stt_dok", use_container_width=True
        ):
            with st.spinner("Mentranskripsi..."):
                teks, error = speech_service.transcribe_safe(
                    audio.getvalue(), getattr(audio, "type", "audio/wav")
                )
            if error:
                st.error(error)
            elif not teks:
                st.warning("Tidak ada ucapan yang dikenali.")
            else:
                lama = st.session_state.get("dok_temuan", "")
                # Simpan ke key sementara agar tidak langsung menimpa widget yang terkunci
                st.session_state["temp_transkripsi"] = f"{lama} {teks}".strip() if lama else teks
                st.success("Transkripsi berhasil, memperbarui...")
                st.rerun()


def _langkah_input() -> None:
    st.subheader("1. Temuan Klinis")

    # Cek apakah ada data transkripsi baru dari penekanan tombol sebelumnya
    if "temp_transkripsi" in st.session_state:
        st.session_state["dok_temuan"] = st.session_state.pop("temp_transkripsi")

    st.text_area(
        "Anamnesis, pemeriksaan fisik, dan hasil penunjang",
        key="dok_temuan",
        height=140,
        label_visibility="collapsed",
        placeholder=(
            "mis. nyeri dada retrosternal 30 menit menjalar ke lengan kiri, "
            "keringat dingin, EKG elevasi ST di V1-V4"
        ),
    )
    _rekam_suara()

# =====================================================
# USULAN
# =====================================================

def _kartu_usulan(item: dict, urutan: int) -> None:
    kode = item["kode"]
    penanda = "🔴 " if item.get("kritis") else ""

    col_info, col_aksi = st.columns([5, 1])
    with col_info:
        icd = f" · ICD-10 {item['icd10']}" if item.get("icd10") else ""
        st.markdown(
            f"**{penanda}{kode} — {item['nama']}**  \n"
            f"<span style='color:#666;font-size:0.85em'>"
            f"{item.get('kategori', '')}{icd} · "
            f"cocok pada: {', '.join(item['kata_cocok'][:8])}</span>",
            unsafe_allow_html=True,
        )
    with col_aksi:
        if st.button("Buka", key=f"ppk_{kode}_{urutan}", use_container_width=True):
            st.session_state["dok_ppk_dipilih"] = kode
            st.rerun()


def _langkah_usulan() -> None:
    st.subheader("2. Kemungkinan Diagnosis")

    temuan = st.session_state.get("dok_temuan", "")
    if not temuan.strip():
        st.info("Isi temuan klinis di atas untuk memunculkan usulan.")
        _cari_manual()
        return

    usulan = _service.usulkan(temuan, limit=6)

    if not usulan:
        st.warning("Tidak ada PPK yang cocok dengan kata kunci pada temuan ini.")
    else:
        st.caption(
            "Daftar ini hasil pencocokan kata kunci terhadap kriteria diagnosis — "
            "**bukan penalaran diagnostik dan bukan urutan kemungkinan**. "
            "Beberapa kondisi di sini berbagi gejala yang hampir sama, jadi "
            "perlakukan seluruhnya sebagai diagnosis banding yang harus "
            "disingkirkan, bukan peringkat."
        )
        for urutan, item in enumerate(usulan):
            _kartu_usulan(item, urutan)

    # Kondisi kritis yang tidak masuk daftar teratas ditampilkan terpisah.
    # Pada nyeri dada, diseksi aorta sering kalah skor dari SKA padahal
    # justru itu yang paling fatal bila terlewat -- dan tatalaksananya
    # berlawanan.
    terlewat = _service.kandidat_kritis_terlewat(
        temuan, [u["kode"] for u in usulan]
    )
    if terlewat:
        st.warning("**Jangan sampai terlewat** — kondisi kritis yang juga memiliki kecocokan:")
        for urutan, item in enumerate(terlewat):
            _kartu_usulan(item, 100 + urutan)

    st.divider()
    _cari_manual()


def _cari_manual() -> None:
    with st.expander("🔍 Cari PPK manual", expanded=False):
        tab_cari, tab_kategori = st.tabs(["Cari", "Jelajahi Kategori"])

        with tab_cari:
            kata = st.text_input("Kode, nama diagnosis, atau ICD-10", key="dok_cari")
            if kata.strip():
                hasil = _service.cari(kata, limit=15)
                if not hasil:
                    st.caption("Tidak ditemukan.")
                for e in hasil:
                    col_a, col_b = st.columns([5, 1])
                    col_a.markdown(f"**{e['kode']}** — {e['nama']}")
                    if col_b.button("Buka", key=f"cari_{e['kode']}", use_container_width=True):
                        st.session_state["dok_ppk_dipilih"] = e["kode"]
                        st.rerun()

        with tab_kategori:
            kategori = st.selectbox(
                "Kategori", options=_service.kategori_list(), key="dok_kategori"
            )
            for e in _service.by_kategori(kategori):
                col_a, col_b = st.columns([5, 1])
                col_a.markdown(f"**{e['kode']}** — {e['nama']}")
                if col_b.button("Buka", key=f"kat_{e['kode']}", use_container_width=True):
                    st.session_state["dok_ppk_dipilih"] = e["kode"]
                    st.rerun()


# =====================================================
# PANDUAN LENGKAP
# =====================================================

def _tampilkan_ppk(kode: str) -> None:
    ppk = _service.rakit(kode)
    if not ppk:
        st.error(f"PPK '{kode}' tidak ditemukan.")
        st.session_state["dok_ppk_dipilih"] = None
        return

    if st.button("← Kembali ke daftar", use_container_width=False):
        st.session_state["dok_ppk_dipilih"] = None
        st.rerun()

    penanda = "🔴 " if ppk["kritis"] else ""
    st.markdown(f"## {penanda}{ppk['nama']}")

    col1, col2, col3 = st.columns(3)
    col1.caption(f"**Kode:** {ppk['kode']}")
    col2.caption(f"**ICD-10:** {ppk['icd10'] or '-'}")
    col3.caption(f"**Kategori:** {ppk['kategori']}")

    if ppk["kritis"]:
        st.error("Kondisi mengancam jiwa — tatalaksana bersifat segera.")

    st.info(ppk["definisi"])

    st.divider()
    st.markdown("### Kriteria Diagnosis")
    for bagian, isi in ppk["kriteria"]:
        if not isi:
            continue
        with st.expander(_LABEL_KRITERIA.get(bagian, bagian.capitalize()),
                         expanded=bagian in ("anamnesis", "kriteria_diagnosis")):
            for baris in isi:
                st.markdown(f"- {baris}")

    st.divider()
    st.markdown("### Panduan Tatalaksana")
    for bagian, isi in ppk["tatalaksana"]:
        if not isi:
            continue
        with st.expander(_LABEL_TATALAKSANA.get(bagian, bagian.capitalize()),
                         expanded=bagian == "awal"):
            for baris in isi:
                st.markdown(f"- {baris}")

    if ppk["komplikasi"]:
        st.divider()
        with st.expander("Komplikasi yang Diwaspadai", expanded=False):
            for baris in ppk["komplikasi"]:
                st.markdown(f"- {baris}")

    if ppk["edukasi"]:
        with st.expander("Edukasi Pasien & Keluarga", expanded=False):
            for baris in ppk["edukasi"]:
                st.markdown(f"- {baris}")

    st.divider()
    st.caption(f"Referensi: {ppk['referensi']}")
    st.caption(
        "Panduan ini adalah kerangka kerja, bukan pengganti penilaian klinis. "
        "Dosis obat mengikuti formularium dan protokol rumah sakit."
    )


# =====================================================
# ENTRY
# =====================================================

def render() -> None:
    _init_state()
    st.title("🩺 Panduan Praktik Klinis")

    peringatan = _service.peringatan_sumber
    if peringatan and "DRAF" in peringatan.upper():
        st.warning(peringatan)

    dipilih = st.session_state.get("dok_ppk_dipilih")
    if dipilih:
        _tampilkan_ppk(dipilih)
        return

    st.caption("Masukkan temuan klinis, lalu pilih panduan yang sesuai.")
    _langkah_input()
    st.divider()
    _langkah_usulan()
