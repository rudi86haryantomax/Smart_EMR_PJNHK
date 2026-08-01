"""
tests/test_form_state.py
==========================================
Test pengelolaan state form asesmen.

LATAR BELAKANG
--------------
Streamlit melarang mengubah `st.session_state[key]` setelah widget dengan
key itu dirender pada putaran yang sama. Versi sebelumnya menyimpan teks
langsung di key widget, sehingga DUA fitur gagal total:

  - hasil transkripsi suara tidak bisa dimasukkan ke kolom S/O
  - tombol "Buat asesmen baru" tidak berfungsi

keduanya dengan galat:
    st.session_state.s_teks cannot be modified after the widget
    with key s_teks is instantiated.

Perbaikannya: data disimpan di kunci tersendiri (`s_data`), terpisah dari
key widget (`s_w_{versi}`). Nomor versi dinaikkan untuk memaksa widget
dibuat ulang agar membaca nilai terbaru.

Mock di bawah MENIRU larangan Streamlit tersebut — kalau kode kembali
menulis ke key widget, test ini gagal seperti aplikasi sungguhan.

Jalankan:
    cd tests && python test_form_state.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ASUHAN_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="form_test_"), "t.db")

DIRENDER: set[str] = set()


class _State(dict):
    """session_state yang menegakkan aturan Streamlit soal key widget."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:
            raise AttributeError(k) from exc

    def __setattr__(self, k, v):
        self.__setitem__(k, v)

    def __setitem__(self, k, v):
        if k in DIRENDER:
            raise Exception(
                f"st.session_state.{k} cannot be modified after the widget "
                f"with key {k} is instantiated."
            )
        dict.__setitem__(self, k, v)


def _noop(*a, **k):
    return None


def _widget(label=None, value=None, key=None, **kw):
    if key:
        DIRENDER.add(key)
    return value if value is not None else ""


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, n):
        return _noop


_st = types.ModuleType("streamlit")
_st.session_state = _State()
for _n in ("markdown", "caption", "write", "divider", "subheader", "title",
           "info", "warning", "error", "success", "metric", "dataframe",
           "json", "code", "rerun"):
    setattr(_st, _n, _noop)
_st.text_area = _widget
_st.text_input = _widget
_st.checkbox = _widget
_st.button = lambda *a, **k: False
_st.audio_input = lambda *a, **k: None
_st.columns = lambda x, **k: [
    _Ctx() for _ in (x if isinstance(x, (list, tuple)) else range(x))
]
_st.tabs = lambda labels, **k: [_Ctx() for _ in labels]
_st.expander = _st.form = _st.spinner = lambda *a, **k: _Ctx()
_st.sidebar = _Ctx()
_st.secrets = {}
_st.cache_resource = lambda f: f
sys.modules["streamlit"] = _st

from pages import asesmen as A  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {extra}")


def render():
    DIRENDER.clear()
    A._langkah_input()
    return set(DIRENDER)


def main() -> int:
    A._init_state()

    print("=" * 62)
    print("TEST 1 -- Data terpisah dari key widget")
    print("=" * 62)
    keys = render()
    check("Widget memakai key ber-versi, bukan nama data",
          all(k.endswith("_w_0") for k in keys), sorted(keys))
    check("Kunci data BUKAN key widget",
          not ({"s_data", "o_data", "label_data"} & keys), sorted(keys))

    print("\n" + "=" * 62)
    print("TEST 2 -- Transkripsi setelah widget dirender (regresi bug)")
    print("=" * 62)
    try:
        A._tambah_teks("s_data", "pasien mengeluh sesak")
        check("Menambah teks tidak melanggar aturan Streamlit", True)
    except Exception as exc:
        check("Menambah teks tidak melanggar aturan Streamlit", False, str(exc)[:80])

    check("Teks masuk ke data", _st.session_state["s_data"] == "pasien mengeluh sesak",
          _st.session_state.get("s_data"))
    check("Versi form dinaikkan", _st.session_state["form_versi"] == 1,
          _st.session_state.get("form_versi"))
    check("Sumber input ditandai 'suara'", _st.session_state["sumber_input"] == "suara")

    A._tambah_teks("s_data", "dan mudah lelah")
    check("Transkripsi kedua DISAMBUNG, bukan menimpa",
          _st.session_state["s_data"] == "pasien mengeluh sesak dan mudah lelah",
          _st.session_state["s_data"])
    check("Sumber input jadi 'campuran' setelah ada isi sebelumnya",
          _st.session_state["sumber_input"] == "campuran")

    print("\n" + "=" * 62)
    print("TEST 3 -- Widget dibuat ulang membaca data terbaru")
    print("=" * 62)
    keys = render()
    check("Key widget ikut berubah versi",
          all(k.endswith("_w_2") for k in keys), sorted(keys))
    check("Data bertahan setelah render ulang",
          _st.session_state["s_data"] == "pasien mengeluh sesak dan mudah lelah")

    print("\n" + "=" * 62)
    print("TEST 4 -- Tombol 'Buat asesmen baru' (regresi bug)")
    print("=" * 62)
    _st.session_state["o_data"] = "edema tungkai"
    _st.session_state["label_data"] = "Bed 3"
    _st.session_state["dipilih"] = ["D.0008"]
    render()  # widget dirender lagi sebelum reset — inilah kondisi bug lama

    try:
        A._reset_form()
        check("Reset tidak melanggar aturan Streamlit", True)
    except Exception as exc:
        check("Reset tidak melanggar aturan Streamlit", False, str(exc)[:80])

    check("Data S dikosongkan", _st.session_state["s_data"] == "")
    check("Data O dikosongkan", _st.session_state["o_data"] == "")
    check("Penanda dikosongkan", _st.session_state["label_data"] == "")
    check("Daftar diagnosis dikosongkan", _st.session_state["dipilih"] == [])
    check("Tidak ada sisa centang intervensi",
          not [k for k in _st.session_state if k.startswith("iv_")])

    keys = render()
    check("Widget dibuat ulang setelah reset (key berganti)",
          all("_w_" in k and not k.endswith("_w_2") for k in keys), sorted(keys))

    print("\n" + "=" * 62)
    print("TEST 5 -- Layanan terjemahan opsional")
    print("=" * 62)
    from services import translate_service as T

    os.environ.pop("GOOGLE_TRANSLATE_API_KEY", None)
    check("Tanpa API key -> tidak tersedia", not T.is_available())

    hasil, galat = T.translate_safe("hello")
    check("Dipanggil tanpa key -> galat rapi, bukan crash",
          galat and "GOOGLE_TRANSLATE_API_KEY" in galat, galat[:60])

    os.environ["GOOGLE_TRANSLATE_API_KEY"] = "kunci-uji"
    try:
        check("Dengan API key -> tersedia", T.is_available())
    finally:
        os.environ.pop("GOOGLE_TRANSLATE_API_KEY", None)

    check("Teks kosong -> hasil kosong tanpa galat", T.translate_safe("") == ("", ""))

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
