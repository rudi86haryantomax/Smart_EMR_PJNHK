"""
tools/validasi_indikator.py
==========================================
Periksa berkas indikator luaran setelah disunting.

    python tools/validasi_indikator.py
    python tools/validasi_indikator.py /path/indikator.json

Selain memeriksa struktur, alat ini juga membandingkan dengan master 3S:
luaran yang dipakai master tetapi belum punya indikator akan dilaporkan,
karena diagnosis itu akan tampil tanpa cara mengukur kemajuannya.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

JENIS_SAH = {"skala5", "angka"}
ARAH_SAH = {"meningkat", "menurun", "membaik"}


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
    return None


def periksa(doc: dict) -> tuple[list[str], list[str]]:
    galat: list[str] = []
    peringatan: list[str] = []

    luaran = doc.get("luaran")
    if not isinstance(luaran, dict):
        return ["Kunci 'luaran' tidak ada atau bukan objek."], []

    for kode, entri in luaran.items():
        if not kode.startswith("L."):
            peringatan.append(f"{kode}: kode luaran biasanya berawalan 'L.'")

        if not str(entri.get("nama") or "").strip():
            galat.append(f"{kode}: 'nama' kosong.")

        arah = str(entri.get("arah") or "").strip().lower()
        if arah not in ARAH_SAH:
            galat.append(f"{kode}: 'arah' harus salah satu dari {sorted(ARAH_SAH)} (isi: {arah!r}).")

        if not str(entri.get("evaluasi_default") or "").strip():
            peringatan.append(f"{kode}: 'evaluasi_default' kosong, akan memakai '24 jam'.")

        daftar = entri.get("indikator")
        if not isinstance(daftar, list) or not daftar:
            galat.append(f"{kode}: 'indikator' kosong — luaran tanpa indikator tidak dapat dievaluasi.")
            continue

        for i, ind in enumerate(daftar, start=1):
            label = f"{kode} indikator ke-{i}"
            if not str(ind.get("nama") or "").strip():
                galat.append(f"{label}: 'nama' kosong.")
            jenis = str(ind.get("jenis") or "").strip()
            if jenis not in JENIS_SAH:
                galat.append(f"{label}: 'jenis' harus 'skala5' atau 'angka' (isi: {jenis!r}).")
            if not str(ind.get("target") or "").strip():
                galat.append(f"{label}: 'target' kosong — tanpa target, evaluasi tidak punya acuan.")
            if jenis == "skala5":
                a = str(ind.get("arah") or "").strip().lower()
                if a not in ARAH_SAH:
                    galat.append(f"{label}: indikator skala5 wajib punya 'arah'.")
            if jenis == "angka" and not str(ind.get("satuan", "")).strip():
                peringatan.append(f"{label}: indikator angka tanpa 'satuan' — nilai jadi ambigu.")

    return galat, peringatan


def bandingkan_master(doc: dict) -> list[str]:
    """Cari luaran yang dipakai master 3S tetapi belum punya indikator."""
    try:
        master = json.loads(
            (ROOT / "data" / "sdki_slki_siki.json").read_text(encoding="utf-8")
        )
    except Exception:
        return []
    dipakai = {d["luaran"]["kode"] for d in master.get("diagnosis", [])
               if d.get("luaran", {}).get("kode")}
    return sorted(dipakai - set(doc.get("luaran", {})))


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "indikator_slki.json"
    print(f"Memeriksa: {path}\n")

    doc = muat(path)
    if doc is None:
        return 1
    print("✅ Sintaks JSON valid.\n")

    galat, peringatan = periksa(doc)
    kurang = bandingkan_master(doc)

    if kurang:
        print(f"⚠️  {len(kurang)} luaran dipakai master 3S tapi belum punya indikator:")
        for k in kurang:
            print(f"   • {k}")
        print("   Diagnosis dengan luaran ini akan tampil tanpa cara mengukur kemajuan.\n")

    if peringatan:
        print(f"⚠️  {len(peringatan)} peringatan:")
        for p in peringatan[:15]:
            print(f"   • {p}")
        if len(peringatan) > 15:
            print(f"   ... dan {len(peringatan) - 15} lainnya")
        print()

    if galat:
        print(f"❌ {len(galat)} masalah yang HARUS diperbaiki:")
        for g in galat[:25]:
            print(f"   • {g}")
        return 1

    print("✅ Struktur data valid.\n")
    total = sum(len(v.get("indikator", [])) for v in doc["luaran"].values())
    print(f"   Luaran    : {len(doc['luaran'])}")
    print(f"   Indikator : {total}")
    print(f"   Rata-rata : {total / max(len(doc['luaran']), 1):.1f} indikator per luaran")
    print("\nSiap dipakai. Jalankan ulang aplikasi untuk memuat perubahan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
