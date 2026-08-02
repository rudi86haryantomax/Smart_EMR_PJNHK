"""
tests/test_ui_intervensi.py
==========================================
Test pemilihan intervensi pada halaman asesmen.

Dua hal yang dijaga di sini:

1. **Tombol pilih-semua benar-benar bekerja.** Di Streamlit, nilai widget
   di `session_state` selalu menang atas parameter `value=`. Kalau
   checkbox dirender dengan `value=`, tombol centang massal akan tampak
   tidak berpengaruh — bug yang sulit dikenali karena kodenya terlihat
   benar.

2. **State checkbox tidak terbawa antar-asesmen.** Streamlit menyimpan
   nilai widget berdasarkan key, dan nilai itu bertahan meski widget-nya
   tidak dirender lagi. Versi sebelumnya membersihkan `intervensi_pilih`
   saat membuat asesmen baru, tetapi tidak membersihkan key checkbox-nya
   — sehingga memilih diagnosis yang sama untuk pasien berikutnya
   menampilkan tindakan yang sudah tercentang dari pasien sebelumnya.
   Perawat melihat pilihan yang tidak pernah ia buat.

Streamlit di-mock; yang diuji logikanya, bukan tampilan.

Jalankan:
    cd tests && python test_ui_intervensi.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ASUHAN_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="ui_test_"), "t.db")

# --------------------------------------------------
# Mock Streamlit
# --------------------------------------------------
KLIK: dict[str, bool] = {}
RERUN: list[int] = []


class _State(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:
            raise AttributeError(k) from exc

    def __setattr__(self, k, v):
        self[k] = v


def _button(label, key=None, **kw):
    return KLIK.get(key, False)


def _checkbox(label, key=None, **kw):
    return bool(_st.session_state.get(key))


def _noop(*a, **k):
    return None


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, n):
        return _noop


class _Col(_Ctx):
    button = staticmethod(_button)
    caption = staticmethod(_noop)
    markdown = staticmethod(_noop)


_st = types.ModuleType("streamlit")
_st.session_state = _State()
for _n in ("markdown", "caption", "write", "divider", "subheader", "title",
           "info", "warning", "error", "success", "text_area", "text_input",
           "selectbox", "audio_input", "metric", "dataframe", "json"):
    setattr(_st, _n, _noop)
_st.button = _button
_st.checkbox = _checkbox
_st.columns = lambda spec, **k: [
    _Col() for _ in (spec if isinstance(spec, (list, tuple)) else range(spec))
]
_st.expander = _st.form = _st.spinner = lambda *a, **k: _Ctx()
_st.tabs = lambda labels, **k: [_Ctx() for _ in labels]
_st.rerun = lambda: RERUN.append(1)
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


def klik(nama_tombol: str, kode: str) -> None:
    """Tekan tombol lalu jalankan putaran berikutnya (meniru st.rerun)."""
    KLIK.clear()
    RERUN.clear()
    KLIK[nama_tombol] = True
    A._pilih_intervensi(kode)
    KLIK.clear()
    A._pilih_intervensi(kode)


def dipilih(kode: str) -> list[str]:
    return _st.session_state["intervensi_pilih"].get(kode, [])


def main() -> int:
    KODE = "D.0008"
    intervensi = A._service.intervensi(KODE)
    total = len(A._service.repo.flat_intervensi(KODE))
    n_observasi = len(intervensi["observasi"])

    print("=" * 62)
    print(f"TEST 1 -- Keadaan awal ({KODE}, {total} tindakan)")
    print("=" * 62)
    _st.session_state["intervensi_pilih"] = {}
    A._pilih_intervensi(KODE)
    check("Belum ada yang tercentang", len(dipilih(KODE)) == 0, dipilih(KODE))
    check(f"Master punya {total} tindakan", total > 5, total)

    print("\n" + "=" * 62)
    print("TEST 2 -- Tombol 'Pilih semua'")
    print("=" * 62)
    KLIK.clear()
    RERUN.clear()
    KLIK[f"all_{KODE}"] = True
    A._pilih_intervensi(KODE)
    check("Memicu rerun", len(RERUN) > 0)
    KLIK.clear()
    A._pilih_intervensi(KODE)
    check(f"Semua {total} tindakan tercentang", len(dipilih(KODE)) == total,
          f"{len(dipilih(KODE))}/{total}")
    check("Isi sesuai daftar master",
          set(dipilih(KODE)) == set(A._service.repo.flat_intervensi(KODE)))

    print("\n" + "=" * 62)
    print("TEST 3 -- Tombol 'Kosongkan'")
    print("=" * 62)
    klik(f"none_{KODE}", KODE)
    check("Semua centang hilang", len(dipilih(KODE)) == 0, dipilih(KODE))

    print("\n" + "=" * 62)
    print("TEST 4 -- Pilih semua per kategori")
    print("=" * 62)
    klik(f"all_{KODE}_observasi", KODE)
    check(f"Hanya observasi tercentang ({n_observasi})",
          len(dipilih(KODE)) == n_observasi, f"{len(dipilih(KODE))} vs {n_observasi}")
    check("Isinya memang butir observasi",
          set(dipilih(KODE)) == set(intervensi["observasi"]))

    klik(f"all_{KODE}_terapeutik", KODE)
    n_gabung = n_observasi + len(intervensi["terapeutik"])
    check("Kategori kedua menambah, bukan mengganti",
          len(dipilih(KODE)) == n_gabung, f"{len(dipilih(KODE))} vs {n_gabung}")

    print("\n" + "=" * 62)
    print("TEST 5 -- State tidak terbawa antar-asesmen (regresi bug)")
    print("=" * 62)
    klik(f"all_{KODE}", KODE)
    check("Semua tercentang sebelum reset", len(dipilih(KODE)) == total)

    A._reset_form()
    # State checkbox di-set False, BUKAN dihapus dengan `del`. `del` saat
    # dipanggil dari dalam callback `on_click` memicu galat session_state
    # di tengah jalan, sehingga reset berhenti sebelum tuntas dan halaman
    # hasil tidak pernah berpindah ke form baru.
    tercentang = [k for k in _st.session_state
                  if k.startswith("iv_") and _st.session_state[k]]
    check("Tidak ada checkbox yang masih tercentang setelah reset",
          not tercentang, tercentang[:3])

    _st.session_state["intervensi_pilih"] = {}
    A._pilih_intervensi(KODE)
    check("Asesmen baru mulai bersih", len(dipilih(KODE)) == 0, dipilih(KODE))

    print("\n" + "=" * 62)
    print("TEST 6 -- Membersihkan satu diagnosis saja")
    print("=" * 62)
    klik(f"all_{KODE}", KODE)
    A._pilih_intervensi("D.0077")
    klik("all_D.0077", "D.0077")
    check("Dua diagnosis punya centang",
          len(dipilih(KODE)) > 0 and len(dipilih("D.0077")) > 0)

    A._bersihkan_centang_intervensi(KODE)
    sisa_kode = [k for k in _st.session_state
                 if k.startswith(f"iv_{KODE}_") and _st.session_state[k]]
    sisa_lain = [k for k in _st.session_state
                 if k.startswith("iv_D.0077_") and _st.session_state[k]]
    check(f"Centang {KODE} direset", not sisa_kode, sisa_kode[:3])
    check("Centang diagnosis lain TIDAK ikut direset", len(sisa_lain) > 0)

    print("\n" + "=" * 62)
    print("TEST 7 -- Diagnosis tanpa sebagian kategori tetap aman")
    print("=" * 62)
    # Sebagian diagnosis punya kategori intervensi yang kosong.
    kode_pendek = next(
        (e["kode"] for e in A._service.semua()
         if not (e["intervensi"].get("edukasi") or [])),
        None,
    )
    if kode_pendek:
        _st.session_state["intervensi_pilih"] = {}
        A._pilih_intervensi(kode_pendek)
        klik(f"all_{kode_pendek}", kode_pendek)
        harapan = len(A._service.repo.flat_intervensi(kode_pendek))
        check(f"{kode_pendek}: pilih semua tetap benar ({harapan})",
              len(dipilih(kode_pendek)) == harapan,
              f"{len(dipilih(kode_pendek))} vs {harapan}")
    else:
        check("Semua diagnosis punya 4 kategori terisi", True)

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
