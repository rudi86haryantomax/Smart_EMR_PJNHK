"""
tests/test_suara.py
==========================================
Test pemilihan metode transkripsi suara.

Yang dijaga: fitur suara TIDAK boleh mati hanya karena API key tidak ada.
Versi sebelumnya mensyaratkan `GOOGLE_STT_API_KEY`, sehingga pengguna yang
tidak punya akun Google Cloud sama sekali tidak bisa memakai fitur ini —
padahal ada jalur gratis yang tidak butuh key.

Jalankan:
    cd tests && python test_suara.py
"""

from __future__ import annotations

import io
import math
import os
import struct
import sys
import types
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_st = types.ModuleType("streamlit")
_st.secrets = {}
sys.modules.setdefault("streamlit", _st)

os.environ.pop("GOOGLE_STT_API_KEY", None)

from services import speech_service as S  # noqa: E402

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


def wav_contoh(detik: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(int(16000 * detik))
        ))
    return buf.getvalue()


def main() -> int:
    print("=" * 62)
    print("TEST 1 -- Tersedia TANPA API key")
    print("=" * 62)
    check("Tidak ada API key di lingkungan uji", not S.punya_cloud_key())
    check("Fitur suara tetap tersedia", S.is_available())
    check("Metode aktif = gratis", S.metode_aktif() == S.METODE_GRATIS, S.metode_aktif())
    check("Keterangan menyebut 'tanpa API key'",
          "tanpa API key" in S.keterangan_metode(), S.keterangan_metode())

    print("\n" + "=" * 62)
    print("TEST 2 -- Beralih ke Cloud bila key diisi")
    print("=" * 62)
    os.environ["GOOGLE_STT_API_KEY"] = "kunci-uji"
    try:
        check("Terdeteksi punya key", S.punya_cloud_key())
        check("Metode aktif = cloud", S.metode_aktif() == S.METODE_CLOUD, S.metode_aktif())
        check("Keterangan menyebut Cloud", "Cloud" in S.keterangan_metode())
    finally:
        os.environ.pop("GOOGLE_STT_API_KEY", None)
    check("Kembali ke gratis setelah key dilepas", S.metode_aktif() == S.METODE_GRATIS)

    print("\n" + "=" * 62)
    print("TEST 3 -- Penanganan masukan bermasalah")
    print("=" * 62)
    teks, galat = S.transcribe_safe(b"", "audio/wav")
    check("Audio kosong ditolak dengan pesan jelas",
          galat and "Tidak ada audio" in galat, galat[:60])

    teks, galat = S.transcribe_safe(b"bukan-audio", "audio/wav")
    check("Berkas bukan audio ditangani, bukan crash",
          galat and "Traceback" not in galat, galat[:60])
    check("Pesan menyebut format WAV", "WAV" in galat, galat[:80])

    teks, galat = S.transcribe_safe(b"x" * (11 * 1024 * 1024), "audio/wav")
    check("Rekaman terlalu besar ditolak", galat and "terlalu besar" in galat, galat[:60])

    print("\n" + "=" * 62)
    print("TEST 4 -- WAV valid diproses (bukan ditolak formatnya)")
    print("=" * 62)
    teks, galat = S.transcribe_safe(wav_contoh(), "audio/wav")
    # Di lingkungan tanpa internet, galat yang wajar adalah soal koneksi —
    # bukan soal format. Itu menandakan audio berhasil dibaca.
    check("Tidak ditolak karena format",
          "Format audio tidak dapat dibaca" not in galat, galat[:70])
    check("Hasilnya teks atau galat koneksi yang rapi",
          isinstance(teks, str) and (not galat or "dihubungi" in galat or "Google" in galat),
          galat[:70])

    print("\n" + "=" * 62)
    print("TEST 5 -- Daftar frasa klinis untuk metode Cloud")
    print("=" * 62)
    check("Memuat istilah kardio", "ortopnea" in S.FRASA_KLINIS and "JVP" in S.FRASA_KLINIS)
    check("Memuat singkatan intensif",
          all(x in S.FRASA_KLINIS for x in ("IABP", "SIMV", "PEEP", "CRRT")))
    check("Memuat istilah sehari-hari", "slem" in S.FRASA_KLINIS)
    check("Tidak melebihi batas Google (500 frasa)", len(S.FRASA_KLINIS) <= 500,
          len(S.FRASA_KLINIS))

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
