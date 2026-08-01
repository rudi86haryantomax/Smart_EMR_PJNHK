"""
core/config.py
==========================================
Konfigurasi aplikasi. Semua lewat environment variable supaya tidak ada
kredensial yang tertulis di kode.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    """Lokasi berkas SQLite."""
    custom = os.environ.get("ASUHAN_DB_PATH")
    if custom:
        path = Path(custom)
    else:
        path = BASE_DIR / "data" / "asuhan.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def sdki_json_path() -> Path:
    custom = os.environ.get("ASUHAN_SDKI_JSON")
    if custom and Path(custom).exists():
        return Path(custom)
    return BASE_DIR / "data" / "sdki_slki_siki.json"


# --------------------------------------------------
# Google Speech-to-Text
# --------------------------------------------------

def google_stt_key() -> str:
    """
    API key Google Cloud Speech-to-Text.

    JANGAN menuliskannya di kode. Set lewat environment:
        export GOOGLE_STT_API_KEY=xxxx
    atau lewat .streamlit/secrets.toml.
    """
    key = os.environ.get("GOOGLE_STT_API_KEY", "").strip()
    if key:
        return key
    try:
        import streamlit as st

        return str(st.secrets.get("GOOGLE_STT_API_KEY", "")).strip()
    except Exception:
        return ""


def stt_language() -> str:
    return os.environ.get("ASUHAN_STT_LANG", "id-ID")


def stt_enabled() -> bool:
    return bool(google_stt_key())
