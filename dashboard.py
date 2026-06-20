# =============================================================================
# Smart EMR - RSJPDHK | Dashboard CPPT Keperawatan
# =============================================================================

import io
import re
import hashlib
import hmac
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict
import sys
import os
from pathlib import Path
from app.services.cdss_engine import analyze_clinical_trends_improved
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# Catatan: speech_recognition & streamlit_mic_recorder diimpor secara kondisional
# agar app tetap jalan meski library tidak terinstal
try:
    import speech_recognition as sr
    from streamlit_mic_recorder import mic_recorder
    SPEECH_AVAILABLE = True
except ImportError:
    SPEECH_AVAILABLE = False


# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# PATCH (2026-06): AI Filter & Translator untuk Voice-to-Text.
# Diimpor secara kondisional (pola sama dengan speech_recognition di atas)
# agar app tetap jalan walau library `anthropic` belum terinstal atau API
# key belum diset — fitur normalisasi AI akan otomatis nonaktif dan
# transkripsi mentah tetap dipakai apa adanya (graceful degradation).
def _get_anthropic_api_key() -> str | None:
    """
    Ambil ANTHROPIC_API_KEY dengan urutan prioritas:
    1) Streamlit secrets (.streamlit/secrets.toml) — cara yang disarankan,
       otomatis ikut terbawa kalau nanti deploy ke Streamlit Community Cloud.
    2) Environment variable OS — fallback untuk dev lokal/server lain yang
       tidak pakai mekanisme secrets Streamlit.
    """
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        # secrets.toml belum ada / belum dikonfigurasi — tidak masalah,
        # lanjut coba environment variable di bawah.
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


try:
    import anthropic
    _anthropic_api_key = _get_anthropic_api_key()
    if not _anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY tidak ditemukan di st.secrets maupun environment variable"
        )
    _anthropic_client = anthropic.Anthropic(api_key=_anthropic_api_key)
    AI_NORMALIZE_AVAILABLE = True
except Exception as exc:
    _anthropic_client = None
    AI_NORMALIZE_AVAILABLE = False
    if "anthropic" in sys.modules:
        # Library-nya ada, tapi key belum ketemu — log supaya gampang di-debug.
        logger.info("AI normalizer nonaktif: %s", exc)


# ── KONFIGURASI HALAMAN ───────────────────────────────────────────────────────
st.set_page_config(page_title="Smart EMR - RSJPDHK", page_icon="🫀", layout="wide")


# ── KONSTANTA ─────────────────────────────────────────────────────────────────
DB_PATH     = "rsjpdhk_emr.db"
API_URL     = "http://127.0.0.1:8000/api/v1/extract"
LAB_API_URL = "http://127.0.0.1:8000/api/v1/analyze-cppt"
API_TIMEOUT = 3       # detik
SESSION_TTL = 60      # menit

# Password disimpan sebagai hash SHA-256 (bukan plaintext)
# Untuk generate hash baru: hashlib.sha256("password".encode()).hexdigest()
KREDENSIAL: dict[str, str] = {
    "rudi": hashlib.sha256("1673".encode()).hexdigest(),
    "jule": hashlib.sha256("2107".encode()).hexdigest(),
}


# ====================================================================
# MASTER DATA 3S: INDIKATOR SLKI & PEMETAAN DIAGNOSA (SDKI)
# ====================================================================

INDIKATOR_SLKI = [
    # ── Respirasi ─────────────────────────────────────────────────────────────
    "Bersihan Jalan Napas (L.01001)",
    "Penyapihan Ventilator (L.01002)",
    "Pertukaran Gas (L.01003)",
    "Pola Napas (L.01004)",
    "Respons Ventilasi Mekanik (L.01005)",
    "Tingkat Aspirasi (L.01006)",
    "Ventilasi Spontan (L.01007)",
    # ── Sirkulasi & Jantung ───────────────────────────────────────────────────
    "Curah Jantung (L.02008)",
    "Keseimbangan Asam Basa (L.02009)",
    "Perfusi Miokard (L.02010)",
    "Perfusi Perifer (L.02011)",
    "Tingkat Perdarahan (L.02012)",
    "Perfusi Pulmonal (L.02013)",
    "Perfusi Serebral (L.02014)",
    "Sirkulasi Spontan (L.02015)",
    "Status Kardiopulmonal (L.02016)",
    # ── Nutrisi & Cairan ──────────────────────────────────────────────────────
    "Status Cairan (L.03028)",
    "Status Nutrisi (L.03030)",
    "Berat Badan (L.03018)",
    "Fungsi Gastrointestinal (L.03019)",
    "Nafsu Makan (L.03024)",
    "Keseimbangan Elektrolit (L.03021)",
    # ── Eliminasi ─────────────────────────────────────────────────────────────
    "Eliminasi Fekal (L.04033)",
    "Eliminasi Urin (L.04034)",
    "Kontinensia Urin (L.04036)",
    # ── Aktivitas & Istirahat ─────────────────────────────────────────────────
    "Ambulasi (L.05001)",
    "Konservasi Energi (L.05040)",
    "Mobilitas Fisik (L.05042)",
    "Toleransi Aktivitas (L.05047)",
    "Status Tidur (L.05045)",
    # ── Persepsi Kognisi ──────────────────────────────────────────────────────
    "Komunikasi Verbal (L.13118)",
    "Orientasi Kognitif (L.09082)",
    "Status Neurologis (L.06053)",
    "Memori (L.09074)",
    # ── Kenyamanan ────────────────────────────────────────────────────────────
    "Kontrol Nyeri (L.08065)",
    "Tingkat Nyeri (L.08066)",
    "Tingkat Kenyamanan (L.08064)",
    # ── Integritas Ego / Psikologis ───────────────────────────────────────────
    "Tingkat Ansietas (L.09093)",
    "Tingkat Depresi (L.09096)",
    "Tingkat Stres (L.09092)",
    "Harga Diri (L.09069)",
    # ── Keamanan & Proteksi ───────────────────────────────────────────────────
    "Integritas Kulit dan Jaringan (L.14125)",
    "Pemulihan Pascabedah (L.14129)",
    "Tingkat Cedera (L.14136)",
    "Tingkat Infeksi (L.14137)",
    "Tingkat Jatuh (L.14138)",
    # ── Pertumbuhan & Perkembangan ────────────────────────────────────────────
    "Ketahanan Personal (L.09074)",
    "Penyesuaian Sosial (L.13121)",
]

MASTER_DX_TO_SLKI = {
    # ── Respirasi ─────────────────────────────────────────────────────────────
    "D.0001": {"kata_kunci": "Bersihan Jalan Napas",  "kode_luaran": "L.01001", "narasi": "Bersihan Jalan Napas Meningkat (L.01001)"},
    "D.0002": {"kata_kunci": "Penyapihan Ventilator", "kode_luaran": "L.01002", "narasi": "Penyapihan Ventilator Meningkat (L.01002)"},
    "D.0003": {"kata_kunci": "Pertukaran Gas",        "kode_luaran": "L.01003", "narasi": "Pertukaran Gas Meningkat (L.01003)"},
    "D.0004": {"kata_kunci": "Ventilasi Spontan",     "kode_luaran": "L.01007", "narasi": "Ventilasi Spontan Meningkat (L.01007)"},
    "D.0005": {"kata_kunci": "Pola Napas",            "kode_luaran": "L.01004", "narasi": "Pola Napas Membaik (L.01004)"},
    "D.0006": {"kata_kunci": "Tingkat Aspirasi",      "kode_luaran": "L.01006", "narasi": "Tingkat Aspirasi Menurun (L.01006)"},
    # ── Sirkulasi & Jantung ───────────────────────────────────────────────────
    "D.0007": {"kata_kunci": "Sirkulasi Spontan",     "kode_luaran": "L.02015", "narasi": "Sirkulasi Spontan Meningkat (L.02015)"},
    "D.0008": {"kata_kunci": "Curah Jantung",         "kode_luaran": "L.02008", "narasi": "Curah Jantung Meningkat (L.02008)"},
    "D.0009": {"kata_kunci": "Perfusi Perifer",       "kode_luaran": "L.02011", "narasi": "Perfusi Perifer Meningkat (L.02011)"},
    "D.0010": {"kata_kunci": "Sirkulasi Spontan",     "kode_luaran": "L.02015", "narasi": "Sirkulasi Spontan Meningkat (L.02015)"},
    "D.0011": {"kata_kunci": "Curah Jantung",         "kode_luaran": "L.02008", "narasi": "Curah Jantung Meningkat (L.02008)"},
    "D.0012": {"kata_kunci": "Tingkat Perdarahan",    "kode_luaran": "L.02012", "narasi": "Tingkat Perdarahan Menurun (L.02012)"},
    "D.0013": {"kata_kunci": "Perfusi Pulmonal",      "kode_luaran": "L.02013", "narasi": "Perfusi Pulmonal Meningkat (L.02013)"},
    "D.0014": {"kata_kunci": "Perfusi Serebral",      "kode_luaran": "L.02014", "narasi": "Perfusi Serebral Meningkat (L.02014)"},
    "D.0015": {"kata_kunci": "Perfusi Miokard",       "kode_luaran": "L.02010", "narasi": "Perfusi Miokard Meningkat (L.02010)"},
    "D.0016": {"kata_kunci": "Status Kardiopulmonal", "kode_luaran": "L.02016", "narasi": "Status Kardiopulmonal Membaik (L.02016)"},
    "D.0017": {"kata_kunci": "Perfusi Serebral",      "kode_luaran": "L.02014", "narasi": "Perfusi Serebral Meningkat (L.02014)"},
    "D.0018": {"kata_kunci": "Perfusi Pulmonal",      "kode_luaran": "L.02013", "narasi": "Perfusi Pulmonal Meningkat (L.02013)"},
    "D.0019": {"kata_kunci": "Perfusi Perifer",       "kode_luaran": "L.02011", "narasi": "Perfusi Perifer Meningkat (L.02011)"},
    # ── Nutrisi & Cairan ───────────────────────────────────────────────
    "D.0020": {"kata_kunci": "Status Nutrisi",        "kode_luaran": "L.03030", "narasi": "Status Nutrisi Membaik (L.03030)"},
    "D.0021": {"kata_kunci": "Keseimbangan Asam Basa","kode_luaran": "L.02009", "narasi": "Keseimbangan Asam Basa Membaik (L.02009)"},
    "D.0022": {"kata_kunci": "Status Cairan",         "kode_luaran": "L.03028", "narasi": "Status Cairan Membaik (L.03028)"},
    "D.0023": {"kata_kunci": "Status Cairan",         "kode_luaran": "L.03028", "narasi": "Status Cairan Membaik (L.03028)"},
    "D.0024": {"kata_kunci": "Fungsi Gastrointestinal","kode_luaran": "L.03019", "narasi": "Fungsi Gastrointestinal Membaik (L.03019)"},
    "D.0025": {"kata_kunci": "Nafsu Makan",           "kode_luaran": "L.03024", "narasi": "Nafsu Makan Membaik (L.03024)"},
    "D.0026": {"kata_kunci": "Status Nutrisi",        "kode_luaran": "L.03030", "narasi": "Status Nutrisi Membaik (L.03030)"},
    # ── Eliminasi ─────────────────────────────────────────────────────────────
    "D.0037": {"kata_kunci": "keseimbangan Elektrolit", "kode_luaran": "L.03021", "narasi": "Keseimbangan Elektrolit Membaik (L.03021)"},
    "D.0038": {"kata_kunci": "Eliminasi Fekal",       "kode_luaran": "L.04033", "narasi": "Eliminasi Fekal Membaik (L.04033)"},
    "D.0039": {"kata_kunci": "Eliminasi Urin",        "kode_luaran": "L.04034", "narasi": "Eliminasi Urin Membaik (L.04034)"},
    "D.0040": {"kata_kunci": "Eliminasi Fekal",       "kode_luaran": "L.04033", "narasi": "Eliminasi Fekal Membaik (L.04033)"},
    "D.0041": {"kata_kunci": "Kontinensia Urin",      "kode_luaran": "L.04036", "narasi": "Kontinensia Urin Meningkat (L.04036)"},
    "D.0043": {"kata_kunci": "Eliminasi Urin",        "kode_luaran": "L.04034", "narasi": "Eliminasi Urin Membaik (L.04034)"},
    # ── Aktivitas & Istirahat ─────────────────────────────────────────────────
    "D.0054": {"kata_kunci": "Mobilitas Fisik",       "kode_luaran": "L.05042", "narasi": "Mobilitas Fisik Meningkat (L.05042)"},
    "D.0055": {"kata_kunci": "Mobilitas Fisik",       "kode_luaran": "L.05042", "narasi": "Mobilitas Fisik Meningkat (L.05042)"},
    "D.0056": {"kata_kunci": "Toleransi Aktivitas",   "kode_luaran": "L.05047", "narasi": "Toleransi Aktivitas Meningkat (L.05047)"},
    "D.0057": {"kata_kunci": "Konservasi Energi",     "kode_luaran": "L.05040", "narasi": "Konservasi Energi Meningkat (L.05040)"},
    "D.0058": {"kata_kunci": "Ambulasi",              "kode_luaran": "L.05001", "narasi": "Ambulasi Meningkat (L.05001)"},
    "D.0055": {"kata_kunci": "Status Tidur",          "kode_luaran": "L.05045", "narasi": "Status Tidur Membaik (L.05045)"},
    # ── Persepsi Kognisi ──────────────────────────────────────────────────────
    "D.0062": {"kata_kunci": "Komunikasi Verbal",     "kode_luaran": "L.13118", "narasi": "Komunikasi Verbal Meningkat (L.13118)"},
    "D.0063": {"kata_kunci": "Orientasi Kognitif",    "kode_luaran": "L.09082", "narasi": "Orientasi Kognitif Meningkat (L.09082)"},
    "D.0064": {"kata_kunci": "Memori",                "kode_luaran": "L.09074", "narasi": "Memori Membaik (L.09074)"},
    "D.0067": {"kata_kunci": "Status Neurologis",     "kode_luaran": "L.06053", "narasi": "Status Neurologis Membaik (L.06053)"},
    # ── Nyeri & Kenyamanan ────────────────────────────────────────────────────
    "D.0077": {"kata_kunci": "Tingkat Nyeri",         "kode_luaran": "L.08066", "narasi": "Tingkat Nyeri Menurun (L.08066)"},
    "D.0078": {"kata_kunci": "Tingkat Nyeri",         "kode_luaran": "L.08066", "narasi": "Tingkat Nyeri Menurun (L.08066)"},
    "D.0079": {"kata_kunci": "Tingkat Kenyamanan",    "kode_luaran": "L.08064", "narasi": "Tingkat Kenyamanan Meningkat (L.08064)"},
    # ── Integritas Ego / Psikologis ───────────────────────────────────────────
    "D.0080": {"kata_kunci": "Tingkat Ansietas",      "kode_luaran": "L.09093", "narasi": "Tingkat Ansietas Menurun (L.09093)"},
    "D.0081": {"kata_kunci": "Tingkat Depresi",       "kode_luaran": "L.09096", "narasi": "Tingkat Depresi Menurun (L.09096)"},
    "D.0082": {"kata_kunci": "Harga Diri",            "kode_luaran": "L.09069", "narasi": "Harga Diri Meningkat (L.09069)"},
    "D.0087": {"kata_kunci": "Tingkat Stres",         "kode_luaran": "L.09092", "narasi": "Tingkat Stres Menurun (L.09092)"},
    # ── Keamanan & Proteksi ───────────────────────────────────────────────────
    "D.0129": {"kata_kunci": "Integritas Kulit",      "kode_luaran": "L.14125", "narasi": "Integritas Kulit dan Jaringan Meningkat (L.14125)"},
    "D.0130": {"kata_kunci": "Integritas Kulit",      "kode_luaran": "L.14125", "narasi": "Integritas Kulit dan Jaringan Meningkat (L.14125)"},
    "D.0131": {"kata_kunci": "Pemulihan Pascabedah",  "kode_luaran": "L.14129", "narasi": "Pemulihan Pascabedah Meningkat (L.14129)"},
    "D.0136": {"kata_kunci": "Tingkat Jatuh",         "kode_luaran": "L.14138", "narasi": "Tingkat Jatuh Menurun (L.14138)"},
    "D.0137": {"kata_kunci": "Tingkat Cedera",        "kode_luaran": "L.14136", "narasi": "Tingkat Cedera Menurun (L.14136)"},
    "D.0142": {"kata_kunci": "Tingkat Infeksi",       "kode_luaran": "L.14137", "narasi": "Tingkat Infeksi Menurun (L.14137)"},
    "D.0143": {"kata_kunci": "Tingkat Infeksi",       "kode_luaran": "L.14137", "narasi": "Tingkat Infeksi Menurun (L.14137)"},
}

SDKI_NAME_MAPPING = {
    # ── Respirasi ─────────────────────────────────────────────────────────────
    "D.0001": "Bersihan Jalan Napas Tidak Efektif",
    "D.0002": "Gangguan Penyapihan Ventilator",
    "D.0003": "Gangguan Pertukaran Gas",
    "D.0004": "Gangguan Ventilasi Spontan",
    "D.0005": "Pola Napas Tidak Efektif",
    "D.0006": "Risiko Aspirasi",
    # ── Sirkulasi & Jantung ───────────────────────────────────────────────────
    "D.0007": "Gangguan Sirkulasi Spontan",
    "D.0008": "Penurunan Curah Jantung",
    "D.0009": "Perfusi Perifer Tidak Efektif",
    "D.0010": "Risiko Gangguan Sirkulasi Spontan",
    "D.0011": "Risiko Penurunan Curah Jantung",
    "D.0012": "Risiko Perdarahan",
    "D.0013": "Risiko Perfusi Pulmonal Tidak Efektif",
    "D.0014": "Gangguan Perfusi Serebral",
    "D.0015": "Risiko Perfusi Miokard Tidak Efektif",
    "D.0016": "Gangguan Status Kardiopulmonal",
    "D.0017": "Risiko Perfusi Serebral Tidak Efektif",
    "D.0018": "Risiko Perfusi Pulmonal Tidak Efektif",
    "D.0019": "Risiko Perfusi Perifer Tidak Efektif",
    # ── Nutrisi & Cairan ──────────────────────────────────────────────────────
    "D.0020": "Defisit Nutrisi",
    "D.0021": "Ketidakseimbangan Asam Basa",
    "D.0022": "Hipervolemia",
    "D.0023": "Hipovolemia",
    "D.0024": "Gangguan Menelan",
    "D.0025": "Ketidakstabilan Kadar Glukosa Darah",
    "D.0026": "Risiko Defisit Nutrisi",
    "D.0027": "Risiko Berat Badan Lebih",
    "D.0037": "Risiko Ketidakseimbangan Elektrolit",
    # ── Eliminasi ─────────────────────────────────────────────────────────────
    "D.0038": "Konstipasi",
    "D.0039": "Gangguan Eliminasi Urin",
    "D.0040": "Diare",
    "D.0041": "Inkontinensia Urin Fungsional",
    "D.0042": "Inkontinensia Urin Stres",
    "D.0043": "Retensi Urin",
    # ── Aktivitas & Istirahat ─────────────────────────────────────────────────
    "D.0054": "Gangguan Mobilitas Fisik",
    "D.0055": "Gangguan Pola Tidur",
    "D.0056": "Intoleransi Aktivitas",
    "D.0057": "Keletihan",
    "D.0058": "Hambatan Ambulasi",
    # ── Persepsi Kognisi ──────────────────────────────────────────────────────
    "D.0062": "Gangguan Komunikasi Verbal",
    "D.0063": "Konfusi Akut",
    "D.0064": "Gangguan Memori",
    "D.0065": "Konfusi Kronis",
    "D.0067": "Penurunan Kapasitas Adaptif Intrakranial",
    # ── Nyeri & Kenyamanan ────────────────────────────────────────────────────
    "D.0077": "Nyeri Akut",
    "D.0078": "Nyeri Kronis",
    "D.0079": "Sindrom Nyeri Kronis",
    # ── Integritas Ego / Psikologis ───────────────────────────────────────────
    "D.0080": "Ansietas",
    "D.0081": "Berduka",
    "D.0082": "Gangguan Citra Tubuh",
    "D.0083": "Gangguan Identitas Diri",
    "D.0085": "Harga Diri Rendah Kronis",
    "D.0086": "Harga Diri Rendah Situasional",
    "D.0087": "Ketidakberdayaan",
    # ── Keamanan & Proteksi ───────────────────────────────────────────────────
    "D.0109": "Risiko Syok",
    "D.0129": "Gangguan Integritas Kulit/Jaringan",
    "D.0130": "Risiko Gangguan Integritas Kulit/Jaringan",
    "D.0131": "Risiko Komplikasi Pascabedah",
    "D.0136": "Risiko Jatuh",
    "D.0137": "Risiko Cedera",
    "D.0142": "Risiko Infeksi",
    "D.0143": "Infeksi",
}

DX_TO_SLKI_MAPPING = {
    # ── Respirasi ─────────────────────────────────────────────────────────────
    "D.0001": {"kode_luaran": "L.01001", "narasi": "Bersihan Jalan Napas Meningkat (L.01001)"},
    "D.0002": {"kode_luaran": "L.01002", "narasi": "Penyapihan Ventilator Meningkat (L.01002)"},
    "D.0003": {"kode_luaran": "L.01003", "narasi": "Pertukaran Gas Meningkat (L.01003)"},
    "D.0004": {"kode_luaran": "L.01007", "narasi": "Ventilasi Spontan Meningkat (L.01007)"},
    "D.0005": {"kode_luaran": "L.01004", "narasi": "Pola Napas Membaik (L.01004)"},
    "D.0006": {"kode_luaran": "L.01006", "narasi": "Tingkat Aspirasi Menurun (L.01006)"},
    # ── Sirkulasi & Jantung ───────────────────────────────────────────────────
    "D.0007": {"kode_luaran": "L.02015", "narasi": "Sirkulasi Spontan Meningkat (L.02015)"},
    "D.0008": {"kode_luaran": "L.02008", "narasi": "Curah Jantung Meningkat (L.02008)"},
    "D.0009": {"kode_luaran": "L.02011", "narasi": "Perfusi Perifer Meningkat (L.02011)"},
    "D.0010": {"kode_luaran": "L.02015", "narasi": "Sirkulasi Spontan Meningkat (L.02015)"},
    "D.0011": {"kode_luaran": "L.02008", "narasi": "Curah Jantung Meningkat (L.02008)"},
    "D.0012": {"kode_luaran": "L.02012", "narasi": "Tingkat Perdarahan Menurun (L.02012)"},
    "D.0013": {"kode_luaran": "L.02013", "narasi": "Perfusi Pulmonal Meningkat (L.02013)"},
    "D.0014": {"kode_luaran": "L.02014", "narasi": "Perfusi Serebral Meningkat (L.02014)"},
    "D.0015": {"kode_luaran": "L.02010", "narasi": "Perfusi Miokard Meningkat (L.02010)"},
    "D.0016": {"kode_luaran": "L.02016", "narasi": "Status Kardiopulmonal Membaik (L.02016)"},
    "D.0017": {"kode_luaran": "L.02014", "narasi": "Perfusi Serebral Meningkat (L.02014)"},
    "D.0018": {"kode_luaran": "L.02013", "narasi": "Perfusi Pulmonal Meningkat (L.02013)"},
    "D.0019": {"kode_luaran": "L.02011", "narasi": "Perfusi Perifer Meningkat (L.02011)"},
    # ── Nutrisi & Cairan ──────────────────────────────────────────────────────
    "D.0020": {"kode_luaran": "L.03030", "narasi": "Status Nutrisi Membaik (L.03030)"},
    "D.0021": {"kode_luaran": "L.02009", "narasi": "Keseimbangan Asam Basa Membaik (L.02009)"},
    "D.0022": {"kode_luaran": "L.03028", "narasi": "Status Cairan Membaik (L.03028)"},
    "D.0023": {"kode_luaran": "L.03028", "narasi": "Status Cairan Membaik (L.03028)"},
    "D.0024": {"kode_luaran": "L.03019", "narasi": "Fungsi Gastrointestinal Membaik (L.03019)"},
    "D.0025": {"kode_luaran": "L.03028", "narasi": "Status Cairan Membaik (L.03028)"},
    "D.0026": {"kode_luaran": "L.03030", "narasi": "Status Nutrisi Membaik (L.03030)"},
    "D.0037": {"kode_luaran": "L.03021", "narasi": "Keseimbangan Elektrolit Membaik (L.03021)"},
    # ── Eliminasi ─────────────────────────────────────────────────────────────
    "D.0038": {"kode_luaran": "L.04033", "narasi": "Eliminasi Fekal Membaik (L.04033)"},
    "D.0039": {"kode_luaran": "L.04034", "narasi": "Eliminasi Urin Membaik (L.04034)"},
    "D.0040": {"kode_luaran": "L.04033", "narasi": "Eliminasi Fekal Membaik (L.04033)"},
    "D.0041": {"kode_luaran": "L.04036", "narasi": "Kontinensia Urin Meningkat (L.04036)"},
    "D.0043": {"kode_luaran": "L.04034", "narasi": "Eliminasi Urin Membaik (L.04034)"},
    # ── Aktivitas & Istirahat ─────────────────────────────────────────────────
    "D.0054": {"kode_luaran": "L.05042", "narasi": "Mobilitas Fisik Meningkat (L.05042)"},
    "D.0055": {"kode_luaran": "L.05045", "narasi": "Status Tidur Membaik (L.05045)"},
    "D.0056": {"kode_luaran": "L.05047", "narasi": "Toleransi Aktivitas Meningkat (L.05047)"},
    "D.0057": {"kode_luaran": "L.05040", "narasi": "Konservasi Energi Meningkat (L.05040)"},
    "D.0058": {"kode_luaran": "L.05001", "narasi": "Ambulasi Meningkat (L.05001)"},
    # ── Persepsi Kognisi ──────────────────────────────────────────────────────
    "D.0062": {"kode_luaran": "L.13118", "narasi": "Komunikasi Verbal Meningkat (L.13118)"},
    "D.0063": {"kode_luaran": "L.09082", "narasi": "Orientasi Kognitif Meningkat (L.09082)"},
    "D.0064": {"kode_luaran": "L.09074", "narasi": "Memori Membaik (L.09074)"},
    "D.0067": {"kode_luaran": "L.06053", "narasi": "Status Neurologis Membaik (L.06053)"},
    # ── Nyeri & Kenyamanan ────────────────────────────────────────────────────
    "D.0077": {"kode_luaran": "L.08066", "narasi": "Tingkat Nyeri Menurun (L.08066)"},
    "D.0078": {"kode_luaran": "L.08066", "narasi": "Tingkat Nyeri Menurun (L.08066)"},
    "D.0079": {"kode_luaran": "L.08064", "narasi": "Tingkat Kenyamanan Meningkat (L.08064)"},
    # ── Integritas Ego / Psikologis ───────────────────────────────────────────
    "D.0080": {"kode_luaran": "L.09093", "narasi": "Tingkat Ansietas Menurun (L.09093)"},
    "D.0081": {"kode_luaran": "L.09096", "narasi": "Tingkat Depresi Menurun (L.09096)"},
    "D.0082": {"kode_luaran": "L.09069", "narasi": "Harga Diri Meningkat (L.09069)"},
    "D.0085": {"kode_luaran": "L.09069", "narasi": "Harga Diri Meningkat (L.09069)"},
    "D.0086": {"kode_luaran": "L.09069", "narasi": "Harga Diri Meningkat (L.09069)"},
    "D.0087": {"kode_luaran": "L.09092", "narasi": "Tingkat Stres Menurun (L.09092)"},
    # ── Keamanan & Proteksi ───────────────────────────────────────────────────
    "D.0109": {"kode_luaran": "L.02016", "narasi": "Status Kardiopulmonal Membaik (L.02016)"},
    "D.0129": {"kode_luaran": "L.14125", "narasi": "Integritas Kulit dan Jaringan Meningkat (L.14125)"},
    "D.0130": {"kode_luaran": "L.14125", "narasi": "Integritas Kulit dan Jaringan Meningkat (L.14125)"},
    "D.0131": {"kode_luaran": "L.14129", "narasi": "Pemulihan Pascabedah Meningkat (L.14129)"},
    "D.0136": {"kode_luaran": "L.14138", "narasi": "Tingkat Jatuh Menurun (L.14138)"},
    "D.0137": {"kode_luaran": "L.14136", "narasi": "Tingkat Cedera Menurun (L.14136)"},
    "D.0142": {"kode_luaran": "L.14137", "narasi": "Tingkat Infeksi Menurun (L.14137)"},
    "D.0143": {"kode_luaran": "L.14137", "narasi": "Tingkat Infeksi Menurun (L.14137)"},
}


# =============================================================================
# FUNGSI AUDIO (OPSIONAL)
# =============================================================================

def transcribe_audio(audio_bytes: bytes) -> str:
    """Mengubah audio bytes (WAV) menjadi teks menggunakan Google Speech Recognition.

    Mengembalikan teks hasil transkripsi, atau pesan error yang diawali '[' jika gagal.
    Caller harus mengecek apakah hasil dimulai dengan '[' untuk mendeteksi kegagalan.
    """
    if not SPEECH_AVAILABLE:
        return "[Fitur speech recognition tidak tersedia. Instal: pip install SpeechRecognition streamlit-mic-recorder]"
    try:
        recognizer = sr.Recognizer()
        audio_file = io.BytesIO(audio_bytes)
        with sr.AudioFile(audio_file) as source:
            audio_data = recognizer.record(source)
        return recognizer.recognize_google(audio_data, language="id-ID")
    except sr.UnknownValueError:
        return "[Suara tidak dapat dikenali — coba ucapkan lebih jelas dan perlahan]"
    except sr.RequestError as exc:
        logger.error("Google Speech API error: %s", exc)
        return "[Gagal terhubung ke layanan Google Speech Recognition — periksa koneksi internet]"
    except Exception as exc:
        logger.error("transcribe_audio error: %s", exc)
        return "[Gagal memproses audio — pastikan mikrofon aktif dan format WAV didukung]"


# PATCH (2026-06): AI Filter & Translator untuk hasil Voice-to-Text.
#
# Google Speech Recognition mengembalikan teks mentah yang sering:
#   - mengandung filler/pengulangan kata akibat cara bicara natural,
#   - salah transkripsi istilah klinis (mis. "es pe o dua" alih-alih "SpO2"),
#   - menulis angka sebagai kata, padahal NumericValueParser di cdss_engine
#     butuh format digit ("(\d+)") agar nilai numerik (SpO2, EF, BNP, dst)
#     bisa terbaca,
#   - memakai variasi istilah yang tidak persis cocok dengan kamus keyword
#     CDSS (mis. "napas sesak" vs "sesak napas").
#
# Fungsi ini menyisipkan AI (Claude) sebagai lapisan pembersih + penyeragam
# istilah SEBELUM teks masuk ke text_area dan ke CDSS engine — tanpa
# mengubah makna klinis atau menambah interpretasi baru.
def normalize_clinical_transcript(raw_text: str, field: str = "S") -> str:
    """
    AI filter & translator untuk hasil voice-to-text.

    Membersihkan filler/disfluensi, menstandarisasi istilah klinis, dan
    mengubah angka lisan menjadi digit — TANPA menambah interpretasi/
    diagnosa baru yang tidak diucapkan pengguna.

    Jika AI tidak tersedia (library belum terinstal / API key belum diset)
    atau terjadi error, fungsi ini mengembalikan teks asli apa adanya
    (graceful degradation) — fitur voice-to-text dasar tetap berfungsi.
    """
    if not AI_NORMALIZE_AVAILABLE:
        return raw_text
    if not raw_text or raw_text.startswith("["):
        return raw_text  # pesan error dari transcribe_audio, lewati

    field_label = "Subjektif (keluhan pasien)" if field == "S" else "Objektif (TTV/pemeriksaan fisik/penunjang)"
    system_prompt = (
        "Anda adalah asisten normalisasi catatan keperawatan kardiovaskular. "
        "Tugas Anda HANYA membersihkan hasil speech-to-text berikut, BUKAN "
        "menambahkan interpretasi atau kesimpulan klinis baru:\n"
        "1. Hapus filler/pengulangan kata akibat hasil speech-to-text.\n"
        "2. Ubah angka yang diucapkan menjadi digit, contoh: "
        "'sembilan puluh delapan persen' -> '98%', "
        "'seratus empat puluh lima per delapan puluh delapan' -> '145/88'.\n"
        "3. Standarisasi istilah ke bentuk baku yang umum dipakai di rekam medis, "
        "contoh: 'es pe o dua' -> 'SpO2', 'efe ejeksi' atau 'ejeksi fraksi' -> 'EF', "
        "'sesak nafas' -> 'sesak napas'.\n"
        "4. JANGAN menambahkan diagnosa, kesimpulan, atau informasi yang tidak "
        "diucapkan oleh pengguna.\n"
        "5. Istilah medis berbahasa Inggris yang sudah baku boleh dibiarkan apa adanya.\n"
        f"Konteks: ini adalah data {field_label} pada pasien jantung.\n"
        "Keluarkan HANYA teks hasil yang sudah dibersihkan, tanpa penjelasan tambahan, "
        "tanpa tanda kutip, dan tanpa awalan apa pun."
    )

    try:
        response = _anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",  # model ringan & cepat, cocok untuk normalisasi per-ucapan
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": raw_text}],
        )
        cleaned = response.content[0].text.strip()
        return cleaned if cleaned else raw_text
    except Exception as exc:
        logger.warning("normalize_clinical_transcript error: %s — memakai teks mentah", exc)
        return raw_text





# =============================================================================
# SESSION STATE
# =============================================================================

def init_session() -> None:
    defaults = {
        "logged_in":        False,
        "user_id":          None,
        "shift":            None,
        "login_at":         None,
        "episode_id":       "EP-2026-00123",
        "daftar_asuhan":    None,
        "draft_cppt":       None,
        "order_list":       {},
        "logbook_payload":  [],
        "emergency_logs":   [],
        "checked_items":    {},
        "hasil_cdss":       None,
        "sumber_cdss_terakhir": "",
        "daftar_diagnosis": [],
        "selected_dx_codes": set(),
        "soap_A": "",
        "soap_P": "",
        # ── Voice-to-Text ─────────────────────────────────────────────────────
        "s_text_area":     "",   # Nilai kolom S (teks manual + VTT)
        "o_text_area":     "",   # Nilai kolom O (teks manual + VTT)
        "last_audio_s_id": None, # ID rekaman S terakhir yang sudah ditranskripsi
        "last_audio_o_id": None, # ID rekaman O terakhir yang sudah ditranskripsi
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


init_session()


def _session_expired() -> bool:
    """Kembalikan True jika sesi sudah melewati SESSION_TTL menit."""
    if not st.session_state.login_at:
        return False
    return datetime.now() - st.session_state.login_at > timedelta(minutes=SESSION_TTL)


def logout(reason: str = "") -> None:
    keys_to_clear = [
        "logged_in", "user_id", "shift", "login_at",
        "daftar_asuhan", "draft_cppt", "order_list",
        "logbook_payload", "checked_items", "hasil_cdss",
        "sumber_cdss_terakhir",
    ]
    for key in keys_to_clear:
        st.session_state[key] = None
    st.session_state.logged_in = False
    if reason:
        st.warning(reason)
    st.rerun()


# =============================================================================
# DATABASE
# =============================================================================

@contextmanager
def get_db():
    """Context manager: buka koneksi, yield conn, commit & tutup otomatis."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_local_database() -> None:
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pelayanan_slki_evaluasi (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id       TEXT    NOT NULL,
                waktu_evaluasi   TEXT    NOT NULL,
                nama_indikator   TEXT    NOT NULL,
                skor_indikator   INTEGER NOT NULL CHECK(skor_indikator BETWEEN 1 AND 5),
                oleh_pegawai     TEXT    NOT NULL
            )
        """)
        conn.commit()

        if conn.execute("SELECT COUNT(*) FROM pelayanan_slki_evaluasi").fetchone()[0] == 0:
            mock_data = [
                ("EP-2026-00123", "2026-06-13 07:00:00", "Curah Jantung (L.02008)",    2, "rudi"),
                ("EP-2026-00123", "2026-06-14 07:00:00", "Curah Jantung (L.02008)",    3, "jule"),
                ("EP-2026-00123", "2026-06-15 07:00:00", "Curah Jantung (L.02008)",    4, "jule"),
                ("EP-2026-00123", "2026-06-13 07:00:00", "Perfusi Perifer (L.02011)",  1, "rudi"),
                ("EP-2026-00123", "2026-06-14 14:00:00", "Perfusi Perifer (L.02011)",  3, "rudi"),
                ("EP-2026-00123", "2026-06-15 13:00:00", "Perfusi Perifer (L.02011)",  4, "rudi"),
                ("EP-2026-00123", "2026-06-14 07:00:00", "Status Cairan (L.03028)",    2, "jule"),
                ("EP-2026-00123", "2026-06-15 07:00:00", "Status Cairan (L.03028)",    3, "jule"),
                ("EP-2026-00123", "2026-06-14 07:00:00", "Integritas Kulit (L.14125)", 4, "jule"),
                ("EP-2026-00123", "2026-06-15 07:00:00", "Integritas Kulit (L.14125)", 3, "jule"),
                ("EP-2026-00123", "2026-06-15 07:00:00", "Tingkat Infeksi (L.14137)",  4, "rudi"),
            ]
            conn.executemany(
                """INSERT INTO pelayanan_slki_evaluasi
                   (episode_id, waktu_evaluasi, nama_indikator, skor_indikator, oleh_pegawai)
                   VALUES (?, ?, ?, ?, ?)""",
                mock_data,
            )
            conn.commit()


init_local_database()


def insert_slki_score(episode_id: str, indikator: str, skor: int, pegawai: str) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO pelayanan_slki_evaluasi
               (episode_id, waktu_evaluasi, nama_indikator, skor_indikator, oleh_pegawai)
               VALUES (?, ?, ?, ?, ?)""",
            (episode_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), indikator, skor, pegawai),
        )
        conn.commit()


def get_latest_slki_scores(episode_id: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT nama_indikator, skor_indikator
               FROM pelayanan_slki_evaluasi
               WHERE id IN (
                   SELECT MAX(id)
                   FROM pelayanan_slki_evaluasi
                   WHERE episode_id = ?
                   GROUP BY nama_indikator
               )""",
            (episode_id,),
        ).fetchall()
    return [(r["nama_indikator"], r["skor_indikator"]) for r in rows]


def fetch_real_slki_trends(episode_id: str) -> pd.DataFrame:
    try:
        with get_db() as conn:
            df = pd.read_sql_query(
                """SELECT
                       strftime('%d/%m %H:%M', waktu_evaluasi) AS "Waktu Evaluasi",
                       skor_indikator                           AS "Skor Indikator",
                       nama_indikator                           AS "Kriteria Hasil (SLKI)"
                   FROM pelayanan_slki_evaluasi
                   WHERE episode_id = ?
                   ORDER BY waktu_evaluasi ASC""",
                conn,
                params=(episode_id,),
            )
        return df
    except Exception as exc:
        logger.error("fetch_real_slki_trends gagal: %s", exc)
        st.error(f"⚠️ Gagal memuat data database lokal: {exc}")
        return pd.DataFrame()


# =============================================================================
# UTILITAS
# =============================================================================

def clean_text(text: str) -> str:
    text = re.sub(r"#{1,3}\s?", "", text)
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"^- ", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def _verify_password(plain: str, stored_hash: str) -> bool:
    """Perbandingan hash konstan-waktu untuk mencegah timing attack."""
    return hmac.compare_digest(
        hashlib.sha256(plain.encode()).hexdigest(),
        stored_hash,
    )


def _parse_intervensi(detail: str) -> list[str]:
    """Pisahkan teks intervensi berdasarkan titik-koma atau newline."""
    return [t.strip() for t in re.split(r"[;\n]", detail) if t.strip()]


# =============================================================================
# CDSS LOKAL (FALLBACK)
# =============================================================================

def local_cdss_rule_engine(
    s_text: str,
    o_text: str,
    force_codes: set[str] | None = None,
) -> list[dict]:
    """
    CDSS lokal berbasis keyword (fallback) + database template intervensi.

    PATCH (2026-06, revisi ke-3) — FIX BUG "luaran belum terpetakan di lokal":
    Fungsi ini awalnya mencampur DUA peran sekaligus: (1) database statis
    template diagnosa→intervensi SDKI/SLKI/SIKI, dan (2) detektor pemicu
    berbasis keyword (>= 2 kata kunci cocok di teks S/O). bridge_engine()
    memanggil fungsi ini untuk MENGAMBIL template intervensi diagnosa yang
    sudah dipilih CDSS v2.0 (weighted scoring + nilai numerik lab/TTV) —
    tapi karena template hanya muncul jika trigger keyword TEKS juga ikut
    terpenuhi, diagnosa yang valid secara klinis (mis. terdeteksi dari
    EF/troponin/laktat, bukan dari kata kunci di teks) malah tidak
    ketemu templatenya ("Luaran belum terpetakan di lokal"), padahal
    template-nya sudah ada & lengkap di database ini.

    Fix: parameter `force_codes` memungkinkan caller (bridge_engine) memaksa
    template tertentu IKUT MUNCUL berdasarkan KODE diagnosa saja, terlepas
    dari hasil deteksi keyword teks — tanpa mengubah perilaku default
    (force_codes=None) yang masih dipakai apa adanya untuk jalur fallback
    "Lokal (Fallback Keyword)" murni berbasis teks.
    """
    combined = (s_text + " " + o_text).lower()
    force_codes_set = force_codes or set()
    rekomendasi = []

    # =========================================================================
    # ── A. SISTEM RESPIRASI ──────────────────────────────────────────────────
    # =========================================================================

    # A1. Bersihan Jalan Napas Tidak Efektif (D.0001)
    napas_kw = ["sekret", "sputum", "dahak", "lendir", "batuk tidak efektif",
                "suara napas tambahan", "ronki", "wheezing", "stridor",
                "tersedak", "sputum berlebih"]
    if "D.0001" in force_codes_set or sum(1 for kw in napas_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0001",
            "diagnosa_keperawatan": (
                "Bersihan Jalan Napas Tidak Efektif b.d Sekresi yang Tertahan / "
                "Hipersekresi Mukus d.d Suara Napas Tambahan, Batuk Tidak Efektif, Sputum Berlebih."
            ),
            "luaran_keperawatan": "Bersihan Jalan Napas Meningkat (L.01001)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor pola napas (frekuensi, kedalaman, usaha napas);"
                    "Auskultasi suara napas tambahan (ronki, wheezing);"
                    "Monitor saturasi oksigen (SpO2) secara kontinu"
                ),
                "Terapeutik": (
                    "Posisikan pasien semi-fowler (30–45°) untuk memaksimalkan ventilasi;"
                    "Lakukan fisioterapi dada (clapping, postural drainage) secara teratur;"
                    "Lakukan penghisapan sekret (suction) bila diperlukan dengan teknik steril;"
                    "Berikan oksigen tambahan sesuai kebutuhan klinis"
                ),
                "Edukasi": (
                    "Ajarkan teknik batuk efektif (deep breathing & huffing);"
                    "Anjurkan minum air hangat untuk mengencerkan sekret;"
                    "Ajarkan cara menggunakan nebulizer mandiri jika diresepkan"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian bronkodilator (salbutamol, ipratropium) via nebulizer;"
                    "Kolaborasi pemberian mukolitik (asetilsistein, ambroksol) sesuai instruksi medis"
                ),
            },
        })

    # A2. Gangguan Pertukaran Gas (D.0003)
    gas_kw = ["spo2", "saturasi", "pco2", "po2", "hipoksia", "hiperkapnia",
              "sianosis", "konfusi akibat hipoksia", "agd", "asidosis"]
    if "D.0003" in force_codes_set or sum(1 for kw in gas_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0003",
            "diagnosa_keperawatan": (
                "Gangguan Pertukaran Gas b.d Ketidakseimbangan Ventilasi-Perfusi / "
                "Perubahan Membran Alveolar-Kapiler d.d SpO2 Menurun, PaCO2 Meningkat."
            ),
            "luaran_keperawatan": "Pertukaran Gas Meningkat (L.01003)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor frekuensi napas, kedalaman, dan usaha napas;"
                    "Monitor saturasi oksigen (SpO2) dan nilai AGD secara berkala;"
                    "Monitor status neurologis (kesadaran, orientasi, gelisah)"
                ),
                "Terapeutik": (
                    "Pertahankan kepatenan jalan napas;"
                    "Posisikan pasien fowler/semi-fowler;"
                    "Berikan oksigen via nasal kanul/masker sesuai kebutuhan SpO2;"
                    "Siapkan alat bantu napas (NIPPV/ventilator) jika ada indikasi"
                ),
                "Edukasi": (
                    "Ajarkan teknik pursed-lip breathing untuk membantu ekspirasi;"
                    "Anjurkan pasien membatasi aktivitas berat saat sesak berat"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemeriksaan AGD (Analisis Gas Darah);"
                    "Kolaborasi ventilasi mekanik non-invasif (BiPAP/CPAP) bila saturasi < 90%"
                ),
            },
        })

    # A3. Pola Napas Tidak Efektif (D.0005)
    pola_napas_kw = ["takipnea", "bradipnea", "dispnea", "sesak napas", "napas cepat",
                     "napas dangkal", "penggunaan otot bantu napas", "pernapasan cuping hidung"]
    if "D.0005" in force_codes_set or sum(1 for kw in pola_napas_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0005",
            "diagnosa_keperawatan": (
                "Pola Napas Tidak Efektif b.d Hambatan Upaya Napas / Deformitas Dinding Dada "
                "d.d Dispnea, Takipnea, Penggunaan Otot Bantu Napas."
            ),
            "luaran_keperawatan": "Pola Napas Membaik (L.01004)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor frekuensi, irama, kedalaman, dan upaya napas;"
                    "Monitor pola napas abnormal (Kussmaul, Cheyne-Stokes, Biot);"
                    "Monitor saturasi oksigen"
                ),
                "Terapeutik": (
                    "Posisikan semi-fowler atau fowler;"
                    "Berikan oksigen sesuai kebutuhan;"
                    "Fasilitasi perubahan posisi yang sering untuk kenyamanan napas"
                ),
                "Edukasi": (
                    "Ajarkan teknik relaksasi napas diafragma;"
                    "Ajarkan pasien untuk tidak menahan napas saat nyeri"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian bronkodilator atau relaksan otot polos sesuai indikasi;"
                    "Kolaborasi pemasangan ventilasi mekanik jika RR < 8 atau > 30 x/menit"
                ),
            },
        })

    # A4. Risiko Aspirasi (D.0006)
    aspirasi_kw = ["disfagia", "kesulitan menelan", "penurunan refleks menelan",
                   "ngt", "sonde", "penurunan kesadaran", "trakeostomi", "muntah berulang"]
    if "D.0006" in force_codes_set or sum(1 for kw in aspirasi_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0006",
            "diagnosa_keperawatan": (
                "Risiko Aspirasi d.d Penurunan Tingkat Kesadaran / Gangguan Menelan / "
                "Pemasangan NGT."
            ),
            "luaran_keperawatan": "Tingkat Aspirasi Menurun (L.01006)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor tingkat kesadaran dan refleks batuk serta menelan;"
                    "Monitor posisi NGT sebelum setiap pemberian nutrisi enteral"
                ),
                "Terapeutik": (
                    "Pertahankan posisi kepala tempat tidur ≥ 30° saat pemberian makan;"
                    "Hentikan makan enteral 30 menit sebelum fisioterapi/prosedur;"
                    "Sediakan alat suction di samping tempat tidur"
                ),
                "Edukasi": (
                    "Ajarkan keluarga mengenali tanda aspirasi dini;"
                    "Anjurkan makan perlahan dengan porsi kecil dan tekstur yang sesuai"
                ),
                "Kolaborasi": (
                    "Konsultasi dengan ahli gizi untuk modifikasi tekstur diet;"
                    "Konsultasi dengan speech therapist jika disfagia berat"
                ),
            },
        })

    # =========================================================================
    # ── B. SISTEM KARDIOVASKULAR & SIRKULASI ─────────────────────────────────
    # =========================================================================

    # B1. Penurunan Curah Jantung (D.0008)
    cardiac_kw = ["penurunan curah jantung", "jantung", "orthopnea", "pnd", "edema",
                  "jvp", "murmur", "ef menurun","ef <= 50", "cardiomegali", "nyeri dada",
                  "infark","STEMI","NSTEMI", "chf", "gagal jantung", "aritmia", "palpitasi",
                  "bradikardia", "takikardia","AFRVR","SVT", "td menurun","TD <= 80", "hipotensi", "syok kardiogenik"]
    if "D.0008" in force_codes_set or sum(1 for kw in cardiac_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0008",
            "diagnosa_keperawatan": (
                "Penurunan Curah Jantung b.d Perubahan Preload / Afterload / Kontraktilitas / "
                "Irama Jantung d.d Dispnea, Edema, Tekanan Darah Abnormal."
            ),
            "luaran_keperawatan": "Curah Jantung Meningkat (L.02008)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor tekanan darah (invasif/non-invasif) dan nadi setiap jam;"
                    "Monitor EKG 12 lead dan interpretasikan irama jantung secara kontinu;"
                    "Monitor saturasi oksigen, CVP, dan tanda-tanda perfusi perifer;"
                    "Monitor intake-output cairan secara ketat setiap shift"
                ),
                "Terapeutik": (
                    "Posisikan pasien semi-fowler (30–45°) untuk mengurangi preload;"
                    "Batasi aktivitas dan berikan lingkungan yang tenang;"
                    "Berikan oksigen untuk mempertahankan SpO2 ≥ 94%;"
                    "Pasang akses IV line yang adekuat"
                ),
                "Edukasi": (
                    "Anjurkan beristirahat secara adekuat dan menghindari Valsalva maneuver;"
                    "Edukasi pasien tentang tanda perburukan (sesak tiba-tiba, nyeri dada hebat);"
                    "Ajarkan pembatasan cairan jika ada restriksi dari DPJP"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian inotropik (dobutamin, dopamin) sesuai instruksi DPJP;"
                    "Kolaborasi pemberian diuretik kuat (furosemid IV) untuk overload;"
                    "Kolaborasi pemeriksaan ekokardiografi, troponin, BNP/NT-proBNP"
                ),
            },
        })

    # B2. Perfusi Perifer Tidak Efektif (D.0009)
    perifer_kw = ["akral dingin", "akral", "pucat", "crt", "crt >2", "nadi lemah",
                  "nadi tidak teraba", "sianosis perifer", "klaudikasio", "edema tungkai",
                  "varises", "baal", "kesemutan ekstremitas"]
    if "D.0009" in force_codes_set or sum(1 for kw in perifer_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0009",
            "diagnosa_keperawatan": (
                "Perfusi Perifer Tidak Efektif b.d Penurunan Aliran Darah Arteri / "
                "Vasokonstriksi d.d Akral Dingin, CRT > 2 Detik, Nadi Perifer Lemah."
            ),
            "luaran_keperawatan": "Perfusi Perifer Meningkat (L.02011)",
            "rencana_intervensi": {
                "Observasi": (
                    "Periksa sirkulasi perifer (nadi, CRT, warna, suhu, edema) setiap 2 jam;"
                    "Monitor ankle-brachial index (ABI) jika tersedia;"
                    "Monitor tanda-tanda deep vein thrombosis (DVT): kemerahan, nyeri betis"
                ),
                "Terapeutik": (
                    "Hindari pakaian ketat dan penekanan pada area terganggu;"
                    "Elevasi ekstremitas bawah 20–30° untuk mengurangi edema;"
                    "Lakukan latihan ROM pasif/aktif untuk melancarkan sirkulasi;"
                    "Hindari pemasangan IV/manset di sisi yang sirkulasinya terganggu"
                ),
                "Edukasi": (
                    "Anjurkan olahraga ringan terjadwal (jalan kaki, sepeda statis);"
                    "Edukasi tentang tanda komplikasi gangguan sirkulasi yang harus segera dilaporkan;"
                    "Anjurkan menghindari merokok dan alkohol"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian antiplatelet (aspirin, clopidogrel) sesuai instruksi;"
                    "Kolaborasi pemberian antikoagulan (heparin, LMWH) jika ada indikasi DVT/PE"
                ),
            },
        })

    # B3. Risiko Perfusi Miokard Tidak Efektif (D.0015)
    mio_kw = ["iskemia", "acs", "stemi", "nstemi", "angina", "troponin",
              "st elevasi", "st depresi", "nyeri dada menjalar", "diaphoresis",
              "cold sweat", "keringat dingin", "nyeri dada kiri"]
    if "D.0015" in force_codes_set or sum(1 for kw in mio_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0015",
            "diagnosa_keperawatan": (
                "Risiko Perfusi Miokard Tidak Efektif d.d Faktor Risiko: Spasme Arteri Koroner / "
                "Aterosklerosis / Peningkatan Enzim Jantung."
            ),
            "luaran_keperawatan": "Perfusi Miokard Meningkat (L.02010)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor EKG 12 lead secara serial untuk perubahan segmen ST;"
                    "Monitor tanda-tanda iskemia: nyeri dada, diaphoresis, mual;"
                    "Monitor kadar troponin I/T dan CK-MB secara serial"
                ),
                "Terapeutik": (
                    "Pertahankan tirah baring absolut di fase akut;"
                    "Pasang akses IV line; berikan oksigen untuk SpO2 ≥ 94%;"
                    "Siapkan defibrilator dan obat emergensi (atropin, epinefrin) di samping tempat tidur"
                ),
                "Edukasi": (
                    "Ajarkan pasien melaporkan segera jika nyeri dada muncul atau memburuk;"
                    "Edukasi pentingnya kepatuhan terapi jangka panjang (DAPT, statin)"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian nitrat sublingual/IV untuk vasodilasi koroner;"
                    "Kolaborasi pemberian DAPT (aspirin + ticagrelor/clopidogrel);"
                    "Koordinasi dengan kardiologi intervensional untuk tindakan PCI/CABG"
                ),
            },
        })

    # B4. Risiko Perdarahan (D.0012)
    perdarahan_kw = ["perdarahan", "hematom", "hemoglobin rendah", "hb turun",
                     "trombositopenia", "koagulopati", "inr", "antikoagulan",
                     "post operasi", "luka operasi", "drain berdarah", "hematuria"]
    if "D.0012" in force_codes_set or sum(1 for kw in perdarahan_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0012",
            "diagnosa_keperawatan": (
                "Risiko Perdarahan d.d Tindakan Pembedahan / Terapi Antikoagulan / "
                "Gangguan Koagulasi."
            ),
            "luaran_keperawatan": "Tingkat Perdarahan Menurun (L.02012)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor tanda-tanda perdarahan aktif (petechiae, ekimosis, hematuria, melena);"
                    "Monitor nilai hemoglobin, hematokrit, trombosit, PT, APTT secara berkala;"
                    "Monitor kondisi luka operasi dan drain setiap jam"
                ),
                "Terapeutik": (
                    "Pertahankan tirah baring jika terjadi perdarahan aktif;"
                    "Hindari prosedur invasif yang tidak perlu pada pasien koagulopati;"
                    "Kompres luka dengan tekanan bila terdapat perdarahan eksternal"
                ),
                "Edukasi": (
                    "Anjurkan menggunakan sikat gigi berbulu halus;"
                    "Edukasi untuk menghindari penggunaan NSAID dan obat pengencer darah tanpa petunjuk dokter"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian transfusi PRC/FFP/trombosit sesuai indikasi dan instruksi DPJP;"
                    "Kolaborasi pemberian vitamin K, protamin, atau antidot antikoagulan jika diperlukan"
                ),
            },
        })

    # B5. Risiko Perfusi Serebral Tidak Efektif (D.0017)
    serebral_kw = ["stroke", "tia", "penurunan kesadaran", "hemiparesis", "afasia",
                   "tekanan intrakranial", "tic", "gcs menurun", "papil edema",
                   "carotid stenosis", "atrial fibrilasi", "emboli serebral"]
    if "D.0017" in force_codes_set or sum(1 for kw in serebral_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0017",
            "diagnosa_keperawatan": (
                "Risiko Perfusi Serebral Tidak Efektif d.d Faktor Risiko: Embolisme / "
                "Arteriosklerosis / Hipertensi / Penurunan Kesadaran."
            ),
            "luaran_keperawatan": "Perfusi Serebral Meningkat (L.02014)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor GCS dan status neurologis secara ketat (pupil, refleks);"
                    "Monitor tekanan darah secara kontinu (hindari hipotesi dan hipertensi mendadak);"
                    "Monitor tanda peningkatan TIK: sakit kepala hebat, muntah proyektil, penurunan GCS"
                ),
                "Terapeutik": (
                    "Posisikan kepala tempat tidur 30° dan netralkan posisi leher;"
                    "Hindari stimulus berlebihan; batasi pengunjung;"
                    "Pertahankan normotermia (hindari demam yang memperburuk iskemia)"
                ),
                "Edukasi": (
                    "Edukasi keluarga tanda stroke (FAST: Face, Arm, Speech, Time);"
                    "Anjurkan kontrol tekanan darah dan kepatuhan terapi di rumah"
                ),
                "Kolaborasi": (
                    "Kolaborasi CT scan/MRI otak untuk diagnostik;"
                    "Kolaborasi pemberian trombolitik (alteplase) jika dalam window period stroke iskemik;"
                    "Kolaborasi dengan neurologi untuk manajemen TIK"
                ),
            },
        })

    # =========================================================================
    # ── C. NUTRISI & CAIRAN ──────────────────────────────────────────────────
    # =========================================================================

    # C1. Hipovolemia (D.0023)
    hipovolemia_kw = ["muntah", "diare", "pendarahan", "turgor", "mukosa kering",
                      "haus", "cekung", "urin sedikit", "lemas", "hipotensi ortostatik",
                      "nadi cepat lemah", "oliguria"]
    is_dehydration = any(x in combined for x in ["diare", "muntah berulang", "turgor lambat"])
    if "D.0023" in force_codes_set or sum(1 for kw in hipovolemia_kw if kw in combined) >= 2 or is_dehydration:
        rekomendasi.append({
            "kode_diagnosa": "D.0023",
            "diagnosa_keperawatan": (
                "Hipovolemia b.d Kehilangan Cairan Aktif / Kegagalan Mekanisme Regulasi "
                "d.d Turgor Menurun, Mukosa Kering, Oliguria, Nadi Cepat Lemah."
            ),
            "luaran_keperawatan": "Status Cairan Membaik (L.03028)",
            "rencana_intervensi": {
                "Observasi": (
                    "Periksa tanda dan gejala hipovolemia (nadi lemah, turgor kulit menurun, "
                    "mukosa kering, urin pekat, hemokonsentrasi);"
                    "Hitung balance cairan dan monitor berat badan harian"
                ),
                "Terapeutik": (
                    "Berikan asupan cairan oral secara bertahap jika toleransi baik;"
                    "Berikan posisi modified Trendelenburg jika ada tanda syok hipovolemik"
                ),
                "Edukasi": (
                    "Anjurkan memperbanyak asupan cairan oral;"
                    "Anjurkan segera melapor jika ada tanda dehidrasi yang memburuk"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian cairan IV isotonis (NaCl 0.9%, RL) secara agresif;"
                    "Kolaborasi pemeriksaan elektrolit dan BUN/kreatinin"
                ),
            },
        })

    # C2. Hipervolemia (D.0022)
    hipervolemia_kw = ["edema", "asites", "edema paru", "overload cairan", "bb naik cepat",
                       "jvp meningkat", "ronki basah", "edema perifer", "oliguria dengan bb naik"]
    if "D.0022" in force_codes_set or sum(1 for kw in hipervolemia_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0022",
            "diagnosa_keperawatan": (
                "Hipervolemia b.d Kelebihan Asupan Cairan / Gangguan Mekanisme Regulasi "
                "d.d Edema, JVP Meningkat, Ronki Basah, Berat Badan Meningkat Mendadak."
            ),
            "luaran_keperawatan": "Status Cairan Membaik (L.03028)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor intake-output cairan setiap shift;"
                    "Timbang berat badan setiap pagi sebelum sarapan;"
                    "Monitor tanda edema paru: dispnea, ronki basah, SpO2 turun"
                ),
                "Terapeutik": (
                    "Batasi asupan cairan sesuai instruksi DPJP (biasanya 500–1000 mL/hari);"
                    "Batasi asupan natrium;"
                    "Posisikan semi-fowler untuk kenyamanan napas"
                ),
                "Edukasi": (
                    "Edukasi pentingnya pembatasan garam dan cairan;"
                    "Ajarkan cara menghitung kebutuhan cairan harian"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian diuretik (furosemid, spironolakton) sesuai instruksi;"
                    "Kolaborasi ultrafiltrasi atau dialisis jika gagal respons diuretik"
                ),
            },
        })

    # C3. Defisit Nutrisi (D.0020)
    nutrisi_kw = ["berat badan turun", "bb turun", "bmi rendah", "malnutrisi",
                  "anoreksia", "mual", "tidak mau makan", "albumin rendah",
                  "protein rendah", "kachexia", "penurunan nafsu makan"]
    if "D.0020" in force_codes_set or sum(1 for kw in nutrisi_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0020",
            "diagnosa_keperawatan": (
                "Defisit Nutrisi b.d Ketidakmampuan Mencerna / Menelan Makanan / "
                "Kurang Asupan Makanan d.d Berat Badan Menurun, Albumin Rendah."
            ),
            "luaran_keperawatan": "Status Nutrisi Membaik (L.03030)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor asupan makan dan minuman setiap hari (food recall);"
                    "Monitor berat badan dan IMT secara berkala;"
                    "Monitor kadar albumin, prealbumin, dan hemoglobin"
                ),
                "Terapeutik": (
                    "Sajikan makanan dalam porsi kecil tapi sering (5–6 kali/hari);"
                    "Berikan makanan tinggi kalori dan protein sesuai anjuran ahli gizi;"
                    "Ciptakan lingkungan makan yang nyaman dan menyenangkan"
                ),
                "Edukasi": (
                    "Ajarkan diet yang tepat sesuai kondisi (diet jantung, DM, renal, dll.);"
                    "Anjurkan keluarga membawa makanan favorit pasien yang masih sesuai diet"
                ),
                "Kolaborasi": (
                    "Konsultasi ahli gizi untuk perencanaan nutrisi komprehensif;"
                    "Kolaborasi nutrisi enteral via NGT jika asupan oral tidak adekuat;"
                    "Kolaborasi nutrisi parenteral (TPN) jika fungsi GI terganggu berat"
                ),
            },
        })

    # C4. Ketidakstabilan Kadar Glukosa Darah (D.0025)
    gula_kw = ["hipoglikemia", "hiperglikemia", "gula darah", "gdp", "gds", "hba1c",
               "diabetes", "dm", "dextrose", "pusing gula", "keringat gula"]
    if "D.0025" in force_codes_set or sum(1 for kw in gula_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0025",
            "diagnosa_keperawatan": (
                "Ketidakstabilan Kadar Glukosa Darah b.d Resistensi Insulin / "
                "Gangguan Sekresi Insulin d.d GDS Abnormal, Poliuria, Polidipsia."
            ),
            "luaran_keperawatan": "Status Cairan Membaik (L.03028)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor kadar glukosa darah sesuai protokol (pre/post makan, setiap 4–6 jam);"
                    "Monitor tanda hipoglikemia: pucat, keringat dingin, tremor, pusing;"
                    "Monitor tanda hiperglikemia: poliuria, polidipsia, pandangan kabur"
                ),
                "Terapeutik": (
                    "Berikan dextrose 40% jika GDS < 70 mg/dL disertai gejala hipoglikemia;"
                    "Pastikan jadwal makan teratur dan tepat waktu sesuai insulin yang diberikan"
                ),
                "Edukasi": (
                    "Ajarkan pengenalan tanda hipoglikemia dan cara mengatasinya (minum jus/permen);"
                    "Edukasi pengaturan pola makan dan aktivitas dalam manajemen DM"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian insulin sesuai sliding scale atau insulin protokol DPJP;"
                    "Konsultasi endokrinologi untuk optimasi manajemen DM perioperatif"
                ),
            },
        })

    # =========================================================================
    # ── D. ELIMINASI ─────────────────────────────────────────────────────────
    # =========================================================================

    # D1. Gangguan Eliminasi Urin (D.0039)
    urin_kw = ["urin sedikit", "oliguria", "anuria", "retensi urin", "disuria",
               "urgensi", "frekuensi bak", "kateter urin", "kreatinin meningkat",
               "gagal ginjal", "azotemia"]
    if "D.0039" in force_codes_set or sum(1 for kw in urin_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0039",
            "diagnosa_keperawatan": (
                "Gangguan Eliminasi Urin b.d Penurunan Kapasitas Kandung Kemih / "
                "Gangguan Fungsi Ginjal d.d Oliguria, Disuria, Retensi Urin."
            ),
            "luaran_keperawatan": "Eliminasi Urin Membaik (L.04034)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor produksi urin setiap jam (target > 0.5 mL/kgBB/jam);"
                    "Monitor karakteristik urin (warna, konsentrasi, kekeruhan);"
                    "Monitor fungsi ginjal: kreatinin, BUN, eGFR"
                ),
                "Terapeutik": (
                    "Pertahankan kepatenan kateter urin (cegah kinking dan sumbatan);"
                    "Jaga kebersihan area kateter dengan teknik aseptik;"
                    "Berikan posisi yang memudahkan berkemih jika tanpa kateter"
                ),
                "Edukasi": (
                    "Anjurkan asupan cairan adekuat 2–2.5 L/hari kecuali ada restriksi;"
                    "Ajarkan teknik latihan kandung kemih (bladder training)"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemasangan kateter urin jika retensi berat;"
                    "Kolaborasi pemeriksaan urinalisis, kultur urin, dan USG ginjal;"
                    "Kolaborasi dengan nefrologi jika ada tanda AKI/CKD"
                ),
            },
        })

    # D2. Konstipasi (D.0038)
    konstipasi_kw = ["konstipasi", "bab keras", "tidak bab", "susah bab", "feses keras",
                     "distensi abdomen", "kembung", "perut keras", "ileus"]
    if "D.0038" in force_codes_set or sum(1 for kw in konstipasi_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0038",
            "diagnosa_keperawatan": (
                "Konstipasi b.d Penurunan Motilitas Gastrointestinal / Tirah Baring Lama / "
                "Efek Obat (Opioid) d.d Tidak BAB > 3 Hari, Feses Keras, Distensi Abdomen."
            ),
            "luaran_keperawatan": "Eliminasi Fekal Membaik (L.04033)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor pola BAB (frekuensi, konsistensi, warna);"
                    "Auskultasi bising usus setiap shift;"
                    "Monitor tanda ileus paralitik atau obstruksi usus"
                ),
                "Terapeutik": (
                    "Berikan posisi nyaman untuk defekasi (duduk atau miring kiri);"
                    "Lakukan pijat abdomen searah jarum jam untuk merangsang peristaltik;"
                    "Berikan enema atau supositoria jika ada instruksi medis"
                ),
                "Edukasi": (
                    "Anjurkan asupan serat tinggi dan cairan yang cukup;"
                    "Anjurkan mobilisasi bertahap untuk merangsang peristaltik usus"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian laksatif (lactulose, bisacodyl) sesuai instruksi;"
                    "Hentikan atau ganti opioid jika memungkinkan untuk mengurangi efek konstipasi"
                ),
            },
        })

    # D3. Diare (D.0040)
    diare_kw = ["diare", "bab cair", "bab >3x", "feses cair", "mencret", "gastroenteritis"]
    if "D.0040" in force_codes_set or sum(1 for kw in diare_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0040",
            "diagnosa_keperawatan": (
                "Diare b.d Inflamasi Gastrointestinal / Efek Samping Antibiotik / "
                "Malabsorpsi d.d BAB Cair > 3x/hari, Kram Abdomen."
            ),
            "luaran_keperawatan": "Eliminasi Fekal Membaik (L.04033)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor frekuensi, konsistensi, warna, dan bau feses;"
                    "Monitor tanda dan gejala dehidrasi akibat diare;"
                    "Monitor nilai elektrolit (K, Na) secara berkala"
                ),
                "Terapeutik": (
                    "Berikan cairan pengganti (oral/IV) sesuai kebutuhan;"
                    "Jaga kebersihan area perianal untuk mencegah iritasi kulit"
                ),
                "Edukasi": (
                    "Anjurkan diet lunak rendah serat dan BRAT (Banana, Rice, Applesauce, Toast);"
                    "Ajarkan kebersihan tangan setelah BAB"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemeriksaan feses (kultur, parasit) untuk identifikasi etiologi;"
                    "Kolaborasi pemberian antidiare, probiotik, atau antibiotik jika ada indikasi"
                ),
            },
        })

    # =========================================================================
    # ── E. AKTIVITAS & ISTIRAHAT ─────────────────────────────────────────────
    # =========================================================================

    # E1. Intoleransi Aktivitas (D.0056)
    aktivitas_kw = ["sesak saat aktivitas", "lelah", "lemah", "tidak mampu beraktivitas",
                    "dyspnea on effort", "doe", "aktivitas terbatas", "toleransi rendah",
                    "kelelahan ekstrem", "kapasitas fungsional rendah"]
    if "D.0056" in force_codes_set or sum(1 for kw in aktivitas_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0056",
            "diagnosa_keperawatan": (
                "Intoleransi Aktivitas b.d Ketidakseimbangan Suplai dan Kebutuhan Oksigen / "
                "Kelemahan d.d Dispnea saat Aktivitas, Kelelahan Berlebih, HR Meningkat."
            ),
            "luaran_keperawatan": "Toleransi Aktivitas Meningkat (L.05047)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor respons kardiorespirasi terhadap aktivitas (HR, RR, TD, SpO2);"
                    "Monitor keluhan sesak, nyeri, dan kelelahan selama dan setelah aktivitas;"
                    "Kaji kemampuan fungsional dasar (ADL) pasien"
                ),
                "Terapeutik": (
                    "Fasilitasi aktivitas fisik bertahap sesuai toleransi (bed exercise → duduk → berdiri);"
                    "Berikan lingkungan yang tenang dan batasi pengunjung;"
                    "Bantu pasien memenuhi ADL sesuai kebutuhan"
                ),
                "Edukasi": (
                    "Ajarkan teknik hemat energi dalam aktivitas sehari-hari;"
                    "Anjurkan latihan fisik bertahap (cardiac rehabilitation jika ada indikasi);"
                    "Edukasi tentang pengenalan tanda intoleransi aktivitas"
                ),
                "Kolaborasi": (
                    "Konsultasi fisioterapi untuk program rehabilitasi kardiak yang terstruktur;"
                    "Kolaborasi pemberian oksigen saat aktivitas jika diperlukan"
                ),
            },
        })

    # E2. Gangguan Pola Tidur (D.0055)
    tidur_kw = ["insomnia", "tidak bisa tidur", "sering terbangun", "tidur tidak nyenyak",
                "gelisah malam", "gangguan tidur", "nyeri mengganggu tidur"]
    if "D.0055" in force_codes_set or sum(1 for kw in tidur_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0055",
            "diagnosa_keperawatan": (
                "Gangguan Pola Tidur b.d Hambatan Lingkungan / Nyeri / Kecemasan "
                "d.d Kesulitan Memulai Tidur, Sering Terbangun, Tidur Tidak Restoratif."
            ),
            "luaran_keperawatan": "Status Tidur Membaik (L.05045)",
            "rencana_intervensi": {
                "Observasi": (
                    "Identifikasi penyebab gangguan tidur (nyeri, lingkungan, kecemasan, obat);"
                    "Monitor pola tidur (jam tidur, kualitas, lama tidur)"
                ),
                "Terapeutik": (
                    "Modifikasi lingkungan tidur (pencahayaan, suhu, kebisingan);"
                    "Jadwalkan prosedur keperawatan untuk tidak membangunkan pasien di waktu tidur;"
                    "Fasilitasi teknik relaksasi sebelum tidur (aromaterapi, musik relaksasi)"
                ),
                "Edukasi": (
                    "Anjurkan menghindari kafein 6 jam sebelum tidur;"
                    "Ajarkan teknik relaksasi progresif dan sleep hygiene"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian sedatif ringan jika sangat diperlukan (sesuai instruksi);"
                    "Kolaborasi manajemen nyeri adekuat sebagai prasyarat kualitas tidur"
                ),
            },
        })

    # E3. Keletihan (D.0057)
    keletihan_kw = ["kelelahan kronis", "fatigue", "tidak bertenaga", "exhausted",
                    "lemah berat", "tidak mampu konsentrasi karena lelah"]
    if "D.0057" in force_codes_set or sum(1 for kw in keletihan_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0057",
            "diagnosa_keperawatan": (
                "Keletihan b.d Kondisi Fisiologis / Anemia / Gangguan Tidur "
                "d.d Tidak Mampu Mempertahankan Aktivitas Rutin, Energi Tidak Pulih."
            ),
            "luaran_keperawatan": "Konservasi Energi Meningkat (L.05040)",
            "rencana_intervensi": {
                "Observasi": (
                    "Identifikasi gangguan fungsi tubuh yang menyebabkan keletihan;"
                    "Monitor tingkat keletihan dengan skala (NRS/VAS fatigue)"
                ),
                "Terapeutik": (
                    "Sediakan lingkungan yang nyaman dan rendah stimulus;"
                    "Lakukan latihan rentang gerak pasif/aktif sesuai toleransi"
                ),
                "Edukasi": (
                    "Ajarkan strategi hemat energi (pacing activities);"
                    "Anjurkan asupan nutrisi adekuat termasuk zat besi untuk anemia"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemeriksaan darah lengkap dan ferritin jika dicurigai anemia;"
                    "Kolaborasi suplemen zat besi atau EPO jika ada indikasi"
                ),
            },
        })

    # =========================================================================
    # ── F. PERSEPSI KOGNISI & NEUROLOGI ──────────────────────────────────────
    # =========================================================================

    # F1. Gangguan Komunikasi Verbal (D.0062)
    komunikasi_kw = ["afasia", "tidak bisa bicara", "bicara pelo", "disartria",
                     "sulit berkomunikasi", "gagap baru", "pasca stroke bicara"]
    if "D.0062" in force_codes_set or sum(1 for kw in komunikasi_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0062",
            "diagnosa_keperawatan": (
                "Gangguan Komunikasi Verbal b.d Penurunan Sirkulasi Serebral / Defek Anatomis / "
                "Hambatan Fisik d.d Tidak Mampu Berbicara, Afasia, Disartria."
            ),
            "luaran_keperawatan": "Komunikasi Verbal Meningkat (L.13118)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor kemampuan bicara, pendengaran, dan pemahaman pasien;"
                    "Identifikasi tipe afasia (ekspresif, reseptif, global)"
                ),
                "Terapeutik": (
                    "Dengarkan dengan penuh perhatian; beri waktu yang cukup untuk merespons;"
                    "Gunakan media alternatif (papan kata, gambar, kartu) untuk komunikasi;"
                    "Sederhanakan kalimat dan gunakan bahasa yang lambat dan jelas"
                ),
                "Edukasi": (
                    "Ajarkan keluarga cara berkomunikasi yang efektif dengan pasien afasia;"
                    "Motivasi pasien untuk tidak frustrasi dan terus mencoba berkomunikasi"
                ),
                "Kolaborasi": (
                    "Konsultasi speech therapist untuk program terapi wicara;"
                    "Koordinasi dengan neurologi untuk evaluasi prognosis komunikasi"
                ),
            },
        })

    # F2. Konfusi Akut (D.0063)
    konfusi_kw = ["bingung", "disorientasi", "konfusi", "delirium", "gelisah tanpa sebab",
                  "agitasi", "tidak kenal keluarga", "halusinasi", "bingung waktu tempat"]
    if "D.0063" in force_codes_set or sum(1 for kw in konfusi_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0063",
            "diagnosa_keperawatan": (
                "Konfusi Akut b.d Gangguan Metabolik / Hipoksia / Efek Obat Sedatif "
                "d.d Disorientasi, Agitasi, Halusinasi, Fluktuasi Kesadaran."
            ),
            "luaran_keperawatan": "Orientasi Kognitif Meningkat (L.09082)",
            "rencana_intervensi": {
                "Observasi": (
                    "Gunakan CAM (Confusion Assessment Method) untuk skrining delirium;"
                    "Monitor tingkat kesadaran (GCS) dan orientasi secara berkala;"
                    "Identifikasi faktor presipitasi: infeksi, hipoglikemia, hipoksia, obat"
                ),
                "Terapeutik": (
                    "Orientasikan pasien terhadap waktu, tempat, dan orang secara berulang;"
                    "Pertahankan lingkungan yang familiar dan tenang;"
                    "Pasang rel pengaman tempat tidur; pertimbangkan restrain lembut jika sangat agitasi"
                ),
                "Edukasi": (
                    "Edukasi keluarga tentang kondisi delirium dan cara membantu reorientasi;"
                    "Anjurkan keluarga membawa benda familiar dari rumah (foto, jam)"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemeriksaan penyebab (infeksi: DL, kultur; metabolik: GDS, elektrolit, AGD);"
                    "Kolaborasi pemberian haloperidol dosis rendah jika sangat agitasi (sesuai instruksi)"
                ),
            },
        })

    # =========================================================================
    # ── G. NYERI & KENYAMANAN ────────────────────────────────────────────────
    # =========================================================================

    # G1. Nyeri Akut (D.0077)
    nyeri_kw = ["nyeri", "sakit", "pain", "vas", "nrs", "visual analogue scale",
                "nyeri dada", "nyeri perut", "nyeri kepala", "nyeri luka",
                "nyeri post op", "nyeri saat napas"]
    if "D.0077" in force_codes_set or sum(1 for kw in nyeri_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0077",
            "diagnosa_keperawatan": (
                "Nyeri Akut b.d Agen Pencedera Fisiologis / Biologis / Kimiawi "
                "d.d Mengeluh Nyeri, Skala Nyeri ≥ 4, Wajah Meringis, Sulit Tidur."
            ),
            "luaran_keperawatan": "Tingkat Nyeri Menurun (L.08066)",
            "rencana_intervensi": {
                "Observasi": (
                    "Identifikasi lokasi, karakteristik, durasi, frekuensi, kualitas nyeri;"
                    "Kaji skala nyeri menggunakan NRS/VAS setiap 4 jam dan setelah intervensi;"
                    "Identifikasi faktor yang memperberat dan meringankan nyeri"
                ),
                "Terapeutik": (
                    "Berikan teknik non-farmakologis: kompres hangat/dingin, distraksi, relaksasi napas dalam;"
                    "Posisikan pasien dengan nyaman untuk mengurangi tekanan pada area nyeri;"
                    "Kontrol lingkungan yang memperberat nyeri (suhu, cahaya, kebisingan)"
                ),
                "Edukasi": (
                    "Jelaskan penyebab dan pemicu nyeri kepada pasien;"
                    "Ajarkan teknik manajemen nyeri non-farmakologis secara mandiri"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian analgesik (paracetamol, NSAID, opioid) sesuai tangga nyeri WHO;"
                    "Evaluasi efektivitas analgesik 30–60 menit setelah pemberian"
                ),
            },
        })

    # G2. Nyeri Kronis (D.0078)
    nyeri_kronis_kw = ["nyeri kronis", "nyeri berulang", "nyeri >3 bulan", "nyeri persisten",
                       "neuropati", "nyeri neuropatik", "nyeri sudah lama"]
    if "D.0078" in force_codes_set or sum(1 for kw in nyeri_kronis_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0078",
            "diagnosa_keperawatan": (
                "Nyeri Kronis b.d Kondisi Muskuloskeletal Kronis / Kerusakan Saraf / "
                "Iskemia d.d Nyeri Persisten > 3 Bulan, Depresi, Pola Tidur Terganggu."
            ),
            "luaran_keperawatan": "Tingkat Nyeri Menurun (L.08066)",
            "rencana_intervensi": {
                "Observasi": (
                    "Kaji skala nyeri kronis (Brief Pain Inventory);"
                    "Monitor efek samping analgesik jangka panjang (tukak lambung, depresi napas)"
                ),
                "Terapeutik": (
                    "Terapkan manajemen nyeri multimodal;"
                    "Fasilitasi konsultasi psikologi untuk manajemen nyeri kronis"
                ),
                "Edukasi": (
                    "Edukasi ekspektasi yang realistis: tujuan bukan menghilangkan nyeri tapi mengelola;"
                    "Ajarkan teknik mindfulness, relaksasi, dan hypnoanalgesia"
                ),
                "Kolaborasi": (
                    "Konsultasi ke klinik nyeri multidisiplin;"
                    "Kolaborasi analgesik adjuvan (gabapentin, antidepresan) sesuai instruksi"
                ),
            },
        })

    # =========================================================================
    # ── H. INTEGRITAS EGO / PSIKOLOGIS ───────────────────────────────────────
    # =========================================================================

    # H1. Ansietas (D.0080)
    ansietas_kw = ["cemas", "khawatir", "takut", "ansietas", "gelisah", "panik",
                   "jantung berdebar karena takut", "tidak tenang", "anxious"]
    if "D.0080" in force_codes_set or sum(1 for kw in ansietas_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0080",
            "diagnosa_keperawatan": (
                "Ansietas b.d Krisis Situasional / Ancaman Terhadap Konsep Diri / "
                "Kondisi Penyakit d.d Merasa Cemas, Gelisah, Takut, Palpitasi."
            ),
            "luaran_keperawatan": "Tingkat Ansietas Menurun (L.09093)",
            "rencana_intervensi": {
                "Observasi": (
                    "Identifikasi tingkat ansietas dan faktor pencetusnya;"
                    "Monitor tanda fisiologis ansietas: takikardia, tremor, berkeringat"
                ),
                "Terapeutik": (
                    "Ciptakan suasana terapeutik dan tunjukkan empati;"
                    "Temani pasien untuk mengurangi rasa takut;"
                    "Ajarkan dan fasilitasi teknik relaksasi (napas dalam, progressive muscle relaxation)"
                ),
                "Edukasi": (
                    "Jelaskan kondisi medis, prosedur, dan rencana tindakan secara jelas;"
                    "Anjurkan keluarga untuk memberikan dukungan emosional"
                ),
                "Kolaborasi": (
                    "Konsultasi psikologi atau psikiatri klinis jika ansietas berat;"
                    "Kolaborasi pemberian anxiolytic (lorazepam, alprazolam dosis rendah) jika dibutuhkan"
                ),
            },
        })

    # =========================================================================
    # ── I. KEAMANAN & PROTEKSI ───────────────────────────────────────────────
    # =========================================================================

    # I1. Gangguan Integritas Kulit/Jaringan (D.0129)
    kulit_kw = ["luka", "dekubitus", "kemerahan kulit", "gesekan", "tirah baring",
                "bedrest lama", "luka tekan", "pressure injury", "pressure ulcer",
                "luka bakar", "lecet", "bulla", "vesikel", "ulkus"]
    if "D.0129" in force_codes_set or sum(1 for kw in kulit_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0129",
            "diagnosa_keperawatan": (
                "Gangguan Integritas Kulit/Jaringan b.d Penurunan Mobilitas / Tirah Baring Lama / "
                "Perubahan Pigmentasi d.d Kerusakan Lapisan Kulit, Perdarahan, Kemerahan."
            ),
            "luaran_keperawatan": "Integritas Kulit dan Jaringan Meningkat (L.14125)",
            "rencana_intervensi": {
                "Observasi": (
                    "Identifikasi risiko dekubitus dengan Braden Scale setiap 24 jam;"
                    "Monitor karakteristik luka (ukuran, kedalaman, warna dasar, eksudat);"
                    "Monitor tanda infeksi pada luka"
                ),
                "Terapeutik": (
                    "Ubah posisi pasien setiap 2 jam (alih baring terjadwal);"
                    "Gunakan kasur dekubitus atau overlay foam pada pasien risiko tinggi;"
                    "Lakukan perawatan luka dengan teknik aseptik menggunakan dressing modern"
                ),
                "Edukasi": (
                    "Anjurkan meningkatkan asupan nutrisi TKTP dan vitamin C/zinc;"
                    "Ajarkan keluarga teknik alih baring mandiri"
                ),
                "Kolaborasi": (
                    "Konsultasi wound care nurse / CWOCN untuk manajemen luka kompleks;"
                    "Kolaborasi debridement bedah atau VAC therapy jika ada indikasi"
                ),
            },
        })

    # I2. Risiko Infeksi (D.0142)
    infeksi_kw = ["iv line", "kateter", "operasi", "leukosit tinggi", "demam",
                  "suhu >38", "suhu >38.5", "pneumonia", "invasif", "wbc tinggi",
                  "infeksi", "sepsis", "iak", "isk", "hap", "vap", "clabsi"]
    if "D.0142" in force_codes_set or sum(1 for kw in infeksi_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0142",
            "diagnosa_keperawatan": (
                "Risiko Infeksi d.d Efek Prosedur Invasif / Imunosupresi / "
                "Kerusakan Integritas Kulit."
            ),
            "luaran_keperawatan": "Tingkat Infeksi Menurun (L.14137)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor tanda dan gejala infeksi (kalor, dolor, rubor, tumor, pus);"
                    "Monitor hasil laboratorium: leukosit, CRP, PCT, kultur;"
                    "Monitor tanda SIRS: suhu, HR, RR, WBC"
                ),
                "Terapeutik": (
                    "Terapkan 5 momen kebersihan tangan WHO secara ketat;"
                    "Batasi jumlah pengunjung;"
                    "Pertahankan teknik aseptik ketat pada semua prosedur invasif;"
                    "Lakukan bundle care pencegahan HAIs (CLABSI, CAUTI, VAP, SSI)"
                ),
                "Edukasi": (
                    "Ajarkan pasien dan keluarga cara cuci tangan yang benar (6 langkah);"
                    "Jelaskan tanda-tanda infeksi yang harus segera dilaporkan"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian antibiotik profilaksis atau definitif sesuai kultur;"
                    "Kolaborasi dengan IPCN (Infection Prevention Control Nurse) untuk audit bundle"
                ),
            },
        })

    # I3. Risiko Jatuh (D.0136)
    jatuh_kw = ["risiko jatuh", "morse fall", "lansia", "lemah", "vertigo",
                "pusing berdiri", "riwayat jatuh", "sedatif", "diuretik malam",
                "balance terganggu", "gaya berjalan tidak stabil"]
    if "D.0136" in force_codes_set or sum(1 for kw in jatuh_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0136",
            "diagnosa_keperawatan": (
                "Risiko Jatuh d.d Usia > 65 Tahun / Penurunan Keseimbangan / "
                "Penggunaan Obat Sedatif/Diuretik."
            ),
            "luaran_keperawatan": "Tingkat Jatuh Menurun (L.14138)",
            "rencana_intervensi": {
                "Observasi": (
                    "Lakukan skrining risiko jatuh dengan Morse Fall Scale setiap shift;"
                    "Identifikasi faktor risiko jatuh: obat, lingkungan, kognitif"
                ),
                "Terapeutik": (
                    "Pasang gelang risiko jatuh dan tanda di pintu kamar;"
                    "Pasang rel pengaman tempat tidur dan kunci roda tempat tidur;"
                    "Pastikan pencahayaan kamar dan kamar mandi adequate;"
                    "Bantu mobilisasi pasien risiko tinggi setiap kali bergerak"
                ),
                "Edukasi": (
                    "Ajarkan pasien menggunakan bel panggil sebelum turun dari tempat tidur;"
                    "Edukasi keluarga untuk selalu mendampingi pasien risiko tinggi"
                ),
                "Kolaborasi": (
                    "Konsultasi fisioterapi untuk latihan keseimbangan dan penguatan otot;"
                    "Review dan sesuaikan obat-obatan yang berisiko jatuh (anticholinergic, benzodiazepin)"
                ),
            },
        })

    # I4. Risiko Komplikasi Pascabedah (D.0131)
    pascabedah_kw = ["post operasi", "pasca operasi", "post op", "pasca cabg",
                     "pasca pci", "pasca kateterisasi", "pasca bypass", "jahitan operasi",
                     "sternotomi", "pasca anestesi", "pemulihan pasca bedah"]
    if "D.0131" in force_codes_set or sum(1 for kw in pascabedah_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0131",
            "diagnosa_keperawatan": (
                "Risiko Komplikasi Pascabedah d.d Prosedur Pembedahan / Anestesi / "
                "Imobilisasi Pascaoperasi."
            ),
            "luaran_keperawatan": "Pemulihan Pascabedah Meningkat (L.14129)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor tanda vital dan tanda perdarahan dari luka operasi setiap jam;"
                    "Monitor produksi drain (warna, volume) secara berkala;"
                    "Monitor fungsi pernafasan dan saturasi oksigen pasca anestesi"
                ),
                "Terapeutik": (
                    "Pertahankan posisi nyaman pasca anestesi (miring lateral jika belum sadar penuh);"
                    "Lakukan perawatan luka operasi dengan teknik steril;"
                    "Mulai mobilisasi dini bertahap sesuai protokol bedah"
                ),
                "Edukasi": (
                    "Ajarkan latihan napas dalam dan batuk efektif untuk mencegah atelektasis;"
                    "Edukasi tanda komplikasi (perdarahan, infeksi, DVT) yang harus dilaporkan"
                ),
                "Kolaborasi": (
                    "Kolaborasi manajemen nyeri multimodal pascaoperasi;"
                    "Kolaborasi fisioterapi dada dan mobilisasi dini;"
                    "Kolaborasi profilaksis tromboembolisme (LMWH, stoking kompresi)"
                ),
            },
        })

    # I5. Risiko Syok (D.0109)
    syok_kw = ["syok", "hipotensi berat", "td sistolik <90", "td <90/60", "map rendah",
               "map <65", "tachycardia kompensasi", "oliguria syok", "mottling",
               "nadi filiform", "capillary refill >3", "lactic acidosis"]
    if "D.0109" in force_codes_set or sum(1 for kw in syok_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0109",
            "diagnosa_keperawatan": (
                "Risiko Syok d.d Hipotensi / Kekurangan Volume Cairan / Sepsis / "
                "Kardiogenik."
            ),
            "luaran_keperawatan": "Status Kardiopulmonal Membaik (L.02016)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor tekanan darah, nadi, MAP, CVP setiap 15–30 menit;"
                    "Monitor tanda perfusi jaringan: CRT, mottling, produksi urin, laktat;"
                    "Monitor EKG kontinu untuk deteksi aritmia"
                ),
                "Terapeutik": (
                    "AKTIFKAN kode blue/rapid response jika ada tanda syok berat;"
                    "Berikan cairan resusitasi (RL/NaCl 0.9%) 500 mL bolus IV cepat;"
                    "Berikan posisi Trendelenburg modifikasi (kecuali syok kardiogenik);"
                    "Pertahankan akses IV 2 jalur dengan jarum besar (≥ 18 G)"
                ),
                "Edukasi": (
                    "Jelaskan kondisi kritis kepada keluarga dengan bahasa yang mudah dipahami;"
                    "Inform consent tindakan invasif yang mungkin diperlukan"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemberian vasopressor (norepinefrin, vasopressin) sesuai instruksi;"
                    "Kolaborasi pemeriksaan laktat, AGD, DL, kultur darah segera;"
                    "Koordinasi transfer ke ICU/ICCU jika syok tidak teratasi"
                ),
            },
        })

    # =========================================================================
    # ── J. VENTILASI MEKANIK & PENYAPIHAN ────────────────────────────────────
    # =========================================================================

    # J1. Gangguan Ventilasi Spontan (D.0004)
    vent_spontan_kw = ["ventilasi mekanik", "tidak bisa napas spontan", "apnea",
                       "apnoe", "gagal napas", "respiratory failure", "fio2 tinggi",
                       "peep", "mode vc", "mode pc", "ards", "kelelahan otot napas",
                       "tidak bisa lepas ventilator", "tergantung ventilator",
                       "drive napas hilang", "co2 retensi berat"]
    if "D.0004" in force_codes_set or sum(1 for kw in vent_spontan_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0004",
            "diagnosa_keperawatan": (
                "Gangguan Ventilasi Spontan b.d Kelelahan Otot Pernapasan / Gangguan Neurologis / "
                "Gagal Napas Tipe II d.d Dispnea Berat, Penggunaan Otot Bantu Napas Masif, "
                "PCO2 Meningkat, Penurunan SpO2."
            ),
            "luaran_keperawatan": "Ventilasi Spontan Meningkat (L.01007)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor mode ventilator, setting FiO2, PEEP, tidal volume, dan compliance paru setiap jam;"
                    "Monitor trigger effort pasien dan sinkronisasi dengan ventilator;"
                    "Monitor AGD (pH, PaCO2, PaO2) secara serial setiap 4–6 jam atau setelah perubahan setting;"
                    "Pantau tanda kelelahan otot napas: paradoxical breathing, penggunaan SCM berlebih"
                ),
                "Terapeutik": (
                    "Pertahankan kepatenan ETT/trakeostomi: cek cuff pressure setiap shift (20–30 cmH2O);"
                    "Lakukan suction ETT dengan teknik steril sesuai indikasi (tidak rutin);"
                    "Posisikan HOB 30–45° untuk mencegah VAP dan optimasi ventilasi;"
                    "Berikan sedasi dan analgesia adekuat sesuai protokol (RASS target -1 s.d 0);"
                    "Cegah komplikasi ventilasi: barotrauma, VAP, dan disuse atrofi otot napas"
                ),
                "Edukasi": (
                    "Edukasi keluarga tentang kondisi ketergantungan ventilator dan prognosis;"
                    "Jelaskan kepada keluarga prosedur suction, perawatan ETT, dan tujuannya"
                ),
                "Kolaborasi": (
                    "Kolaborasi optimasi setting ventilator (lung protective strategy: TV 6 mL/kgBBi, Pplat < 30);"
                    "Kolaborasi pemberian neuromuscular blockade (cisatracurium) jika ARDS berat;"
                    "Kolaborasi prone positioning jika P/F ratio < 150 dan tidak ada kontraindikasi;"
                    "Kolaborasi fisioterapi dada dan mobilisasi dini sambil terpasang ventilator"
                ),
            },
        })

    # J2. Gangguan Penyapihan Ventilator (D.0002)
    weaning_kw = ["penyapihan", "weaning", "sapih ventilator", "trial sbт", "sbt",
                  "spontaneous breathing trial", "gagal weaning", "ekstubasi",
                  "rencana ekstubasi", "psmv", "psv mode", "cpap mode",
                  "rapid shallow breathing", "rsbi", "tidak toleran sbt",
                  "distress pasca ekstubasi", "reintubasi", "post extubation stridor"]
    if "D.0002" in force_codes_set or sum(1 for kw in weaning_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0002",
            "diagnosa_keperawatan": (
                "Gangguan Penyapihan Ventilator b.d Ketidakcukupan Kekuatan Otot Pernapasan / "
                "Obstruksi Jalan Napas / Ketergantungan Ventilator > 4 Hari "
                "d.d Gagal Spontaneous Breathing Trial, RSBI > 105, Agitasi saat Weaning."
            ),
            "luaran_keperawatan": "Penyapihan Ventilator Meningkat (L.01002)",
            "rencana_intervensi": {
                "Observasi": (
                    "Kaji kesiapan weaning harian: pasien sadar, kooperatif, refleks batuk adekuat, "
                    "SpO2 ≥ 94% dengan FiO2 ≤ 0.4, PEEP ≤ 5, hemodinamik stabil;"
                    "Hitung RSBI (Rapid Shallow Breathing Index = RR/TV) sebelum SBT;"
                    "Monitor tanda gagal weaning: RR > 35, SpO2 < 90%, HR > 140, agitasi berat, diaforesis"
                ),
                "Terapeutik": (
                    "Laksanakan SBT dengan T-piece atau PSV 5–8 cmH2O selama 30–120 menit;"
                    "Hentikan SBT segera jika ada tanda distres; kembalikan setting semula;"
                    "Optimalkan faktor yang menghambat weaning: atasi nyeri, ansietas, sekresi, overload cairan;"
                    "Latih otot napas dengan IMT (Inspiratory Muscle Training) jika ventilasi > 7 hari;"
                    "Siapkan perlengkapan ekstubasi: suction, BVM, laringoskop, ETT cadangan"
                ),
                "Edukasi": (
                    "Jelaskan proses penyapihan kepada pasien yang sadar dengan bahasa sederhana;"
                    "Ajarkan pasien teknik napas diafragma dan batuk efektif sebelum ekstubasi;"
                    "Edukasi keluarga tentang timeline dan kemungkinan reintubasi"
                ),
                "Kolaborasi": (
                    "Kolaborasi protokol weaning terstruktur bersama DPJP dan fisioterapis paru;"
                    "Kolaborasi pemberian methylprednisolon profilaksis 12 jam sebelum ekstubasi jika ventilasi > 7 hari;"
                    "Kolaborasi pemasangan trakeostomi jika weaning failure berulang (> 3 kali SBT gagal);"
                    "Kolaborasi monitoring pasca ekstubasi ketat selama 24 jam pertama"
                ),
            },
        })

    # =========================================================================
    # ── K. SIRKULASI SPONTAN & CARDIAC ARREST ────────────────────────────────
    # =========================================================================

    # K1. Gangguan Sirkulasi Spontan (D.0007) — Henti Jantung / Post-ROSC
    rosc_kw = ["henti jantung", "cardiac arrest", "vf", "ventrikel fibrilasi",
               "pulseless vt", "vt tanpa nadi", "asistol", "pea",
               "rosc", "return of spontaneous circulation", "post rosc",
               "rjp", "resusitasi", "defibrilasi", "defibrillasi", "aed",
               "tidak ada nadi", "henti nafas dan jantung", "dnr"]
    if "D.0007" in force_codes_set or sum(1 for kw in rosc_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0007",
            "diagnosa_keperawatan": (
                "Gangguan Sirkulasi Spontan b.d Fibrilasi Ventrikel / Asistol / PEA "
                "d.d Tidak Ada Denyut Nadi, Apnea, Hilang Kesadaran."
            ),
            "luaran_keperawatan": "Sirkulasi Spontan Meningkat (L.02015)",
            "rencana_intervensi": {
                "Observasi": (
                    "VERIFIKASI henti jantung: cek responsivitas, napas, dan nadi karotis (< 10 detik);"
                    "Monitor irama EKG segera setelah ROSC: identifikasi penyebab (4H 4T);"
                    "Monitor hemodinamik pasca ROSC: TD, MAP (target ≥ 65 mmHg), SpO2, ETCO2;"
                    "Monitor tanda kerusakan otak pasca anoksik: kesadaran, pupil, GCS"
                ),
                "Terapeutik": (
                    "AKTIFKAN code blue dan mulai BLS (kompresi dada 100–120 kali/menit, kedalaman 5–6 cm);"
                    "Berikan oksigen 100% dan lakukan ventilasi 30:2 atau 10 napas/menit jika terintubasi;"
                    "Lakukan defibrilasi segera untuk VF/pVT dengan energi 200 J (bifasik);"
                    "Pasang akses IV/IO; pertahankan kompresi berkualitas tinggi selama resusitasi;"
                    "Pasca ROSC: pertahankan SpO2 94–98%, PaCO2 35–45 mmHg, hindari hiperoksia"
                ),
                "Edukasi": (
                    "Edukasi keluarga secara jelas tentang kondisi kritis dan prognosis pasca henti jantung;"
                    "Diskusikan arahan perawatan lanjutan (DNR/DNI) jika kondisi tidak membaik"
                ),
                "Kolaborasi": (
                    "Kolaborasi ACLS: epinefrin 1 mg IV/IO setiap 3–5 menit; amiodaron 300 mg IV untuk VF refrakter;"
                    "Kolaborasi TTM (Targeted Temperature Management) 32–36°C jika pasien koma pasca ROSC;"
                    "Kolaborasi coronary angiography/PCI segera jika dicurigai ACS sebagai penyebab;"
                    "Koordinasi transfer ke ICU/ICCU untuk monitoring pasca resusitasi intensif"
                ),
            },
        })

    # K2. Risiko Gangguan Sirkulasi Spontan (D.0010)
    risiko_rosc_kw = ["risiko henti jantung", "aritmia mengancam", "vt stabil",
                      "blok av derajat 3", "blok av total", "av block komplit",
                      "qt memanjang", "lqts", "torsades", "torsade de pointes",
                      "bradikardi berat", "hr < 40", "bradikardia simtomatik",
                      "sinkop berulang", "pre-arrest", "deteriorasi klinis cepat"]
    if "D.0010" in force_codes_set or sum(1 for kw in risiko_rosc_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0010",
            "diagnosa_keperawatan": (
                "Risiko Gangguan Sirkulasi Spontan d.d Aritmia Mengancam Jiwa / "
                "Blok AV Total / QTc Memanjang / Deteriorasi Hemodinamik."
            ),
            "luaran_keperawatan": "Sirkulasi Spontan Meningkat (L.02015)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor EKG kontinu 24 jam; identifikasi perubahan irama yang mengancam;"
                    "Ukur dan dokumentasikan interval QTc setiap pergantian shift;"
                    "Monitor tanda pre-arrest: hipotensi progresif, penurunan kesadaran, nadi lemah"
                ),
                "Terapeutik": (
                    "Pastikan defibrillator/AED dan trolley emergensi siap di samping tempat tidur;"
                    "Pasang akses IV yang berfungsi baik;"
                    "Batasi obat-obatan yang memperpanjang QT (konsultasi farmasi);"
                    "Pertahankan elektrolit dalam batas normal: K+ 4–5 mEq/L, Mg2+ 2–2.5 mEq/dL"
                ),
                "Edukasi": (
                    "Ajarkan pasien segera menekan bel jika merasakan palpitasi, nyeri dada, atau hampir pingsan;"
                    "Edukasi keluarga mengenali tanda pra-arrest dan cara memanggil bantuan"
                ),
                "Kolaborasi": (
                    "Kolaborasi kardiologi untuk pertimbangan pemasangan pacu jantung sementara (TVP);"
                    "Kolaborasi koreksi elektrolit agresif (KCl, MgSO4 IV) jika ada hipokalemia/hipomagnesemia;"
                    "Kolaborasi pemberian antiaritmia (amiodaron, atropin, adenosin) sesuai jenis aritmia"
                ),
            },
        })

    # K3. Risiko Penurunan Curah Jantung (D.0011)
    risiko_cj_kw = ["risiko penurunan curah jantung", "ef borderline", "ef 40",
                    "disfungsi diastolik", "hipertrofi ventrikel", "hipertensi tidak terkontrol",
                    "stenosis aorta berat", "regurgitasi mitral", "mr berat", "as berat",
                    "kardiomiopati", "amyloidosis jantung", "miokarditis"]
    if "D.0011" in force_codes_set or sum(1 for kw in risiko_cj_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0011",
            "diagnosa_keperawatan": (
                "Risiko Penurunan Curah Jantung d.d Disfungsi Ventrikel / Valvular Disease Berat / "
                "Kardiomiopati."
            ),
            "luaran_keperawatan": "Curah Jantung Meningkat (L.02008)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor tanda vital dan tanda-tanda penurunan curah jantung setiap shift;"
                    "Monitor output urin setiap jam (target > 0.5 mL/kgBB/jam);"
                    "Monitor distensi vena jugular, auskultasi bunyi jantung S3/S4"
                ),
                "Terapeutik": (
                    "Batasi aktivitas fisik sesuai kapasitas fungsional (NYHA class);"
                    "Pantau keseimbangan cairan ketat;"
                    "Optimalkan posisi semi-fowler"
                ),
                "Edukasi": (
                    "Edukasi pembatasan natrium dan cairan;"
                    "Ajarkan pemantauan berat badan harian (lapor jika naik > 1 kg/hari)"
                ),
                "Kolaborasi": (
                    "Kolaborasi ekokardiografi serial untuk evaluasi fungsi ventrikel;"
                    "Kolaborasi optimasi terapi GDMT (ACEi/ARB/ARNI, beta-blocker, MRA, SGLT2i);"
                    "Kolaborasi heart team untuk pertimbangan tindakan kateterisasi/bedah valvular"
                ),
            },
        })

    # K4. Gangguan Perfusi Serebral (D.0014) — Aktual
    serebral_aktual_kw = ["stroke iskemik", "stroke hemoragik", "ich", "sah",
                          "penurunan gcs", "gcs turun", "hemiplegia", "hemiparesis aktual",
                          "afasia aktual", "disartria berat", "hemianopia",
                          "ptosis mendadak", "deviasi mata", "babinski positif",
                          "perdarahan intra serebral", "infark serebri"]
    if "D.0014" in force_codes_set or sum(1 for kw in serebral_aktual_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0014",
            "diagnosa_keperawatan": (
                "Gangguan Perfusi Serebral b.d Sumbatan Arteri Serebral / Perdarahan Intrakranial "
                "d.d Penurunan GCS, Hemiparesis, Afasia, Deviasi Mata."
            ),
            "luaran_keperawatan": "Perfusi Serebral Meningkat (L.02014)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor GCS, kekuatan motorik, dan defisit neurologis setiap 1–2 jam;"
                    "Monitor tekanan darah MAP (target sesuai jenis stroke: iskemik vs hemoragik);"
                    "Monitor tanda PTIK: nyeri kepala hebat, mual-muntah proyektil, Cushing reflex"
                ),
                "Terapeutik": (
                    "Posisikan kepala HOB 30°, leher netral, hindari rotasi leher;"
                    "Cegah stimulasi berlebihan: batasi prosedur nyeri, batasi pengunjung;"
                    "Pertahankan normoglikemia (GDS 140–180 mg/dL) dan normotermia (suhu < 37.5°C);"
                    "Pasang kateter urin untuk monitoring output ketat"
                ),
                "Edukasi": (
                    "Edukasi keluarga tentang F.A.S.T dan perjalanan penyakit stroke;"
                    "Jelaskan tujuan monitoring neurologi intensif kepada keluarga"
                ),
                "Kolaborasi": (
                    "Kolaborasi CT scan/MRI otak segera (< 25 menit arrival);"
                    "Kolaborasi rtPA 0.9 mg/kgBB IV jika stroke iskemik < 4.5 jam onset dan eligible;"
                    "Kolaborasi neurologi/bedah saraf untuk penatalaksanaan stroke hemoragik;"
                    "Kolaborasi mannitol/hipertonik saline jika ada tanda herniasi otak"
                ),
            },
        })

    # K5. Risiko Perfusi Pulmonal Tidak Efektif — Emboli Paru (D.0013/D.0018)
    pe_kw = ["emboli paru", "pulmonary embolism", "pe", "tromboemboli", "dvt",
             "deep vein thrombosis", "nyeri dada pleuritik", "hemoptisis",
             "tachycardia tanpa sebab", "d-dimer tinggi", "troponin naik pe",
             "strain ventrikel kanan", "s1q3t3", "hipoksia mendadak",
             "wells score", "right heart strain"]
    if "D.0013" in force_codes_set or sum(1 for kw in pe_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0013",
            "diagnosa_keperawatan": (
                "Risiko Perfusi Pulmonal Tidak Efektif d.d Tromboemboli Vena / Deep Vein Thrombosis / "
                "Imobilisasi Lama / Hiperkoagulabilitas."
            ),
            "luaran_keperawatan": "Perfusi Pulmonal Meningkat (L.02013)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor gejala PE: nyeri dada pleuritik, dispnea mendadak, hemoptisis, takikardia;"
                    "Monitor SpO2, RR, dan tanda hemodinamik setiap jam;"
                    "Monitor nilai D-dimer, troponin, BNP, dan hasil CTPA"
                ),
                "Terapeutik": (
                    "Berikan oksigen untuk mempertahankan SpO2 ≥ 95%;"
                    "Pasang IV line dan pertahankan akses yang adekuat;"
                    "Lakukan leg exercise dan mobilisasi dini untuk pasien bedrest;"
                    "Pasang stoking kompresi elastis gradasi (GCS) pada seluruh pasien bedrest"
                ),
                "Edukasi": (
                    "Edukasi pasien untuk melaporkan segera nyeri betis, bengkak, atau sesak mendadak;"
                    "Ajarkan pentingnya gerakan aktif tungkai selama bedrest"
                ),
                "Kolaborasi": (
                    "Kolaborasi antikoagulasi (heparin UFH bolus + infus, atau LMWH enoxaparin) segera;"
                    "Kolaborasi CTPA (CT Pulmonary Angiography) untuk konfirmasi diagnosis;"
                    "Kolaborasi trombolisis sistemik atau embolektomi jika PE masif dengan syok;"
                    "Kolaborasi pemasangan vena cava filter jika antikoagulan kontraindikasi"
                ),
            },
        })

    # K6. Gangguan Status Kardiopulmonal (D.0016)
    kardiopulmonal_kw = ["gagal napas dan jantung bersamaan", "acute cor pulmonale",
                         "right heart failure akut", "pulmonary hypertension krisis",
                         "hipertensi pulmonal berat", "phtn", "svri tinggi",
                         "pvri tinggi", "right ventricular failure", "rvf",
                         "hepatojugular reflux", "pericardial effusion", "tamponade"]
    if "D.0016" in force_codes_set or sum(1 for kw in kardiopulmonal_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0016",
            "diagnosa_keperawatan": (
                "Gangguan Status Kardiopulmonal b.d Gagal Jantung Kanan Akut / Hipertensi Pulmonal / "
                "Tamponade Jantung d.d Tekanan CVP Meningkat, Distensi Vena Jugular, Muffled Heart Sound."
            ),
            "luaran_keperawatan": "Status Kardiopulmonal Membaik (L.02016)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor CVP/RAP secara kontinu (target ≤ 8 mmHg);"
                    "Monitor tanda tamponade: paradoxical pulse (pulsus paradoksus > 10 mmHg), Beck's triad;"
                    "Monitor output urin setiap jam; monitor SpO2, MAP, dan tanda kongesti vena sistemik"
                ),
                "Terapeutik": (
                    "Posisikan semi-fowler; hindari posisi Trendelenburg pada gagal jantung kanan;"
                    "Batasi cairan IV secara ketat; hindari fluid challenge berlebihan pada RV failure;"
                    "Pertahankan afterload RV rendah: hindari hipoksia dan hiperkapnia"
                ),
                "Edukasi": (
                    "Edukasi keluarga tentang kondisi kompleks dan perlunya monitoring intensif;"
                    "Jelaskan tujuan setiap prosedur invasif monitoring hemodinamik"
                ),
                "Kolaborasi": (
                    "Kolaborasi ekokardiografi segera untuk evaluasi fungsi RV dan pericard effusion;"
                    "Kolaborasi perikardiosentesis segera jika tamponade jantung (emergency);"
                    "Kolaborasi inhaled NO atau sildenafil jika hipertensi pulmonal krisis;"
                    "Kolaborasi dukungan mekanis sirkulasi (IABP, ECMO VA) jika refrakter"
                ),
            },
        })

    # =========================================================================
    # ── L. KETIDAKSEIMBANGAN ELEKTROLIT & METABOLIK ──────────────────────────
    # =========================================================================

    # L1. Hipokalemia / Hiperkalemia
    kalium_kw = ["hipokalemia", "hiperkalemia", "kalium rendah", "kalium tinggi",
                 "k+ rendah", "k+ tinggi", "k <3", "k >5.5", "k <3.5", "k >6",
                 "kelemahan otot", "kram otot", "aritmia elektrolit",
                 "gelombang u", "gelombang t tinggi", "peaked t wave",
                 "serum kalium", "hipokalemi", "hiperkalemi"]
    if "D.0037" in force_codes_set or sum(1 for kw in kalium_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0037",
            "diagnosa_keperawatan": (
                "Risiko Ketidakseimbangan Elektrolit (Kalium) d.d Diuretik Loop / Muntah / Diare / "
                "Gagal Ginjal / Terapi Digitalis — Hipokalemia/Hiperkalemia."
            ),
            "luaran_keperawatan": "Keseimbangan Elektrolit Membaik (L.03021)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor kadar kalium serum setiap 6–8 jam atau setelah koreksi;"
                    "Monitor EKG untuk tanda kelainan elektrolit: gelombang U (hipoK), peaked T (hiperK);"
                    "Monitor kekuatan otot, refleks, dan keluhan kelemahan/kram"
                ),
                "Terapeutik": (
                    "HIPOKALEMIA: koreksi KCl IV via syringe pump maks 20 mEq/jam melalui central line;"
                    "HIPERKALEMIA: berikan kalsium glukonat 10% IV untuk stabilisasi membran jantung;"
                    "Berikan insulin + dextrose 40% untuk redistribusi K+ intrasel (hiperK);"
                    "Berikan kayexalate atau furosemid untuk ekskresi K+ (hiperK)"
                ),
                "Edukasi": (
                    "Anjurkan konsumsi makanan tinggi kalium jika hipokalemia ringan (pisang, alpukat, kentang);"
                    "Edukasi pembatasan makanan tinggi kalium jika hiperkalemia (gagal ginjal)"
                ),
                "Kolaborasi": (
                    "Kolaborasi pemeriksaan elektrolit lengkap (Na, K, Cl, Mg, Ca, PO4) secara serial;"
                    "Kolaborasi hemodialisis segera jika hiperkalemia berat (K > 6.5) dengan perubahan EKG;"
                    "Kolaborasi review dan penyesuaian dosis diuretik bersama DPJP"
                ),
            },
        })

    # L2. Hiponatremia / Hipernatremia
    natrium_kw = ["hiponatremia", "hipernatremia", "natrium rendah", "natrium tinggi",
                  "na rendah", "na tinggi", "na <130", "na >150", "siadh",
                  "dilusi natrium", "sodium rendah", "hiponatrem", "hipernatrem",
                  "penurunan kesadaran elektrolit", "kejang elektrolit"]
    if "D.0037" in force_codes_set or sum(1 for kw in natrium_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0037",
            "diagnosa_keperawatan": (
                "Risiko Ketidakseimbangan Elektrolit (Natrium) d.d SIADH / Polidipsia / "
                "Diabetes Insipidus / Pemberian Cairan Hipotonik Berlebih — Hiponatremia/Hipernatremia."
            ),
            "luaran_keperawatan": "Keseimbangan Elektrolit Membaik (L.03021)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor kadar natrium serum minimal setiap 6 jam selama koreksi aktif;"
                    "Monitor tanda hiponatremia berat: penurunan kesadaran, kejang, letargi;"
                    "Monitor osmolalitas serum dan urin; monitor turgor, mukosa, dan tanda edema"
                ),
                "Terapeutik": (
                    "HIPONATREMIA BERAT (Na < 125 + gejala): berikan NaCl 3% hipertonik secara HATI-HATI;"
                    "Koreksi natrium maksimal 8–10 mEq/L per 24 jam (hindari osmotic demyelination);"
                    "HIPERNATREMIA: koreksi dengan cairan hipotonik (D5W, NaCl 0.45%) perlahan;"
                    "Restriksi cairan jika SIADH"
                ),
                "Edukasi": (
                    "Edukasi pembatasan air bebas pada pasien SIADH;"
                    "Anjurkan keluarga melaporkan tanda-tanda perubahan perilaku atau penurunan kesadaran"
                ),
                "Kolaborasi": (
                    "Kolaborasi elektrolit panel serial dan osmolalitas serum + urin;"
                    "Kolaborasi endokrinologi/nefrologi jika SIADH atau DI;"
                    "Kolaborasi pemberian tolvaptan (vaptans) pada SIADH euvolemik yang refrakter"
                ),
            },
        })

    # L3. Hipomagnesemia & Hipofosfatemia (sering luput di ICU jantung)
    mg_phos_kw = ["hipomagnesemia", "magnesium rendah", "mg rendah", "mg <1.5",
                  "hipofosfatemia", "fosfat rendah", "po4 rendah",
                  "refeeding syndrome", "aritmia refrakter", "prolonged qt",
                  "tetani", "kram halus", "tanda chvostek", "tanda trousseau"]
    if "D.0037" in force_codes_set or sum(1 for kw in mg_phos_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0037",
            "diagnosa_keperawatan": (
                "Risiko Ketidakseimbangan Elektrolit (Magnesium/Fosfat) d.d Nutrisi Enteral Parenteral / "
                "Diuretik Kronik / Refeeding Syndrome — Hipomagnesemia/Hipofosfatemia."
            ),
            "luaran_keperawatan": "Keseimbangan Elektrolit Membaik (L.03021)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor kadar Mg dan PO4 serum setiap 24–48 jam pada pasien ICU/ICCU;"
                    "Monitor tanda hipomagnesemia: Chvostek sign, Trousseau sign, aritmia;"
                    "Monitor tanda hipofosfatemia: kelemahan otot napas, konfusi, hemolisis"
                ),
                "Terapeutik": (
                    "Berikan MgSO4 IV 2 g dalam 15–60 menit untuk hipomagnesemia simtomatik;"
                    "Koreksi fosfat IV secara perlahan (sodium/potassium phosphate infus);"
                    "Tangani refeeding syndrome: mulai nutrisi perlahan, suplementasi elektrolit proaktif"
                ),
                "Edukasi": (
                    "Edukasi tentang makanan kaya magnesium (kacang-kacangan, biji-bijian, sayur hijau);"
                    "Edukasi pasien post-operasi jantung tentang pentingnya kontrol elektrolit rutin"
                ),
                "Kolaborasi": (
                    "Kolaborasi panel elektrolit lengkap termasuk Mg dan PO4 pada semua pasien ICU/ICCU;"
                    "Kolaborasi ahli gizi untuk manajemen nutrisi pada refeeding syndrome"
                ),
            },
        })

    # L4. Asidosis / Alkalosis Metabolik
    acid_base_kw = ["asidosis metabolik", "alkalosis metabolik", "asidosis respiratorik",
                    "alkalosis respiratorik", "ph rendah", "ph tinggi", "ph <7.35",
                    "ph >7.45", "bikarbonat rendah", "bikarbonat tinggi", "be negatif",
                    "base excess negatif", "laktat tinggi", "lactat", "lactic",
                    "agd asidosis", "agd alkalosis", "ketoasidosis"]
    if "D.0021" in force_codes_set or sum(1 for kw in acid_base_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0021",
            "diagnosa_keperawatan": (
                "Ketidakseimbangan Asam-Basa d.d Perfusi Jaringan Tidak Efektif / "
                "Sepsis / Ketoasidosis / Gagal Ginjal / Ventilasi Tidak Adekuat."
            ),
            "luaran_keperawatan": "Keseimbangan Asam Basa Membaik (L.02009)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor AGD serial: pH, PaCO2, PaO2, HCO3, BE, SpO2;"
                    "Monitor kadar laktat serum setiap 2–4 jam pada asidosis laktat;"
                    "Identifikasi penyebab primer gangguan asam-basa (metabolik vs respiratorik)"
                ),
                "Terapeutik": (
                    "Asidosis metabolik berat (pH < 7.1): pertimbangkan NaHCO3 IV setelah kalkulasi deficit;"
                    "Optimasi ventilasi mekanik untuk koreksi asidosis respiratorik;"
                    "Tangani penyebab dasar: sepsis, DKA, gagal ginjal, overdosis"
                ),
                "Edukasi": (
                    "Edukasi keluarga tentang pentingnya pemeriksaan AGD berkala;"
                    "Jelaskan keterkaitan antara kondisi penyakit dan gangguan asam-basa"
                ),
                "Kolaborasi": (
                    "Kolaborasi AGD dan laktat setiap 2–6 jam sesuai kondisi klinis;"
                    "Kolaborasi nefrologi untuk terapi pengganti ginjal (CRRT) jika asidosis refrakter;"
                    "Kolaborasi endokrin untuk penanganan DKA/HHS"
                ),
            },
        })

    # =========================================================================
    # ── M. TERMOREGULASI & INFEKSI SISTEMIK ──────────────────────────────────
    # =========================================================================

    # M1. Hipertermia (D.0130)
    hipertermia_kw = ["demam tinggi", "hipertermia", "suhu >39", "suhu 40", "suhu 39",
                      "fever", "suhu tubuh meningkat", "panas tinggi", "hiperpireksia",
                      "demam pasca operasi", "demam hari ke", "febris"]
    if "D.0130" in force_codes_set or sum(1 for kw in hipertermia_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0130",
            "diagnosa_keperawatan": (
                "Hipertermia b.d Proses Infeksi / Peradangan / Hipermetabolisme / "
                "Gangguan Termoregulasi Pasca Bedah d.d Suhu Tubuh > 38.5°C, Kulit Merah dan Panas, Takikardia."
            ),
            "luaran_keperawatan": "Termoregulasi Membaik (L.14134)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor suhu tubuh minimal setiap 2 jam; dokumentasikan pola demam (kontinua, remiten, intermiten);"
                    "Monitor tanda-tanda sepsis: HR > 90, RR > 20, WBC > 12.000 atau < 4.000;"
                    "Monitor tanda dehidrasi akibat demam dan diaphoresis"
                ),
                "Terapeutik": (
                    "Berikan kompres air hangat (bukan air es) pada aksila dan lipat paha;"
                    "Ganti linen yang lembab; pakaikan pakaian tipis;"
                    "Berikan cairan oral/IV adekuat untuk mengganti kehilangan cairan akibat demam"
                ),
                "Edukasi": (
                    "Anjurkan pasien banyak minum cairan;"
                    "Edukasi tentang pentingnya kontrol demam untuk mencegah komplikasi"
                ),
                "Kolaborasi": (
                    "Kolaborasi antipiretik: paracetamol 500–1000 mg IV/oral setiap 6–8 jam;"
                    "Kolaborasi kultur darah (2 set), urin, sputum sebelum pemberian antibiotik;"
                    "Kolaborasi antibiotik empiris broad-spectrum jika ada dugaan sepsis"
                ),
            },
        })

    # M2. Hipotermia (D.0131)
    hipotermia_kw = ["hipotermia", "suhu rendah", "suhu <36", "suhu <35", "menggigil berat",
                     "kedinginan", "cold exposure", "akral sangat dingin", "perioperatif hipotermia",
                     "post bypass hipotermia", "ttm", "target temperature management"]
    if "D.0131" in force_codes_set or sum(1 for kw in hipotermia_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0131",
            "diagnosa_keperawatan": (
                "Hipotermia b.d Paparan Lingkungan Dingin / Pasca Cardiopulmonary Bypass / "
                "Targeted Temperature Management d.d Suhu Tubuh < 36°C, Menggigil, Vasokonstriksi."
            ),
            "luaran_keperawatan": "Termoregulasi Membaik (L.14134)",
            "rencana_intervensi": {
                "Observasi": (
                    "Monitor suhu inti (core temperature) secara kontinu: rektal, esofageal, atau bladder probe;"
                    "Monitor kardiovaskular: aritmia hipotermia (AF, VF), pemanjangan interval QT;"
                    "Monitor koagulopati dan asidosis yang diperburuk hipotermia"
                ),
                "Terapeutik": (
                    "Berikan selimut penghangat aktif (forced air warming blanket);"
                    "Berikan cairan IV yang dihangatkan (≥ 39°C) jika diperlukan resusitasi;"
                    "Pada TTM pasca ROSC: pertahankan suhu target 32–36°C sesuai protokol selama 24 jam;"
                    "Hindari rewarming terlalu cepat (maks 0.25–0.5°C/jam)"
                ),
                "Edukasi": (
                    "Edukasi keluarga tentang tujuan TTM (neuroproteksi pasca henti jantung);"
                    "Jelaskan prosedur monitoring suhu invasif"
                ),
                "Kolaborasi": (
                    "Kolaborasi penggunaan perangkat TTM (Arctic Sun, Thermogard) dengan tim ICU;"
                    "Kolaborasi manajemen menggigil: buspiron, magnesium, meperidin, sedasi;"
                    "Kolaborasi hematologi untuk manajemen koagulopati hipotermia"
                ),
            },
        })

    # M3. Sepsis / Infeksi Aktual (D.0143)
    sepsis_kw = ["sepsis", "septik", "septic shock", "syok sepsis", "pct tinggi",
                 "procalcitonin tinggi", "qsofa", "sofa score", "bakteremia",
                 "infeksi sistemik", "wbc >12000", "wbc <4000", "bandemia",
                 "kultur positif", "bacteremia", "fungemia", "candida sistemik"]
    if "D.0143" in force_codes_set or sum(1 for kw in sepsis_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0143",
            "diagnosa_keperawatan": (
                "Infeksi (Sepsis) b.d Kuman Patogen Masuk ke Sirkulasi Sistemik / Imunosupresi "
                "d.d Demam/Hipotermi, Takikardia, Leukositosis/Leukopenia, PCT Meningkat, Disfungsi Organ."
            ),
            "luaran_keperawatan": "Tingkat Infeksi Menurun (L.14137)",
            "rencana_intervensi": {
                "Observasi": (
                    "Terapkan Sepsis Bundle (Surviving Sepsis Campaign Hour-1);"
                    "Monitor SOFA score dan tanda disfungsi organ (kreatinin, bilirubin, trombosit, GCS);"
                    "Monitor tanda syok septik: MAP < 65 mmHg, laktat > 2 mmol/L"
                ),
                "Terapeutik": (
                    "Ambil kultur darah 2 set (aerob + anaerob) dari 2 lokasi BERBEDA sebelum antibiotik;"
                    "Berikan cairan resusitasi 30 mL/kgBB kristaloid dalam 3 jam pertama;"
                    "Mulai antibiotik broad-spectrum IV dalam 1 jam diagnosis sepsis;"
                    "Pasang kateter urin untuk monitoring output ketat"
                ),
                "Edukasi": (
                    "Edukasi keluarga tentang kondisi sepsis, prognosis, dan pentingnya terapi agresif;"
                    "Jelaskan perlunya isolasi dan pencegahan transmisi"
                ),
                "Kolaborasi": (
                    "Kolaborasi antibiotik empiris sesuai sumber infeksi dan pola kuman lokal RSJPDHK;"
                    "Kolaborasi vasopressor (norepinefrin target MAP ≥ 65) jika cairan tidak cukup;"
                    "Kolaborasi kontrol sumber infeksi: cabut CVC yang terinfeksi, drainase abses;"
                    "Kolaborasi intensivis/ID untuk optimasi terapi antibiotik berbasis kultur"
                ),
            },
        })

    # =========================================================================
    # ── N. GANGGUAN MOBILITAS & KOMPLIKASI IMOBILISASI ───────────────────────
    # =========================================================================

    # N1. Gangguan Mobilitas Fisik (D.0054)
    mobilitas_kw = ["tidak bisa bergerak", "hemiplegia", "paraplegia", "tetraplegia",
                    "kelemahan anggota gerak", "imobilisasi", "bedrest total",
                    "kekuatan otot menurun", "tidak bisa berdiri sendiri",
                    "kekuatan otot 0", "kekuatan otot 1", "kekuatan otot 2",
                    "pasca operasi mobilitas", "post stroke mobilitas"]
    if "D.0054" in force_codes_set or sum(1 for kw in mobilitas_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0054",
            "diagnosa_keperawatan": (
                "Gangguan Mobilitas Fisik b.d Penurunan Kekuatan Otot / Kerusakan Integritas Struktur Tulang / "
                "Efek Agen Farmakologis d.d Kekuatan Otot Menurun, Rentang Gerak Terbatas, Gerakan Terbatas."
            ),
            "luaran_keperawatan": "Mobilitas Fisik Meningkat (L.05042)",
            "rencana_intervensi": {
                "Observasi": (
                    "Kaji kekuatan otot dengan skala MRC (0–5) pada seluruh ekstremitas;"
                    "Monitor tanda komplikasi imobilisasi: DVT, dekubitus, kontraktur, pneumonia hipostatik;"
                    "Monitor toleransi terhadap mobilisasi bertahap"
                ),
                "Terapeutik": (
                    "Lakukan ROM pasif 2–3 kali/hari pada seluruh sendi yang terkena;"
                    "Posisikan pasien dalam alignment yang baik untuk mencegah kontraktur;"
                    "Lakukan mobilisasi bertahap: miring-berbaring → duduk → berdiri → berjalan;"
                    "Pasang splint/orthosis jika diperlukan untuk mencegah foot drop"
                ),
                "Edukasi": (
                    "Ajarkan keluarga teknik ROM aktif-asistif;"
                    "Motivasi pasien untuk aktif berpartisipasi dalam program rehabilitasi"
                ),
                "Kolaborasi": (
                    "Konsultasi fisioterapi untuk program mobilisasi dan rehabilitasi terstruktur;"
                    "Konsultasi dokter spesialis rehabilitasi medis untuk tatalaksana komprehensif"
                ),
            },
        })

    # N2. Hambatan Ambulasi (D.0058)
    ambulasi_kw = ["tidak bisa berjalan", "kesulitan berjalan", "gaya berjalan terganggu",
                   "membutuhkan alat bantu jalan", "walker", "kruk", "tripod", "kursi roda",
                   "pasca amputasi", "luka kaki diabetik", "neuropati perifer berat"]
    if "D.0058" in force_codes_set or sum(1 for kw in ambulasi_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0058",
            "diagnosa_keperawatan": (
                "Hambatan Ambulasi b.d Penurunan Kekuatan Otot / Nyeri / Gangguan Neuromuskular "
                "d.d Tidak Mampu Berjalan, Membutuhkan Alat Bantu, Gaya Berjalan Tidak Normal."
            ),
            "luaran_keperawatan": "Ambulasi Meningkat (L.05001)",
            "rencana_intervensi": {
                "Observasi": (
                    "Kaji kemampuan ambulasi menggunakan Timed Up and Go (TUG) test;"
                    "Monitor keseimbangan dan risiko jatuh saat mobilisasi"
                ),
                "Terapeutik": (
                    "Latih berjalan dengan alat bantu yang sesuai (walker, tripod, kruk);"
                    "Pastikan lingkungan aman: tidak licin, pegangan tersedia, cukup penerangan"
                ),
                "Edukasi": (
                    "Ajarkan teknik penggunaan alat bantu jalan yang benar;"
                    "Edukasi keluarga cara mendampingi dan memotivasi pasien dalam latihan berjalan"
                ),
                "Kolaborasi": (
                    "Konsultasi fisioterapi untuk program ambulasi progresif;"
                    "Konsultasi ortotik-prostetik jika diperlukan alat bantu khusus"
                ),
            },
        })

    # =========================================================================
    # ── O. NYERI PASCA OPERASI JANTUNG (KHUSUS RSJPDHK) ─────────────────────
    # =========================================================================

    # O1. Nyeri Akut Pasca Bedah Jantung / Sternotomi
    nyeri_cardiac_kw = ["nyeri sternotomi", "nyeri luka sternum", "nyeri pasca cabg",
                        "nyeri pasca operasi jantung", "nyeri post operasi jantung",
                        "nyeri sternum", "nyeri dada post op", "nyeri thoracotomy",
                        "nyeri insisi dada", "nyeri pasca torakotomi"]
    if "D.0077" in force_codes_set or sum(1 for kw in nyeri_cardiac_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0077",
            "diagnosa_keperawatan": (
                "Nyeri Akut b.d Agen Pencedera Fisik (Prosedur Sternotomi/Torakotomi) "
                "d.d Mengeluh Nyeri Dada/Sternum NRS ≥ 4, Wajah Meringis, Napas Terbatas karena Nyeri."
            ),
            "luaran_keperawatan": "Tingkat Nyeri Menurun (L.08066)",
            "rencana_intervensi": {
                "Observasi": (
                    "Kaji nyeri PQRST setiap 2–4 jam dan 30 menit pasca pemberian analgesik;"
                    "Monitor komplikasi analgesik opioid: mual, konstipasi, depresi napas, sedasi berlebih;"
                    "Monitor tanda nyeri tidak terkontrol yang menghambat napas dalam dan batuk efektif"
                ),
                "Terapeutik": (
                    "Ajarkan pasien menggunakan bantal untuk splinting saat batuk efektif (sternal support);"
                    "Terapkan analgesia multimodal: opioid + paracetamol + NSAID (jika tidak ada kontraindikasi);"
                    "Pertimbangkan PCA (Patient Controlled Analgesia) untuk nyeri pasca operasi berat;"
                    "Gunakan teknik non-farmakologis: relaksasi, distraksi musik, TENS"
                ),
                "Edukasi": (
                    "Ajarkan teknik batuk efektif dengan teknik sternal splinting (tekan bantal ke dada saat batuk);"
                    "Edukasi bahwa nyeri yang tidak terkontrol menghambat pemulihan dan meningkatkan komplikasi paru"
                ),
                "Kolaborasi": (
                    "Kolaborasi protokol analgesia multimodal pasca bedah jantung bersama DPJP bedah dan anestesi;"
                    "Kolaborasi nerve block (paravertebral, intercostal) atau epidural thorakal jika diperlukan;"
                    "Konsultasi tim pain management jika nyeri refrakter"
                ),
            },
        })

    # =========================================================================
    # ── P. MASALAH PSIKOSOSIAL LANJUTAN ──────────────────────────────────────
    # =========================================================================

    # P1. Gangguan Citra Tubuh (D.0082) — pasca operasi jantung, amputasi, stoma
    citra_kw = ["tidak menerima kondisi", "malu dengan kondisi fisik", "menolak melihat luka",
                "cemas dengan perubahan tubuh", "tidak percaya diri", "merasa tidak sempurna",
                "pasca amputasi perasaan", "scar sternotomi", "bekas operasi",
                "stoma", "body image terganggu"]
    if "D.0082" in force_codes_set or sum(1 for kw in citra_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0082",
            "diagnosa_keperawatan": (
                "Gangguan Citra Tubuh b.d Perubahan Struktur/Fungsi Tubuh Pasca Operasi / "
                "Amputasi d.d Tidak Mau Melihat/Menyentuh Area yang Berubah, Perasaan Negatif tentang Tubuh."
            ),
            "luaran_keperawatan": "Citra Tubuh Meningkat (L.09067)",
            "rencana_intervensi": {
                "Observasi": (
                    "Kaji persepsi pasien terhadap perubahan tubuhnya secara terbuka;"
                    "Monitor tanda depresi dan ansietas terkait perubahan citra tubuh"
                ),
                "Terapeutik": (
                    "Berikan dukungan emosional dan dengarkan tanpa menghakimi;"
                    "Fasilitasi kontak bertahap dengan area tubuh yang berubah;"
                    "Hubungkan pasien dengan kelompok dukungan (support group) pasca operasi jantung"
                ),
                "Edukasi": (
                    "Diskusikan perubahan tubuh yang akan terjadi dan cara adaptasi positif;"
                    "Informasikan bahwa perasaan negatif adalah reaksi normal dan dapat diatasi"
                ),
                "Kolaborasi": (
                    "Konsultasi psikologi klinis untuk terapi kognitif-behavioral;"
                    "Konsultasi occupational therapy untuk optimasi fungsi sehari-hari"
                ),
            },
        })

    # P2. Ketidakberdayaan / Hopelessness (D.0087)
    hopeless_kw = ["merasa tidak berguna", "putus asa", "tidak ada harapan", "menyerah",
                   "tidak mau berobat", "menolak terapi", "pasrah berlebihan",
                   "depresi berat klinis", "tidak mau makan karena putus asa",
                   "tidak mau rehabilitasi", "niat bunuh diri"]
    if "D.0087" in force_codes_set or sum(1 for kw in hopeless_kw if kw in combined) >= 2:
        rekomendasi.append({
            "kode_diagnosa": "D.0087",
            "diagnosa_keperawatan": (
                "Ketidakberdayaan b.d Penyakit Kronis / Prognosis Buruk / Kehilangan Kendali "
                "d.d Ekspresi Tidak Ada Harapan, Menolak Terapi, Apatis, Tidak Mau Berpartisipasi."
            ),
            "luaran_keperawatan": "Tingkat Stres Menurun (L.09092)",
            "rencana_intervensi": {
                "Observasi": (
                    "Kaji tingkat ketidakberdayaan dan faktor penyebabnya;"
                    "Skrining depresi dengan PHQ-9; skrining risiko bunuh diri jika ada indikasi"
                ),
                "Terapeutik": (
                    "Berikan kesempatan pasien mengungkapkan perasaan tanpa menghakimi;"
                    "Tetapkan tujuan kecil yang dapat dicapai untuk meningkatkan rasa kendali;"
                    "Libatkan pasien aktif dalam setiap pengambilan keputusan perawatan"
                ),
                "Edukasi": (
                    "Edukasi tentang penyakit, rencana terapi, dan harapan yang realistis;"
                    "Libatkan keluarga sebagai sistem dukungan utama"
                ),
                "Kolaborasi": (
                    "Konsultasi psikologi atau psikiatri segera jika ada risiko bunuh diri;"
                    "Konsultasi pekerja sosial medis untuk dukungan psikososial komprehensif"
                ),
            },
        })

    return rekomendasi


# =============================================================================
# INTEGRASI API
# =============================================================================

def _validate_asuhan_item(item: dict) -> bool:
    """Pastikan satu item asuhan memiliki semua field wajib dengan tipe benar."""
    required = {
        "kode_diagnosa":        str,
        "diagnosa_keperawatan": str,
        "luaran_keperawatan":   str,
        "rencana_intervensi":   dict,
    }
    for field, expected_type in required.items():
        val = item.get(field)
        if not isinstance(val, expected_type) or not val:
            return False
    return True


def call_backend_api(s_input: str, o_input: str) -> list[dict] | None:
    """
    Kirim data ke FastAPI backend dan kembalikan list asuhan yang sudah divalidasi.
    Return None jika API tidak dapat dihubungi atau response tidak valid.
    """
    payload = {"text": f"Subjektif: {s_input}. Objektif: {o_input}."}
    try:
        res = requests.post(API_URL, json=payload, timeout=API_TIMEOUT)
        res.raise_for_status()
        data = res.json()
        if not isinstance(data, list):
            logger.warning("API response bukan list: %s", type(data))
            return None
        valid = [item for item in data if _validate_asuhan_item(item)]
        if not valid:
            logger.warning("API response tidak memiliki item valid.")
            return None
        return valid
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.Timeout:
        logger.warning("API timeout setelah %s detik", API_TIMEOUT)
        return None
    except requests.exceptions.HTTPError as exc:
        logger.error("API HTTP error: %s", exc)
        return None
    except (ValueError, KeyError) as exc:
        logger.error("API response parsing error: %s", exc)
        return None


# ✅ Import CDSS v2.0 langsung (sudah diimpor di bagian atas file —
# baris ini sengaja tidak diimpor ulang untuk menghindari duplikasi)


def call_cdss_api(s_input: str, o_input: str, use_api: bool = False) -> Dict:
    """
    Call CDSS untuk analisis diagnosa
    
    Args:
        s_input: Subjective data
        o_input: Objective data
        use_api: Jika True, coba API dulu. Jika False, gunakan local engine
    
    Returns:
        Dict dengan struktur:
        {
            'status': 'success',
            'recommendations': [...],
            'numeric_findings': {...},
            'clinical_context': {...}
        }
    """
    
    # ✅ OPTION 1A: Coba API dulu (jika tersedia), fallback ke local v2.0
    if use_api:
        try:
            logger.debug("Trying CDSS API call...")
            response = requests.post(
                "http://localhost:8000/api/cdss",  # Adjust URL as needed
                json={"s_text": s_input, "o_text": o_input},
                timeout=5
            )
            if response.status_code == 200:
                logger.debug("CDSS API success, using API response")
                return response.json()
            else:
                logger.debug("CDSS API returned %s, falling back to local", response.status_code)
        except Exception as e:
            logger.debug("CDSS API error: %s, using local engine", str(e))
    
    # ✅ OPTION 1B: Gunakan CDSS v2.0 local (RECOMMENDED)
    logger.debug("Using local CDSS v2.0 engine")
    result = analyze_clinical_trends_improved(s_input, o_input)
    result = bridge_engine(result, s_input, o_input)
    return result

def formulasikan_asuhan(s_input: str, o_input: str) -> tuple[list[dict], str, Dict]:
    """
    Jalankan pipeline hybrid CDSS.

    PATCH (2026-06, revisi ke-2) — FIX BUG UTAMA "hanya 1 diagnosa di CPPT":
    Revisi pertama sudah menyambungkan local_cdss_rule_engine() ke
    bridge_engine() dengan benar (CDSS v2.0 jadi sumber data_asuhan), TAPI
    backend FastAPI eksternal di API_URL (".../api/v1/extract") ternyata
    hidup & merespons di environment user — dan endpoint itu MASIH ditaruh
    sebagai prioritas #1 di atas CDSS v2.0. Begitu call_backend_api()
    berhasil (mengembalikan list non-None, walau cuma 1 item valid), fungsi
    ini langsung `return` lebih dulu — CDSS v2.0 + bridge_engine yang sudah
    benar tidak pernah sempat dipakai sama sekali. Endpoint /api/v1/extract
    ini tampaknya layanan ekstraksi NLP terpisah yang belum lengkap
    (validasinya hanya meloloskan 1 dari N item), bukan CDSS v2.0 yang
    dioptimalkan dengan weighted scoring & pembacaan nilai numerik lab/TTV.

    Fix: CDSS v2.0 lokal (weighted scoring + bridge_engine) sekarang jadi
    PRIMARY source. Backend API eksternal didemosikan jadi fallback opsional
    — hanya dipakai jika CDSS v2.0 sama sekali tidak mendeteksi diagnosa apa
    pun untuk input tsb. local_cdss_rule_engine mentah (keyword-only, tanpa
    skor) tetap jadi fallback paling akhir.

    Urutan prioritas sumber data_asuhan (REVISI):
      1) CDSS v2.0 lokal (weighted scoring + bridge_engine) — PRIMARY
      2) Backend API eksternal (API_URL) — fallback, hanya jika v2.0 nihil
      3) local_cdss_rule_engine mentah (keyword-only) — fallback terakhir

    Kembalikan (daftar_asuhan, sumber, hasil_cdss) di mana:
      - sumber: "CDSS v2.0 (Weighted + Bridge)" / "API (fallback)" / "Lokal (Fallback Keyword)"
      - hasil_cdss: dict lengkap CDSS v2.0 (untuk panel insight & ranking),
        selalu dihitung agar panel insight tetap tampil apa pun sumber datanya.
    """
    hasil_cdss = call_cdss_api(s_input, o_input, use_api=False)

    recs = (hasil_cdss or {}).get("recommendations", [])
    bridged_asuhan = [rec for rec in recs if _validate_asuhan_item(rec)]
    if bridged_asuhan:
        return bridged_asuhan, "CDSS v2.0 (Weighted + Bridge)", hasil_cdss

    api_result = call_backend_api(s_input, o_input)
    if api_result is not None:
        return api_result, "API (fallback)", hasil_cdss

    lokal_result = local_cdss_rule_engine(s_input, o_input)
    return lokal_result, "Lokal (Fallback Keyword)", hasil_cdss

def bridge_engine(main_engine_output, s_text, o_text):
    """
    Fungsi jembatan versi 2.0: Sinkronisasi mutlak berdasarkan KODE DIAGNOSA (Code).
    Memastikan Penurunan Curah Jantung (D.0008) memunculkan intervensi jantung yang tepat.

    PATCH (2026-06, revisi ke-3): kode diagnosa yang sudah dipilih CDSS v2.0
    (main_engine_output) sekarang dikirim sebagai force_codes ke
    local_cdss_rule_engine(), supaya template intervensinya WAJIB diambil
    berdasarkan kode diagnosa saja — bukan disyaratkan keyword teks juga
    ikut cocok secara independen. Ini yang sebelumnya menyebabkan sebagian
    diagnosa (yang terdeteksi CDSS v2.0 dari nilai numerik lab/TTV, bukan
    dari kata kunci di teks) muncul sebagai "Luaran belum terpetakan di
    lokal." padahal templatenya sudah ada & lengkap di database lokal.
    """
    # 1. Kumpulkan kode diagnosa yang sudah dipilih CDSS v2.0, lalu jalankan
    #    CDSS lokal dengan kode-kode itu di-force supaya template intervensi
    #    & luarannya WAJIB diambil terlepas dari hasil deteksi keyword teks.
    main_recommendations = main_engine_output.get('recommendations', [])
    codes_needed = {
        rec.get('code', '').upper().strip()
        for rec in main_recommendations
        if rec.get('code')
    }
    local_res = local_cdss_rule_engine(s_text, o_text, force_codes=codes_needed)

    # 2. Buat mapping cepat berdasarkan KODE DIAGNOSA (Contoh: 'd.0008')
    local_code_mapping = {}
    for item in local_res:
        kode_lokal = item.get('kode_diagnosa')
        if kode_lokal:
            local_code_mapping[kode_lokal.upper().strip()] = item

    # 3. Ambil rekomendasi diagnosa berprioritas dari Engine Utama
    final_recommendations = []
    
    for rec in main_recommendations:
        # Ambil kode diagnosa dari engine utama (Misal: 'D.0008')
        main_code = rec.get('code', '').upper().strip()
        
        # Cari kecocokan mutlak berdasarkan KODE di CDSS Lokal Anda
        matched_local = local_code_mapping.get(main_code)
        
        if matched_local:
            # JIKA COCOK: Ambil intervensi lokal, masukkan ke struktur data yang dibaca dashboard
            rec['rencana_intervensi'] = matched_local.get('rencana_intervensi', {})
            rec['luaran_keperawatan'] = matched_local.get('luaran_keperawatan', '')
            rec['kode_diagnosa'] = main_code
            rec['diagnosa_keperawatan'] = matched_local.get('diagnosa_keperawatan', rec.get('name'))
        else:
            # JIKA TIDAK COCOK: Cari fallback manual dari Master Data dashboard Anda jika ada
            rec['rencana_intervensi'] = {
                "Observasi": "Intervensi spesifik belum terpicu di CDSS Lokal. Silakan periksa manual.",
                "Terapeutik": "-", "Edukasi": "-", "Kolaborasi": "-"
            }
            rec['luaran_keperawatan'] = "Luaran belum terpetakan di lokal."
            rec['kode_diagnosa'] = main_code
            rec['diagnosa_keperawatan'] = rec.get('name')
            
        final_recommendations.append(rec)
    priority_weights = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
    final_recommendations.sort(key=lambda x: (priority_weights.get(x.get('priority', 'MEDIUM'), 2), -x.get('score', 0)))

    # Masukkan kembali list yang sudah di-sort dengan benar ke output
    main_engine_output['recommendations'] = final_recommendations
    return main_engine_output    
    
# =============================================================================
# CPPT & LOGBOOK
# =============================================================================

def generate_cppt_and_logbook(daftar_asuhan: list[dict], subjektif: str, objektif: str) -> str:
    """
    Sinkronisasi Otomatis List Intervensi Terpilih (Kolom P).
    Membaca intervensi yang dicentang dari checked_items session state.
    """
    cppt  = "CATATAN PERKEMBANGAN PASIEN TERINTEGRASI (CPPT)\n"
    cppt += f"ID Episode : {st.session_state.get('episode_id', '-')} | Shift: {st.session_state.get('shift', '-')}\n"
    cppt += f"Tanggal    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    cppt += "======================================================================\n\n"
    cppt += f"S: {subjektif.strip()}\n"
    cppt += f"O: {objektif.strip()}\n\n"

    latest_slki = get_latest_slki_scores(st.session_state.episode_id)

    # ASSESSMENT
    cppt += "A (Assessment / Diagnosa Keperawatan):\n"
    for idx, asuhan in enumerate(daftar_asuhan, 1):
        kode_dx  = asuhan.get("kode_diagnosa", "ERR").strip()
        nama_dx  = (
            asuhan.get("nama_diagnosa")
            or asuhan.get("diagnosa")
            or asuhan.get("nama")
            or ""
        )
        nama_dx = str(nama_dx).strip()
        for sep in [" b.d", " d.d"]:
            if sep in nama_dx:
                nama_dx = nama_dx.split(sep)[0]
        nama_dx = nama_dx.strip() or SDKI_NAME_MAPPING.get(kode_dx, "Diagnosa Tidak Diketahui")

        status_rekomendasi = " | Terkini -> Belum ada entri skor perkembangan terbaru."
        mapping_info = DX_TO_SLKI_MAPPING.get(kode_dx)
        if latest_slki and mapping_info:
            for slki_nama, skor in latest_slki:
                if mapping_info["kode_luaran"] in slki_nama:
                    status_rekomendasi = (
                        f" | Terkini -> {mapping_info['narasi']} [Skor Akhir: {skor}/5]"
                    )
                    break

        cppt += f"   {idx}. ({kode_dx}) {nama_dx}{status_rekomendasi}\n"

    cppt += "\n"

    # PLAN & LOGBOOK
    cppt += "P:\n"
    kategori_siki  = ["Observasi", "Terapeutik", "Edukasi", "Kolaborasi"]
    logbook_entries = []
    checked = st.session_state.get("checked_items", {})

    for asuhan in daftar_asuhan:
        kode = asuhan.get("kode_diagnosa", "N/A").strip()
        nama_diag = (
            asuhan.get("nama_diagnosa")
            or asuhan.get("diagnosa")
            or asuhan.get("nama")
            or ""
        )
        nama_diag = str(nama_diag)
        for sep in [" b.d", " d.d"]:
            nama_diag = nama_diag.split(sep)[0]
        nama_diag = nama_diag.strip() or SDKI_NAME_MAPPING.get(kode, "Diagnosa Keperawatan")

        intervensi_raw = asuhan.get("rencana_intervensi", asuhan.get("intervensi", {}))
        tindakan_dipilih = []

        for pilar in kategori_siki:
            if pilar not in intervensi_raw:
                continue
            items_teks = _parse_intervensi(intervensi_raw[pilar])
            statuses   = checked.get(kode, {}).get(pilar, [])

            for i, item_text in enumerate(items_teks):
                if isinstance(item_text, dict):
                    item_text = item_text.get("nama", "")
                item_text = str(item_text).strip()
                if i < len(statuses) and statuses[i] and item_text not in tindakan_dipilih:
                    tindakan_dipilih.append(item_text)
                    logbook_entries.append({
                        "timestamp":    datetime.now().isoformat(),
                        "nip_pegawai":  st.session_state.get("user_id", "-"),
                        "shift":        st.session_state.get("shift", "-"),
                        "episode_id":   st.session_state.get("episode_id", "-"),
                        "kode_siki":    item_text,
                        "kode_diagnosa": kode,
                    })

        if tindakan_dipilih:
            cppt += f"- ({kode}) {nama_diag}: {', '.join(tindakan_dipilih)}.\n"
        else:
            cppt += f"- ({kode}) {nama_diag}: Lanjutkan intervensi sesuai rencana dasar.\n"

    st.session_state.logbook_payload = logbook_entries
    user_id_safe = str(st.session_state.get("user_id", "Keperawatan")).upper()
    return cppt + f"\nDivalidasi oleh: {user_id_safe}"


# =============================================================================
# KOMPONEN UI
# =============================================================================

def render_grafik_slki(episode_id: str) -> None:
    df = fetch_real_slki_trends(episode_id)
    if df.empty:
        st.info("Belum ada data evaluasi SLKI untuk ID Episode ini di database.")
        return

    with st.container(border=True):
        st.markdown(
            f"📈 **Tren Pemantauan Indikator SLKI (Multi-Diagnosa)** | Episode: `{episode_id}`"
        )
        fig = px.line(
            df,
            x="Waktu Evaluasi",
            y="Skor Indikator",
            color="Kriteria Hasil (SLKI)",
            markers=True,
            line_shape="spline",
            range_y=[0.5, 5.5],
            template="plotly_white",
        )
        fig.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        )
        fig.update_yaxes(tickvals=[1, 2, 3, 4, 5], title_text="Skor SLKI")
        fig.update_xaxes(title_text=None)
        st.plotly_chart(fig, use_container_width=True)


def render_checklist(asuhan: dict, kode: str) -> None:
    """
    Status checkbox disimpan di st.session_state.checked_items[kode][pilar][i].
    """
    prioritas = st.session_state.order_list.get(kode, 1)
    with st.expander(f"📋 PRIORITAS {prioritas}: {kode}", expanded=True):
        st.error(asuhan.get("diagnosa_keperawatan", ""))
        c1, c2 = st.columns([1, 2])
        with c1:
            st.info(asuhan.get("luaran_keperawatan", ""))
        with c2:
            if kode not in st.session_state.checked_items:
                st.session_state.checked_items[kode] = {}

            for pilar, detail in asuhan.get("rencana_intervensi", {}).items():
                st.markdown(f"**{pilar}**")
                items = _parse_intervensi(detail)

                if pilar not in st.session_state.checked_items[kode]:
                    st.session_state.checked_items[kode][pilar] = [False] * len(items)

                for i, item_text in enumerate(items):
                    cb_key  = f"cb_{kode}_{pilar}_{i}"
                    current = st.session_state.checked_items[kode][pilar][i]
                    new_val = st.checkbox(item_text, value=current, key=cb_key)
                    st.session_state.checked_items[kode][pilar][i] = new_val


# =============================================================================
# CALLBACK: AUTO-SWAP PRIORITAS
# =============================================================================

def handle_priority_swap(changed_kode: str) -> None:
    """
    Callback untuk mendeteksi tabrakan urutan prioritas diagnosa
    dan melakukan pertukaran (swap) posisi secara real-time.
    """
    new_prio = st.session_state[f"prio_select_{changed_kode}"]
    old_prio = st.session_state.order_list.get(changed_kode)

    if new_prio == old_prio:
        return

    for kode, prio in st.session_state.order_list.items():
        if kode != changed_kode and prio == new_prio:
            st.session_state.order_list[kode]           = old_prio
            st.session_state[f"prio_select_{kode}"]     = old_prio
            break

    st.session_state.order_list[changed_kode] = new_prio


# =============================================================================
# FUNGSI EVALUASI SOAP
# =============================================================================

def update_soap_from_status() -> None:
    """Buat narasi A dan P berdasarkan status masing-masing diagnosa."""
    evaluasi_A = "=== EVALUASI DIAGNOSIS (ASSESSMENT) ===\n"
    rencana_P  = "=== RENCANA TINDAK LANJUT (PLAN) ===\n"

    for idx, diag in enumerate(st.session_state.daftar_diagnosis, 1):
        status = diag["status"]
        nama   = diag["nama"]

        if status == "Belum Teratasi":
            evaluasi_A += f"{idx}. {nama}: Belum Teratasi (Kriteria luaran/SLKI belum tercapai).\n"
            rencana_P  += (
                f"{idx}. u/ {nama}:\n"
                "   - Lanjutkan intervensi keperawatan/medis (SIKI) sesuai rencana tindakan awal.\n"
                "   - Monitor tanda-tanda vital dan perkembangan klinis secara ketat.\n"
            )
        elif status == "Teratasi Sebagian":
            evaluasi_A += (
                f"{idx}. {nama}: Teratasi Sebagian "
                "(Kriteria luaran/SLKI tercapai sebagian, menunjukkan perbaikan klinis).\n"
            )
            rencana_P += (
                f"{idx}. u/ {nama}:\n"
                "   - Lanjutkan intervensi harian yang esensial.\n"
                "   - Lakukan re-evaluasi indikator klinis pada sif berikutnya.\n"
            )
        elif status == "Teratasi (Selesai)":
            evaluasi_A += (
                f"{idx}. {nama}: Teratasi (Kriteria luaran/SLKI tercapai sepenuhnya).\n"
            )
            rencana_P += (
                f"{idx}. u/ {nama}:\n"
                "   - Hentikan intervensi aktif.\n"
                "   - Pertahankan kondisi optimal pasien (maintenance).\n"
            )

    st.session_state.soap_A = evaluasi_A
    st.session_state.soap_P = rencana_P


# =============================================================================
# APLIKASI UTAMA
# =============================================================================

def main_app() -> None:
    # Cek TTL sesi di setiap render
    if _session_expired():
        logout("⏰ Sesi Anda telah berakhir (1 jam). Silakan login kembali.")

    # Inisialisasi soap_P jika baru pertama render
    if not st.session_state.soap_P:
        update_soap_from_status()

    # ── Sidebar ──────────────────────────────────────────────────────────────
    st.sidebar.write(f"👤 User: **{st.session_state.user_id.upper()}**")
    st.sidebar.write(f"⏰ Sif Kerja: **{st.session_state.shift}**")
    st.sidebar.write(f"🏷️ ID Episode: `{st.session_state.episode_id}`")

    remaining = SESSION_TTL - int(
        (datetime.now() - st.session_state.login_at).total_seconds() / 60
    )
    st.sidebar.caption(f"🕐 Sesi berakhir dalam ±{remaining} menit")

    if st.sidebar.button("Logout"):
        logout()

    st.sidebar.write("---")
    with st.sidebar.expander("🛠️ Alat Simulasi Input Data Klinis Riil"):
        st.caption("Suntikkan skor perkembangan indikator asuhan secara berkala.")
        indikator_pilihan = st.selectbox("Pilih Indikator Evaluasi:", INDIKATOR_SLKI)
        skor_pilihan      = st.slider("Skor Hasil Evaluasi (1–5):", 1, 5, 3)

        if st.sidebar.button("Kirim ke Database Lokal"):
            insert_slki_score(
                st.session_state.episode_id,
                indikator_pilihan,
                skor_pilihan,
                st.session_state.user_id,
            )
            st.sidebar.success("Berhasil! Tersimpan permanen.")
            time.sleep(0.4)
            st.rerun()

    # ── Konten Utama ─────────────────────────────────────────────────────────
    st.title("🫀 CATATAN PERKEMBANGAN PASIEN TERINTEGRASI")

    render_grafik_slki(st.session_state.episode_id)
    st.write(" ")

    with st.expander("📋 Log Audit Keamanan: Riwayat Akses Darurat (Bypass Manual)"):
        if st.session_state.emergency_logs:
            st.dataframe(
                pd.DataFrame(st.session_state.emergency_logs), use_container_width=True
            )
        else:
            st.info("Aman: Belum ada aktivitas bypass manual pada sesi ini.")

    # ── Voice to Text Input ───────────────────────────────────────────────────
    if SPEECH_AVAILABLE:
        with st.container(border=True):
            st.markdown("#### 🎙️ Input Suara — Voice to Text")
            st.caption(
                "Tekan **Mulai Rekam**, ucapkan temuan klinis, lalu tekan **Selesai**. "
                "Teks akan otomatis ditambahkan ke kolom S atau O di bawah. "
                "Bisa dilanjutkan dengan pengetikan manual."
            )
            if AI_NORMALIZE_AVAILABLE:
                st.caption("🧠 Filter & standarisasi istilah klinis berbasis AI: **Aktif**")
            else:
                st.caption(
                    "🧠 Filter AI: **Nonaktif** (teks dipakai apa adanya dari speech-to-text). "
                    "Aktifkan dengan: `pip install anthropic` dan set environment variable `ANTHROPIC_API_KEY`."
                )
            vcol1, vcol2 = st.columns(2)

            with vcol1:
                st.markdown("**🗣️ Rekam Data Subjektif (S)**")
                audio_s = mic_recorder(
                    start_prompt="🎤 Mulai Rekam S",
                    stop_prompt="⏹ Selesai Rekam S",
                    key="mic_s",
                    format="wav",
                )
                if audio_s and audio_s.get("id") != st.session_state.last_audio_s_id:
                    st.session_state.last_audio_s_id = audio_s["id"]
                    with st.spinner("🔄 Mengonversi suara subjektif ke teks..."):
                        hasil_s = transcribe_audio(audio_s["bytes"])
                    if not hasil_s.startswith("["):
                        if AI_NORMALIZE_AVAILABLE:
                            with st.spinner("🧹 Membersihkan & menstandarisasi istilah klinis..."):
                                hasil_s = normalize_clinical_transcript(hasil_s, field="S")
                        prev = st.session_state.s_text_area
                        st.session_state.s_text_area = (
                            (prev + " " + hasil_s).strip() if prev else hasil_s
                        )
                        st.toast("✅ Teks subjektif berhasil ditambahkan!", icon="🎙️")
                    else:
                        st.warning(f"⚠️ VTT-S: {hasil_s}")

            with vcol2:
                st.markdown("**🗣️ Rekam Data Objektif (O)**")
                audio_o = mic_recorder(
                    start_prompt="🎤 Mulai Rekam O",
                    stop_prompt="⏹ Selesai Rekam O",
                    key="mic_o",
                    format="wav",
                )
                if audio_o and audio_o.get("id") != st.session_state.last_audio_o_id:
                    st.session_state.last_audio_o_id = audio_o["id"]
                    with st.spinner("🔄 Mengonversi suara objektif ke teks..."):
                        hasil_o = transcribe_audio(audio_o["bytes"])
                    if not hasil_o.startswith("["):
                        if AI_NORMALIZE_AVAILABLE:
                            with st.spinner("🧹 Membersihkan & menstandarisasi istilah klinis..."):
                                hasil_o = normalize_clinical_transcript(hasil_o, field="O")
                        prev = st.session_state.o_text_area
                        st.session_state.o_text_area = (
                            (prev + " " + hasil_o).strip() if prev else hasil_o
                        )
                        st.toast("✅ Teks objektif berhasil ditambahkan!", icon="🎙️")
                    else:
                        st.warning(f"⚠️ VTT-O: {hasil_o}")
    else:
        st.info(
            "ℹ️ Fitur Voice to Text tidak aktif. "
            "Instal dengan: `pip install SpeechRecognition streamlit-mic-recorder`",
            icon="🎙️",
        )

    col1, col2 = st.columns(2)
    with col1:
        s_input = st.text_area(
            "📋 S (Subjektif)" + (" — VTT Aktif 🎙️" if SPEECH_AVAILABLE else ""),
            placeholder="Masukkan keluhan subjektif pasien atau gunakan rekam suara di atas...",
            height=150,
            key="s_text_area",
        )
        if st.button(
            "🗑️ Hapus & Reset Kolom S", key="clear_s_btn", use_container_width=True
        ):
            st.session_state.s_text_area = ""
            st.rerun()
    with col2:
        o_input = st.text_area(
            "📊 O (Objektif: TTV/Monitor/Px.Fisik/Penunjang)" + (" — VTT Aktif 🎙️" if SPEECH_AVAILABLE else ""),
            placeholder="Masukkan pemeriksaan objektif atau gunakan rekam suara di atas...",
            height=150,
            key="o_text_area",
        )
        if st.button(
            "🗑️ Hapus & Reset Kolom O", key="clear_o_btn", use_container_width=True
        ):
            st.session_state.o_text_area = ""
            st.rerun()

    if st.button("Formulasikan Standar 3S", type="primary"):
        if not s_input.strip() and not o_input.strip():
            st.warning("⚠️ Mohon isi minimal satu kolom data S atau O.")
        else:
            with st.spinner("Menganalisis data klinis..."):
                # PATCH (2026-06): data_asuhan & hasil_cdss sekarang berasal
                # dari satu pipeline yang sama (lihat docstring
                # formulasikan_asuhan) sehingga checklist diagnosa, urutan
                # prioritas, dan CPPT selalu konsisten dengan ranking CDSS
                # v2.0 (weighted scoring) yang ditampilkan di panel insight —
                # tidak lagi dua sumber data yang berbeda/tidak sinkron.
                data_asuhan, sumber, hasil_cdss = formulasikan_asuhan(s_input, o_input)

            if data_asuhan:
                st.session_state.checked_items     = {}
                st.session_state.daftar_asuhan     = data_asuhan
                st.session_state.hasil_cdss        = hasil_cdss
                st.session_state.sumber_cdss_terakhir = sumber
                st.session_state.order_list        = {
                    a["kode_diagnosa"]: i + 1 for i, a in enumerate(data_asuhan)
                }
                st.session_state.selected_dx_codes = {
                    a["kode_diagnosa"] for a in data_asuhan
                }
                st.caption(f"ℹ️ Sumber CDSS: **{sumber}**")
                st.rerun()
            else:
                st.warning("⚠️ CDSS tidak mendeteksi kata kunci klinis spesifik 3S pada input narasi Anda.")
                st.rerun()

    if st.session_state.daftar_asuhan:
        # ─────────────────────────────────────────────────────────────────
        # PATCH (2026-06): Panel insight CDSS v2.0.
        # Sebelumnya blok ini mengecek cdss_data.get("analisis") yang TIDAK
        # PERNAH ada di struktur output analyze_clinical_trends_improved()
        # versi lama, sehingga alert ini tidak pernah tampil dan seluruh
        # hasil weighted-scoring v2.0 (numeric findings, clinical context,
        # ranking diagnosa) terbuang percuma. Engine sudah diperbaiki agar
        # mengembalikan key 'analisis', dan panel di bawah ini sekarang
        # menampilkan informasi lengkapnya — bukan cuma satu baris alert.
        # ─────────────────────────────────────────────────────────────────
        cdss_data = st.session_state.get("hasil_cdss")
        if cdss_data and cdss_data.get("status") == "success":
            analisis_text = cdss_data.get("analisis")
            if analisis_text:
                st.error(f"🚨 **CDSS ALERT:** {analisis_text}")

            with st.expander("🧠 CDSS v2.0 — Detail Analisis (Weighted Scoring)", expanded=bool(analisis_text)):
                ctx = cdss_data.get("clinical_context", {})
                ctx_labels = {
                    "is_postoperative_cardiac": "Post-operatif Kardiak",
                    "is_acute_decompensated_hf": "Gagal Jantung Akut",
                    "is_acute_coronary_syndrome": "Sindrom Koroner Akut",
                    "is_cardiogenic_shock": "Syok Kardiogenik",
                    "has_mechanical_complication": "Komplikasi Mekanik",
                    "is_on_ecmo_vad": "Dukungan ECMO/VAD",
                }
                active_flags = [label for key, label in ctx_labels.items() if ctx.get(key)]
                if active_flags:
                    st.markdown("**Konteks klinis terdeteksi:** " + ", ".join(active_flags))

                numeric_findings = cdss_data.get("numeric_findings", {})
                if numeric_findings:
                    st.markdown("**Nilai numerik terbaca dari input:** " + ", ".join(
                        f"{k.upper()}={v}" for k, v in numeric_findings.items()
                    ))

                recs = cdss_data.get("recommendations", [])
                if recs:
                    sumber_aktif = st.session_state.get("sumber_cdss_terakhir", "")
                    label_ranking = (
                        "**Ranking diagnosa (CDSS v2.0) — sumber checklist di bawah:**"
                        if sumber_aktif.startswith("CDSS v2.0")
                        else "**Ranking diagnosa (CDSS v2.0, untuk pembanding — sumber checklist saat ini: "
                             f"{sumber_aktif or 'tidak diketahui'}):**"
                    )
                    st.markdown(label_ranking)
                    for r in recs[:8]:
                        st.write(f"- `[{r['priority']}]` **{r['code']}** — {r['name']} (skor: {r['score']})")
                else:
                    st.caption("Tidak ada diagnosa yang melewati ambang skor v2.0 untuk input ini.")

        st.write("---")
        st.subheader("☑️ Pilih Diagnosa yang Akan Digunakan")
        st.caption(
            "CDSS mendeteksi diagnosa berikut. "
            "Hilangkan centang untuk **mengecualikan** diagnosa tertentu dari CPPT & rencana asuhan."
        )

        if "selected_dx_codes" not in st.session_state:
            st.session_state.selected_dx_codes = {
                a["kode_diagnosa"] for a in st.session_state.daftar_asuhan
            }

        for asuhan in st.session_state.daftar_asuhan:
            kode       = asuhan.get("kode_diagnosa", "ERR")
            short_name = SDKI_NAME_MAPPING.get(kode, kode)
            is_sel     = kode in st.session_state.selected_dx_codes
            new_val    = st.checkbox(
                f"**{kode}** — {short_name}",
                value=is_sel,
                key=f"select_dx_{kode}",
            )
            if new_val:
                st.session_state.selected_dx_codes.add(kode)
            else:
                st.session_state.selected_dx_codes.discard(kode)

        # Filter asuhan berdasarkan yang dipilih user
        asuhan_terpilih = [
            a for a in st.session_state.daftar_asuhan
            if a["kode_diagnosa"] in st.session_state.selected_dx_codes
        ]

        if not asuhan_terpilih:
            st.warning("⚠️ Pilih minimal satu diagnosa untuk melanjutkan rencana asuhan.")
        else:
            st.write("---")
            st.subheader("🔢 Atur Urutan Prioritas Diagnosa")

            n    = len(asuhan_terpilih)
            cols = st.columns(n)

            for i, asuhan in enumerate(asuhan_terpilih):
                kode         = asuhan.get("kode_diagnosa", "ERR")
                current_prio = st.session_state.order_list.get(kode, i + 1)
                with cols[i]:
                    st.selectbox(
                        f"Urutan {kode}",
                        options=list(range(1, n + 1)),
                        index=min(current_prio - 1, n - 1),
                        key=f"prio_select_{kode}",
                        on_change=handle_priority_swap,
                        args=(kode,),
                    )

            with st.form("form_asuhan"):
                sorted_diagnosa = sorted(
                    asuhan_terpilih,
                    key=lambda x: st.session_state.order_list.get(x["kode_diagnosa"], 99),
                )
                for asuhan in sorted_diagnosa:
                    st.write("---")
                    render_checklist(asuhan, asuhan.get("kode_diagnosa", "ERR"))

                st.write(" ")
                if st.form_submit_button("Simpan & Finalisasi CPPT", type="primary"):
                    # Bangun daftar_diagnosis dinamis dari diagnosa yang dipilih
                    new_daftar_dx = []
                    for idx, asuhan in enumerate(sorted_diagnosa, 1):
                        kode_dx    = asuhan.get("kode_diagnosa", "ERR")
                        short_name = SDKI_NAME_MAPPING.get(kode_dx, "Diagnosa Tidak Diketahui")
                        slki_info  = DX_TO_SLKI_MAPPING.get(kode_dx, {})
                        luaran     = slki_info.get(
                            "narasi", asuhan.get("luaran_keperawatan", "")
                        )
                        new_daftar_dx.append({
                            "id":     idx,
                            "nama":   f"{short_name} ({kode_dx})",
                            "luaran": luaran,
                            "kode":   kode_dx,
                            "status": "Belum Teratasi",
                        })
                    st.session_state.daftar_diagnosis = new_daftar_dx
                    update_soap_from_status()
                    st.session_state.draft_cppt = generate_cppt_and_logbook(
                        sorted_diagnosa, s_input, o_input
                    )
                    st.rerun()

    if st.session_state.draft_cppt:
        st.subheader("✍️ Edit CPPT & Logbook")
        teks_clean = clean_text(st.session_state.draft_cppt)
        st.session_state.draft_cppt = st.text_area(
            "Narasi Final:", value=teks_clean, height=350
        )

        st.write("---")
        st.subheader("📋 Evaluasi Diagnosis & Luaran Pasien")

        if not st.session_state.daftar_diagnosis:
            st.info(
                "ℹ️ Belum ada diagnosa yang difinalisasi. "
                "Klik **Simpan & Finalisasi CPPT** di atas untuk memuat daftar evaluasi."
            )
        else:
            for index, diag in enumerate(st.session_state.daftar_diagnosis):
                col1_diag, col2_diag = st.columns([3, 2])
                with col1_diag:
                    st.markdown(f"**{index + 1}. {diag['nama']}**")
                    if diag.get("luaran"):
                        st.caption(f"🎯 Luaran SLKI: {diag['luaran']}")
                with col2_diag:
                    status_opsi  = ["Belum Teratasi", "Teratasi Sebagian", "Teratasi (Selesai)"]
                    default_idx  = status_opsi.index(diag["status"])
                    selected_status = st.selectbox(
                        f"Status Masalah {diag['id']}",
                        options=status_opsi,
                        index=default_idx,
                        key=f"status_diag_{diag['id']}",
                        label_visibility="collapsed",
                    )
                    if selected_status != diag["status"]:
                        st.session_state.daftar_diagnosis[index]["status"] = selected_status
                        update_soap_from_status()

        st.subheader("✍️ Hasil Analisis & Rencana Asuhan Standar 3S (A & P)")
        col_A, col_P = st.columns(2)
        with col_A:
            st.text_area("A (Assessment / Analisis)", value=st.session_state.soap_A, height=150)
        with col_P:
            st.text_area("P (Plan / Penatalaksanaan)", value=st.session_state.soap_P, height=150)

        st.write("---")
        if st.button("✅ Simpan Permanen & Sinkronisasi Logbook"):
            st.success(f"Catatan tersimpan di Episode `{st.session_state.episode_id}`!")
            with st.expander("🛠 Debug: Payload Logbook e-Kinerja (F-NUR-03)"):
                st.json({
                    "status":        "TERINTEGRASI_LOGBOOK",
                    "total_records": len(st.session_state.logbook_payload),
                })
            st.dataframe(st.session_state.logbook_payload)


# =============================================================================
# HALAMAN LOGIN
# =============================================================================

def login_page() -> None:
    _, center_col, _ = st.columns([1, 2, 1])

    with center_col:
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(
                "<h2 style='text-align:center;color:#FF4B4B;'>🫀 Smart EMR</h2>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<h4 style='text-align:center;margin-top:-15px;color:#555;'>"
                "Pusat Otentikasi RME - RSJPDHK</h4>",
                unsafe_allow_html=True,
            )
            st.write("---")

            sif_kerja = st.selectbox(
                "⏰ Shif Tugas Saat Ini:",
                ["Pagi (07:00 - 14:00)", "Sore (14:00 - 21:00)", "Malam (21:00 - 07:00)"],
            )
            shift_clean = sif_kerja.split()[0]

            st.write("<br>", unsafe_allow_html=True)
            st.markdown("🎯 **Otentikasi Utama**")

            mock_finger = st.selectbox(
                "Simulasi Finger Print:",
                ["Rudi", "Jule", "Sidik Jari Tidak Terdaftar"],
            )

            if st.button(
                "👆 ISI CPPT - PINDAI SIDIK JARI SEKARANG",
                type="primary",
                use_container_width=True,
            ):
                user_map    = {"Rudi": "rudi", "Jule": "jule"}
                target_user = user_map.get(mock_finger)
                if target_user:
                    with st.spinner("🔄 Membaca enkripsi template biometrik..."):
                        time.sleep(1.0)
                    st.toast("Biometrik Terverifikasi via Hardware!", icon="🔑")
                    st.session_state.update({
                        "logged_in":  True,
                        "user_id":    target_user,
                        "shift":      shift_clean,
                        "login_at":   datetime.now(),
                        "episode_id": "EP-2026-00123",
                    })
                    st.rerun()
                else:
                    st.error("Gagal Otentikasi: Sidik jari tidak cocok dengan registri SIMRS.")

            st.write("<br>", unsafe_allow_html=True)

            with st.expander("⚠️ MASUK MODE DARURAT (Bypass Sensor Rusak/Jari Luka)"):
                st.warning(
                    "Perhatian: Login manual dipantau ketat. "
                    "Log tindakan bypass akan tercatat di Command Center & Audit Berkala."
                )
                u      = st.text_input("👤 ID Darurat:", placeholder="Masukkan ID")
                p      = st.text_input("🔒 Password Darurat:", type="password", placeholder="Masukkan password")
                alasan = st.text_area("📝 Alasan Klinis/Teknis Wajib:", placeholder="Contoh: Mesin Fingerprint error")

                if st.button("Konfirmasi Override & Masuk", type="secondary", use_container_width=True):
                    u_clean = u.lower().strip()
                    if not alasan.strip():
                        st.error("Gagal Akses: Alasan darurat wajib diisi!")
                    elif u_clean in KREDENSIAL and _verify_password(p, KREDENSIAL[u_clean]):
                        st.session_state.emergency_logs.append({
                            "Waktu Kejadian":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "NIP Pelaku":         u.upper(),
                            "Sif Kerja":          shift_clean,
                            "Alasan Kedaruratan": alasan,
                            "Metode Akses":       "BYPASS_MANUAL_OVERRIDE",
                            "Status":             "TEREKAM",
                        })
                        st.session_state.update({
                            "logged_in":  True,
                            "user_id":    u_clean,
                            "shift":      shift_clean,
                            "login_at":   datetime.now(),
                            "episode_id": "EP-2026-00123",
                        })
                        st.rerun()
                    else:
                        st.error("Kredensial darurat salah.")

        st.markdown(
            "<p style='text-align:center;color:#888;font-size:12px;'>"
            "Sistem Informasi Keperawatan Berbasis DED F-NUR © 2026</p>",
            unsafe_allow_html=True,
        )


# =============================================================================
# ENTRY POINT
# =============================================================================

if st.session_state.logged_in:
    main_app()
else:
    login_page()
