"""
tools/validasi_ppk.py
==========================================
Periksa berkas PPK setelah disunting.

    python tools/validasi_ppk.py
    python tools/validasi_ppk.py /path/ke/ppk-rs-anda.json

Sama seperti `validasi_sdki.py`: menangkap kesalahan sintaks JSON dengan
menunjukkan baris persisnya, plus memeriksa kelengkapan struktur yang
tidak dilihat parser.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BAGIAN_KRITERIA = ("anamnesis", "pemeriksaan_fisik", "penunjang", "kriteria_diagnosis")
BAGIAN_TATALAKSANA = ("awal", "farmakologis", "non_farmakologis", "rujukan")
WAJIB = ("kode", "nama", "kategori", "definisi", "referensi")


def muat(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"❌ Berkas tidak ditemukan: {path}")
    except json.JSONDecodeError as exc:
        print(f"❌ JSON tidak valid — {exc.msg}")
        print(f"   Baris {exc.lineno}, kolom {exc.colno}\n")
        baris = path.read_text(encoding="utf-8").splitlines()
        for i in range(max(0, exc.lineno - 3), min(len(baris), exc.lineno + 2)):
            tanda = ">>" if i + 1 == exc.lineno else "  "
            print(f"   {tanda} {i+1:5d} | {baris[i]}")
        print("\n   Penyebab tersering: koma kelebihan sebelum ] atau },")
        print("   koma kurang antar-entri, atau tanda kutip belum ditutup.")
    return None


def periksa(doc: dict) -> tuple[list[str], list[str]]:
    galat: list[str] = []
    peringatan: list[str] = []

    entri = doc.get("ppk")
    if not isinstance(entri, list):
        return ["Kunci 'ppk' tidak ada atau bukan list."], []

    terlihat: set[str] = set()
    for idx, e in enumerate(entri, start=1):
        if not isinstance(e, dict):
            galat.append(f"Entri ke-{idx} bukan objek JSON.")
            continue

        kode = str(e.get("kode") or "").strip()
        label = kode or f"entri ke-{idx}"

        if not kode:
            galat.append(f"{label}: 'kode' kosong.")
        elif kode in terlihat:
            galat.append(f"{label}: kode duplikat.")
        terlihat.add(kode)

        for kunci in WAJIB:
            if not str(e.get(kunci) or "").strip():
                galat.append(f"{label}: '{kunci}' kosong.")

        if not str(e.get("icd10") or "").strip():
            peringatan.append(f"{label}: 'icd10' kosong — berguna untuk pencarian dan pelaporan.")

        kriteria = e.get("kriteria") or {}
        for bagian in BAGIAN_KRITERIA:
            if bagian not in kriteria:
                galat.append(f"{label}: 'kriteria.{bagian}' tidak ada (boleh list kosong).")
            elif not isinstance(kriteria[bagian], list):
                galat.append(f"{label}: 'kriteria.{bagian}' harus list.")
        if not kriteria.get("anamnesis"):
            galat.append(f"{label}: 'kriteria.anamnesis' kosong — minimal satu butir.")
        if not kriteria.get("kriteria_diagnosis"):
            peringatan.append(f"{label}: 'kriteria.kriteria_diagnosis' kosong.")

        tak_dikenal = set(kriteria) - set(BAGIAN_KRITERIA)
        if tak_dikenal:
            peringatan.append(
                f"{label}: bagian kriteria tak dikenal {sorted(tak_dikenal)} — tidak akan ditampilkan.")

        tata = e.get("tatalaksana") or {}
        for bagian in BAGIAN_TATALAKSANA:
            if bagian not in tata:
                galat.append(f"{label}: 'tatalaksana.{bagian}' tidak ada (boleh list kosong).")
            elif not isinstance(tata[bagian], list):
                galat.append(f"{label}: 'tatalaksana.{bagian}' harus list.")
        if not tata.get("awal"):
            galat.append(f"{label}: 'tatalaksana.awal' kosong — minimal satu butir.")

        tak_dikenal_t = set(tata) - set(BAGIAN_TATALAKSANA)
        if tak_dikenal_t:
            peringatan.append(
                f"{label}: bagian tatalaksana tak dikenal {sorted(tak_dikenal_t)} — tidak akan ditampilkan.")

        for opsional in ("edukasi", "komplikasi"):
            if opsional in e and not isinstance(e[opsional], list):
                galat.append(f"{label}: '{opsional}' harus list.")

    return galat, peringatan


def ringkas(doc: dict) -> None:
    entri = doc.get("ppk", [])
    print(f"   Total PPK  : {len(entri)}")
    kategori: dict[str, int] = {}
    for e in entri:
        k = e.get("kategori", "Lainnya")
        kategori[k] = kategori.get(k, 0) + 1
    print("   Per kategori:")
    for nama, jumlah in sorted(kategori.items(), key=lambda x: -x[1]):
        print(f"      {nama:30} {jumlah}")

    try:
        from services.ppk_service import KODE_KRITIS
        ada = [k for k in KODE_KRITIS if k in {e.get("kode") for e in entri}]
        hilang = sorted(set(KODE_KRITIS) - set(ada))
        print(f"   Ditandai kritis: {len(ada)}")
        if hilang:
            print(f"   ⚠️  Kode kritis tidak ada di data: {hilang}")
            print("      Perbarui KODE_KRITIS di services/ppk_service.py")
    except Exception:
        pass


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "ppk_kardiovaskular.json"
    print(f"Memeriksa: {path}\n")

    doc = muat(path)
    if doc is None:
        return 1
    print("✅ Sintaks JSON valid.\n")

    galat, peringatan = periksa(doc)

    if peringatan:
        print(f"⚠️  {len(peringatan)} peringatan:")
        for p in peringatan:
            print(f"   • {p}")
        print()

    if galat:
        print(f"❌ {len(galat)} masalah yang HARUS diperbaiki:")
        for g in galat:
            print(f"   • {g}")
        return 1

    print("✅ Struktur data valid.\n")
    ringkas(doc)
    print("\nSiap dipakai. Jalankan ulang aplikasi untuk memuat perubahan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
