"""
components/kriteria_sdki.py
==========================================
Tampilan skor CDSS dan kriteria SDKI (mayor / minor / faktor risiko).

Berkas ini SENGAJA IDENTIK di dua aplikasi:
  * asuhan    -> components/kriteria_sdki.py               (smartcare.streamlit.app)
  * smart_emr -> emr_ai_system/components/kriteria_sdki.py (smart-emr.streamlit.app)
supaya perawat melihat skor, label prioritas, dan tanda kriteria yang SAMA
untuk diagnosis yang sama di aplikasi mana pun. Bila diubah, salin ke
keduanya.

Murni TAMPILAN: hanya menyusun HTML dari data yang sudah dihitung mesin
(DiagnosisService di asuhan, adapter sdki_engine di smart_emr). Tidak ada
pencocokan atau perhitungan skor di sini.

`kriteria_cek` berbentuk:
    {"mayor": [{"teks": "Dispnea", "cocok": True}, ...],
     "minor": [...], "faktor_risiko": [...]}
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable, Mapping

KELOMPOK = (
    ("mayor", "Mayor"),
    ("minor", "Minor"),
    ("faktor_risiko", "Faktor risiko"),
)

# Ambang di teks ini mengikuti label_prioritas() asuhan & _prioritas()
# adapter smart_emr. Bila ambangnya diubah, ubah juga teks ini.
KETERANGAN = (
    "✓ = kriteria terpenuhi oleh data S/O. Skor hanya bobot relatif untuk "
    "mengurutkan usulan, bukan persentase: CRITICAL ≥ 8 · HIGH ≥ 4 · "
    "MEDIUM ≥ 2 · LOW < 2."
)

# (latar, teks) lencana prioritas. Keduanya ditetapkan supaya tetap
# terbaca di tema terang maupun gelap.
_WARNA_PRIORITAS = {
    "CRITICAL": ("#fde8e8", "#b42318"),
    "HIGH": ("#ffedd5", "#b54708"),
    "MEDIUM": ("#fef3c7", "#854d0e"),
    "LOW": ("#e8edf3", "#475467"),
}

# Hijau menengah: kontras cukup di latar terang maupun gelap.
_WARNA_CENTANG = "#12b76a"


def format_skor(skor: Any) -> str:
    """Skor dibulatkan 2 desimal, sama dengan ranking smart_emr (13.05, 4.0)."""
    try:
        return str(round(float(skor), 2))
    except (TypeError, ValueError):
        return "-"


def html_lencana(prioritas: str, skor: Any) -> str:
    """Lencana berwarna, mis. 'CRITICAL · skor 13.05'."""
    label = str(prioritas or "LOW").upper()
    latar, warna = _WARNA_PRIORITAS.get(label, _WARNA_PRIORITAS["LOW"])
    return (
        f"<span style='background:{latar};color:{warna};border-radius:6px;"
        f"padding:1px 8px;font-size:0.8em;font-weight:600;white-space:nowrap'>"
        f"{escape(label)} · skor {format_skor(skor)}</span>"
    )


def hitung(kriteria_cek: Mapping[str, Any] | None, kunci: str) -> tuple[int, int]:
    """(jumlah terpenuhi, jumlah total) untuk satu kelompok kriteria."""
    isi = (kriteria_cek or {}).get(kunci) or []
    return sum(1 for k in isi if k.get("cocok")), len(isi)


def teks_rincian(
    kriteria_cek: Mapping[str, Any] | None,
    *,
    dari_konteks: bool = False,
    konteks: bool = False,
    numerik: bool = False,
) -> str:
    """
    Ringkasan dasar skor dalam satu baris, mis.
    'mayor 7/16 terpenuhi · diperkuat konteks klinis & nilai numerik'.

    Diagnosis aktual diringkas dari kriteria mayor (tumpuan penegakan SDKI);
    diagnosis risiko (tanpa mayor) dari faktor risikonya.
    """
    bagian = []
    for kunci, label in (("mayor", "mayor"), ("faktor_risiko", "faktor risiko")):
        cocok, total = hitung(kriteria_cek, kunci)
        if total:
            bagian.append(f"{label} {cocok}/{total} terpenuhi")
            break
    if dari_konteks:
        bagian.append("dimunculkan dari konteks klinis")
    else:
        penguat = [nama for nama, aktif in (("konteks klinis", konteks),
                                            ("nilai numerik", numerik)) if aktif]
        if penguat:
            bagian.append("diperkuat " + " & ".join(penguat))
    return " · ".join(bagian)


def html_kriteria(kriteria_cek: Mapping[str, Any] | None) -> str:
    """Daftar kriteria per kelompok: ✓ terpenuhi oleh data S/O, ○ belum tampak."""
    blok = []
    for kunci, label in KELOMPOK:
        isi = (kriteria_cek or {}).get(kunci) or []
        if not isi:
            continue
        cocok = sum(1 for k in isi if k.get("cocok"))
        baris = "".join(
            (
                f"<li style='margin:0;break-inside:avoid'><span style='color:{_WARNA_CENTANG};"
                f"font-weight:700'>✓</span> <b>{escape(str(k.get('teks', '')))}</b></li>"
            )
            if k.get("cocok")
            else f"<li style='margin:0;break-inside:avoid;opacity:.6'>○ {escape(str(k.get('teks', '')))}</li>"
            for k in isi
        )
        blok.append(
            f"<div style='margin-top:6px'><b>{label}</b> "
            f"<span style='opacity:.7'>({cocok}/{len(isi)} terpenuhi)</span></div>"
            # Dua kolom bila lebar >= 2x240px (daftar panjang jadi ringkas);
            # di layar sempit otomatis kembali satu kolom.
            f"<ul style='list-style:none;padding-left:2px;margin:2px 0 0;"
            f"columns:2 240px;column-gap:24px'>{baris}</ul>"
        )
    return "".join(blok) or "<div style='opacity:.7'>Kriteria tidak tercatat di master.</div>"


def html_dasar_skor(
    kriteria_cek: Mapping[str, Any] | None,
    *,
    dari_konteks: bool = False,
    konteks: bool = False,
    numerik: bool = False,
    alasan: Iterable[Any] | None = None,
) -> str:
    """Isi panel dasar skor: ringkasan, (opsional) 'cocok pada', lalu kriteria."""
    isi = ""
    rincian = teks_rincian(kriteria_cek, dari_konteks=dari_konteks,
                           konteks=konteks, numerik=numerik)
    if rincian:
        isi += f"<div style='opacity:.75'>{escape(rincian)}</div>"
    alasan_teks = ", ".join(str(a) for a in (alasan or []))
    if alasan_teks:
        isi += f"<div style='opacity:.75'>Cocok pada: {escape(alasan_teks)}</div>"
    return isi + html_kriteria(kriteria_cek)


def html_item_ranking(rec: Mapping[str, Any]) -> str:
    """
    Satu baris ranking CDSS smart_emr yang bisa diklik untuk membuka kriteria
    SDKI-nya. Memakai <details> HTML karena st.expander tidak boleh
    bersarang di dalam expander "Detail Analisis".
    """
    judul = (
        f"<code>[{escape(str(rec.get('priority', '')))}]</code> "
        f"<b>{escape(str(rec.get('code', '')))}</b> — "
        f"{escape(str(rec.get('name', '')))} (skor: {format_skor(rec.get('score'))})"
    )
    cek = rec.get("kriteria_cek")
    if not cek:
        # Jalur cadangan (bridge / mesin lama) tidak membawa kriteria.
        return f"<div style='margin:2px 0'>• {judul}</div>"
    isi = html_dasar_skor(
        cek,
        dari_konteks=bool(rec.get("dari_konteks")),
        konteks=bool(rec.get("cardiac_context_boost")),
        numerik=bool(rec.get("numeric_boost")),
        alasan=rec.get("alasan"),
    )
    return (
        "<details style='margin:2px 0'>"
        f"<summary style='cursor:pointer'>{judul}</summary>"
        f"<div style='margin:4px 0 10px 18px;font-size:0.92em'>{isi}</div>"
        "</details>"
    )
