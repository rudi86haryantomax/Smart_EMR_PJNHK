"""
repositories/sdki_repository.py
==========================================
Akses master data SDKI / SLKI / SIKI (standar 3S keperawatan PPNI).

KENAPA REPOSITORY, BUKAN IMPORT DICT LANGSUNG
---------------------------------------------
Sebelumnya data ini berupa `config/sdki_mappings.py` di smartcare-web dan
diimpor sebagai dict global (`SDKI_MASTER_MAPPING[...]`). Tiga masalah
dengan cara itu:

1. Setiap pembaruan konten klinis berarti menyunting file Python. Salah
   satu koma hilang -> seluruh aplikasi gagal start, bukan cuma fitur
   CDSS-nya.
2. Tim klinis yang memelihara isinya harus menyentuh kode.
3. Sumber datanya terkunci di satu bentuk. Memindahkannya ke tabel
   database nanti berarti menyunting setiap pemanggil.

Dengan repository, pemanggil cukup tahu `SdkiRepository`. Sumbernya bisa
diganti tanpa mengubah satu pun call-site.

URUTAN SUMBER DATA (yang pertama ketemu dipakai)
------------------------------------------------
1. `ASUHAN_SDKI_JSON=/path/ke/file.json` -- untuk memperbarui konten
   klinis TANPA redeploy kode. Ini jalur yang disarankan di produksi:
   tim klinis mengekspor dari Excel ke JSON, taruh di path itu, restart.
2. `data/sdki_slki_siki.json` -- data bawaan yang
   ikut ter-bundel bersama core.

CATATAN LISENSI & AKURASI KLINIS
--------------------------------
Isi data (kriteria diagnostik dan redaksi intervensi) merupakan adaptasi
kerja internal RSJPDHK, bukan salinan verbatim buku SDKI/SLKI/SIKI PPNI.
Repository ini TIDAK memvalidasi kebenaran klinisnya -- verifikasi
terhadap buku resmi PPNI tetap tanggung jawab tim keperawatan sebelum
dipakai sebagai acuan legal atau audit klinis.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from core.config import BASE_DIR, sdki_json_path
from core.exceptions import NotFoundError
from core.kategori import kategori_dari_luaran, urutkan_prioritas

_DEFAULT_JSON = sdki_json_path()
_INDIKATOR_JSON = BASE_DIR / "data" / "indikator_slki.json"
_LOAD_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None

# Kata yang terlalu umum untuk dipakai sebagai penanda diagnosis. Tanpa
# daftar ini, `suggest()` akan mencocokkan hampir semua diagnosis pada
# teks apa pun karena kata seperti "menurun" muncul di mana-mana.
_STOPWORDS = {
    "yang", "dan", "atau", "pada", "dari", "dengan", "untuk", "tidak",
    "lebih", "kurang", "saat", "dalam", "akibat", "tampak", "merasa",
    "mengeluh", "menurun", "meningkat", "berubah", "abnormal", "normal",
    "sulit", "mampu", "sering", "jika", "perlu", "tanda", "gejala",
    "kondisi", "pasien", "terkait", "secara", "berlebihan", "bagian",
}


def _load_source() -> dict[str, Any]:
    """Baca dokumen JSON dari sumber yang tersedia."""
    override = os.environ.get("ASUHAN_SDKI_JSON")
    candidates = [Path(override)] if override else []
    candidates.append(_DEFAULT_JSON)

    for path in candidates:
        if path and path.exists():
            with path.open(encoding="utf-8") as handle:
                doc = json.load(handle)
            doc.setdefault("meta", {})["_sumber_file"] = str(path)
            return doc

    raise FileNotFoundError(
        "Data SDKI tidak ditemukan. Set ASUHAN_SDKI_JSON atau pastikan "
        f"{_DEFAULT_JSON} ada."
    )


def _get_document() -> dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        with _LOAD_LOCK:
            if _CACHE is None:
                _CACHE = _load_source()
    return _CACHE


def reload_data() -> None:
    """
    Buang cache supaya perubahan file JSON terbaca tanpa restart proses.
    Berguna saat tim klinis memperbarui konten di lingkungan yang tidak
    bisa sering di-restart.
    """
    global _CACHE
    with _LOAD_LOCK:
        _CACHE = None
    _tokenize.cache_clear()


# Perawat menulis asesmen dengan bahasa sehari-hari, sedangkan kriteria
# SDKI memakai istilah baku. Tanpa pemetaan ini, "kaki bengkak" tidak
# pernah cocok dengan kriteria "Edema", dan "sesak" tidak cocok dengan
# "Dispnea" -- padahal maksudnya sama. Arah pemetaan: sehari-hari -> baku.
# Perawat menulis asesmen dengan bahasa sehari-hari dan singkatan ruangan,
# sedangkan kriteria SDKI memakai istilah baku. Tanpa pemetaan ini,
# "slem kental" tidak pernah cocok dengan kriteria "Sputum berlebih", dan
# "Kalium 2,9" tidak memicu Risiko Ketidakseimbangan Elektrolit — padahal
# maksudnya jelas bagi siapa pun yang membaca.
#
# Arah pemetaan: istilah yang DITULIS -> istilah yang ADA DI KRITERIA.
# Hanya padanan yang maknanya setara secara klinis yang dimasukkan;
# menambah kata yang cuma "berhubungan" akan membuat hampir semua
# diagnosis cocok dengan hampir semua teks, dan usulannya jadi tidak
# berguna.
_SINONIM = {
    # --- pernapasan ---
    "sesak": "dispnea",
    "nafas": "napas",
    "slem": "sputum",
    "slem kental": "sputum",
    "dahak": "sputum",
    "sekret": "sputum",
    "lendir": "sputum",
    "mengi": "wheezing",
    "biru": "sianosis",
    "kebiruan": "sianosis",
    "baring": "ortopnea",
    "berbaring": "ortopnea",
    "terlentang": "ortopnea",
    "intubasi": "ventilasi",
    "ventilator": "ventilasi",
    "simv": "ventilasi",
    "peep": "ventilasi",
    "fio": "oksigen",
    "ekstubasi": "penyapihan",
    "weaning": "penyapihan",

    # --- sirkulasi ---
    "berdebar": "palpitasi",
    "bengkak": "edema",
    "sembab": "edema",
    "asites": "edema",
    "inotropik": "kontraktilitas",
    "vasopresor": "hipotensi",
    "norepinefrin": "hipotensi",
    "dobutamin": "kontraktilitas",
    "laktat": "hipoperfusi",
    "iabp": "curah",
    "syok": "hipotensi",

    # --- cairan & elektrolit ---
    "anuria": "oliguria",
    "kalium": "elektrolit",
    "hipokalemia": "elektrolit",
    "hiperkalemia": "elektrolit",
    "natrium": "elektrolit",
    "magnesium": "elektrolit",
    "kalsium": "elektrolit",
    "balance": "cairan",
    "crrt": "ginjal",
    "hemodialisis": "ginjal",
    "dialisis": "ginjal",
    "kencing": "urin",
    "berkemih": "urin",

    # --- asam basa ---
    "asidosis": "ph",
    "alkalosis": "ph",
    "agd": "ph",

    # --- infeksi ---
    "kultur": "patogen",
    "pneumonia": "patogen",
    "sepsis": "patogen",
    "leukositosis": "leukosit",
    "antibiotik": "patogen",
    "demam": "demam",
    "panas": "demam",

    # --- umum ---
    "lemas": "lelah",
    "letih": "lelah",
    "keletihan": "lelah",
    "kelelahan": "lelah",
    "capek": "lelah",
    "muntah": "mual",
    "pingsan": "sinkop",
    "luka": "jaringan",
    "infus": "intravena",
    "jalan": "berjalan",
    "gerak": "pergerakan",
}

# Singkatan klinis yang panjangnya <= 3 huruf. Tanpa daftar ini semuanya
# terbuang oleh filter panjang minimum, padahal justru ini yang paling
# sering dipakai di catatan keperawatan kardiovaskular.
_SINGKATAN_PENTING = {
    # Kardio-respirasi
    "jvp", "ekg", "abi", "crt", "gcs", "tik", "agd", "imt", "rom",
    "asi", "pnd", "svr", "pvr", "bak", "bab", "hb", "ht", "ph",
    # Perawatan intensif — sering muncul di catatan ICU dan justru
    # paling menentukan, tapi akan terbuang oleh filter panjang minimum.
    "iabp", "simv", "peep", "crrt", "ards", "ett", "cvp", "map",
    "vte", "hr", "rr", "td",
    # Penanda laboratorium: gabungan huruf-angka, sebagian hanya 3 karakter
    # sehingga perlu didaftarkan eksplisit.
    "po2", "ph", "abg", "be", "bun",
}


@lru_cache(maxsize=2048)
def _tokenize(text: str) -> frozenset[str]:
    r"""
    Pecah teks menjadi kata kunci untuk pencocokan.

    Pola `[a-z]+\d*` (huruf boleh diikuti angka) BUKAN sekadar `[a-z]+`.
    Alasannya penting: penanda laboratorium seperti PCO2, PO2, HCO3, FiO2,
    dan SpO2 adalah gabungan huruf-angka. Dengan pola lama, "PCO2" terbaca
    "pco" — hanya tiga huruf, lalu terbuang oleh batas panjang minimum.
    Padahal justru nilai-nilai itulah yang mendefinisikan Gangguan
    Pertukaran Gas, sehingga diagnosis tersebut nyaris tidak pernah muncul
    pada kasus dengan hasil analisis gas darah.

    Angka murni (nilai pengukuran seperti "50" atau "7,20") tetap dibuang:
    angkanya berubah-ubah antar-pasien dan tidak menandai diagnosis apa pun.
    """
    words = re.findall(r"[a-z]+\d*", str(text).lower())
    out: set[str] = set()
    for word in words:
        if word in _STOPWORDS:
            continue
        if word in _SINGKATAN_PENTING:
            out.add(word)
            continue
        if len(word) <= 3:
            continue
        out.add(_SINONIM.get(word, word))
    return frozenset(out)


class SdkiRepository:
    """
    Repository read-only untuk master data 3S.

    Berbeda dari repository lain di paket ini, konstruktornya tidak
    memerlukan koneksi database karena sumbernya berkas JSON. Parameter
    `conn` tetap diterima agar bentuk pemanggilannya seragam dengan
    repository lain -- dan agar suatu saat implementasinya bisa beralih ke
    tabel database tanpa mengubah call-site.
    """

    def __init__(self, conn=None):
        self.conn = conn
        self._cache_bobot: dict[str, float] | None = None
        self._cache_indikator: dict[str, Any] | None = None

    # =================================================
    # META
    # =================================================

    @property
    def meta(self) -> dict[str, Any]:
        return dict(_get_document().get("meta", {}))

    def _entries(self) -> list[dict[str, Any]]:
        return _get_document().get("diagnosis", [])

    def _index(self) -> dict[str, dict[str, Any]]:
        return {e["kode"]: e for e in self._entries()}

    # =================================================
    # FINDER
    # =================================================

    def find(self, kode: str) -> dict[str, Any] | None:
        """Diagnosis lengkap berdasarkan kode, atau None."""
        if not kode:
            return None
        return self._index().get(str(kode).strip().upper())

    def get(self, kode: str) -> dict[str, Any]:
        """Sama seperti find(), tapi melempar NotFoundError kalau tidak ada."""
        entry = self.find(kode)
        if not entry:
            raise NotFoundError(f"Diagnosis '{kode}' tidak ditemukan di master SDKI.")
        return entry

    def exists(self, kode: str) -> bool:
        return self.find(kode) is not None

    def get_name(self, kode: str, default: str = "") -> str:
        entry = self.find(kode)
        return entry["nama"] if entry else default

    def all(self) -> list[dict[str, Any]]:
        return list(self._entries())

    def all_codes(self) -> list[str]:
        return [e["kode"] for e in self._entries()]

    def count(self) -> int:
        return len(self._entries())

    # =================================================
    # FILTER
    # =================================================

    def by_jenis(self, jenis: str) -> list[dict[str, Any]]:
        """'Aktual' atau 'Risiko'."""
        target = str(jenis).strip().lower()
        return [e for e in self._entries() if str(e.get("jenis", "")).lower() == target]

    def by_luaran(self, kode_slki: str) -> list[dict[str, Any]]:
        """Semua diagnosis yang bermuara ke satu luaran SLKI."""
        target = str(kode_slki).strip().upper()
        return [e for e in self._entries() if e.get("luaran", {}).get("kode") == target]

    def sdki_only(self) -> list[dict[str, Any]]:
        """Hanya diagnosis berkode SDKI resmi (D.xxxx)."""
        return [e for e in self._entries() if e.get("is_sdki")]

    def lokal_only(self) -> list[dict[str, Any]]:
        """
        Diagnosis tambahan hasil kesepakatan internal yang tidak ada di
        SDKI (mis. versi 'aktual' dari diagnosis yang di SDKI hanya
        tersedia sebagai 'risiko'). Ditandai kode LOKAL.xxx.
        """
        return [e for e in self._entries() if not e.get("is_sdki")]

    def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """Cari berdasarkan kode atau nama diagnosis."""
        term = str(keyword or "").strip().lower()
        if not term:
            return []

        exact, partial = [], []
        for entry in self._entries():
            nama = entry["nama"].lower()
            kode = entry["kode"].lower()
            if term == kode or term == nama:
                exact.append(entry)
            elif term in kode or term in nama:
                partial.append(entry)
        return (exact + partial)[:limit]

    # =================================================
    # BAGIAN SPESIFIK
    # =================================================

    def get_luaran(self, kode: str) -> dict[str, Any]:
        entry = self.find(kode)
        return dict(entry.get("luaran", {})) if entry else {}

    def get_kriteria(self, kode: str) -> dict[str, list[str]]:
        entry = self.find(kode)
        return dict(entry.get("kriteria", {})) if entry else {}

    def get_intervensi(self, kode: str, kategori: str | None = None) -> Any:
        """
        Intervensi SIKI. Tanpa `kategori` mengembalikan seluruh dict
        (observasi/terapeutik/edukasi/kolaborasi); dengan kategori
        mengembalikan list untuk kategori tersebut saja.
        """
        entry = self.find(kode)
        if not entry:
            return [] if kategori else {}
        intervensi = entry.get("intervensi", {})
        if kategori:
            return list(intervensi.get(str(kategori).strip().lower(), []))
        return {k: list(v) for k, v in intervensi.items()}

    def get_catatan(self, kode: str) -> str | None:
        entry = self.find(kode)
        return entry.get("catatan") if entry else None

    def get_terkait(self, kode: str) -> list[dict[str, Any]]:
        """Diagnosis lain yang berhubungan (mis. pasangan risiko <-> aktual)."""
        entry = self.find(kode)
        if not entry:
            return []
        return [self.find(k) for k in entry.get("terkait", []) if self.find(k)]

    def status_verifikasi(self, kode: str) -> str:
        """
        Status verifikasi terhadap SDKI resmi, dari kolom berkas mapping.

        Entri bertanda 'PERLU VERIFIKASI' adalah diagnosis yang belum
        dipastikan kesesuaiannya dengan buku SDKI PPNI -- umumnya
        diagnosis tambahan hasil kesepakatan internal. Ini perlu terlihat
        oleh perawat saat memilih, bukan hanya tersimpan di data.
        """
        entry = self.find(kode)
        return str(entry.get("status_verifikasi", "")) if entry else ""

    def perlu_verifikasi(self, kode: str) -> bool:
        return "VERIFIKASI" in self.status_verifikasi(kode).upper()

    # =================================================
    # INDIKATOR LUARAN (SLKI)
    # =================================================
    # Indikator disimpan di berkas TERPISAH (data/indikator_slki.json),
    # bukan di dalam master 3S. Alasannya: keduanya berubah dengan irama
    # berbeda. Master 3S mengikuti buku SDKI/SLKI/SIKI dan jarang berubah,
    # sedangkan target dan waktu evaluasi kerap disesuaikan dengan
    # kebijakan unit. Memisahkannya membuat pembaruan salah satu tidak
    # berisiko merusak yang lain.

    def _indikator_doc(self) -> dict[str, Any]:
        if self._cache_indikator is None:
            berkas = Path(os.environ.get("ASUHAN_INDIKATOR_JSON", "")) \
                if os.environ.get("ASUHAN_INDIKATOR_JSON") else _INDIKATOR_JSON
            try:
                self._cache_indikator = json.loads(berkas.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                # Aplikasi tetap berjalan tanpa berkas indikator; bagian
                # ini hanya melengkapi, bukan menentukan alur utama.
                self._cache_indikator = {"meta": {}, "luaran": {}}
        return self._cache_indikator

    def indikator(self, kode: str) -> list[dict[str, Any]]:
        """
        Daftar indikator terukur untuk luaran diagnosis ini.

        Menerima kode diagnosis (D.xxxx) maupun kode luaran (L.xxxxx),
        supaya pemanggil tidak perlu menerjemahkan sendiri.
        """
        kode = str(kode or "").strip().upper()
        kode_luaran = kode if kode.startswith("L.") else self.get_luaran(kode).get("kode", "")
        entri = self._indikator_doc().get("luaran", {}).get(kode_luaran)
        return list(entri.get("indikator", [])) if entri else []

    def evaluasi_default(self, kode: str) -> str:
        """Saran waktu evaluasi untuk luaran ini."""
        kode = str(kode or "").strip().upper()
        kode_luaran = kode if kode.startswith("L.") else self.get_luaran(kode).get("kode", "")
        entri = self._indikator_doc().get("luaran", {}).get(kode_luaran)
        return str(entri.get("evaluasi_default", "24 jam")) if entri else "24 jam"

    def arah_luaran(self, kode: str) -> str:
        """'meningkat', 'menurun', atau 'membaik' — menentukan makna skala 1-5."""
        kode = str(kode or "").strip().upper()
        kode_luaran = kode if kode.startswith("L.") else self.get_luaran(kode).get("kode", "")
        entri = self._indikator_doc().get("luaran", {}).get(kode_luaran)
        return str(entri.get("arah", "")) if entri else ""

    def catatan_luaran(self, kode: str) -> str:
        kode = str(kode or "").strip().upper()
        kode_luaran = kode if kode.startswith("L.") else self.get_luaran(kode).get("kode", "")
        entri = self._indikator_doc().get("luaran", {}).get(kode_luaran)
        return str(entri.get("catatan", "")) if entri else ""

    def punya_indikator(self, kode: str) -> bool:
        return bool(self.indikator(kode))

    @property
    def meta_indikator(self) -> dict[str, Any]:
        return dict(self._indikator_doc().get("meta", {}))

    def kategori(self, kode: str) -> str:
        """Kategori SDKI, diturunkan dari prefix kode luaran SLKI."""
        return kategori_dari_luaran(self.get_luaran(kode).get("kode"))

    def urutkan(self, kode_list: list[str]) -> list[dict[str, Any]]:
        """Urutkan daftar kode sesuai usulan prioritas klinis (ABC lebih dulu)."""
        entries = [e for e in (self.find(k) for k in kode_list) if e]
        return urutkan_prioritas(entries)

    def flat_intervensi(self, kode: str) -> list[str]:
        """Seluruh tindakan dalam satu list datar, untuk checklist di UI."""
        intervensi = self.get_intervensi(kode)
        out: list[str] = []
        for kategori in ("observasi", "terapeutik", "edukasi", "kolaborasi"):
            out.extend(intervensi.get(kategori, []))
        return out

    # =================================================
    # PENCOCOKAN UNTUK CDSS
    # =================================================

    def _bobot_kata(self) -> dict[str, float]:
        """
        Bobot tiap kata berdasarkan kelangkaannya di seluruh master 3S.

        Kata yang muncul di BANYAK diagnosis hampir tidak membedakan apa
        pun. "Gelisah" ada di belasan diagnosis; kalau dihitung sama
        beratnya dengan "sputum" atau "PCO2" — yang hanya muncul di satu
        dua diagnosis — maka diagnosis yang kebetulan memuat kata umum
        akan naik peringkat tanpa alasan klinis, dan mendesak turun
        diagnosis yang cocok pada temuan yang benar-benar menentukan.

        Itu bukan dugaan: pada kasus ICU dengan hasil AGD dan sputum
        purulen, "Nyeri Akut" sempat menempati peringkat lebih tinggi
        daripada "Bersihan Jalan Napas Tidak Efektif" semata-mata karena
        cocok pada "dingin" dan "gelisah".

        Bobotnya memakai gagasan inverse document frequency: makin sedikit
        diagnosis yang memuat sebuah kata, makin besar bobotnya.
        """
        if self._cache_bobot is None:
            jumlah_dok: dict[str, int] = {}
            entri = self._entries()
            for e in entri:
                kriteria = e.get("kriteria", {})
                kantong = list(kriteria.get("mayor", []))
                kantong += kriteria.get("minor", [])
                kantong += kriteria.get("faktor_risiko", [])
                kantong.append(e.get("nama", ""))
                for kata in _tokenize(" ".join(kantong)):
                    jumlah_dok[kata] = jumlah_dok.get(kata, 0) + 1

            total = max(len(entri), 1)
            self._cache_bobot = {
                kata: math.log(1 + total / n) for kata, n in jumlah_dok.items()
            }
        return self._cache_bobot

    def suggest(
        self,
        text: str,
        limit: int = 5,
        min_score: float = 0.06,
    ) -> list[dict[str, Any]]:
        """
        Usulkan diagnosis berdasarkan kemiripan teks asesmen dengan
        kriteria diagnostik.

        PERINGATAN: ini pencocokan kata kunci berbobot, BUKAN penalaran
        klinis. Hasilnya adalah kandidat untuk dipertimbangkan perawat,
        bukan penegakan diagnosis. Skornya berguna untuk mengurutkan
        kandidat, tidak bermakna sebagai ukuran kepastian.

        Mengembalikan list dict berisi `diagnosis`, `skor`, dan
        `kata_cocok` supaya UI bisa menjelaskan DASAR usulannya — saran
        tanpa alasan sulit dievaluasi oleh perawat.
        """
        tokens = _tokenize(text)
        if not tokens:
            return []

        bobot = self._bobot_kata()
        # Kata yang tidak ada di master sama sekali diberi bobot netral.
        bobot_bawaan = math.log(1 + len(self._entries()))

        scored = []
        for entry in self._entries():
            kriteria = entry.get("kriteria", {})
            bag: list[str] = []
            for key in ("mayor", "minor", "faktor_risiko"):
                bag.extend(kriteria.get(key, []))
            bag.append(entry.get("nama", ""))

            entry_tokens = _tokenize(" ".join(bag))
            if not entry_tokens:
                continue

            matched = tokens & entry_tokens
            if not matched:
                continue

            # Jumlahkan bobot kata yang cocok, lalu dinormalisasi terhadap
            # akar jumlah token kriteria supaya diagnosis dengan daftar
            # kriteria panjang tidak otomatis unggul hanya karena panjang.
            nilai = sum(bobot.get(k, bobot_bawaan) for k in matched)
            score = nilai / (len(entry_tokens) ** 0.5)

            if score >= min_score:
                scored.append({
                    "diagnosis": entry,
                    "kode": entry["kode"],
                    "nama": entry["nama"],
                    "skor": round(score, 4),
                    # Kata paling menentukan ditaruh di depan agar perawat
                    # langsung melihat dasar terkuat usulan ini.
                    "kata_cocok": sorted(
                        matched, key=lambda k: -bobot.get(k, bobot_bawaan)
                    ),
                })

        scored.sort(key=lambda x: (-x["skor"], x["kode"]))
        return scored[:limit]

    # =================================================
    # KOMPATIBILITAS DENGAN KODE LAMA
    # =================================================
    # Dua mapping di bawah menggantikan SDKI_NAME_MAPPING dan
    # DX_TO_SLKI_MAPPING dari config/sdki_mappings.py, tapi diturunkan
    # dari sumber yang sama sehingga tidak bisa lagi tidak sinkron.
    # Pada file lama keduanya ditulis terpisah dan jumlahnya sudah berbeda
    # (55 vs 58) -- artinya ada entri yang punya pemetaan SLKI tapi tidak
    # punya nama, atau sebaliknya.

    def name_mapping(self) -> dict[str, str]:
        return {e["kode"]: e["nama"] for e in self._entries()}

    def dx_to_slki(self) -> dict[str, str]:
        return {
            e["kode"]: e.get("luaran", {}).get("kode", "")
            for e in self._entries()
            if e.get("luaran", {}).get("kode")
        }

    def slki_name_mapping(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for entry in self._entries():
            luaran = entry.get("luaran", {})
            if luaran.get("kode"):
                out.setdefault(luaran["kode"], luaran.get("nama", ""))
        return out

    # =================================================
    # PEMELIHARAAN DATA
    # =================================================

    def validate(self) -> dict[str, Any]:
        """
        Periksa integritas master data. Dipakai di test dan bisa
        dijalankan setelah tim klinis memperbarui JSON, supaya kesalahan
        ketik ketahuan sebelum masuk produksi.
        """
        problems: list[str] = []
        seen: set[str] = set()

        for entry in self._entries():
            kode = entry.get("kode", "")
            if not kode:
                problems.append("Ada entri tanpa kode.")
                continue
            if kode in seen:
                problems.append(f"{kode}: kode duplikat.")
            seen.add(kode)

            if not entry.get("nama"):
                problems.append(f"{kode}: nama diagnosis kosong.")
            if not entry.get("luaran", {}).get("kode"):
                problems.append(f"{kode}: luaran SLKI kosong.")

            kriteria = entry.get("kriteria", {})
            jenis = str(entry.get("jenis", "")).lower()
            if jenis == "risiko" and not kriteria.get("faktor_risiko"):
                problems.append(f"{kode}: jenis Risiko tapi faktor risiko kosong.")
            if jenis == "aktual" and not kriteria.get("mayor"):
                problems.append(f"{kode}: jenis Aktual tapi kriteria mayor kosong.")

            intervensi = entry.get("intervensi", {})
            for kategori in ("observasi", "terapeutik", "edukasi", "kolaborasi"):
                if kategori not in intervensi:
                    problems.append(f"{kode}: kategori intervensi '{kategori}' tidak ada.")
            if not intervensi.get("observasi"):
                problems.append(f"{kode}: intervensi observasi kosong.")

            for ref in entry.get("terkait", []):
                if ref not in {e.get("kode") for e in self._entries()}:
                    problems.append(f"{kode}: referensi terkait '{ref}' tidak dikenal.")

        return {
            "valid": not problems,
            "jumlah": len(self._entries()),
            "masalah": problems,
        }

    def export_json(self, path: str | Path) -> Path:
        """
        Tulis salinan master data ke berkas. Dipakai sebagai titik awal
        saat tim klinis ingin menyunting konten lalu memakainya lewat
        ASUHAN_SDKI_JSON.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(_get_document(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return target


__all__ = ["SdkiRepository", "reload_data"]
