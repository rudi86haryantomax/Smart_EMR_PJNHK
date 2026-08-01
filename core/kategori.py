"""
core/kategori.py
==========================================
Pemetaan kategori SDKI dan usulan urutan prioritas diagnosis.

Kode luaran SLKI ternyata sudah mengandung kategorinya pada dua digit
pertama (L.01 = Respirasi, L.02 = Sirkulasi, dst). Itu dipakai di sini
supaya kategori tidak perlu disimpan ulang sebagai kolom terpisah yang
bisa tidak sinkron dengan datanya.

URUTAN PRIORITAS
----------------
Urutan di bawah mengikuti kaidah yang lazim dipakai dalam praktik:
ABC (Airway-Breathing-Circulation) lebih dulu, lalu keselamatan, lalu
kebutuhan fisiologis lain, baru psikososial. Diagnosis "Aktual"
didahulukan atas "Risiko" pada tingkat yang sama, karena masalah yang
sudah terjadi menuntut tindakan lebih segera daripada yang baru berpotensi.

PENTING: ini USULAN awal untuk menghemat waktu penyusunan, BUKAN
penentuan prioritas. Konteks pasien sering membalik urutan ini -- mis.
Risiko Jatuh bisa jadi prioritas utama pada pasien yang hemodinamiknya
sudah stabil. Perawat tetap yang memutuskan dan bisa mengubah urutannya.
"""

from __future__ import annotations

# prefix kode SLKI -> (nama kategori, bobot prioritas; makin kecil makin didahulukan)
KATEGORI_SLKI: dict[str, tuple[str, int]] = {
    "L.01": ("Respirasi", 1),
    "L.02": ("Sirkulasi", 2),
    "L.14": ("Keamanan & Proteksi", 3),
    "L.03": ("Nutrisi & Cairan", 4),
    "L.04": ("Eliminasi", 5),
    "L.08": ("Nyeri & Kenyamanan", 6),
    "L.05": ("Aktivitas & Istirahat", 7),
    "L.09": ("Integritas Ego", 8),
    "L.13": ("Penyuluhan & Pembelajaran", 9),
}

_DEFAULT = ("Lainnya", 50)


def kategori_dari_luaran(kode_slki: str | None) -> str:
    prefix = str(kode_slki or "")[:4]
    return KATEGORI_SLKI.get(prefix, _DEFAULT)[0]


def bobot_kategori(kode_slki: str | None) -> int:
    prefix = str(kode_slki or "")[:4]
    return KATEGORI_SLKI.get(prefix, _DEFAULT)[1]


def bobot_prioritas(diagnosis: dict) -> tuple[int, int, str]:
    """
    Kunci pengurutan: (kategori, jenis, kode).

    Jenis 'Aktual' (0) didahulukan atas 'Risiko' (1). Kode dipakai sebagai
    pemutus imbang supaya urutannya stabil -- tanpa itu, dua diagnosis
    berbobot sama bisa bertukar posisi setiap kali halaman dimuat ulang,
    dan perawat akan melihat urutan yang berubah-ubah tanpa sebab.
    """
    luaran = (diagnosis.get("luaran") or {}).get("kode", "")
    jenis = 0 if str(diagnosis.get("jenis", "")).lower() == "aktual" else 1
    return (bobot_kategori(luaran), jenis, diagnosis.get("kode", ""))


def urutkan_prioritas(daftar: list[dict]) -> list[dict]:
    """Urutkan diagnosis sesuai usulan prioritas klinis."""
    return sorted(daftar, key=bobot_prioritas)


def semua_kategori() -> list[str]:
    return [nama for nama, _ in sorted(KATEGORI_SLKI.values(), key=lambda x: x[1])]
