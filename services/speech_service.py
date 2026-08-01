"""
services/speech_service.py
==========================================
Transkripsi suara ke teks.

TIGA METODE, DIURUTKAN DARI YANG PALING MUDAH
---------------------------------------------

1. **Google Speech Recognition gratis** (bawaan) — TANPA API key.
   Rekaman dikirim ke layanan pengenalan suara Google lewat pustaka
   `SpeechRecognition`. Cukup untuk pemakaian sehari-hari dan tidak perlu
   akun apa pun.

   Batasnya jujur: memakai titik akhir yang tidak resmi didokumentasikan
   Google, ada pembatasan jumlah permintaan, dan Google dapat
   menghentikannya sewaktu-waktu. Untuk alat bantu pembelajaran itu
   dapat diterima; untuk layanan yang harus selalu tersedia, tidak.

2. **Google Cloud Speech-to-Text** (opsional, butuh API key berbayar).
   Lebih andal DAN lebih akurat untuk istilah klinis, karena mendukung
   `speechContexts` — daftar frasa yang diprioritaskan. Tanpa itu
   "ortopnea" sering menjadi "orto nea" dan "JVP" menjadi "je vi pi",
   justru kata-kata yang paling menentukan hasil pencocokan diagnosis.

   Aktif hanya bila `GOOGLE_STT_API_KEY` diisi.

CATATAN PENTING SOAL API KEY
----------------------------
API key Google tidak berlaku lintas layanan. Key untuk Google Translate
TIDAK bisa dipakai untuk Speech-to-Text, dan sebaliknya — masing-masing
API harus diaktifkan tersendiri di Google Cloud Console. Bila key sudah
diisi tetapi transkripsi selalu gagal, kemungkinan besar API-nya belum
diaktifkan untuk key tersebut. Pesan galat dari Google diteruskan apa
adanya agar penyebabnya terlihat.

Metode gratis tetap berjalan meski key tidak ada, jadi fitur suara tidak
pernah benar-benar mati hanya karena urusan key.
"""

from __future__ import annotations

import io
from typing import Any

from core.config import google_stt_key, stt_language
from core.exceptions import SpeechError

_TIMEOUT = 30
_MAX_BYTES = 10 * 1024 * 1024

# Frasa yang diprioritaskan agar istilah klinis tidak salah ditranskripsi.
# Hanya berlaku pada metode Cloud (berbayar); metode gratis tidak
# menyediakan mekanisme serupa.
FRASA_KLINIS = [
    "dispnea", "ortopnea", "takipnea", "bradipnea", "hiperventilasi",
    "sianosis", "diaforesis", "edema", "asites", "JVP", "CRT",
    "ronkhi", "wheezing", "mengi", "sputum", "hemoptisis", "slem",
    "takikardia", "bradikardia", "aritmia", "palpitasi", "murmur",
    "hipotensi", "hipertensi", "hipoksemia", "hipovolemia", "hipervolemia",
    "oliguria", "poliuria", "disuria", "nokturia", "hematuria", "anuria",
    "afasia", "disartria", "apraksia", "parastesia", "klaudikasio",
    "ansietas", "letargi", "malaise", "anoreksia", "mual", "muntah",
    "EKG", "AGD", "GCS", "ABI", "SpO2", "saturasi oksigen",
    "IABP", "SIMV", "PEEP", "CRRT", "inotropik", "vasopresor",
    "asidosis", "alkalosis", "laktat", "kalium", "natrium",
    "nyeri dada", "sesak napas", "akral dingin", "turgor kulit",
]

METODE_GRATIS = "gratis"
METODE_CLOUD = "cloud"


# =====================================================
# KETERSEDIAAN
# =====================================================

def _punya_speech_recognition() -> bool:
    try:
        import speech_recognition  # noqa: F401

        return True
    except ImportError:
        return False


def punya_cloud_key() -> bool:
    return bool(google_stt_key())


def is_available() -> bool:
    """True bila setidaknya satu metode transkripsi dapat dipakai."""
    return _punya_speech_recognition() or punya_cloud_key()


def metode_aktif() -> str:
    """Metode yang akan dipakai bila `transcribe()` dipanggil sekarang."""
    if punya_cloud_key():
        return METODE_CLOUD
    return METODE_GRATIS


def keterangan_metode() -> str:
    if punya_cloud_key():
        return "Google Cloud Speech-to-Text (API key terpasang, akurasi istilah klinis lebih baik)"
    if _punya_speech_recognition():
        return "Google Speech Recognition gratis (tanpa API key)"
    return "Belum tersedia — pasang: pip install SpeechRecognition"


# =====================================================
# METODE 1 — GRATIS (tanpa API key)
# =====================================================

def _transcribe_gratis(audio_bytes: bytes, language: str) -> str:
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise SpeechError(
            "Pustaka SpeechRecognition belum terpasang. "
            "Install dengan: pip install SpeechRecognition"
        ) from exc

    pengenal = sr.Recognizer()
    try:
        with sr.AudioFile(io.BytesIO(audio_bytes)) as sumber:
            data = pengenal.record(sumber)
    except Exception as exc:
        raise SpeechError(
            "Format audio tidak dapat dibaca. Rekaman harus berupa WAV. "
            f"({exc})"
        ) from exc

    try:
        return pengenal.recognize_google(data, language=language)
    except sr.UnknownValueError:
        # Bukan galat: audio terkirim tapi tidak ada ucapan yang dikenali.
        return ""
    except sr.RequestError as exc:
        raise SpeechError(
            "Layanan pengenalan suara Google tidak dapat dihubungi. "
            "Periksa koneksi internet. Bila berulang, layanan gratisnya "
            f"mungkin sedang membatasi permintaan. ({exc})"
        ) from exc


# =====================================================
# METODE 2 — GOOGLE CLOUD (butuh API key)
# =====================================================

_ENDPOINT = "https://speech.googleapis.com/v1/speech:recognize"


def _encoding_for(mime: str) -> dict[str, Any]:
    mime = (mime or "").lower()
    if "webm" in mime:
        return {"encoding": "WEBM_OPUS", "sampleRateHertz": 48000}
    if "ogg" in mime or "opus" in mime:
        return {"encoding": "OGG_OPUS", "sampleRateHertz": 48000}
    if "flac" in mime:
        return {"encoding": "FLAC"}
    if "mp3" in mime or "mpeg" in mime:
        return {"encoding": "MP3", "sampleRateHertz": 44100}
    # WAV: sample rate sengaja tidak dikirim agar dibaca dari header berkas.
    return {"encoding": "LINEAR16"}


def _transcribe_cloud(audio_bytes: bytes, mime_type: str, language: str,
                      frasa_tambahan: list[str] | None) -> str:
    import base64

    try:
        import requests
    except ImportError as exc:
        raise SpeechError("Pustaka requests belum terpasang.") from exc

    config: dict[str, Any] = {
        "languageCode": language,
        "enableAutomaticPunctuation": True,
        "model": "latest_long",
        "speechContexts": [{
            "phrases": (FRASA_KLINIS + (frasa_tambahan or []))[:500],
            "boost": 15.0,
        }],
        **_encoding_for(mime_type),
    }
    payload = {
        "config": config,
        "audio": {"content": base64.b64encode(audio_bytes).decode("ascii")},
    }

    try:
        respons = requests.post(
            _ENDPOINT, params={"key": google_stt_key()}, json=payload, timeout=_TIMEOUT
        )
    except requests.Timeout as exc:
        raise SpeechError("Google Speech tidak merespons (timeout).") from exc
    except requests.RequestException as exc:
        raise SpeechError(f"Gagal menghubungi Google Speech: {exc}") from exc

    if respons.status_code != 200:
        rinci = ""
        try:
            rinci = respons.json().get("error", {}).get("message", "")
        except ValueError:
            rinci = respons.text[:200]
        raise SpeechError(
            f"Google Speech menolak permintaan: {rinci}\n\n"
            "Penyebab tersering: API 'Cloud Speech-to-Text' belum diaktifkan "
            "untuk key ini. API key Google tidak berlaku lintas layanan — "
            "key untuk Translate tidak bisa dipakai untuk Speech-to-Text."
        )

    hasil = respons.json().get("results") or []
    if not hasil:
        return ""

    potongan = []
    for item in hasil:
        alternatif = item.get("alternatives") or []
        if alternatif:
            potongan.append(str(alternatif[0].get("transcript", "")).strip())
    return " ".join(p for p in potongan if p).strip()


# =====================================================
# API PUBLIK
# =====================================================

def transcribe(
    audio_bytes: bytes,
    mime_type: str = "audio/wav",
    language: str | None = None,
    frasa_tambahan: list[str] | None = None,
) -> str:
    """
    Transkripsi audio menjadi teks. Melempar SpeechError bila gagal.

    Metode dipilih otomatis: Cloud bila API key ada, gratis bila tidak.
    """
    if not audio_bytes:
        raise SpeechError("Tidak ada audio untuk ditranskripsi.")
    if len(audio_bytes) > _MAX_BYTES:
        raise SpeechError(
            "Rekaman terlalu besar (maksimal sekitar 10 MB / 60 detik). "
            "Rekam dalam potongan yang lebih pendek."
        )

    bahasa = language or stt_language()

    if punya_cloud_key():
        return _transcribe_cloud(audio_bytes, mime_type, bahasa, frasa_tambahan)
    return _transcribe_gratis(audio_bytes, bahasa)


def transcribe_safe(audio_bytes: bytes, mime_type: str = "audio/wav") -> tuple[str, str]:
    """
    Versi yang tidak melempar exception.
    Mengembalikan (teks, pesan_galat) — salah satunya pasti kosong.
    """
    try:
        return transcribe(audio_bytes, mime_type), ""
    except SpeechError as exc:
        return "", str(exc)
    except Exception as exc:  # pragma: no cover
        return "", f"Kesalahan tak terduga saat transkripsi: {exc}"
