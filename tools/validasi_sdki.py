"""
tools/validasi_sdki.py
==========================================
Periksa berkas master 3S setelah disunting.

JALANKAN SETIAP KALI selesai mengedit `data/sdki_slki_siki.json`:

    python tools/validasi_sdki.py

atau untuk berkas lain:

    python tools/validasi_sdki.py /path/ke/berkas.json

Kenapa perlu: JSON tidak memaafkan koma yang kelebihan atau kurang, dan
kesalahan seperti itu baru ketahuan saat aplikasi dibuka — biasanya
sebagai pesan teknis yang membingungkan. Alat ini menunjukkan baris
persisnya, plus memeriksa hal-hal yang tidak dilihat parser JSON:
kode duplikat, luaran kosong, kategori tak dikenal, dan sebagainya.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.kategori import KATEGORI_SLKI, kategori_dari_luaran  # noqa: E402

KATEGORI_SIKI = ("observasi", "terapeutik", "edukasi", "kolaborasi")
JENIS_SAH = {"aktual", "risiko"}


def muat(path: Path) -> dict | None:
    """Baca JSON, laporkan lokasi kesalahan sintaks kalau ada."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"❌ Berkas tidak ditemukan: {path}")
    except json.JSONDecodeError as exc:
        print(f"❌ JSON tidak valid — {exc.msg}")
        print(f"   Baris {exc.lineno}, kolom {exc.colno}")
        baris = path.read_text(encoding="utf-8").splitlines()
        awal, akhir = max(0, exc.lineno - 3), min(len(baris), exc.lineno + 2)
        print()
        for i in range(awal, akhir):
            tanda = ">>" if i + 1 == exc.lineno else "  "
            print(f"   {tanda} {i+1:5d} | {baris[i]}")
        print()
        print("   Penyebab tersering: koma kelebihan sebelum ] atau },")
        print("   koma kurang antar-entri, atau tanda kutip yang belum ditutup.")
    return None


def periksa(doc: dict) -> tuple[list[str], list[str]]:
    """Kembalikan (galat, peringatan)."""
    galat: list[str] = []
    peringatan: list[str] = []

    entri = doc.get("diagnosis")
    if not isinstance(entri, list):
        return ["Kunci 'diagnosis' tidak ada atau bukan list."], []

    kode_terlihat: set[str] = set()
    semua_kode = {e.get("kode") for e in entri if isinstance(e, dict)}

    for idx, e in enumerate(entri, start=1):
        if not isinstance(e, dict):
            galat.append(f"Entri ke-{idx} bukan objek JSON.")
            continue

        kode = str(e.get("kode") or "").strip()
        label = kode or f"entri ke-{idx}"

        if not kode:
            galat.append(f"{label}: 'kode' kosong.")
        elif kode in kode_terlihat:
            galat.append(f"{label}: kode duplikat.")
        kode_terlihat.add(kode)

        if not str(e.get("nama") or "").strip():
            galat.append(f"{label}: 'nama' kosong.")

        jenis = str(e.get("jenis") or "").strip().lower()
        if jenis not in JENIS_SAH:
            galat.append(f"{label}: 'jenis' harus 'Aktual' atau 'Risiko' (isi: {e.get('jenis')!r}).")

        # --- luaran ---
        luaran = e.get("luaran") or {}
        kode_l = str(luaran.get("kode") or "").strip()
        if not kode_l:
            galat.append(f"{label}: 'luaran.kode' kosong.")
        else:
            if not str(luaran.get("nama") or "").strip():
                galat.append(f"{label}: 'luaran.nama' kosong.")
            prefix = kode_l[:4]
            if prefix not in KATEGORI_SLKI:
                peringatan.append(
                    f"{label}: prefix luaran '{prefix}' tidak dikenal, "
                    f"kategori jadi 'Lainnya' dan prioritasnya paling akhir. "
                    f"Daftarkan di core/kategori.py kalau ini kategori baru."
                )

        # --- kriteria ---
        kriteria = e.get("kriteria") or {}
        for kunci in ("mayor", "minor", "faktor_risiko"):
            if not isinstance(kriteria.get(kunci, []), list):
                galat.append(f"{label}: 'kriteria.{kunci}' harus berupa list.")

        if jenis == "aktual" and not kriteria.get("mayor"):
            galat.append(f"{label}: jenis Aktual wajib punya 'kriteria.mayor'.")
        if jenis == "risiko" and not kriteria.get("faktor_risiko"):
            galat.append(f"{label}: jenis Risiko wajib punya 'kriteria.faktor_risiko'.")
        if jenis == "risiko" and kriteria.get("mayor"):
            peringatan.append(
                f"{label}: jenis Risiko tapi punya 'kriteria.mayor' — "
                "biasanya diagnosis Risiko hanya punya faktor risiko."
            )

        # --- intervensi ---
        intervensi = e.get("intervensi") or {}
        for kunci in KATEGORI_SIKI:
            if kunci not in intervensi:
                galat.append(f"{label}: kategori intervensi '{kunci}' tidak ada (boleh list kosong).")
            elif not isinstance(intervensi.get(kunci), list):
                galat.append(f"{label}: 'intervensi.{kunci}' harus berupa list.")
        if not intervensi.get("observasi"):
            galat.append(f"{label}: 'intervensi.observasi' kosong — minimal satu tindakan.")

        tak_dikenal = set(intervensi) - set(KATEGORI_SIKI)
        if tak_dikenal:
            peringatan.append(
                f"{label}: kategori intervensi tak dikenal {sorted(tak_dikenal)} — "
                "tidak akan ditampilkan aplikasi."
            )

        # --- terkait ---
        terkait = e.get("terkait", [])
        if not isinstance(terkait, list):
            galat.append(f"{label}: 'terkait' harus berupa list.")
        else:
            for ref in terkait:
                if ref not in semua_kode:
                    galat.append(f"{label}: 'terkait' menunjuk kode '{ref}' yang tidak ada.")

        if "is_sdki" not in e:
            peringatan.append(f"{label}: 'is_sdki' tidak ada, dianggap false.")

    return galat, peringatan


def ringkas(doc: dict) -> None:
    entri = doc.get("diagnosis", [])
    aktual = sum(1 for e in entri if str(e.get("jenis", "")).lower() == "aktual")
    risiko = len(entri) - aktual
    sdki = sum(1 for e in entri if e.get("is_sdki"))

    print(f"   Total diagnosis   : {len(entri)}")
    print(f"   Aktual / Risiko   : {aktual} / {risiko}")
    print(f"   SDKI resmi / lokal: {sdki} / {len(entri) - sdki}")

    kategori: dict[str, int] = {}
    for e in entri:
        nama = kategori_dari_luaran((e.get("luaran") or {}).get("kode"))
        kategori[nama] = kategori.get(nama, 0) + 1
    print("   Per kategori      :")
    for nama, jumlah in sorted(kategori.items(), key=lambda x: -x[1]):
        print(f"      {nama:28} {jumlah}")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "sdki_slki_siki.json"
    print(f"Memeriksa: {path}\n")

    doc = muat(path)
    if doc is None:
        return 1

    print("✅ Sintaks JSON valid.\n")

    galat, peringatan = periksa(doc)

    if peringatan:
        print(f"⚠️  {len(peringatan)} peringatan (tidak menggagalkan, tapi periksa lagi):")
        for p in peringatan:
            print(f"   • {p}")
        print()

    if galat:
        print(f"❌ {len(galat)} masalah yang HARUS diperbaiki:")
        for g in galat:
            print(f"   • {g}")
        print()
        return 1

    print("✅ Struktur data valid.\n")
    ringkas(doc)
    print("\nSiap dipakai. Jalankan ulang aplikasi untuk memuat perubahan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
