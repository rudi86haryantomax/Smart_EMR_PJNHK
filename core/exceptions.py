"""core/exceptions.py -- exception domain aplikasi asuhan."""


class AsuhanError(Exception):
    """Akar semua error aplikasi."""


class ValidationError(AsuhanError):
    """Input tidak memenuhi syarat."""


class NotFoundError(AsuhanError):
    """Entitas tidak ditemukan."""


class SpeechError(AsuhanError):
    """Kegagalan transkripsi suara."""
