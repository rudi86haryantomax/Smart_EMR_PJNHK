"""
services/translate_service.py
==========================================
Terjemahan teks memakai Google Cloud Translation API (v2).

KENAPA BERGUNA DI SINI
----------------------
Pencocokan diagnosis bekerja pada kriteria SDKI yang berbahasa Indonesia.
Bila catatan asesmen ditulis atau didiktekan sebagian dalam bahasa Inggris
— hal yang lazim untuk istilah klinis — sebagian kata tidak akan pernah
cocok. Menerjemahkannya lebih dulu menaikkan cakupan usulan.

Berbeda dengan Speech-to-Text, layanan ini memakai API key Google
Translate. Keduanya API yang TERPISAH: key yang aktif untuk Translate
tidak otomatis aktif untuk Speech-to-Text, dan sebaliknya. Masing-masing
harus diaktifkan tersendiri di Google Cloud Console.

Fitur ini opsional. Tanpa key, aplikasi berjalan normal dan tombol
terjemahan tidak ditampilkan.
"""

from __future__ import annotations

import os

from core.exceptions import AsuhanError

_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
_TIMEOUT = 20
_MAX_KARAKTER = 5000


class TranslateError(AsuhanError):
    """Kegagalan penerjemahan."""


def api_key() -> str:
    """
    API key Google Translate. JANGAN tulis di kode.

        export GOOGLE_TRANSLATE_API_KEY=xxxx

    atau isi di .streamlit/secrets.toml.
    """
    kunci = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip()
    if kunci:
        return kunci
    try:
        import streamlit as st

        return str(st.secrets.get("GOOGLE_TRANSLATE_API_KEY", "")).strip()
    except Exception:
        return ""


def is_available() -> bool:
    return bool(api_key())


def translate(teks: str, ke: str = "id", dari: str | None = None) -> str:
    """
    Terjemahkan teks. Melempar TranslateError bila gagal.

    `dari=None` membuat Google mendeteksi bahasa sumber sendiri — tepat
    untuk catatan klinis yang sering bercampur Indonesia dan Inggris.
    """
    isi = str(teks or "").strip()
    if not isi:
        return ""
    if len(isi) > _MAX_KARAKTER:
        raise TranslateError(
            f"Teks terlalu panjang ({len(isi)} karakter, batas {_MAX_KARAKTER}). "
            "Terjemahkan per bagian."
        )

    kunci = api_key()
    if not kunci:
        raise TranslateError(
            "GOOGLE_TRANSLATE_API_KEY belum diatur. Set environment variable "
            "atau isi di .streamlit/secrets.toml."
        )

    try:
        import requests
    except ImportError as exc:
        raise TranslateError("Pustaka requests belum terpasang.") from exc

    payload = {"q": isi, "target": ke, "format": "text"}
    if dari:
        payload["source"] = dari

    try:
        respons = requests.post(
            _ENDPOINT, params={"key": kunci}, data=payload, timeout=_TIMEOUT
        )
    except requests.Timeout as exc:
        raise TranslateError("Google Translate tidak merespons (timeout).") from exc
    except requests.RequestException as exc:
        raise TranslateError(f"Gagal menghubungi Google Translate: {exc}") from exc

    if respons.status_code != 200:
        rinci = ""
        try:
            rinci = respons.json().get("error", {}).get("message", "")
        except ValueError:
            rinci = respons.text[:200]
        raise TranslateError(
            f"Google Translate menolak permintaan: {rinci}\n\n"
            "Penyebab tersering: API 'Cloud Translation' belum diaktifkan "
            "untuk key ini."
        )

    try:
        hasil = respons.json()["data"]["translations"][0]["translatedText"]
    except (KeyError, IndexError, ValueError) as exc:
        raise TranslateError("Bentuk jawaban Google Translate tidak dikenali.") from exc

    return str(hasil).strip()


def translate_safe(teks: str, ke: str = "id") -> tuple[str, str]:
    """Versi yang tidak melempar. Mengembalikan (hasil, pesan_galat)."""
    try:
        return translate(teks, ke), ""
    except TranslateError as exc:
        return "", str(exc)
    except Exception as exc:  # pragma: no cover
        return "", f"Kesalahan tak terduga saat menerjemahkan: {exc}"
