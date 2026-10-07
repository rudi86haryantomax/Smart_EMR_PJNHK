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
    # Kelainan jantung bawaan yang DISEBUT di teks kriteria ("mis. VSD besar"
    # pada Risiko Penurunan Curah Jantung). Tanpa ini "VSD" terbuang (3 huruf)
    # dan kecocokannya hanya kebetulan lewat kata umum "besar".
    "vsd",
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


# =====================================================================
# PENALARAN ARAH & NUMERIK UNTUK PENCOCOKAN KRITERIA
# =====================================================================
# Kenapa ini ada: pencocokan token murni membuang kata arah ("meningkat",
# "menurun") sebagai stopword. Untuk tanda vital & lab, ARAH itulah
# diagnosisnya -- "Tekanan darah menurun" (Hipovolemia) vs "meningkat"
# jadi tak terbedakan, sehingga pasien hipertensi bisa keliru cocok
# Hipovolemia. Bagian ini mengembalikan arah ke dalam pencocokan, HANYA
# untuk kriteria yang memang menyangkut ukuran terukur.

_ARAH_NAIK = {
    "meningkat", "peningkatan", "naik", "kenaikan", "tinggi", "menaik",
    "bertambah", "melebar", "memanjang", "cepat", "kuat",
}
_ARAH_TURUN = {
    "menurun", "penurunan", "turun", "rendah", "menyempit", "memendek",
    "lambat", "berkurang",
}

# Kata yang boleh menyertai pernyataan vital "murni" tanpa membatalkannya
# sebagai kriteria terukur (mis. "FREKUENSI nadi meningkat", "TD ARTERI").
# Kalau selain penanda-ukuran + arah + kata-kata ini masih ada sisa kata,
# berarti kriterianya bukan sekadar nilai vital (mis. "otot bantu napas")
# dan tak boleh diperlakukan sebagai konfirmasi terukur.
_ALLOW_VITAL = {"frekuensi", "arteri", "perifer", "tubuh"}

# Tanda satu-kata yang sudah memuat arahnya sendiri.
_SIGN_IMPLISIT: dict[str, tuple[str, str]] = {
    "takikardia": ("hr", "naik"), "takikardi": ("hr", "naik"),
    "bradikardia": ("hr", "turun"), "bradikardi": ("hr", "turun"),
    "takipnea": ("rr", "naik"), "takipneu": ("rr", "naik"),
    "bradipnea": ("rr", "turun"),
    "hipoksemia": ("spo2", "turun"), "hipoksia": ("spo2", "turun"),
    "hipertermia": ("suhu", "naik"), "demam": ("suhu", "naik"),
    "febris": ("suhu", "naik"), "hipotermia": ("suhu", "turun"),
    "hipertensi": ("td", "naik"), "hipotensi": ("td", "turun"),
}

# Kata penanda tiap ukuran. "frekuensi" sengaja TIDAK dipakai sendiri
# karena ambigu (nadi vs napas); yang menentukan nomina di sebelahnya.
# "jantung" dihindari (muncul di "gagal jantung", "curah jantung").
_UKURAN_PENANDA: dict[str, set[str]] = {
    "td": {"tekanan", "darah", "sistolik", "diastolik", "tensi", "mmhg", "map"},
    "hr": {"nadi", "denyut", "palpitasi"},
    "rr": {"napas", "pernapasan", "respirasi", "ventilasi"},
    "spo2": {"saturasi", "oksigen", "oksigenasi", "sianosis"},
    "suhu": {"suhu", "temperatur", "temperature"},
}

# Diagnosis yang MENSYARATKAN konteks/alat tertentu yang tak bisa
# disimpulkan dari tanda vital saja. Tanpa jangkar ini, derangemen napas
# pada pasien mana pun keliru memunculkan diagnosis seputar ventilator
# mekanik. Bila tak satu pun kata jangkar muncul di asesmen, diagnosisnya
# ditekan jauh -- tetap tampil kalau memang tak ada saingan, tapi tak lagi
# merebut peringkat atas.
_JANGKAR: dict[str, set[str]] = {
    "D.0002": {"ventilator", "ventilasi", "mekanik", "ett", "endotrakeal",
               "weaning", "penyapihan", "cpap", "bipap", "ippv",
               "simv", "peep"},
    "D.0004": {"ventilator", "ventilasi", "mekanik", "ett", "endotrakeal",
               "intubasi", "otot bantu", "gagal napas", "cpap", "bipap", "pco",
               "simv", "peep"},
    # Diagnosis konteks maternal/neonatal: hanya relevan bila asesmen memang
    # tentang menyusui / tumbuh-kembang anak. Tanpa penanda ini, kata umum
    # ("naik", "nyeri") membuatnya salah muncul pada pasien dewasa kardiak.
    # (PJNHK menangani bayi jantung, jadi diagnosis TIDAK dibuang -- hanya
    # dijangkar ke konteksnya.)
    "D.0029": {"menyusu", "laktasi", "payudara", "puting", "perlekatan"},
    # "Berat Badan Lebih" (kelebihan berat) sering salah muncul pada anak
    # gagal-tumbuh/kurus karena kata umum "berat badan/anak". Butuh penanda
    # kelebihan berat -- kalau tidak ada, tekan.
    "D.0018": {"gemuk", "obesitas", "obese", "overweight", "kelebihan berat",
               "imt", "indeks massa", "lipatan kulit", "kegemukan", "berlebih"},
    "D.0106": {"tumbuh kembang", "perkembangan", "milestone", "balita", "bayi",
               "neonatus", "prematur", "pengasuhan", "stimulasi"},
    "D.0108": {"pertumbuhan", "tumbuh kembang", "balita", "bayi", "neonatus",
               "prematur", "bblr", "stunting", "gagal tumbuh"},
}

_LABEL_UKURAN = {"td": "tekanan darah", "hr": "frekuensi nadi",
                 "rr": "frekuensi napas", "spo2": "saturasi", "suhu": "suhu"}
_LABEL_ARAH = {"naik": "tinggi", "turun": "rendah"}

# Ambang bobot IDF di mana sebuah kata dianggap cukup spesifik untuk
# menopang kecocokan kriteria seorang diri (mis. "jvp", "sputum").
_IDF_SPESIFIK = 3.0

# Kata yang hanya menyatakan LETAK, BAGIAN TUBUH, WAKTU, UKURAN/ARAH, atau
# kata umum -- bukan temuan klinis. Kata ini tetap dihitung bila kriterianya
# terpenuhi lewat kata lain (atau separuh katanya cocok), tetapi TIDAK boleh
# SENDIRIAN memenuhi sebuah kriteria lewat jalur `_IDF_SPESIFIK`.
#
# Kenapa perlu: dengan 59 diagnosis, ~89% kosakata master lolos ambang IDF
# 3.0, jadi kata apa pun bisa jadi "bukti tunggal". Akibatnya pada pasien
# STEMI dewasa, "nyeri dada KIRI" memenuhi "Kelainan struktur jantung
# kongenital (... jantung KIRI hipoplastik)", "ronkhi basal kedua PARU"
# memenuhi "mesin pintas jantung PARU", dan "TD TINGGI" memenuhi "Kadar
# kolesterol TINGGI". Kata klinis yang bermakna sendiri (ginjal, hati,
# terpasang, selang, operasi, jvp, sputum, ...) sengaja TIDAK dimasukkan.
_KATA_UMUM = frozenset({
    # letak, posisi & bagian tubuh (TEMPAT temuan, bukan temuannya)
    "kiri", "kanan", "atas", "bawah", "anterior", "posterior", "samping",
    "posisi", "basal", "paru", "kepala", "mata", "mulut", "muka", "wajah",
    "hidung", "rongga", "thoraks", "tulang", "sendi", "kulit", "mukosa",
    "usus", "kandung",
    # waktu & urutan
    "hari", "bulan", "tahun", "sehari", "seminggu", "kali", "satu",
    "pertama", "terakhir", "baru", "lama", "lanjut", "sudah", "pernah",
    "sebelum", "sesudah", "selama", "sampai", "tiba", "timbul", "muncul",
    # ukuran, arah & mutu (pengubah -- temuannya ada di kata lain)
    "tinggi", "rendah", "naik", "turun", "cepat", "lambat", "besar", "kecil",
    "panjang", "minimal", "banyak", "jumlah", "cukup", "penuh", "terlalu",
    "baik", "buruk", "kering", "warna", "dosis", "frekuensi",
    # orang & kata umum
    "anak", "bayi", "dewasa", "orang", "lain", "sendiri", "mesin", "alat",
    "obat", "nilai", "hasil", "status", "umum", "khas", "tertentu", "utuh",
    "antara", "tentang", "sesuai", "masuk",
})

# Frasa kriteria yang menyatakan KETIADAAN tanda ("Nadi tidak teraba",
# "Tidak ada napas"). Karena "tidak" ikut terbuang stopword, tanpa penjaga
# ini kriteria KETIADAAN justru cocok pada temuan yang JUSTRU ADA. Kriteria
# seperti ini tak bisa diverifikasi lewat tumpang-tindih kata. (Sengaja
# spesifik: TIDAK menyaring "tidak efektif"/"tidak mampu" -- temuan positif.)
_NEGASI_ABSEN = (
    "tidak teraba", "tidak ada", "tidak terukur", "tidak sadar",
    "tidak bernapas", "tidak terdengar", "tidak tampak",
)


def _ukur_kriteria(kriteria: str) -> tuple[str | None, str | None]:
    """
    Dari SATU teks kriteria SDKI, kembalikan (ukuran, arah) bila kriteria
    itu menyangkut tanda terukur -- mis. "Tekanan darah menurun" ->
    ("td", "turun"), "Takikardia" -> ("hr", "naik"). Kalau bukan kriteria
    terukur, kembalikan (None, None) dan biarkan jalur token menanganinya.
    """
    teks = kriteria.lower()
    kata = set(re.findall(r"[a-z]+", teks))

    for w, (uk, ar) in _SIGN_IMPLISIT.items():
        if w in kata:
            # Hanya bila kriterianya memang pernyataan tanda itu sendiri
            # (mis. "Takikardia"), BUKAN kata yang kebetulan muncul di dalam
            # frasa panjang (mis. "Takipnea ... saat menyusu" di diagnosis
            # menyusui). Kalau tidak murni, biarkan jalur token menilainya.
            sisa = kata - {w} - _ARAH_NAIK - _ARAH_TURUN - _ALLOW_VITAL
            if len(sisa) <= 1:
                return uk, ar

    if kata & _ARAH_NAIK:
        arah = "naik"
    elif kata & _ARAH_TURUN:
        arah = "turun"
    else:
        return None, None

    for uk, penanda in _UKURAN_PENANDA.items():
        if kata & penanda:
            # Hanya pernyataan vital "murni": kalau masih ada kata lain di
            # luar penanda-ukuran + arah + kata sambung yang diizinkan,
            # kriterianya bukan sekadar nilai vital -- biarkan jalur token.
            sisa = kata - penanda - _ARAH_NAIK - _ARAH_TURUN - _ALLOW_VITAL
            if sisa:
                return None, None
            return uk, arah
    return None, None


def _ekstrak_vital(teks: str) -> dict[str, str]:
    """
    Simpulkan arah tiap tanda vital dari teks asesmen: 'naik'/'turun'/'normal'.
    Menggabungkan ANGKA ("TD 150/90", "SpO2 91%") dan FRASA ("nadi
    meningkat", "takikardia"). Angka diprioritaskan.
    """
    t = teks.lower()
    arah: dict[str, str] = {}

    m = re.search(r"(?:td|tekanan darah|tensi)\D{0,15}?(\d{2,3})\s*/\s*(\d{2,3})", t) \
        or re.search(r"(\d{2,3})\s*/\s*(\d{2,3})\s*mmhg", t)
    if m:
        sis, dia = int(m.group(1)), int(m.group(2))
        arah["td"] = "naik" if (sis >= 140 or dia >= 90) else "turun" if (sis <= 90 or dia <= 60) else "normal"

    m = re.search(r"(?:nadi|heart rate|hr|denyut(?: jantung)?)\D{0,12}?(\d{2,3})", t)
    if m:
        hr = int(m.group(1))
        arah["hr"] = "naik" if hr > 100 else "turun" if hr < 60 else "normal"

    m = re.search(r"(?:frekuensi napas|pernapasan|respirasi|\brr\b)\D{0,12}?(\d{1,2})", t)
    if m:
        rr = int(m.group(1))
        arah["rr"] = "naik" if rr > 20 else "turun" if rr < 12 else "normal"

    m = re.search(r"(?:spo2|sao2|saturasi(?: oksigen)?)\D{0,12}?(\d{2,3})", t)
    if m:
        s = int(m.group(1))
        if s <= 100:
            arah["spo2"] = "turun" if s < 94 else "normal"

    m = re.search(r"(?:suhu|temperatur|temp)\D{0,12}?(\d{2}(?:[.,]\d)?)", t)
    if m:
        s = float(m.group(1).replace(",", "."))
        if 30 <= s <= 45:
            arah["suhu"] = "naik" if s >= 37.8 else "turun" if s < 36.0 else "normal"

    for w, (uk, ar) in _SIGN_IMPLISIT.items():
        if uk not in arah and re.search(rf"\b{w}\b", t):
            arah[uk] = ar

    kata = re.findall(r"[a-z]+", t)
    for i, w in enumerate(kata):
        naik = w in _ARAH_NAIK
        turun = w in _ARAH_TURUN
        if not (naik or turun):
            continue
        jendela = set(kata[max(0, i - 3):i] + kata[i + 1:i + 4])
        for uk, penanda in _UKURAN_PENANDA.items():
            if uk not in arah and (jendela & penanda):
                arah[uk] = "naik" if naik else "turun"
    return arah


def _label_vital(uk: str, ar: str) -> str:
    return f"{_LABEL_UKURAN.get(uk, uk)} {_LABEL_ARAH.get(ar, ar)}"


def _nilai_kelompok(
    kriteria_list: list[str],
    tokens: frozenset[str],
    vital: dict[str, str],
    bobot: dict[str, float],
    bobot_bawaan: float,
) -> tuple[float, set[str], int, int]:
    """
    Nilai satu kelompok kriteria (mayor / minor / faktor_risiko / nama)
    terhadap asesmen. Mengembalikan (skor, kata_cocok, jumlah, jumlah_cocok).

    Dua jalur:
      * Kriteria terukur (punya ukuran+arah) -> hanya terpenuhi bila arah
        asesmen SAMA; arah berlawanan tidak terpenuhi.
      * Kriteria kualitatif -> token intersection, tapi butuh bukti CUKUP
        (>= separuh token isi kriteria, ATAU satu token spesifik/langka),
        supaya "Bunyi napas tambahan" tak dianggap cocok hanya karena "napas".
    """
    total = 0.0
    matched: set[str] = set()
    n = len(kriteria_list)
    n_cocok = 0

    for crit in kriteria_list:
        low = crit.lower()
        if any(neg in low for neg in _NEGASI_ABSEN):
            continue  # kriteria KETIADAAN tanda -- tak bisa diverifikasi positif
        uk, ar = _ukur_kriteria(crit)
        if uk and ar:
            keadaan = vital.get(uk)
            if keadaan == ar:
                # Konfirmasi terukur = kontribusi MODEST tetap. Derangemen
                # vital (frekuensi napas naik, dsb.) itu non-spesifik -- ada di
                # banyak diagnosis -- jadi tak boleh sekuat tanda spesifik
                # (jvp, sputum) yang ber-IDF tinggi. Nilai tetap juga mencegah
                # kriteria panjang meledakkan skor.
                total += 1.3
                matched.add(_label_vital(uk, ar))
                n_cocok += 1
            continue

        ctoks = _tokenize(crit)
        if not ctoks:
            continue
        cocok = tokens & ctoks
        if not cocok:
            continue
        cukup = (
            len(cocok) >= max(1, (len(ctoks) + 1) // 2)
            or any(bobot.get(k, bobot_bawaan) >= _IDF_SPESIFIK
                   and k not in _KATA_UMUM for k in cocok)
        )
        if not cukup:
            continue
        total += sum(bobot.get(k, bobot_bawaan) for k in cocok)
        matched |= cocok
        n_cocok += 1

    return total, matched, n, n_cocok


def _kontradiksi_mayor(mayor_list: list[str], vital: dict[str, str]) -> bool:
    """
    True bila untuk suatu ukuran vital, diagnosis HANYA menyebut arah yang
    BERLAWANAN dengan keadaan pasien (mis. Hipovolemia hanya "TD menurun"
    padahal TD pasien tinggi). Dikelompokkan per ukuran supaya diagnosis
    yang mendaftarkan KEDUA arah (mis. Penurunan Curah Jantung: "TD menurun"
    DAN "TD meningkat") tidak ikut tertekan.
    """
    per_ukuran: dict[str, set[str]] = {}
    for crit in mayor_list:
        uk, ar = _ukur_kriteria(crit)
        if uk and ar:
            per_ukuran.setdefault(uk, set()).add(ar)

    for uk, arah_terdaftar in per_ukuran.items():
        keadaan = vital.get(uk)
        if keadaan and keadaan != "normal" and keadaan not in arah_terdaftar:
            return True
    return False


# =====================================================================
# DETEKSI KONTEKS KLINIS KARDIOVASKULAR + BOOST NUMERIK  (gabungan CDSS 2.0)
# =====================================================================
# Bagian ini memindahkan kekuatan CDSS 2.0 lama -- mengenali KONTEKS klinis
# (ACS/STEMI, gagal jantung akut, syok kardiogenik, pasca-CABG/PCI, dukungan
# ECMO/VAD/IABP, komplikasi mekanis) dan membaca MAGNITUDO nilai numerik
# (EF, troponin, BNP, laktat, Hb, SpO2, HR, TD) -- lalu MENGGABUNGKANNYA
# dengan matcher kriteria SDKI mayor/minor di atas.
#
# Prinsip gabungan ("best of both"):
#   * Matcher kriteria SDKI tetap TULANG PUNGGUNG & penjaga (anti-kontradiksi,
#     anti-negasi) -- ia yang mem-VETO (mis. Hipovolemia pada pasien TD tinggi).
#   * Konteks & numerik hanya MENGUATKAN (amplifikasi multiplikatif) diagnosis
#     yang SUDAH punya bukti kriteria, jadi tidak mengarang diagnosis.
#   * PENGECUALIAN terbatas: diagnosis RISIKO yang faktor risikonya adalah
#     konteks itu sendiri (mis. ACS -> Risiko Perfusi Miokard) boleh
#     DIMUNCULKAN dari konteks walau kata faktor-risikonya tak tertulis
#     persis -- ini justru cara penegakan diagnosis risiko yang benar.
#
# PENTING: target boost dipetakan lewat NAMA diagnosa kanonik, BUKAN kode.
# Kode di CDSS 2.0 lama sempat keliru (mis. Risiko Perfusi Miokard ditulis
# D.0015, Risiko Syok D.0109) dan sudah dibetulkan di master baru
# (D.0014, D.0039, ...). Resolusi nama->kode memakai master aktif sehingga
# selalu ikut kode yang benar.

# Ambang & interpretasi nilai numerik (dari CDSS 2.0, threshold diperbaiki).
_NUM_THRESHOLD: dict[str, dict[str, tuple[float, float]]] = {
    "spo2": {"critical": (0, 85), "high": (85, 90), "moderate": (90, 94), "normal": (94, 101)},
    "hemoglobin": {"critical": (0, 7), "high": (7, 10), "moderate": (10, 13), "normal": (13, 25)},
    "ejection_fraction": {"critical": (0, 30), "high": (30, 40), "moderate": (40, 50), "normal": (50, 101)},
    "troponin": {"normal": (0, 0.04), "moderate": (0.04, 0.1), "high": (0.1, 0.5), "critical": (0.5, float("inf"))},
    "bnp": {"normal": (0, 100), "moderate": (100, 200), "high": (200, 500), "critical": (500, float("inf"))},
    "heart_rate": {"critical_low": (0, 40), "normal": (40, 120), "high": (120, 300)},
    "systolic_bp": {"critical_low": (0, 91), "normal": (91, 180), "high": (180, 300)},
    "lactate": {"normal": (0, 1), "moderate": (1, 2), "high": (2, 4), "critical": (4, float("inf"))},
}

_NUM_REGEX: dict[str, list[str]] = {
    "spo2": [r"(?:spo2|sp\.?o2|o2\s+sat)[:\s]*(\d+)\s*%?", r"saturasi\s*[:\s]*(\d+)\s*%?"],
    "ejection_fraction": [r"(?:ef|ejection\s+fraction)[:\s]*(\d+)\s*%?", r"fraksi\s+ejeksi[:\s]*(\d+)\s*%?"],
    "hemoglobin": [r"(?:hb|hemoglobin)[:\s]*(\d+(?:[.,]\d+)?)\s*(?:g|mg)?"],
    "heart_rate": [r"(?:hr|heart\s+rate)[:\s]*(\d+)", r"denyut\s+jantung[:\s]*(\d+)", r"\bnadi\b[:\s]*(\d+)"],
    "systolic_bp": [r"(?:bp|blood\s+pressure|tekanan\s+darah|tensi)[:\s]*(\d+)\s*/\s*\d+",
                    r"sistolik[:\s]*(\d+)", r"\btd\b[:\s]*(\d+)\s*/\s*\d+"],
    "troponin": [r"troponin\s*[it]?[:\s]*(\d+(?:[.,]\d+)?)"],
    "bnp": [r"(?:nt[-\s]?pro[-\s]?bnp|bnp)[:\s]*(\d+(?:[.,]\d+)?)"],
    "lactate": [r"(?:lactate|laktat|asam\s+laktat)[:\s]*(\d+(?:[.,]\d+)?)"],
}


def _ekstrak_numerik(teks: str) -> dict[str, str]:
    """
    Ekstrak nilai numerik klinis dari teks lalu interpretasi ke level
    ('critical'/'high'/'moderate'/'normal'/'critical_low'). Berbeda dari
    `_ekstrak_vital` (yang menyimpulkan ARAH untuk anti-kontradiksi), fungsi
    ini menilai MAGNITUDO untuk boost skor -- mis. EF 25% -> critical.
    """
    t = teks.lower()
    hasil: dict[str, str] = {}
    for param, patterns in _NUM_REGEX.items():
        for pat in patterns:
            m = re.search(pat, t)
            if not m:
                continue
            try:
                val = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                continue
            for level, (lo, hi) in _NUM_THRESHOLD[param].items():
                if lo <= val < hi:
                    hasil[param] = level
                    break
            break
    return hasil


def _deteksi_konteks(teks: str, numerik: dict[str, str]) -> dict[str, bool]:
    """
    Deteksi pola/konteks klinis kardiovaskular (port CardiacSpecificRules
    CDSS 2.0). Menggabungkan kata kunci klinis + konfirmasi numerik.
    """
    t = teks.lower()

    def ada(*kws: str) -> bool:
        return any(re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", t) for k in kws)

    postop = ada("post operasi", "post-op", "postop", "post op", "pascabedah",
                 "pasca", "post-cabg", "cabg", "post-pci", "post pci", "pasca-cabg",
                 "pasca pci", "bypass", "graft", "sternotomi", "ligasi",
                 "drain dada", "drain mediastinum", "off-pump", "on-pump")

    hf = ada("ortopnea", "orthopnea", "pnd", "paroxysmal nocturnal", "edema paru",
             "pulmonary edema", "chf", "ahf", "gagal jantung", "dekompensasi",
             "heart failure")
    if numerik.get("ejection_fraction") in ("critical", "high", "moderate"):
        hf = True
    if numerik.get("bnp") in ("high", "critical"):
        hf = True

    acs = ada("acs", "sindrom koroner", "stemi", "nstemi", "st elevasi",
              "st-elevasi", "st depresi", "iskemia", "angina", "infark")
    if numerik.get("troponin") in ("moderate", "high", "critical"):
        acs = True

    shock = ada("syok kardiogenik", "cardiogenic shock", "syok", "renjatan",
                "hipotensi berat", "perfusi buruk", "akral dingin", "iabp",
                "norepinefrin", "norepinephrine", "vasopresin", "dobutamin",
                "dobutamine", "milrinone", "epinefrin", "adrenalin")
    if numerik.get("systolic_bp") == "critical_low" and numerik.get("heart_rate") == "high":
        shock = True
    if numerik.get("lactate") in ("high", "critical") and \
            numerik.get("systolic_bp") == "critical_low":
        shock = True
    pressor = ["norepinefrin", "norepinephrine", "vasopresin", "vasopressin",
               "dobutamin", "dobutamine", "milrinone", "epinefrin", "adrenalin"]
    if sum(1 for p in pressor if ada(p)) >= 2:
        shock = True

    # Komplikasi mekanik AKUT (khas pasca-infark): sengaja TIDAK memakai "vsd"
    # telanjang -- pada pusat jantung, VSD paling sering justru kelainan
    # kongenital, bukan ruptur septum pasca-infark.
    mech = ada("ruptur septum", "ruptur dinding", "free wall rupture", "ruptur",
               "tamponade", "regurgitasi mitral akut", "mitral regurgitation",
               "murmur baru", "komplikasi mekanik", "papillary muscle")

    support = ada("ecmo", "vad", "impella", "iabp", "intra-aortic balloon",
                  "intra aortic balloon", "balon pompa intra aorta", "lvad", "rvad")

    return {"postop": postop, "hf": hf, "acs": acs, "shock": shock,
            "mech": mech, "support": support}


_KONTEKS_LABEL: dict[str, str] = {
    "shock": "Kecurigaan Syok",
    "mech": "Kecurigaan Komplikasi Mekanik",
    "acs": "Pola ACS/Iskemia",
    "hf": "Pola Gagal Jantung Akut",
    "postop": "Pasca-operatif Kardiak",
    "support": "Dukungan Sirkulasi Mekanik (ECMO/VAD/IABP)",
}

# Label konteks RINGKAS untuk ditampilkan sebagai ALASAN pada tiap usulan
# (lebih pendek & manusiawi dari banner). Dipakai menggantikan penanda kabur
# "(dari konteks klinis)" supaya perawat tahu dasar konteksnya.
_KONTEKS_ALASAN: dict[str, str] = {
    "shock": "syok",
    "support": "dukungan sirkulasi mekanik",
    "acs": "ACS/iskemia",
    "hf": "gagal jantung akut",
    "postop": "pasca-operasi jantung",
    "mech": "komplikasi mekanik",
}

# Token terlalu umum untuk DITAMPILKAN sebagai alasan (modifier/kuantitas/
# posisi tanpa makna klinis mandiri). Ini HANYA menyaring TAMPILAN alasan,
# BUKAN pencocokan/skor -- "cocok pada: dosis, tinggi" jadi bersih.
_STOP_ALASAN = {
    "dosis", "tinggi", "rendah", "besar", "kecil", "minimal", "banyak",
    "sedikit", "ringan", "kiri", "kanan", "atas", "bawah", "frekuensi",
    "lebih", "kurang", "sekitar", "tampak", "adanya",
}

# Nama diagnosa kanonik (dipetakan ke kode master aktif saat runtime).
_N_PCJ = "Penurunan Curah Jantung"
_N_RPCJ = "Risiko Penurunan Curah Jantung"
_N_PPTE = "Perfusi Perifer Tidak Efektif"
_N_RPM = "Risiko Perfusi Miokard Tidak Efektif"
_N_GPG = "Gangguan Pertukaran Gas"
_N_PNTE = "Pola Napas Tidak Efektif"
_N_HV = "Hipervolemia"
_N_HPV = "Hipovolemia"
_N_RPERD = "Risiko Perdarahan"
_N_RINF = "Risiko Infeksi"
_N_RSYOK = "Risiko Syok"
_N_NYERI = "Nyeri Akut"

# Konteks -> AMPLIFIKASI (poin boost untuk diagnosis yang SUDAH ada buktinya).
_KONTEKS_AMP: dict[str, dict[str, float]] = {
    "hf": {_N_PCJ: 2, _N_HV: 2, _N_GPG: 2, _N_PNTE: 2, _N_RPCJ: 1},
    "acs": {_N_RPM: 3, _N_PCJ: 2, _N_NYERI: 1},
    "shock": {_N_PCJ: 3, _N_PPTE: 3, _N_RSYOK: 3, _N_RPCJ: 2, _N_GPG: 1},
    "postop": {_N_RPERD: 3, _N_RINF: 2, _N_NYERI: 1},
    "support": {_N_RPERD: 2, _N_RPCJ: 2, _N_RSYOK: 2},
    "mech": {_N_PCJ: 2, _N_RSYOK: 2, _N_PPTE: 1},
}

# Konteks -> SURFACING diagnosis RISIKO. Nilai ini adalah LANTAI skor: bila
# diagnosis risiko itu tak punya bukti kriteria (atau buktinya tipis), skornya
# diangkat ke lantai ini supaya muncul sebagai prioritas yang wajar. Hanya
# diagnosis RISIKO yang konteksnya MEMANG faktor risikonya (bukan mengarang
# diagnosis akut).
_KONTEKS_SURFACE: dict[str, dict[str, float]] = {
    "acs": {_N_RPM: 4.0},
    "shock": {_N_RSYOK: 4.5, _N_RPCJ: 3.0},
    "postop": {_N_RPERD: 4.0, _N_RINF: 3.5},
    "support": {_N_RPERD: 3.5, _N_RPCJ: 3.5},
}

# Boost numerik: param -> (poin per level, target berdasarkan arah).
# Untuk param yang "rendah = buruk" (spo2/ef/hb) target sama untuk semua level
# derangemen. Untuk hr/sbp yang dua arah, target dipisah per level.
_NUMERIK_MAP: dict[str, dict[str, Any]] = {
    "spo2": {"pts": {"critical": 5, "high": 3, "moderate": 1}, "any": [_N_GPG, _N_PNTE]},
    "ejection_fraction": {"pts": {"critical": 5, "high": 4, "moderate": 2}, "any": [_N_PCJ, _N_RPCJ, _N_HV]},
    "hemoglobin": {"pts": {"critical": 5, "high": 3, "moderate": 1}, "any": [_N_RPERD, _N_PPTE]},
    "troponin": {"pts": {"critical": 5, "high": 4, "moderate": 2}, "any": [_N_RPM, _N_PCJ]},
    "bnp": {"pts": {"critical": 5, "high": 3, "moderate": 1}, "any": [_N_PCJ, _N_HV]},
    "lactate": {"pts": {"critical": 5, "high": 3, "moderate": 1}, "any": [_N_PPTE, _N_PCJ, _N_RSYOK, _N_GPG]},
    "heart_rate": {"pts": {"high": 3, "critical_low": 4},
                   "high": [_N_PCJ, _N_RPM, _N_RSYOK], "critical_low": [_N_PCJ, _N_RSYOK]},
    "systolic_bp": {"pts": {"critical_low": 5, "high": 2},
                    "critical_low": [_N_PCJ, _N_HPV, _N_RSYOK, _N_RPCJ], "high": [_N_RPM, _N_PCJ]},
}

# Seberapa kuat konteks/numerik mengangkat skor (multiplikatif) & skor surfacing.
_BETA_KONTEKS = 0.14
_KAPPA_SURFACE = 1.0


def _norm_nama(s: str) -> str:
    """Normalisasi nama diagnosa untuk resolusi nama->kode (tahan beda ejaan)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s).lower()).split())


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

    def _nama_index(self) -> dict[str, str]:
        """Peta nama-diagnosa-ternormalisasi -> kode, dari master aktif.

        Inilah yang membuat pemetaan boost tahan terhadap perbedaan kode:
        target ditulis sebagai NAMA kanonik, lalu diresolusi ke kode master
        yang sedang dipakai (yang kodenya sudah dibetulkan).
        """
        idx = getattr(self, "_cache_nama_idx", None)
        if idx is None:
            idx = {}
            for e in self._entries():
                nm = _norm_nama(e.get("nama", ""))
                if nm and nm not in idx:
                    idx[nm] = e["kode"]
            self._cache_nama_idx = idx
        return idx

    def _kode_dari_nama(self, nama: str) -> str | None:
        return self._nama_index().get(_norm_nama(nama))

    @staticmethod
    def _rapikan_alasan(matched, kode, alasan_konteks, dari_konteks,
                        bobot, bobot_bawaan, low_text="") -> list[str]:
        """
        Susun daftar 'cocok pada' yang JELAS bagi perawat:
          * tanda terukur (mis. 'tekanan darah rendah') di depan;
          * kata yang BERSEBELAHAN di teks asli digabung jadi frasa utuh
            (mis. 'penyakit jantung bawaan', 'akral dingin') supaya konsep
            multi-kata tak terpecah jadi token lepas -- ini juga menyelamatkan
            kata umum yang bermakna DALAM frasa ('berat badan') sambil tetap
            menyembunyikannya bila berdiri sendiri (dosis/tinggi/kiri/...);
          * alasan konteks ditulis manusiawi ('konteks: syok, dukungan
            sirkulasi mekanik') menggantikan penanda kabur '(dari konteks
            klinis)'. Untuk diagnosis yang MUNCUL karena konteks, konteks
            ditaruh paling depan sebagai dasar utama.
        Murni untuk TAMPILAN -- tidak menyentuh skor/pemeringkatan.
        """
        kata = {m for m in matched if " " not in m}      # token tunggal
        frasa_ukur = [m for m in matched if " " in m]     # label terukur (sudah frasa)

        # Gabungkan token cocok yang berdampingan di teks asli menjadi frasa.
        # Tanda baca (koma/titik/'+'/'/'...) MEMUTUS frasa supaya klausa
        # terpisah tak terangkai ('akral dingin, CRT' -> 'akral dingin'
        # dan 'crt' terpisah, bukan 'akral dingin crt'). Frasa yang
        # SELURUHNYA kata umum
        # (mis. 'dosis tinggi') dibuang -- itu derau, bukan konsep klinis.
        frasa_teks: list[str] = []
        terpakai: set[str] = set()
        for klausa in re.split(r"[^a-z0-9 ]+", low_text.lower()):
            seq = re.findall(r"[a-z]+\d*", klausa)
            i = 0
            while i < len(seq):
                if seq[i] in kata:
                    j = i
                    while j < len(seq) and seq[j] in kata:
                        j += 1
                    frase = seq[i:j]
                    if len(frase) >= 2 and any(t not in _STOP_ALASAN for t in frase):
                        frasa_teks.append(" ".join(frase))
                        terpakai.update(frase)
                    i = j
                else:
                    i += 1
        # de-dup frasa (jaga urutan kemunculan)
        _lihat: set[str] = set()
        frasa_teks = [f for f in frasa_teks if not (f in _lihat or _lihat.add(f))]

        # Token yang sudah masuk frasa tak diulang; sisanya disaring stopword.
        singel = [k for k in kata if k not in terpakai and k not in _STOP_ALASAN]

        def _w(frasa: str) -> float:  # bobot frasa = jumlah bobot kata penyusun
            return sum(bobot.get(t, bobot_bawaan) for t in frasa.split())

        # Urut utama = bobot (tanda spesifik/langka di depan); tiebreak
        # alfabet supaya urutan DETERMINISTIK (tak tergantung hash-seed set).
        frasa = frasa_ukur + frasa_teks
        frasa.sort(key=lambda s: (-_w(s), s))
        singel.sort(key=lambda s: (-bobot.get(s, bobot_bawaan), s))
        tampil = frasa + singel

        labels = alasan_konteks.get(kode) or []
        kt = ("konteks: " + ", ".join(labels)) if labels else None
        if dari_konteks:
            hasil = ([kt] if kt else []) + tampil
            return hasil or ["pertimbangan klinis"]
        if kt:
            return tampil + [kt]
        return tampil or ["pertimbangan klinis"]

    def _boost_konteks_numerik(
        self, konteks: dict[str, bool], numerik: dict[str, str]
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, list[str]]]:
        """
        Bangun peta kode->poin dari konteks & numerik (via resolusi nama):
          * amp_konteks : boost dari konteks klinis (amplifikasi)
          * amp_numerik : boost dari magnitudo nilai numerik (amplifikasi)
          * surface     : skor absolut untuk MEMUNCULKAN diagnosis risiko
          * alasan      : kode -> label konteks manusiawi (untuk ditampilkan)
        """
        amp_konteks: dict[str, float] = {}
        amp_numerik: dict[str, float] = {}
        surface: dict[str, float] = {}
        alasan: dict[str, list[str]] = {}

        def tambah(peta: dict[str, float], nama: str, poin: float,
                   mode: str = "sum", label: str | None = None) -> None:
            kode = self._kode_dari_nama(nama)
            if not kode:
                return
            if mode == "max":
                peta[kode] = max(peta.get(kode, 0.0), poin)
            else:
                peta[kode] = peta.get(kode, 0.0) + poin
            if label and label not in alasan.setdefault(kode, []):
                alasan[kode].append(label)

        for flag, aktif in konteks.items():
            if not aktif:
                continue
            lbl = _KONTEKS_ALASAN.get(flag)
            for nama, poin in _KONTEKS_AMP.get(flag, {}).items():
                tambah(amp_konteks, nama, poin, label=lbl)
            for nama, skor in _KONTEKS_SURFACE.get(flag, {}).items():
                tambah(surface, nama, skor, mode="max", label=lbl)

        for param, level in numerik.items():
            spec = _NUMERIK_MAP.get(param)
            if not spec:
                continue
            poin = spec["pts"].get(level, 0)
            if not poin:
                continue
            targets = spec.get(level) if level in spec else spec.get("any", [])
            for nama in (targets or []):
                tambah(amp_numerik, nama, poin)  # numerik ditampilkan terpisah

        return amp_konteks, amp_numerik, surface, alasan

    def konteks_klinis(self, text: str) -> dict[str, Any]:
        """
        Kembalikan konteks klinis + ringkasan satu-baris untuk panel alert
        (mis. 'Konteks: Pola ACS/Iskemia | ...'), meniru banner CDSS 2.0.
        """
        numerik = _ekstrak_numerik(text)
        konteks = _deteksi_konteks(text, numerik)
        flags = [(_KONTEKS_LABEL[k]) for k in
                 ("shock", "mech", "acs", "hf", "postop", "support") if konteks.get(k)]
        ringkasan = ("Konteks: " + ", ".join(flags)) if flags else ""
        return {"konteks": konteks, "numerik": numerik,
                "flags": flags, "ringkasan": ringkasan}

    def suggest(
        self,
        text: str,
        limit: int = 5,
        min_score: float = 0.0,
        sertakan_lokal: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Usulkan diagnosis SDKI dari teks asesmen -- dengan penalaran
        arah/numerik ringan, bukan sekadar tumpang-tindih kata.

        PERINGATAN: tetap alat bantu PENYARING, bukan penegakan diagnosis.
        Hasilnya kandidat untuk dipertimbangkan perawat; skor relatif,
        hanya untuk mengurutkan. Selalu dikonfirmasi manusia.

        Beda dari versi kata-kunci polos sebelumnya:
          * Kriteria MAYOR ditimbang jauh lebih berat dari MINOR (penegakan
            SDKI bertumpu pada tanda mayor); faktor risiko & nama di jalur
            sendiri.
          * Tanda terukur (TD/nadi/napas/SpO2/suhu) dicocokkan lewat ARAH:
            "Tekanan darah menurun" tak lagi cocok pada pasien ber-TD tinggi;
            arah mayor yang BERLAWANAN menekan diagnosis (anti-kontradiksi) --
            mencegah Hipovolemia muncul pada pasien overload.
          * Kriteria "ketiadaan tanda" ("nadi tidak teraba") tak lagi cocok
            pada temuan yang ADA (anti-negasi) -- menekan diagnosis peri-henti.
          * Kriteria multi-kata butuh bukti cukup, bukan satu kata umum.
          * Diagnosis LOKAL (non-SDKI) tidak diusulkan (`sertakan_lokal=False`);
            set True bila ingin memunculkannya.

        Mengembalikan list dict: `diagnosis`, `kode`, `nama`, `skor`,
        `kata_cocok`, plus `mayor_cocok`/`mayor_total` untuk transparansi.
        """
        tokens = _tokenize(text)
        if not tokens:
            return []

        vital = _ekstrak_vital(text)
        low_text = text.lower()
        bobot = self._bobot_kata()
        # Kata yang tidak ada di master sama sekali diberi bobot netral.
        bobot_bawaan = math.log(1 + len(self._entries()))

        # --- Konteks klinis & numerik (gabungan CDSS 2.0) --------------------
        numerik = _ekstrak_numerik(text)
        konteks = _deteksi_konteks(text, numerik)
        amp_konteks, amp_numerik, surface, alasan_konteks = \
            self._boost_konteks_numerik(konteks, numerik)

        scored = []
        for entry in self._entries():
            if not sertakan_lokal and not entry.get("is_sdki"):
                continue

            kriteria = entry.get("kriteria", {})
            s_mayor, cocok_mayor, n_mayor, c_mayor = _nilai_kelompok(
                kriteria.get("mayor", []), tokens, vital, bobot, bobot_bawaan)
            s_minor, cocok_minor, n_minor, _ = _nilai_kelompok(
                kriteria.get("minor", []), tokens, vital, bobot, bobot_bawaan)
            s_risiko, cocok_risiko, n_risiko, _ = _nilai_kelompok(
                kriteria.get("faktor_risiko", []), tokens, vital, bobot, bobot_bawaan)
            # Nama diagnosis juga sinyal: perawat kerap menulis konsepnya
            # langsung (mis. "ventilator" -> nama "Gangguan Ventilasi Spontan")
            # sementara kriteria formal memakai kata lain.
            s_nama, cocok_nama, _, _ = _nilai_kelompok(
                [entry.get("nama", "")], tokens, vital, bobot, bobot_bawaan)

            matched = cocok_mayor | cocok_minor | cocok_risiko | cocok_nama
            if not matched:
                continue

            # Mayor dominan; minor menopang; faktor risiko & nama jalur sendiri.
            nilai = 1.0 * s_mayor + 0.4 * s_minor + 0.85 * s_risiko + 0.7 * s_nama
            # Bonus bila proporsi kriteria mayor terpenuhi tinggi (semangat
            # aturan "80% mayor" penegakan SDKI).
            cakupan_mayor = (c_mayor / n_mayor) if n_mayor else 0.0
            nilai *= (1.0 + 0.3 * cakupan_mayor)
            # Amplifikasi konteks klinis + magnitudo numerik (gabungan CDSS 2.0):
            # HANYA menguatkan diagnosis yang sudah punya bukti kriteria, dan
            # diterapkan SEBELUM penalti di bawah supaya anti-kontradiksi/
            # anti-negasi tetap bisa mem-VETO (Hipovolemia tetap tertekan
            # walau ada boost numerik/konteks).
            boost_kode = amp_konteks.get(entry["kode"], 0.0) + amp_numerik.get(entry["kode"], 0.0)
            if boost_kode:
                nilai *= (1.0 + _BETA_KONTEKS * boost_kode)
            # Kontradiksi arah pada kriteria mayor -> tekan jauh, tetap tampil.
            if _kontradiksi_mayor(kriteria.get("mayor", []), vital):
                nilai *= 0.15
            # Jangkar konteks: diagnosis yang butuh alat/konteks khusus tapi
            # tak ada penandanya di asesmen -> tekan jauh.
            jangkar = _JANGKAR.get(entry["kode"])
            if jangkar and not any(a in low_text for a in jangkar):
                nilai *= 0.1

            # Normalisasi ringan supaya diagnosis berkriteria sangat panjang
            # tidak otomatis unggul hanya karena panjang.
            denom = max((n_mayor + n_minor + n_risiko) ** 0.25, 1.0)
            score = nilai / denom

            # Lantai konteks: diagnosis RISIKO yang konteksnya adalah faktor
            # risikonya diangkat minimal ke lantai surfacing (mis. ACS ->
            # Risiko Perfusi Miokard tetap tampil sebagai prioritas walau
            # bukti kriterianya tipis). max() -> bukti kuat tetap menang.
            dari_konteks = False
            lantai = surface.get(entry["kode"])
            if lantai is not None and lantai > score:
                score = lantai
                dari_konteks = True

            if score < min_score:
                continue

            kata_cocok = self._rapikan_alasan(
                matched, entry["kode"], alasan_konteks, dari_konteks,
                bobot, bobot_bawaan, low_text)

            scored.append({
                "diagnosis": entry,
                "kode": entry["kode"],
                "nama": entry["nama"],
                "skor": round(score, 4),
                # Konfirmasi terukur (mis. "tekanan darah tinggi") di depan
                # sebagai dasar terkuat, lalu kata langka.
                "kata_cocok": kata_cocok,
                "mayor_cocok": c_mayor,
                "mayor_total": n_mayor,
                "konteks_boost": round(amp_konteks.get(entry["kode"], 0.0), 1),
                "numerik_boost": round(amp_numerik.get(entry["kode"], 0.0), 1),
                "dari_konteks": dari_konteks,
            })

        # --- Surfacing diagnosis RISIKO dari konteks -------------------------
        # Diagnosis risiko yang faktor risikonya ADALAH konteks terdeteksi
        # (mis. ACS -> Risiko Perfusi Miokard) dimunculkan walau kata faktor
        # risikonya tak tertulis persis -- inilah kekuatan CDSS 2.0 yang
        # dijaga: hanya untuk diagnosis RISIKO (bukan mengarang diagnosis
        # akut), tetap tunduk pada `sertakan_lokal`, dan skor moderat sehingga
        # tak mengalahkan diagnosis akut yang benar-benar ada buktinya.
        sudah_ada = {r["kode"] for r in scored}
        idx = self._index()
        for kode, skor_surface in surface.items():
            if kode in sudah_ada:
                continue
            entry = idx.get(kode)
            if not entry:
                continue
            if not sertakan_lokal and not entry.get("is_sdki"):
                continue
            skor = round(_KAPPA_SURFACE * skor_surface, 4)
            if skor < min_score:
                continue
            scored.append({
                "diagnosis": entry,
                "kode": kode,
                "nama": entry.get("nama", ""),
                "skor": skor,
                "kata_cocok": self._rapikan_alasan(
                    set(), kode, alasan_konteks, True, bobot, bobot_bawaan),
                "mayor_cocok": 0,
                "mayor_total": len(entry.get("kriteria", {}).get("mayor", [])),
                "konteks_boost": round(skor_surface, 1),
                "numerik_boost": 0.0,
                "dari_konteks": True,
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
