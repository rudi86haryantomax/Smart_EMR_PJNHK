"""
app.py
==========================================
Aplikasi Asuhan Keperawatan berbasis SDKI / SLKI / SIKI.

Jalankan:
    streamlit run app.py

Aplikasi ini SENGAJA dibuat tanpa login dan tanpa manajemen pasien --
fokusnya satu hal: membantu perawat menyusun tabel diagnosis, luaran, dan
intervensi dari data asesmen. Identitas pasien tidak disimpan; kalau
diperlukan penanda, pakai kolom "Penanda" yang bebas diisi (mis. nomor
bed) sehingga tidak ada data pribadi yang tersimpan di berkas SQLite.

Struktur berlapisnya mengikuti proyek besar SmartCare:
    app.py / pages / components   -> tampilan
        services                  -> business rules
            repositories          -> akses data
                models / core     -> domain & konfigurasi
                database          -> koneksi SQLite
"""

from __future__ import annotations

import importlib
import traceback

import streamlit as st

st.set_page_config(
    page_title="Asuhan Keperawatan",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

from core import lingkungan, profesi as prof  # noqa: E402
from database.connection import baca_saja, init_database  # noqa: E402
from repositories.asesmen_repository import AsesmenRepository  # noqa: E402
from services import speech_service, translate_service  # noqa: E402
from services.diagnosis_service import DiagnosisService  # noqa: E402

MENU = {
    "asesmen":     {"judul": "Asesmen Baru",   "ikon": "💉", "modul": "pages.asesmen"},
    "tatalaksana": {"judul": "Tatalaksana",    "ikon": "🩺", "modul": "pages.tatalaksana"},
    "riwayat":     {"judul": "Riwayat",        "ikon": "📚", "modul": "pages.riwayat"},
}


@st.cache_resource
def _siapkan_database() -> bool:
    """
    Buat tabel sekali per proses. `cache_resource` dipakai supaya skema
    tidak diperiksa ulang pada setiap rerun Streamlit.
    """
    init_database()
    return True


def _pilih_profesi() -> None:
    """
    Pemilihan profesi. BUKAN autentikasi — tidak ada verifikasi identitas
    maupun pembatasan kewenangan; ini hanya menentukan alur kerja mana
    yang ditampilkan.
    """
    st.title("Selamat datang")
    st.caption("Pilih profesi Anda untuk menentukan alur kerja.")

    # Peringatan lingkungan ditaruh di sini -- layar pertama yang pasti
    # dilihat -- bukan disembunyikan di sidebar atau dokumentasi.
    _peringatan_lingkungan()
    st.write("")

    kolom = st.columns(len(prof.PROFESI))
    for kolom_ke, (kode, meta) in zip(kolom, prof.semua()):
        with kolom_ke:
            st.markdown(f"### {meta['ikon']} {meta['nama']}")
            st.caption(meta["deskripsi"])
            if st.button(
                f"Masuk sebagai {meta['nama']}",
                key=f"pilih_{kode}",
                use_container_width=True,
                type="primary",
            ):
                st.session_state["profesi"] = kode
                st.session_state["halaman"] = prof.halaman_awal(kode)
                st.rerun()

    st.divider()
    st.caption(
        "Pilihan ini tidak membatasi akses dan tidak menyimpan identitas — "
        "hanya menentukan tampilan alur kerja."
    )
    st.caption("Developed by Ns. Rudi Haryanto, S.Kep., M.M " \
    "(rudi07haryanto@gmail.com)")


def _peringatan_lingkungan() -> None:
    """
    Tampilkan keterbatasan lingkungan yang tidak terlihat dari layar.

    Nadanya mengikuti mode pemakaian: pada mode pembelajaran cukup
    informasi biasa, pada mode klinis barulah ditegaskan sebagai
    peringatan. Peringatan gawat yang muncul terus tanpa sebab justru
    melatih orang mengabaikannya.
    """
    klinis = lingkungan.mode_klinis()

    if lingkungan.bisa_diakses_publik():
        (st.error if klinis else st.info)(lingkungan.pesan_publik())
    if lingkungan.penyimpanan_sementara():
        (st.warning if klinis else st.caption)(lingkungan.pesan_sementara())


def _sidebar() -> str:
    with st.sidebar:
        st.markdown("## 🩺 Asuhan Keperawatan")
        st.caption("SDKI · SLKI · SIKI")
        st.divider()

        profesi = st.session_state.get("profesi", prof.DEFAULT)
        st.success(f"{prof.ikon(profesi)} {prof.nama(profesi)}")
        if st.button("Ganti profesi", use_container_width=True, key="ganti_profesi"):
            _reset_profesi()
            st.rerun()
        st.divider()

        halaman = st.session_state.get("halaman", prof.halaman_awal(profesi))
        for kode in prof.menu_untuk(profesi):
            meta = MENU.get(kode)
            if not meta:
                continue
            aktif = "✅ " if kode == halaman else ""
            if st.button(
                f"{aktif}{meta['ikon']} {meta['judul']}",
                key=f"nav_{kode}",
                use_container_width=True,
            ):
                if kode == "asesmen":
                    _mulai_asesmen_baru()
                st.session_state["halaman"] = kode
                st.rerun()

        st.divider()
        if lingkungan.penyimpanan_sementara():
            st.caption("Riwayat bersifat sementara — unduh bila perlu disimpan.")
        if lingkungan.bisa_diakses_publik() and lingkungan.mode_klinis():
            st.caption("⚠️ Aplikasi publik — jangan masukkan identitas pasien.")
        _status()

        st.divider()
        st.markdown("💬 [Saran & Masukan](https://forms.gle/mhmcJk7mHX4WNPBRA)")
        st.caption("Developed by Ns. Rudi Haryanto, S.Kep., M.M " \
            "(rudi07haryanto@gmail.com)")


    profesi = st.session_state.get("profesi", prof.DEFAULT)
    return st.session_state.get("halaman", prof.halaman_awal(profesi))


def _reset_profesi() -> None:
    """Kembali ke pemilihan profesi dan bersihkan state alur kerja."""
    # "dok_data" menggantikan "dok_temuan" setelah data dipisahkan dari
    # key widget di pages/tatalaksana; keduanya dibersihkan agar sisa
    # dari versi lama ikut terhapus.
    for kunci in ("profesi", "halaman", "dok_ppk_dipilih",
                  "dok_data", "dok_temuan", "dok_versi",
                  "asesmen_tersimpan", "riwayat_dibuka"):
        st.session_state.pop(kunci, None)


def _mulai_asesmen_baru() -> None:
    """
    Bersihkan draf asesmen yang sedang berjalan sebelum masuk ke menu
    "Asesmen Baru" di sidebar.

    Tombol menu ini murni navigasi (pindah `halaman`) -- beda dari
    tombol "➕ Buat asesmen baru" di halaman hasil asesmen yang sudah
    memicu reset sendiri lewat `on_click`. Tanpa pemanggilan eksplisit
    ini, S/O, penanda, dan diagnosis yang belum tersimpan tetap
    nempel setiap kali menu ini ditekan -- termasuk saat perawat
    sedang di halaman lain lalu kembali ke "Asesmen Baru".

    Dibungkus try/except supaya urutan pemasangan patch tidak saling
    menjatuhkan: kalau `pages/asesmen/__init__.py` belum diperbarui
    dengan `reset_asesmen()`, menu tetap bisa dipakai untuk pindah
    halaman seperti sebelumnya, hanya saja belum membersihkan draf.
    """
    try:
        from pages.asesmen import reset_asesmen
        reset_asesmen()
    except ImportError:
        pass


def _status() -> None:
    """Ringkasan kondisi aplikasi, membantu memastikan setup sudah benar."""
    profesi = st.session_state.get("profesi", prof.DEFAULT)

    try:
        if profesi == prof.DOKTER:
            from services.ppk_service import PpkService
            jumlah_dx = PpkService().repo.count()
            label_dx = "Panduan PPK"
        else:
            jumlah_dx = DiagnosisService().repo.count()
            label_dx = "Master 3S"
    except Exception:
        jumlah_dx, label_dx = 0, "Master"

    try:
        with baca_saja() as conn:
            total = AsesmenRepository(conn).total()
    except Exception:
        total = 0

    st.caption(f"{label_dx}: **{jumlah_dx}** entri")
    st.caption(f"Asesmen tersimpan: **{total}**")

    if speech_service.is_available():
        st.caption("Voice-to-text: **aktif**")
        with st.expander("Metode transkripsi"):
            st.caption(speech_service.keterangan_metode())
            if not speech_service.punya_cloud_key():
                st.caption(
                    "Berjalan tanpa API key. Untuk akurasi istilah klinis yang "
                    "lebih baik, Google Cloud Speech-to-Text dapat dipasang "
                    "lewat `GOOGLE_STT_API_KEY` — opsional dan berbayar."
                )
    else:
        st.caption("Voice-to-text: nonaktif")
        with st.expander("Cara mengaktifkan"):
            st.markdown(
                "Pasang pustaka pengenalan suara (gratis, tanpa API key):\n\n"
                "```bash\npip install SpeechRecognition\n```"
            )

    if translate_service.is_available():
        st.caption("Terjemahan: **aktif**")


def main() -> None:
    _siapkan_database()

    if not st.session_state.get("profesi"):
        _pilih_profesi()
        return

    profesi = st.session_state["profesi"]
    st.session_state.setdefault("halaman", prof.halaman_awal(profesi))

    halaman = _sidebar()
    # Kalau halaman tersimpan tidak termasuk alur profesi saat ini (mis.
    # setelah berganti profesi), kembalikan ke halaman awal profesi itu.
    if not prof.boleh_akses(halaman, profesi):
        halaman = prof.halaman_awal(profesi)
        st.session_state["halaman"] = halaman

    meta = MENU.get(halaman) or MENU[prof.halaman_awal(profesi)]

    try:
        modul = importlib.import_module(meta["modul"])
        modul.render()
    except Exception as exc:
        # Satu halaman yang gagal tidak boleh mematikan seluruh aplikasi;
        # sidebar tetap bisa dipakai untuk berpindah.
        st.error(f"Terjadi kesalahan: {exc}")
        with st.expander("Detail teknis"):
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
