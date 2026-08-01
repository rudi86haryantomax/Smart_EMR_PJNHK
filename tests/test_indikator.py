"""
tests/test_indikator.py
==========================================
Test indikator luaran SLKI: target, baseline, dan waktu evaluasi.

LATAR BELAKANG
--------------
Sebelumnya luaran hanya berupa kode dan nama, mis. "L.02008 Curah Jantung
Meningkat". Rumusan itu tidak dapat dievaluasi: tidak jelas apa yang
diukur, berapa targetnya, dan kapan dinilai ulang. Indikator terukur
melengkapi bagian yang hilang itu.

Berkas indikator sengaja TERPISAH dari master 3S karena keduanya berubah
dengan irama berbeda — master mengikuti buku SDKI/SLKI/SIKI, sedangkan
target dan waktu evaluasi kerap disesuaikan kebijakan unit.

Jalankan:
    cd tests && python test_indikator.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ASUHAN_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="ind_test_"), "t.db")

_st = types.ModuleType("streamlit")
_st.secrets = {}
sys.modules.setdefault("streamlit", _st)

from models.asesmen import DiagnosisPilihan  # noqa: E402
from repositories.sdki_repository import SdkiRepository  # noqa: E402
from services import export_service as E  # noqa: E402
from services.diagnosis_service import DiagnosisService  # noqa: E402

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


def main() -> int:
    repo = SdkiRepository()
    service = DiagnosisService()

    print("=" * 62)
    print("TEST 1 -- Cakupan: setiap luaran punya indikator")
    print("=" * 62)
    doc = json.loads((ROOT / "data" / "indikator_slki.json").read_text(encoding="utf-8"))
    check("Berkas indikator termuat", bool(doc.get("luaran")))
    check("41 luaran punya indikator", len(doc["luaran"]) == 41, len(doc["luaran"]))

    dipakai = {e["luaran"]["kode"] for e in repo.all() if e.get("luaran", {}).get("kode")}
    kurang = sorted(dipakai - set(doc["luaran"]))
    check("Tidak ada luaran master tanpa indikator", not kurang, kurang)

    tanpa = [e["kode"] for e in repo.all() if not repo.punya_indikator(e["kode"])]
    check("Seluruh 55 diagnosis punya indikator", not tanpa, tanpa[:5])

    total = sum(len(v["indikator"]) for v in doc["luaran"].values())
    check(f"Total indikator memadai ({total})", total >= 200, total)

    print("\n" + "=" * 62)
    print("TEST 2 -- Struktur indikator")
    print("=" * 62)
    masalah_jenis, masalah_target, masalah_arah, masalah_satuan = [], [], [], []
    for kode, entri in doc["luaran"].items():
        for ind in entri["indikator"]:
            if ind.get("jenis") not in ("skala5", "angka"):
                masalah_jenis.append(f"{kode}/{ind.get('nama')}")
            if not str(ind.get("target") or "").strip():
                masalah_target.append(f"{kode}/{ind.get('nama')}")
            if ind.get("jenis") == "skala5" and ind.get("arah") not in (
                "meningkat", "menurun", "membaik"
            ):
                masalah_arah.append(f"{kode}/{ind.get('nama')}")
            if ind.get("jenis") == "angka" and not str(ind.get("satuan") or "").strip():
                masalah_satuan.append(f"{kode}/{ind.get('nama')}")

    check("Semua indikator berjenis sah", not masalah_jenis, masalah_jenis[:3])
    check("Semua indikator punya target", not masalah_target, masalah_target[:3])
    check("Indikator skala punya arah", not masalah_arah, masalah_arah[:3])
    check("Indikator angka punya satuan", not masalah_satuan, masalah_satuan[:3])

    arah_salah = [k for k, v in doc["luaran"].items()
                  if v.get("arah") not in ("meningkat", "menurun", "membaik")]
    check("Setiap luaran punya arah sah", not arah_salah, arah_salah[:3])
    tanpa_waktu = [k for k, v in doc["luaran"].items()
                   if not str(v.get("evaluasi_default") or "").strip()]
    check("Setiap luaran punya waktu evaluasi", not tanpa_waktu, tanpa_waktu[:3])

    print("\n" + "=" * 62)
    print("TEST 3 -- Akses lewat repository")
    print("=" * 62)
    ind = repo.indikator("D.0008")
    check("indikator() dengan kode diagnosis", len(ind) == 8, len(ind))
    check("indikator() dengan kode luaran", repo.indikator("L.02008") == ind)
    check("Kode tak dikenal -> []", repo.indikator("D.9999") == [])

    check("arah_luaran D.0008 = meningkat", repo.arah_luaran("D.0008") == "meningkat",
          repo.arah_luaran("D.0008"))
    check("evaluasi_default D.0008 = 8 jam", repo.evaluasi_default("D.0008") == "8 jam",
          repo.evaluasi_default("D.0008"))
    check("evaluasi_default kode tak dikenal -> nilai aman",
          repo.evaluasi_default("D.9999") == "24 jam")

    nama = [i["nama"] for i in ind]
    check("Indikator D.0008 memuat frekuensi nadi", "Frekuensi nadi" in nama, nama)
    check("Indikator D.0008 memuat CRT",
          any("kapiler" in n.lower() for n in nama), nama)

    print("\n" + "=" * 62)
    print("TEST 4 -- Waktu evaluasi sesuai kegawatan")
    print("=" * 62)
    # Luaran pada kondisi kritis dievaluasi lebih cepat daripada luaran
    # psikososial — bila terbalik, jadwalnya tidak masuk akal secara klinis.
    check("Sirkulasi spontan dievaluasi 1 jam",
          repo.evaluasi_default("L.02015") == "1 jam", repo.evaluasi_default("L.02015"))
    check("Perfusi serebral dievaluasi 4 jam",
          repo.evaluasi_default("L.02014") == "4 jam", repo.evaluasi_default("L.02014"))
    check("Nyeri dievaluasi setelah intervensi",
          "intervensi" in repo.evaluasi_default("L.08066"),
          repo.evaluasi_default("L.08066"))
    check("Citra tubuh dievaluasi mingguan",
          "minggu" in repo.evaluasi_default("L.09067"), repo.evaluasi_default("L.09067"))
    check("Risiko jatuh dievaluasi per shift",
          "shift" in repo.evaluasi_default("L.14138"), repo.evaluasi_default("L.14138"))

    print("\n" + "=" * 62)
    print("TEST 5 -- Indikator masuk ke tabel asuhan")
    print("=" * 62)
    tabel = service.rakit_tabel([
        DiagnosisPilihan("D.0008", 1, []),
        DiagnosisPilihan("D.0077", 2, []),
    ])
    baris = tabel[0]
    check("Tabel memuat indikator", len(baris.get("indikator", [])) == 8,
          len(baris.get("indikator", [])))
    check("Tabel memuat waktu evaluasi", baris.get("evaluasi_default") == "8 jam",
          baris.get("evaluasi_default"))
    check("Tabel memuat arah luaran", baris.get("arah_luaran") == "meningkat")
    check("Diagnosis kedua juga punya indikator",
          len(tabel[1].get("indikator", [])) > 0)

    hilang = service.rakit_tabel([DiagnosisPilihan("D.9999", 1, [])])[0]
    check("Kode tak dikenal tidak membuat gagal", hilang.get("indikator") == [])

    print("\n" + "=" * 62)
    print("TEST 6 -- Indikator ikut di ekspor")
    print("=" * 62)
    sel = E._sel_luaran(baris)
    check("Kolom luaran memuat kode SLKI", "L.02008" in sel)
    check("Kolom luaran memuat waktu evaluasi", "Evaluasi: 8 jam" in sel, sel[:80])
    check("Kolom luaran memuat daftar indikator", "Indikator" in sel)
    check("Baseline dikosongkan untuk diisi tangan", "......." in sel)
    check("Target tercantum", "60-100" in sel)
    check("Satuan tercantum", "x/menit" in sel)

    print("\n" + "=" * 62)
    print("TEST 7 -- Ketahanan bila berkas indikator hilang")
    print("=" * 62)
    # Indikator bersifat melengkapi; ketiadaannya tidak boleh mematikan
    # alur utama aplikasi.
    os.environ["ASUHAN_INDIKATOR_JSON"] = "/tmp/tidak-ada-berkas-ini.json"
    repo2 = SdkiRepository()
    try:
        check("Tanpa berkas indikator, aplikasi tetap jalan",
              repo2.indikator("D.0008") == [])
        check("Waktu evaluasi jatuh ke nilai aman",
              repo2.evaluasi_default("D.0008") == "24 jam")
        check("Diagnosis tetap dapat diambil", repo2.find("D.0008") is not None)
    finally:
        os.environ.pop("ASUHAN_INDIKATOR_JSON", None)

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
