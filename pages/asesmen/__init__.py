"""
pages/asesmen
==========================================
Alur utama: input data S & O -> usulan diagnosis -> pilih & susun
prioritas -> simpan -> tabel asuhan lengkap.

Dibuat sebagai satu halaman berurutan (bukan beberapa halaman terpisah)
karena keempat langkah itu satu tarikan kerja. Memecahnya membuat perawat
kehilangan konteks data S/O saat sedang memilih diagnosis.
"""

from __future__ import annotations

import streamlit as st

from components import tabel_asuhan
from database.connection import baca_saja, unit_of_work
from models.asesmen import Asesmen, DiagnosisPilihan
from repositories.asesmen_repository import AsesmenRepository
from services import speech_service, translate_service
from services.diagnosis_service import DiagnosisService

_service = DiagnosisService()


# =====================================================
# STATE
# =====================================================

# Data disimpan pada kunci TERSENDIRI, terpisah dari key widget.
#
# Streamlit melarang mengubah `session_state[key]` setelah widget dengan
# key itu dirender pada putaran yang sama. Versi sebelumnya menyimpan teks
# langsung di key widget ("s_teks"), sehingga hasil transkripsi maupun
# tombol "Asesmen Baru" gagal dengan galat:
#     st.session_state.s_teks cannot be modified after the widget
#     with key s_teks is instantiated.
#
# Dengan memisahkan data ("s_data") dari key widget ("s_w_{versi}"), data
# boleh diubah kapan saja; nomor versi dinaikkan untuk memaksa widget
# dibuat ulang agar membaca nilai terbaru.


def _versi() -> int:
    return st.session_state.get("form_versi", 0)


def _segarkan_form() -> None:
    """Paksa widget teks dibuat ulang agar membaca data terbaru."""
    st.session_state["form_versi"] = _versi() + 1


def _init_state() -> None:
    st.session_state.setdefault("s_data", "")
    st.session_state.setdefault("o_data", "")
    st.session_state.setdefault("label_data", "")
    st.session_state.setdefault("form_versi", 0)
    st.session_state.setdefault("dipilih", [])          # list kode, urut = prioritas
    st.session_state.setdefault("intervensi_pilih", {})  # kode -> list tindakan
    st.session_state.setdefault("sumber_input", "teks")
    st.session_state.setdefault("asesmen_tersimpan", None)


def _reset_form() -> None:
    for key in ("s_data", "o_data", "label_data"):
        st.session_state[key] = ""
    _segarkan_form()
    st.session_state["dipilih"] = []
    st.session_state["intervensi_pilih"] = {}
    st.session_state["sumber_input"] = "teks"
    st.session_state["asesmen_tersimpan"] = None
    _bersihkan_centang_intervensi()


def reset_asesmen() -> None:
    """
    Titik masuk publik untuk memulai asesmen baru dari LUAR modul ini.

    Dipakai oleh menu "Asesmen Baru" di sidebar (`app.py`), yang murni
    tombol navigasi pindah `halaman` -- beda dari tombol
    "➕ Buat asesmen baru" di `_tampilkan_hasil()` yang sudah memicu
    `_reset_form()` sendiri lewat `on_click`. Tanpa titik masuk ini,
    pindah ke menu "Asesmen Baru" cuma mengganti halaman tanpa
    membersihkan draf S/O/penanda/diagnosis, karena `_init_state()`
    memakai `setdefault` -- tidak pernah menimpa nilai yang sudah ada
    di session_state.
    """
    _reset_form()


# =====================================================
# LANGKAH 1 -- INPUT
# =====================================================

def _tambah_teks(kunci_data: str, teks: str) -> None:
    """Sambung hasil transkripsi ke teks yang ada, bukan menimpanya."""
    lama = st.session_state.get(kunci_data, "")
    st.session_state[kunci_data] = f"{lama} {teks}".strip() if lama else teks
    st.session_state["sumber_input"] = "campuran" if lama else "suara"
    _segarkan_form()


def _proses_audio(audio_bytes: bytes, mime: str, kunci_data: str) -> None:
    """Transkripsi lalu langsung masukkan ke field."""
    with st.spinner("Mengubah suara jadi teks..."):
        teks, galat = speech_service.transcribe_safe(audio_bytes, mime)

    if galat:
        st.error(galat)
        return
    if not teks:
        st.warning("Tidak ada ucapan yang dikenali. Coba bicara lebih dekat ke mikrofon.")
        return

    _tambah_teks(kunci_data, teks)
    st.rerun()


def _rekam_suara(kunci_data: str, judul: str) -> None:
    """
    Perekam suara satu tombol: tekan untuk mulai, tekan lagi untuk berhenti,
    dan teks langsung muncul.

    Pemutar audio sengaja tidak ditampilkan dan tidak ada tombol
    transkripsi terpisah. Rekaman di sini bukan untuk didengarkan ulang,
    hanya untuk diubah menjadi teks — setiap langkah tambahan di antaranya
    hanya memperlambat pekerjaan di samping tempat tidur pasien.
    """
    if not speech_service.is_available():
        return

    slot = "s" if kunci_data.startswith("s") else "o"

    try:
        from streamlit_mic_recorder import mic_recorder

        rekaman = mic_recorder(
            start_prompt=f"🎤  Rekam {judul}",
            stop_prompt="⏹️  Berhenti — ubah ke teks",
            just_once=True,
            use_container_width=True,
            format="wav",
            key=f"mic_{slot}_{_versi()}",
        )
        if rekaman and rekaman.get("bytes"):
            _proses_audio(rekaman["bytes"], "audio/wav", kunci_data)
        return
    except ImportError:
        pass

    # Cadangan bila streamlit-mic-recorder tidak terpasang: perekam bawaan
    # Streamlit. Alurnya dua langkah, tetapi tetap berfungsi.
    with st.expander(f"🎤 Rekam suara untuk {judul}", expanded=False):
        st.caption("Maksimal sekitar 60 detik per rekaman.")
        audio = st.audio_input(f"Rekam {judul}", key=f"audio_{slot}_{_versi()}")
        if audio is not None and st.button(
            "Ubah ke teks", key=f"btn_stt_{slot}", use_container_width=True
        ):
            _proses_audio(audio.getvalue(), getattr(audio, "type", "audio/wav"), kunci_data)


def _terjemahkan(kunci_data: str) -> None:
    """
    Terjemahkan isi field ke bahasa Indonesia.

    Berguna karena kriteria SDKI berbahasa Indonesia: bagian catatan yang
    ditulis dalam bahasa Inggris tidak akan pernah cocok sebelum
    diterjemahkan. Hanya muncul bila API key Translate tersedia.
    """
    if not translate_service.is_available():
        return
    if not st.session_state.get(kunci_data, "").strip():
        return

    slot = "s" if kunci_data.startswith("s") else "o"
    if st.button("🌐 Terjemahkan ke Indonesia", key=f"tr_{slot}_{_versi()}",
                 use_container_width=True):
        with st.spinner("Menerjemahkan..."):
            hasil, galat = translate_service.translate_safe(
                st.session_state[kunci_data], "id"
            )
        if galat:
            st.error(galat)
        elif hasil and hasil.strip() != st.session_state[kunci_data].strip():
            st.session_state[kunci_data] = hasil
            _segarkan_form()
            st.rerun()
        else:
            st.info("Teks sudah berbahasa Indonesia.")


def _langkah_input() -> None:
    st.subheader("1. Data Asesmen")

    if not speech_service.is_available():
        st.caption(
            "Fitur suara nonaktif — pustaka pengenalan suara belum terpasang "
            "(`pip install SpeechRecognition`). Input teks tetap bisa dipakai."
        )

    versi = _versi()

    st.session_state["label_data"] = st.text_input(
        "Penanda (opsional)",
        value=st.session_state.get("label_data", ""),
        key=f"label_w_{versi}",
        placeholder="mis. Bed 3 / Tn. A / Shift pagi",
    )

    col_s, col_o = st.columns(2)
    for kolom, slot, kunci_data, judul, contoh in (
        (col_s, "s", "s_data", "S — Data Subjektif",
         "mis. mengeluh sesak saat berbaring, mudah lelah, sulit tidur"),
        (col_o, "o", "o_data", "O — Data Objektif",
         "mis. edema tungkai, JVP meningkat, ronkhi basal, TD 150/90"),
    ):
        with kolom:
            st.markdown(f"**{judul}**")
            # Nilai diambil dari data, hasil suntingan disimpan balik ke data.
            # Key widget diberi nomor versi supaya bisa dibuat ulang saat
            # isinya diubah dari luar (transkripsi, terjemahan, reset).
            st.session_state[kunci_data] = st.text_area(
                judul,
                value=st.session_state.get(kunci_data, ""),
                height=150,
                label_visibility="collapsed",
                placeholder=contoh,
                key=f"{slot}_w_{versi}",
            )
            _rekam_suara(kunci_data, judul.split("—")[-1].strip())
            _terjemahkan(kunci_data)


# =====================================================
# LANGKAH 2 -- USULAN DIAGNOSIS
# =====================================================

def _langkah_usulan() -> None:
    st.subheader("2. Usulan Diagnosis")

    s_teks = st.session_state.get("s_data", "")
    o_teks = st.session_state.get("o_data", "")

    if not f"{s_teks}{o_teks}".strip():
        st.info("Isi data S dan/atau O di atas untuk memunculkan usulan.")
        return

    usulan = _service.usulkan(s_teks, o_teks, limit=8)

    if not usulan:
        st.warning(
            "Tidak ada diagnosis yang cocok dengan kata kunci pada data ini. "
            "Gunakan pencarian manual di bawah."
        )
    else:
        st.caption(
            "Usulan berasal dari pencocokan kata kunci terhadap kriteria SDKI — "
            "**bukan penegakan diagnosis**. Periksa dasar kecocokannya, lalu "
            "pilih sendiri yang sesuai kondisi pasien."
        )

        dipilih = st.session_state["dipilih"]
        for item in usulan:
            kode = item["kode"]
            sudah = kode in dipilih
            col_info, col_aksi = st.columns([5, 1])

            with col_info:
                tanda = "⚠️ " if item.get("perlu_verifikasi") else ""
                st.markdown(
                    f"**{tanda}{kode} — {item['nama']}**  \n"
                    f"<span style='color:#666;font-size:0.85em'>"
                    f"{item['jenis']} · {item['kategori']} · "
                    f"cocok pada: {', '.join(item['kata_cocok'])}</span>",
                    unsafe_allow_html=True,
                )
                if item.get("perlu_verifikasi"):
                    st.caption(
                        "⚠️ Diagnosis ini belum diverifikasi terhadap SDKI resmi "
                        "(tambahan internal). Pastikan sesuai kebijakan unit Anda."
                    )
                with st.expander("Lihat kriteria", expanded=False):
                    kriteria = item["diagnosis"].get("kriteria", {})
                    for label, kunci in (("Mayor", "mayor"), ("Minor", "minor"),
                                         ("Faktor risiko", "faktor_risiko")):
                        isi = kriteria.get(kunci) or []
                        if isi:
                            st.markdown(f"*{label}*")
                            for k in isi:
                                st.markdown(f"- {k}")

            with col_aksi:
                if sudah:
                    st.button("✓ Dipilih", key=f"sel_{kode}", disabled=True,
                              use_container_width=True)
                elif st.button("Pilih", key=f"sel_{kode}", use_container_width=True):
                    dipilih.append(kode)
                    st.rerun()

    # Pencarian manual disandingkan, bukan disembunyikan sebagai cadangan:
    # pencocokan kata kunci pasti melewatkan diagnosis kalau perawat
    # memakai istilah yang tidak ada di kriteria.
    st.divider()
    with st.expander("🔍 Cari diagnosis manual", expanded=not usulan):
        kata = st.text_input("Kode atau nama diagnosis", key="cari_manual")
        if kata.strip():
            hasil = _service.cari(kata, limit=15)
            if not hasil:
                st.caption("Tidak ditemukan.")
            for entry in hasil:
                kode = entry["kode"]
                col_a, col_b = st.columns([5, 1])
                col_a.markdown(f"**{kode}** — {entry['nama']}")
                if kode in st.session_state["dipilih"]:
                    col_b.button("✓", key=f"man_{kode}", disabled=True,
                                 use_container_width=True)
                elif col_b.button("Pilih", key=f"man_{kode}", use_container_width=True):
                    st.session_state["dipilih"].append(kode)
                    st.rerun()


# =====================================================
# LANGKAH 3 -- PRIORITAS
# =====================================================

def _langkah_prioritas() -> None:
    dipilih: list[str] = st.session_state.get("dipilih", [])
    if not dipilih:
        return

    st.subheader("3. Urutan Prioritas")
    st.caption(
        f"{len(dipilih)} diagnosis dipilih. Urutan menentukan prioritas asuhan "
        "— nomor 1 dikerjakan lebih dulu."
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("↕️ Susun otomatis (kaidah ABC)", use_container_width=True):
            st.session_state["dipilih"] = _service.usulkan_prioritas(dipilih)
            st.rerun()
    with col_b:
        if st.button("🗑️ Kosongkan pilihan", use_container_width=True):
            st.session_state["dipilih"] = []
            st.session_state["intervensi_pilih"] = {}
            _bersihkan_centang_intervensi()
            st.rerun()

    st.caption(
        "Susun otomatis memakai urutan Respirasi → Sirkulasi → Keamanan → "
        "kebutuhan lain, dengan diagnosis Aktual didahulukan atas Risiko. "
        "Ini titik awal; sesuaikan dengan kondisi pasien."
    )

    for posisi, kode in enumerate(dipilih):
        entry = _service.detail(kode)
        nama = entry["nama"] if entry else kode
        kategori = _service.repo.kategori(kode) if entry else "-"

        col_no, col_nama, col_up, col_down, col_del = st.columns([0.5, 5, 0.7, 0.7, 0.7])
        col_no.markdown(f"### {posisi + 1}")
        col_nama.markdown(
            f"**{kode} — {nama}**  \n"
            f"<span style='color:#666;font-size:0.85em'>{kategori}</span>",
            unsafe_allow_html=True,
        )

        if col_up.button("↑", key=f"up_{kode}", disabled=posisi == 0,
                         use_container_width=True):
            dipilih[posisi - 1], dipilih[posisi] = dipilih[posisi], dipilih[posisi - 1]
            st.rerun()
        if col_down.button("↓", key=f"down_{kode}", disabled=posisi == len(dipilih) - 1,
                           use_container_width=True):
            dipilih[posisi + 1], dipilih[posisi] = dipilih[posisi], dipilih[posisi + 1]
            st.rerun()
        if col_del.button("✕", key=f"del_{kode}", use_container_width=True):
            dipilih.pop(posisi)
            st.session_state["intervensi_pilih"].pop(kode, None)
            _bersihkan_centang_intervensi(kode)
            st.rerun()

        with st.expander(f"Pilih intervensi untuk {kode}", expanded=False):
            _pilih_intervensi(kode)


_KATEGORI_SIKI = ("observasi", "terapeutik", "edukasi", "kolaborasi")


def _kunci_intervensi(kode: str, kategori: str, idx: int) -> str:
    return f"iv_{kode}_{kategori}_{idx}"


def _semua_kunci(kode: str, intervensi: dict) -> list[str]:
    return [
        _kunci_intervensi(kode, kategori, idx)
        for kategori in _KATEGORI_SIKI
        for idx in range(len(intervensi.get(kategori) or []))
    ]


def _set_centang(kunci_list: list[str], nilai: bool) -> None:
    for kunci in kunci_list:
        st.session_state[kunci] = nilai


def _bersihkan_centang_intervensi(kode: str | None = None) -> None:
    """
    Reset state checkbox intervensi ke tidak tercentang.

    Perlu dilakukan eksplisit: Streamlit menyimpan nilai widget di
    `session_state` berdasarkan key, dan nilai itu BERTAHAN meski
    widget-nya tidak dirender lagi. Tanpa pembersihan ini, membuat
    asesmen baru lalu memilih diagnosis yang sama akan menampilkan
    tindakan yang sudah tercentang dari pasien sebelumnya — perawat
    melihat pilihan yang tidak pernah ia buat.

    Nilai di-set jadi False, BUKAN dihapus (`del`). Fungsi ini juga
    dipanggil dari `_reset_form()`, yang dijalankan lewat `on_click`
    tombol "➕ Buat asesmen baru" di halaman hasil. `del` pada key
    checkbox saat dipanggil dari dalam callback `on_click` memicu
    galat session_state di tengah jalan — callback berhenti sebelum
    sempat menuntaskan reset, sehingga `asesmen_tersimpan` gagal
    berubah jadi None dan halaman hasil tidak pernah berpindah ke
    form baru. Meng-set ke False aman dipanggil baik dari callback
    maupun dari alur render biasa, dan tetap membuat checkbox tampil
    kosong saat diagnosis yang sama dipakai lagi.
    """
    awalan = f"iv_{kode}_" if kode else "iv_"
    for kunci in [k for k in st.session_state if k.startswith(awalan)]:
        st.session_state[kunci] = False


def _pilih_intervensi(kode: str) -> None:
    """
    Centang tindakan SIKI yang akan dikerjakan.

    Kalau tidak ada yang dicentang, tabel akhir menampilkan SELURUH
    intervensi sebagai acuan — supaya perawat yang melewati langkah ini
    tetap mendapat rencana lengkap, bukan tabel kosong.
    """
    intervensi = _service.intervensi(kode)
    terpilih = set(st.session_state["intervensi_pilih"].get(kode, []))
    semua_kunci = _semua_kunci(kode, intervensi)

    # Nilai awal ditanam ke session_state lebih dulu, bukan lewat parameter
    # `value=` pada checkbox. Kalau memakai `value=`, tombol "pilih semua"
    # tidak akan berpengaruh: nilai dari session_state selalu menang atas
    # `value=`, sehingga centang massal tampak tidak terjadi.
    for kategori in _KATEGORI_SIKI:
        for idx, tindakan in enumerate(intervensi.get(kategori) or []):
            kunci = _kunci_intervensi(kode, kategori, idx)
            if kunci not in st.session_state:
                st.session_state[kunci] = tindakan in terpilih

    jumlah_total = len(semua_kunci)
    jumlah_dipilih = sum(1 for k in semua_kunci if st.session_state.get(k))

    # Tombol ditaruh SEBELUM checkbox. Streamlit tidak mengizinkan
    # mengubah nilai widget yang sudah dirender pada putaran yang sama,
    # jadi urutannya menentukan.
    col_semua, col_kosong, col_info = st.columns([1.1, 1.1, 2])
    if col_semua.button("✓ Pilih semua", key=f"all_{kode}", use_container_width=True):
        _set_centang(semua_kunci, True)
        st.rerun()
    if col_kosong.button("Kosongkan", key=f"none_{kode}", use_container_width=True):
        _set_centang(semua_kunci, False)
        st.rerun()
    col_info.caption(f"{jumlah_dipilih} dari {jumlah_total} tindakan dipilih")

    baru: list[str] = []
    for kategori in _KATEGORI_SIKI:
        tindakan_list = intervensi.get(kategori) or []
        if not tindakan_list:
            continue

        kunci_kategori = [
            _kunci_intervensi(kode, kategori, idx) for idx in range(len(tindakan_list))
        ]
        col_judul, col_aksi = st.columns([4, 1])
        col_judul.markdown(f"*{kategori.capitalize()}*")
        # Pilih-semua per kategori berguna karena perawat sering mengambil
        # seluruh observasi tapi menyaring terapeutik sesuai kondisi pasien.
        if col_aksi.button(
            "semua", key=f"all_{kode}_{kategori}", use_container_width=True
        ):
            _set_centang(kunci_kategori, True)
            st.rerun()

        for idx, tindakan in enumerate(tindakan_list):
            if st.checkbox(tindakan, key=_kunci_intervensi(kode, kategori, idx)):
                baru.append(tindakan)

    st.session_state["intervensi_pilih"][kode] = baru


# =====================================================
# LANGKAH 4 -- SIMPAN
# =====================================================

def _langkah_simpan() -> None:
    dipilih: list[str] = st.session_state.get("dipilih", [])
    if not dipilih:
        return

    st.subheader("4. Simpan")
    catatan = st.text_area("Catatan tambahan (opsional)", height=70, key="catatan")

    if not st.button("💾 Simpan Asuhan", type="primary", use_container_width=True):
        return

    asesmen = Asesmen(
        label=st.session_state.get("label_data", ""),
        data_subjektif=st.session_state.get("s_data", ""),
        data_objektif=st.session_state.get("o_data", ""),
        sumber_input=st.session_state.get("sumber_input", "teks"),
        catatan=catatan,
    )

    if not asesmen.is_valid():
        st.error("Data S dan O tidak boleh kosong keduanya.")
        return

    pilihan = [
        DiagnosisPilihan(
            kode_diagnosis=kode,
            prioritas=urutan,
            intervensi_dipilih=st.session_state["intervensi_pilih"].get(kode, []),
        )
        for urutan, kode in enumerate(dipilih, start=1)
    ]

    try:
        # Asesmen dan daftar diagnosisnya ditulis dalam SATU transaksi,
        # supaya tidak pernah ada asesmen tersimpan tanpa diagnosisnya.
        with unit_of_work() as conn:
            repo = AsesmenRepository(conn)
            asesmen_id = repo.create(asesmen)
            repo.set_diagnosis(asesmen_id, pilihan)
        st.session_state["asesmen_tersimpan"] = asesmen_id
        st.success("Asuhan tersimpan.")
        st.rerun()
    except Exception as exc:
        st.error(f"Gagal menyimpan: {exc}")


# =====================================================
# HASIL
# =====================================================

def _tampilkan_hasil(asesmen_id: int) -> None:
    with baca_saja() as conn:
        asesmen = AsesmenRepository(conn).find(asesmen_id)

    if not asesmen:
        st.error("Asesmen tidak ditemukan.")
        return

    tabel = _service.rakit_tabel(asesmen.diagnosis)

    st.success(f"Tersimpan sebagai **{asesmen.nomor}**")
    tabel_asuhan.render(asesmen, tabel)

    st.divider()
    
    # Perbaikan pada tombol di bawah ini:
    st.button(
        "➕ Buat asesmen baru", 
        type="primary", 
        use_container_width=True,
        on_click=_reset_form
    )


# =====================================================
# ENTRY
# =====================================================

def render() -> None:
    _init_state()
    st.title("🩺 Asuhan Keperawatan")

    tersimpan = st.session_state.get("asesmen_tersimpan")
    if tersimpan:
        _tampilkan_hasil(tersimpan)
        return

    st.caption("Isi data asesmen, pilih diagnosis, susun prioritas, lalu simpan.")
    _langkah_input()
    st.divider()
    _langkah_usulan()
    st.divider()
    _langkah_prioritas()
    _langkah_simpan()
