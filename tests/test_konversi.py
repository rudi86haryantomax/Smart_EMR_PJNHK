"""
tests/test_konversi.py
==========================================
Test konversi Excel <-> JSON.

Yang paling penting diuji di sini: ROUND-TRIP tidak boleh mengubah isi.
Konversi yang diam-diam memotong atau menggabung butir jauh lebih
berbahaya daripada konversi yang gagal terang-terangan — kalimat klinis
yang terpotong berubah maknanya tanpa ada yang tahu.

Jalankan:
    cd tests && python test_konversi.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from _konversi import (  # noqa: E402
    deteksi_format_json,
    deteksi_format_kolom,
    gabung_sel,
    ke_bool,
    pecah_sel,
    ambil,
    taruh,
)

PASS = 0
FAIL = 0
TMP = tempfile.mkdtemp(prefix="konversi_test_")


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {extra}")


def jalankan(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, cwd=str(ROOT)
    )


def main() -> int:
    print("=" * 62)
    print("TEST 1 -- Pemecahan sel multi-baris")
    print("=" * 62)
    check("Baris baru jadi pemisah",
          pecah_sel("satu\ndua\ntiga") == ["satu", "dua", "tiga"])
    check("Tanda pipa jadi pemisah",
          pecah_sel("satu | dua") == ["satu", "dua"])
    check("Sel kosong -> []", pecah_sel("") == [] and pecah_sel(None) == [])
    check("Penanda daftar '-' dibuang",
          pecah_sel("- satu\n- dua") == ["satu", "dua"])
    check("Penomoran '1.' dibuang",
          pecah_sel("1. satu\n2. dua") == ["satu", "dua"])
    check("Baris kosong diabaikan",
          pecah_sel("satu\n\n\ndua") == ["satu", "dua"])

    # Ini inti perbaikan bug: titik koma TIDAK boleh memecah kalimat.
    kalimat = "Kendali laju: beta-blocker atau penyekat kalsium; digoksin pada gagal jantung"
    check("Titik koma di dalam kalimat TIDAK memecah butir",
          pecah_sel(kalimat) == [kalimat], pecah_sel(kalimat))

    kalimat2 = "Monitor EKG; monitor saturasi\nBerikan oksigen"
    check("Titik koma tetap utuh meski ada baris baru lain",
          pecah_sel(kalimat2) == ["Monitor EKG; monitor saturasi", "Berikan oksigen"],
          pecah_sel(kalimat2))

    print("\n" + "=" * 62)
    print("TEST 2 -- Penggabungan & konversi nilai")
    print("=" * 62)
    check("Daftar digabung dengan baris baru",
          gabung_sel(["a", "b"]) == "a\nb")
    check("Daftar kosong -> string kosong", gabung_sel([]) == "")
    check("String tetap apa adanya", gabung_sel("teks") == "teks")
    check("Bolak-balik utuh", pecah_sel(gabung_sel(["a", "b", "c"])) == ["a", "b", "c"])

    check("ke_bool: 'Ya' -> True", ke_bool("Ya") is True)
    check("ke_bool: 'Tidak' -> False", ke_bool("Tidak") is False)
    check("ke_bool: True -> True", ke_bool(True) is True)
    check("ke_bool: kosong -> False", ke_bool("") is False)

    print("\n" + "=" * 62)
    print("TEST 3 -- Jalur bersarang")
    print("=" * 62)
    obj = {"luaran": {"kode": "L.01001"}}
    check("ambil() jalur bersarang", ambil(obj, "luaran.kode") == "L.01001")
    check("ambil() jalur tidak ada -> None", ambil(obj, "luaran.tidakada") is None)
    baru: dict = {}
    taruh(baru, "kriteria.mayor", ["x"])
    check("taruh() membuat objek bersarang", baru == {"kriteria": {"mayor": ["x"]}})

    print("\n" + "=" * 62)
    print("TEST 4 -- Deteksi format")
    print("=" * 62)
    check("JSON dengan kunci 'diagnosis' -> sdki",
          deteksi_format_json({"diagnosis": []}) == "sdki")
    check("JSON dengan kunci 'ppk' -> ppk",
          deteksi_format_json({"ppk": []}) == "ppk")
    check("JSON tak dikenal -> None", deteksi_format_json({"lain": []}) is None)
    check("Kolom SDKI terdeteksi",
          deteksi_format_kolom(["Kode", "Nama Luaran", "Intervensi Observasi"]) == "sdki")
    check("Kolom PPK terdeteksi",
          deteksi_format_kolom(["Kode", "ICD-10", "Tatalaksana Awal"]) == "ppk")
    check("Kolom asing -> None", deteksi_format_kolom(["A", "B"]) is None)

    print("\n" + "=" * 62)
    print("TEST 5 -- ROUND-TRIP: JSON -> Excel -> JSON harus identik")
    print("=" * 62)
    for fmt, berkas, kunci in [
        ("sdki", "sdki_slki_siki", "diagnosis"),
        ("ppk", "ppk_kardiovaskular", "ppk"),
    ]:
        asli_path = ROOT / "data" / f"{berkas}.json"
        if not asli_path.exists():
            check(f"{fmt}: berkas sumber ada", False, asli_path)
            continue

        asli = json.loads(asli_path.read_text(encoding="utf-8"))
        xlsx = os.path.join(TMP, f"{berkas}.xlsx")
        out = os.path.join(TMP, f"{berkas}_kembali.json")

        r1 = jalankan("tools/json_ke_excel.py", fmt, xlsx)
        check(f"{fmt}: ekspor ke Excel berhasil", os.path.exists(xlsx), r1.stdout[-200:])

        r2 = jalankan("tools/excel_ke_json.py", xlsx, out)
        check(f"{fmt}: impor kembali berhasil", os.path.exists(out), r2.stdout[-300:])

        if not os.path.exists(out):
            continue

        kembali = json.loads(Path(out).read_text(encoding="utf-8"))
        a, b = asli[kunci], kembali[kunci]

        check(f"{fmt}: jumlah entri sama ({len(a)})", len(a) == len(b), f"{len(a)} vs {len(b)}")

        beda = []
        for x, y in zip(a, b):
            for k in x:
                if k == "catatan" and not x[k] and not y.get(k):
                    continue
                if x.get(k) != y.get(k):
                    beda.append(f"{x['kode']}.{k}")
        check(f"{fmt}: isi identik setelah round-trip", not beda, beda[:5])

    print("\n" + "=" * 62)
    print("TEST 6 -- Impor menolak data rusak")
    print("=" * 62)
    from openpyxl import Workbook

    def buat_xlsx(nama, judul, baris):
        wb = Workbook()
        ws = wb.active
        ws.append(judul)
        for b in baris:
            ws.append(b)
        p = os.path.join(TMP, nama)
        wb.save(p)
        return p

    judul_ppk = ["Kode", "ICD-10", "Nama Diagnosis", "Kategori", "Definisi",
                 "Anamnesis", "Tatalaksana Awal"]

    p = buat_xlsx("kode_kosong.xlsx", judul_ppk,
                  [["", "I21", "Tes", "Kat", "Def", "anamnesis", "awal"]])
    r = jalankan("tools/excel_ke_json.py", p, os.path.join(TMP, "x.json"))
    check("Kode kosong ditolak", r.returncode != 0 and "Kode kosong" in r.stdout, r.stdout[-150:])

    p = buat_xlsx("duplikat.xlsx", judul_ppk, [
        ["PPK.X.001", "I21", "Tes", "Kat", "Def", "anamnesis", "awal"],
        ["PPK.X.001", "I22", "Tes2", "Kat", "Def", "anamnesis", "awal"],
    ])
    r = jalankan("tools/excel_ke_json.py", p, os.path.join(TMP, "x.json"))
    check("Kode duplikat ditolak", r.returncode != 0 and "duplikat" in r.stdout, r.stdout[-150:])

    p = buat_xlsx("tanpa_awal.xlsx", judul_ppk,
                  [["PPK.X.001", "I21", "Tes", "Kat", "Def", "anamnesis", ""]])
    r = jalankan("tools/excel_ke_json.py", p, os.path.join(TMP, "x.json"))
    check("Tatalaksana Awal kosong ditolak",
          r.returncode != 0 and "Tatalaksana Awal kosong" in r.stdout, r.stdout[-150:])

    judul_sdki = ["Kode", "Nama Diagnosis", "Jenis", "Kriteria Mayor", "Faktor Risiko",
                  "Kode Luaran", "Nama Luaran", "Intervensi Observasi"]
    p = buat_xlsx("jenis_salah.xlsx", judul_sdki,
                  [["D.9001", "Tes", "Actual", "mayor", "", "L.01001", "Luaran", "obs"]])
    r = jalankan("tools/excel_ke_json.py", p, os.path.join(TMP, "x.json"))
    check("Jenis salah tulis ditolak", r.returncode != 0 and "Jenis harus" in r.stdout, r.stdout[-150:])

    p = buat_xlsx("aktual_tanpa_mayor.xlsx", judul_sdki,
                  [["D.9001", "Tes", "Aktual", "", "", "L.01001", "Luaran", "obs"]])
    r = jalankan("tools/excel_ke_json.py", p, os.path.join(TMP, "x.json"))
    check("Aktual tanpa Kriteria Mayor ditolak",
          r.returncode != 0 and "Kriteria Mayor" in r.stdout, r.stdout[-150:])

    print("\n" + "=" * 62)
    print("TEST 7 -- Impor tidak menimpa saat gagal, dan mencadangkan saat berhasil")
    print("=" * 62)
    target = os.path.join(TMP, "target.json")
    Path(target).write_text(json.dumps({"meta": {"tanda": "asli"}, "ppk": []}), encoding="utf-8")

    p = buat_xlsx("rusak2.xlsx", judul_ppk,
                  [["", "I21", "Tes", "Kat", "Def", "anamnesis", "awal"]])
    jalankan("tools/excel_ke_json.py", p, target)
    tetap = json.loads(Path(target).read_text(encoding="utf-8"))
    check("Berkas target TIDAK diubah saat impor gagal",
          tetap["meta"].get("tanda") == "asli", tetap)

    p = buat_xlsx("benar.xlsx", judul_ppk,
                  [["PPK.X.001", "I21", "Tes", "Kat", "Def", "anamnesis", "awal"]])
    r = jalankan("tools/excel_ke_json.py", p, target)
    check("Impor benar berhasil", r.returncode == 0, r.stdout[-150:])
    cadangan = list(Path(TMP).glob("target.backup-*.json"))
    check("Cadangan berkas lama dibuat", len(cadangan) == 1, [c.name for c in cadangan])
    baru = json.loads(Path(target).read_text(encoding="utf-8"))
    check("Meta lama dipertahankan", baru["meta"].get("tanda") == "asli", baru["meta"])
    check("Jumlah diperbarui di meta", baru["meta"].get("jumlah") == 1, baru["meta"])

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
