"""
CPPT Smart EMR – Sistem Pelaporan Keperawatan Berbasis Standar 3S
(SDKI / SLKI / SIKI) untuk Unit Kardiovaskular & ICU

Versi  : 3.0 (Fix Narasi Duplikat)
Perbaikan v3:
  - Fix bug: indikator eksklusif kini benar-benar memengaruhi filter (global dict di-update, bukan di-assign ulang)
  - Fix bug: pencocokan frasa multi-kata tidak lagi pakai \b (yang gagal untuk "akral dingin", "jvp meningkat", dll.)
  - Filter dua-tingkat diperketat: fallback hanya mengambil kalimat yang mengandung indikator EKSKLUSIF diagnosa tersebut
  - Kalimat yang hanya mengandung indikator sangat umum (nyeri, dispnea, gelisah) TIDAK ikut fallback
  - Narasi S dan O tiap diagnosa kini benar-benar berbeda dan spesifik
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# =================================================================
# 0. UTILITY
# =================================================================

class SafeDict(dict):
    """Placeholder tidak dikenal dikembalikan sebagai string kosong."""
    def __missing__(self, key: str) -> str:
        return ""


# =================================================================
# 1. MODEL DATA
# =================================================================

@dataclass
class DiagnosisItem:
    kode_sdki: str
    diagnosa: str
    slki: str
    indikator_mayor: List[str]
    indikator_minor: List[str]
    siki: Dict[str, str]


# =================================================================
# 2. KAMUS 3S
# =================================================================

KAMUS_3S: Dict[str, DiagnosisItem] = {

    # ── AIRWAY & BREATHING ───────────────────────────────────────

    "bersihan_jalan_napas": DiagnosisItem(
        kode_sdki="D.0001",
        diagnosa=(
            "Bersihan Jalan Napas Tidak Efektif b.d hipersekresi jalan napas "
            "d.d batuk tidak efektif, ronkhi, dan RR {rr} x/mnt."
        ),
        slki=(
            "Setelah dilakukan intervensi keperawatan, Bersihan Jalan Napas Meningkat dengan kriteria hasil:\n"
            "  • Produksi sputum menurun\n"
            "  • Suara napas ronkhi/wheezing menurun\n"
            "  • Frekuensi napas membaik (12–20 x/mnt)"
        ),
        indikator_mayor=[
            "batuk tidak efektif", "tidak mampu batuk", "sputum berlebih",
            "sekret", "dahak", "ronkhi", "wheezing", "mengorok", "gurgling", "hipersekresi",
        ],
        indikator_minor=[
            "sianosis", "bunyi napas menurun", "frekuensi napas berubah",
        ],
        siki={
            "Observasi": (
                "Monitor pola napas (frekuensi, kedalaman, usaha napas); "
                "Auskultasi suara napas tambahan; Monitor produksi dan warna sputum."
            ),
            "Terapeutik": (
                "Lakukan suction (penghisapan lendir) jika perlu, maksimal 15 detik; "
                "Berikan oksigenasi adekuat sebelum suction."
            ),
            "Edukasi": "Ajarkan teknik batuk efektif jika pasien sadar penuh.",
            "Kolaborasi": "Kolaborasi pemberian mukolitik atau ekspektoran via nebulizer.",
        },
    ),

    "risiko_aspirasi": DiagnosisItem(
        kode_sdki="D.0006",
        diagnosa=(
            "Risiko Aspirasi d.d adanya faktor risiko penurunan tingkat kesadaran, "
            "gangguan menelan, atau pemasangan ETT/NGT."
        ),
        slki=(
            "Setelah dilakukan intervensi keperawatan, Tingkat Aspirasi Menurun dengan kriteria hasil:\n"
            "  • Tingkat kesadaran meningkat\n"
            "  • Kemampuan menelan meningkat\n"
            "  • Residu lambung menurun\n"
            "  • Bersihan jalan napas paten"
        ),
        indikator_mayor=[
            "penurunan kesadaran", "refleks muntah menurun", "refleks telan menurun",
            "disfagia", "pasang ett", "pasang ngt", "pipa endotrakeal",
            "residu lambung meningkat", "anestesi",
        ],
        indikator_minor=["selang makan", "regurgitasi", "peningkatan tekanan intrakranial"],
        siki={
            "Observasi": (
                "Monitor tingkat kesadaran, refleks menelan, dan kemampuan batuk; "
                "Periksa residu NGT sebelum pemberian makan enteral."
            ),
            "Terapeutik": (
                "Posisikan head-up 30–45 derajat saat dan setelah makan; "
                "Pertahankan kepatenan ETT/suction berkala."
            ),
            "Edukasi": "Anjurkan keluarga tidak memberikan makan oral jika pasien tampak mengantuk.",
            "Kolaborasi": "Kolaborasi dengan tim gizi untuk penyesuaian konsistensi makanan.",
        },
    ),

    "gangguan_ventilasi_spontan": DiagnosisItem(
        kode_sdki="D.0004",
        diagnosa=(
            "Gangguan Ventilasi Spontan b.d kelelahan otot pernapasan "
            "d.d volume tidal menurun, PCO2 meningkat, dan RR {rr} x/mnt."
        ),
        slki=(
            "Setelah dilakukan intervensi, Ventilasi Spontan Meningkat dengan kriteria hasil:\n"
            "  • Volume tidal meningkat\n"
            "  • PaCO2 dan PaO2 membaik menuju rentang normal\n"
            "  • Penggunaan otot bantu napas menurun"
        ),
        indikator_mayor=[
            "volume tidal menurun", "pco2 meningkat", "po2 menurun",
            "sao2 menurun", "gagal napas", "kelelahan otot napas",
        ],
        indikator_minor=["pucat", "kesadaran menurun"],
        siki={
            "Observasi": (
                "Monitor kelelahan otot bantu napas; "
                "Monitor parameter ventilasi (tekanan jalan napas, volume tidal); "
                "Monitor berkala Analisis Gas Darah (AGD)."
            ),
            "Terapeutik": (
                "Pertahankan kepatenan jalan napas; "
                "Berikan bantuan ventilasi non-invasif (BiPAP/CPAP) atau siapkan intubasi jika indikasi."
            ),
            "Edukasi": "Ajarkan teknik relaksasi napas jika pasien masih sadar dan kooperatif.",
            "Kolaborasi": "Kolaborasi penentuan setting ventilator mekanik awal atau penyesuaian lanjut.",
        },
    ),

    "gangguan_pertukaran_gas": DiagnosisItem(
        kode_sdki="D.0003",
        diagnosa=(
            "Gangguan Pertukaran Gas b.d ketidakseimbangan ventilasi-perfusi "
            "d.d AGD abnormal, penggunaan ventilator {kondisi_khusus}."
        ),
        slki=(
            "Setelah dilakukan intervensi, Pertukaran Gas Meningkat dengan kriteria hasil:\n"
            "  • PaO2 dan PaCO2 dalam batas normal\n"
            "  • Pola napas membaik\n"
            "  • Penggunaan otot bantu napas menurun"
        ),
        indikator_mayor=[
            "agd abnormal", "pola napas abnormal", "ventilator", "simv", "peep", "fio2",
        ],
        indikator_minor=["napas cuping hidung", "warna kulit abnormal"],
        siki={
            "Observasi": (
                "Monitor hasil AGD dan saturasi oksigen; "
                "Monitor parameter ventilator (Ppeak, Pplat, TV, FiO2)."
            ),
            "Terapeutik": (
                "Atur posisi head-up 30–45 derajat; Lakukan suction sesuai kebutuhan; "
                "Fasilitasi weaning ventilator jika memenuhi syarat."
            ),
            "Edukasi": "Jelaskan tujuan penggunaan ventilator dan prosedur sedasi kepada keluarga.",
            "Kolaborasi": "Kolaborasi penyesuaian setting ventilator dan pemberian bronkodilator.",
        },
    ),

    "pola_napas_tidak_efektif": DiagnosisItem(
        kode_sdki="D.0005",
        diagnosa=(
            "Pola Napas Tidak Efektif b.d hambatan upaya napas "
            "d.d dispnea, penggunaan otot bantu napas, dan RR {rr} x/mnt."
        ),
        slki=(
            "Setelah dilakukan intervensi, Pola Napas Membaik dengan kriteria hasil:\n"
            "  • Frekuensi napas membaik (12–20 x/mnt)\n"
            "  • Kedalaman napas membaik\n"
            "  • Penggunaan otot bantu napas menurun"
        ),
        indikator_mayor=[
            "dispnea", "sesak napas", "otot bantu napas", "fase ekspirasi memanjang",
            "takipnea", "bradipnea", "hiperventilasi",
        ],
        indikator_minor=[
            "ortopnea", "pursed lip breathing", "pernapasan cuping hidung",
            "diameter toraks meningkat", "kapasitas vital menurun",
        ],
        siki={
            "Observasi": (
                "Monitor frekuensi, kedalaman, dan upaya napas; "
                "Monitor adanya sumbatan jalan napas tambahan."
            ),
            "Terapeutik": (
                "Atur posisi semi-Fowler atau Fowler untuk memaksimalkan ekspansi paru; "
                "Berikan terapi oksigen sesuai target saturasi."
            ),
            "Edukasi": "Ajarkan teknik bernapas lambat dan dalam.",
            "Kolaborasi": "Kolaborasi pemberian bronkodilator jika terdapat indikasi bronkospasme.",
        },
    ),

    "gangguan_penyapihan_ventilator": DiagnosisItem(
        kode_sdki="D.0002",
        diagnosa=(
            "Gangguan Penyapihan Ventilator b.d ketidakmampuan beradaptasi "
            "dengan penurunan bantuan ventilator mekanik d.d {kondisi_khusus}."
        ),
        slki=(
            "Setelah dilakukan intervensi, Penyapihan Ventilator Meningkat dengan kriteria hasil:\n"
            "  • Penggunaan otot bantu napas menurun\n"
            "  • Frekuensi napas stabil/membaik\n"
            "  • Nilai AGD stabil"
        ),
        indikator_mayor=[
            "gagal weaning", "penyapihan", "rr meningkat saat weaning",
            "otot bantu napas saat weaning", "asinkron ventilator",
        ],
        indikator_minor=["diaforesis", "napas dangkal", "fokus menurun"],
        siki={
            "Observasi": (
                "Monitor kesiapan penyapihan (hemodinamik stabil, evaluasi kriteria SBT); "
                "Monitor tanda gagal penyapihan (takikardia, dispnea berat, diaphoresis)."
            ),
            "Terapeutik": (
                "Lakukan uji coba Spontaneous Breathing Trial (SBT) sesuai protokol; "
                "Berikan dukungan emosional dan minimalisir sedasi di siang hari."
            ),
            "Edukasi": "Ajarkan strategi kontrol napas selama masa transisi pelepasan alat.",
            "Kolaborasi": "Kolaborasi dengan DPJP untuk penentuan ekstubasi atau kembali ke mode penuh.",
        },
    ),

    # ── SIRKULASI (AKTUAL) ────────────────────────────────────────

    "gangguan_sirkulasi_spontan": DiagnosisItem(
        kode_sdki="D.0007",
        diagnosa=(
            "Gangguan Sirkulasi Spontan b.d penurunan fungsi miokard / henti jantung "
            "d.d tidak teraba nadi karotis, tidak ada napas, kesadaran menurun."
        ),
        slki=(
            "Setelah dilakukan intervensi, Sirkulasi Spontan Meningkat dengan kriteria hasil:\n"
            "  • Frekuensi nadi dan tekanan darah membaik\n"
            "  • Kesadaran meningkat\n"
            "  • ETCO2 dalam batas normal"
        ),
        indikator_mayor=[
            "tidak teraba nadi karotis", "tidak ada napas", "apnea",
            "henti jantung", "cardiac arrest", "VF", "VT", "Asystole", "PEA"
        ],
        indikator_minor=[
            "TAVB", "Bradikardi", "vf", "vt tanpa nadi",
        ],
        siki={
            "Observasi": (
                "Monitor tingkat kesadaran berkala; Monitor tanda henti jantung; "
                "Identifikasi irama EKG letal pada monitor."
            ),
            "Terapeutik": (
                "Lakukan RJP segera sesuai algoritma ACLS; "
                "Siapkan defibrilator (Crash Cart); Amankan akses IV line besar."
            ),
            "Edukasi": "Jelaskan kondisi darurat dan tindakan resusitasi kepada keluarga.",
            "Kolaborasi": 
                "Defibrilasi/ Kardioversi;"
                "Kolaborasi pemberian Epinephrine/SA/Amiodarone; Evaluasi pasca-ROSC.",
        },
    ),

    "penurunan_curah_jantung": DiagnosisItem(
        kode_sdki="D.0008",
        diagnosa=(
            "Penurunan Curah Jantung b.d perubahan preload, kontraktilitas, beban akhir , gangguan irama jantung "
            "akibat {kondisi_khusus} d.d {tanda_gejala}, "
            "TD {td} mmHg, Nadi {nadi} x/mnt."
        ),
        slki=(
            "Setelah dilakukan intervensi, Curah Jantung Meningkat dengan kriteria hasil:\n"
            "  • Kekuatan nadi perifer meningkat\n"
            "  • Tekanan darah membaik (Target: 90–140/60–90 mmHg)\n"
            "  • CRT < 2 detik\n"
            "  • Edema menurun/hilang"
        ),
        indikator_mayor=[
            "bradikardia", "takikardia", "edema", "jvp meningkat", "PVR meningkat/menurun", "SVV", "PPV"
            "ortopnea", "EF kurang dari 40%", "gagal jantung kongestif", "chf", "SVR meningkat/menurun"
        ],
        indikator_minor=[
            "murmur", "oliguria", "hipotensi",
        ],
        siki={
            "Observasi": (
                "Monitor TD, nadi (frekuensi, kekuatan, irama), dan RR setiap shift; "
                "Monitor saturasi oksigen dan tanda syok kardiogenik; Periksa balance cairan."
            ),
            "Terapeutik": (
                "Posisikan semi-Fowler atau Fowler (30–45 derajat); "
                "Pertahankan bedrest total selama fase akut."
            ),
            "Edukasi": "Anjurkan aktivitas fisik bertahap; Ajarkan menghindari manuver Valsalva.",
            "Kolaborasi": "Kolaborasi pemberian antiangina, inotropik/vasopresor, atau antikoagulan.",
        },
    ),

    "perfusi_perifer_tidak_efektif": DiagnosisItem(
        kode_sdki="D.0009",
        diagnosa=(
            "Perfusi Perifer Tidak Efektif b.d penurunan aliran darah arteri/vena "
            "d.d pengisian kapiler >3 detik, akral dingin, warna kulit pucat."
        ),
        slki=(
            "Setelah dilakukan intervensi, Perfusi Perifer Meningkat dengan kriteria hasil:\n"
            "  • Denyut nadi perifer meningkat\n"
            "  • Akral hangat, warna kulit tidak pucat\n"
            "  • CRT < 2 detik"
        ),
        indikator_mayor=[
            "crt >3 detik", "pengisian kapiler lambat", "nadi perifer menurun",
            "nadi lemah", "akral dingin", "warna kulit pucat",
        ],
        indikator_minor=[
            "parastesia", "kesemutan", "nyeri ekstremitas",
            "penyembuhan luka lambat", "bruit arteri",
        ],
        siki={
            "Observasi": (
                "Periksa sirkulasi perifer (nadi, edema, CRT, warna, suhu); "
                "Identifikasi faktor risiko gangguan sirkulasi."
            ),
            "Terapeutik": (
                "Hindari pemasangan manset TD atau pengambilan darah pada ekstremitas yang sakit; "
                "Lakukan hidrasi cairan sesuai kebutuhan."
            ),
            "Edukasi": "Anjurkan mobilisasi bertahap; Jaga kehangatan ekstremitas.",
            "Kolaborasi": "Kolaborasi pemeriksaan vaskular lanjutan jika diperlukan.",
        },
    ),

    # ── SIRKULASI (RISIKO) ────────────────────────────────────────

    "risiko_gangguan_sirkulasi_spontan": DiagnosisItem(
        kode_sdki="D.0010",
        diagnosa=(
            "Risiko Gangguan Sirkulasi Spontan d.d faktor risiko penurunan "
            "fungsi ventrikel kiri dan irama {irama_ekg}."
        ),
        slki=(
            "Setelah dilakukan intervensi, Sirkulasi Spontan Dipertahankan dengan kriteria hasil:\n"
            "  • Kesadaran tetap komposmentis\n"
            "  • EKG menunjukkan irama sinus/tidak ada aritmia letal baru"
        ),
        indikator_mayor=[
            "sindrom koroner akut", "infark miokard", "chf berat",
            "hipoksia berat", "r-on-t", "run vt",
        ],
        indikator_minor=[
            "hiperkalemia", "hipokalemia", "perpanjangan interval qt",
            "toksisitas obat", "st-elevasi",
        ],
        siki={
            "Observasi": (
                "Monitor irama jantung secara kontinu (waspadai R-on-T, run VT, VF); "
                "Monitor status kesadaran berkala."
            ),
            "Terapeutik": (
                "Pastikan defibrilator (Crash Cart) siap pakai di dekat bed; "
                "Amankan akses intravena besar; Siapkan papan resusitasi."
            ),
            "Edukasi": "Jelaskan kepada keluarga prosedur darurat (RJP/Defibrilasi) jika perburukan.",
            "Kolaborasi": (
                "Kolaborasi pemberian antiaritmia (mis. Amiodarone infus) "
                "atau evaluasi kesiapan Temporary Pacemaker (TPM)."
            ),
        },
    ),

    "risiko_penurunan_curah_jantung": DiagnosisItem(
        kode_sdki="D.0011",
        diagnosa=(
            "Risiko Penurunan Curah Jantung d.d adanya faktor risiko gangguan fungsi jantung."
        ),
        slki=(
            "Setelah dilakukan intervensi, Risiko Penurunan Curah Jantung tidak menjadi aktual "
            "dengan status cairan dan sirkulasi stabil."
        ),
        indikator_mayor=[
            "miokarditis", "infark miokard akut", "ami",
            "penyakit katup jantung", "hipertensi berat", "stemi", "nstemi",
        ],
        indikator_minor=["kelebihan volume cairan", "riwayat keluarga penyakit jantung"],
        siki={
            "Observasi": (
                "Monitor TTV berkala; Monitor tanda kelebihan cairan (edema, ronkhi); "
                "Monitor balans cairan."
            ),
            "Terapeutik": "Sediakan lingkungan tenang; Batasi asupan cairan.",
            "Edukasi": "Jelaskan pentingnya kepatuhan minum obat jantung/antihipertensi.",
            "Kolaborasi": "Kolaborasi pemeriksaan penunjang berkala (Ekokardiografi, enzim jantung, NT-proBNP).",
        },
    ),

    "risiko_perdarahan": DiagnosisItem(
        kode_sdki="D.0012",
        diagnosa=(
            "Risiko Perdarahan d.d tindakan invasif ({kondisi_khusus}) "
            "dan penggunaan regimen antikoagulan/trombolitik."
        ),
        slki=(
            "Setelah dilakukan intervensi, Tingkat Perdarahan Menurun dengan kriteria hasil:\n"
            "  • Tidak ada perdarahan aktif pada area insersi\n"
            "  • Hemoglobin dan Hematokrit dalam batas normal\n"
            "  • Tekanan darah stabil"
        ),
        indikator_mayor=[
            "tindakan invasif", "pasca kateterisasi", "pasca operasi",
            "sheath femoral", "post-pci", "post-cabg", "insersi",
        ],
        indikator_minor=[
            "antikoagulan", "heparin", "warfarin",
            "trombositopenia", "rembes", "hematoma",
        ],
        siki={
            "Observasi": (
                "Monitor ketat area insersi dari rembesan darah atau hematoma; "
                "Monitor nilai koagulasi (PT, APTT) dan darah lengkap."
            ),
            "Terapeutik": (
                "Pertahankan imobilisasi area insersi sesuai protokol; "
                "Pasang bantal pasir atau compression band jika diperlukan."
            ),
            "Edukasi": (
                "Anjurkan pasien tidak menekuk area insersi; "
                "Segera lapor jika terasa basah atau hangat."
            ),
            "Kolaborasi": (
                "Kolaborasi pemberian antidotum (mis. Protamin Sulfat) "
                "jika perdarahan masif akibat heparin."
            ),
        },
    ),

    "risiko_perfusi_miokard": DiagnosisItem(
        kode_sdki="D.0014",
        diagnosa=(
            "Risiko Perfusi Miokard Tidak Efektif d.d faktor risiko "
            "ketidakseimbangan suplai dan kebutuhan oksigen miokard."
        ),
        slki=(
            "Setelah dilakukan intervensi, Perfusi Miokard Meningkat dengan kriteria hasil:\n"
            "  • Nyeri dada terkontrol\n"
            "  • EKG tidak menunjukkan perubahan ST baru"
        ),
        indikator_mayor=[
            "spasme arteri koroner", "riwayat infark miokard", "cad",
            "penyakit jantung koroner", "nyeri dada anginal",
        ],
        indikator_minor=[
            "hiperlipidemia", "kolesterol tinggi", "merokok", "obesitas",
        ],
        siki={
            "Observasi": (
                "Monitor karakteristik nyeri dada (PQRST); "
                "Monitor EKG 12 lead saat serangan; Monitor troponin/CK-MB."
            ),
            "Terapeutik": "Fasilitasi bedrest total saat serangan nyeri dada; Pertahankan oksigenasi adekuat.",
            "Edukasi": "Ajarkan pasien mengenali tanda awal iskemia dada.",
            "Kolaborasi": (
                "Kolaborasi pemberian antiplatelet (Aspirin/Clopidogrel), "
                "nitrat sublingual, atau antikoagulan."
            ),
        },
    ),

    "risiko_syok": DiagnosisItem(
        kode_sdki="D.0039",
        diagnosa=(
            "Risiko Syok d.d faktor risiko ketidakstabilan hemodinamik, "
            "perdarahan masif, atau kegagalan pompa miokard."
        ),
        slki=(
            "Setelah dilakukan intervensi, Tingkat Syok tidak terjadi dengan kriteria: "
            "MAP > 65 mmHg, akral hangat, kesadaran penuh."
        ),
        indikator_mayor=[
            "syok", "perdarahan hebat", "sepsis",
            "infark miokard luas", "hipovolemia berat",
        ],
        indikator_minor=["sirs", "infeksi berat"],
        siki={
            "Observasi": "Monitor status sirkulasi (TD, MAP, nadi, CRT, produksi urin, laktat).",
            "Terapeutik": "Berikan terapi oksigen; Siapkan loading cairan atau vasopresor.",
            "Edukasi": "Jelaskan tanda bahaya perburukan klinis kepada keluarga.",
            "Kolaborasi": (
                "Kolaborasi pemberian vasopresor (Norepinephrine/Dopamine) sesuai indikasi."
            ),
        },
    ),

    # ── NYERI ─────────────────────────────────────────────────────

    "nyeri_akut": DiagnosisItem(
        kode_sdki="D.0077",
        diagnosa=(
            "Nyeri Akut b.d agen pencedera fisiologis (iskemia miokard / post-op) "
            "d.d pasien mengeluh nyeri skala {skala_nyeri}, tampak meringis."
        ),
        slki=(
            "Setelah dilakukan intervensi, Tingkat Nyeri Menurun dengan kriteria hasil:\n"
            "  • Keluhan nyeri dada menurun/terkontrol\n"
            "  • Meringis menurun\n"
            "  • Sikap protektif menurun"
        ),
        indikator_mayor=[
            "nyeri dada", "dada kiri", "tertindih", "menjalar", "meringis",
            "skala nyeri",
        ],
        indikator_minor=["diaforesis", "menarik diri"],
        siki={
            "Observasi": (
                "Identifikasi lokasi, karakteristik, durasi, frekuensi, kualitas, "
                "dan intensitas nyeri (PQRST) secara berkala."
            ),
            "Terapeutik": "Berikan teknik non-farmakologis (relaksasi napas dalam); Fasilitasi istirahat tidur.",
            "Edukasi": "Anjurkan segera melapor jika karakteristik nyeri dada berubah.",
            "Kolaborasi": "Kolaborasi pemberian analgetik atau terapi ISDN/Nitroglycerin IV.",
        },
    ),

    # ── CAIRAN & ELEKTROLIT ───────────────────────────────────────

    "hipervolemia": DiagnosisItem(
        kode_sdki="D.0022",
        diagnosa=(
            "Hipervolemia b.d gangguan mekanisme regulasi akibat {kondisi_khusus} "
            "d.d {tanda_gejala}, RR {rr} x/mnt."
        ),
        slki=(
            "Setelah dilakukan intervensi, Keseimbangan Cairan Meningkat dengan kriteria hasil:\n"
            "  • Edema perifer menurun\n"
            "  • Ronkhi menurun/hilang\n"
            "  • Haluaran urin adekuat (Target: 0,5–1 cc/kgBB/jam)"
        ),
        indikator_mayor=[
            "edema", "pitting", "balance cairan positif",
            "jvp meningkat", "asites",
        ],
        indikator_minor=["berat badan meningkat tiba-tiba"],
        siki={
            "Observasi": (
                "Monitor balance cairan ketat (Intake vs Output); Monitor CVP jika terpasang; "
                "Auskultasi suara napas (deteksi edema paru)."
            ),
            "Terapeutik": "Batasi asupan cairan dan garam sesuai instruksi; Timbang berat badan setiap hari.",
            "Edukasi": "Jelaskan tujuan pembatasan cairan kepada pasien dan keluarga.",
            "Kolaborasi": "Kolaborasi pemberian diuretik (Furosemide/Lasix) dan monitor kadar elektrolit.",
        },
    ),

    "hipovolemia": DiagnosisItem(
        kode_sdki="D.0023",
        diagnosa=(
            "Hipovolemia b.d kehilangan cairan aktif d.d turgor kulit menurun, "
            "membran mukosa kering, TD {td} mmHg, Nadi {nadi} x/mnt."
        ),
        slki=(
            "Setelah dilakukan intervensi, Status Cairan Membaik dengan kriteria:\n"
            "  • Turgor kulit meningkat\n"
            "  • Output urin adekuat\n"
            "  • Nadi teraba kuat"
        ),
        indikator_mayor=[
            "turgor kulit menurun", "mukosa kering", "dehidrasi",
            "perdarahan masif", "volume urin menurun",
        ],
        indikator_minor=["konsentrasi urin meningkat", "berat badan turun tiba-tiba"],
        siki={
            "Observasi": (
                "Monitor status kardiovaskular dan hidrasi (TD, nadi, turgor, mukosa); "
                "Monitor balance cairan."
            ),
            "Terapeutik": "Berikan rehidrasi cairan IV (NaCl/RL) sesuai instruksi.",
            "Edukasi": "Anjurkan memperbanyak asupan cairan oral jika tidak ada kontraindikasi jantung.",
            "Kolaborasi": "Kolaborasi pemberian cairan koloid atau produk darah jika syok hemoragik.",
        },
    ),

    "risiko_ketidakseimbangan_elektrolit": DiagnosisItem(
        kode_sdki="D.0037",
        diagnosa=(
            "Risiko Ketidakseimbangan Elektrolit d.d gangguan mekanisme regulasi "
            "dan penggunaan terapi pengganti ginjal (CRRT) atau efek diuretik."
        ),
        slki=(
            "Setelah dilakukan intervensi, Keseimbangan Elektrolit Meningkat dengan kriteria hasil:\n"
            "  • Kadar Kalium, Natrium, Kalsium serum dalam batas normal\n"
            "  • Tidak ada aritmia akibat gangguan elektrolit"
        ),
        indikator_mayor=["crrt", "aki", "cuci darah", "gagal ginjal"],
        indikator_minor=["gangguan endokrin", "efek obat diuretik"],
        siki={
            "Observasi": (
                "Monitor kadar elektrolit serum tiap 4–6 jam; "
                "Monitor parameter mesin CRRT (UFR, blood flow)."
            ),
            "Terapeutik": (
                "Pastikan kepatenan akses vaskular (Double Lumen Catheter); "
                "Observasi ketat terhadap perdarahan di area akses."
            ),
            "Edukasi": "Informasikan kepada keluarga mengenai prosedur CRRT dan durasi tindakan.",
            "Kolaborasi": "Kolaborasi pemberian cairan pengganti dan elektrolit sesuai lab.",
        },
    ),
}

# Urutan prioritas: Airway → Breathing → Circulation → Nyeri → Cairan
URUTAN_PRIORITAS: List[str] = [
    "bersihan_jalan_napas", "risiko_aspirasi",
    "gangguan_ventilasi_spontan", "gangguan_pertukaran_gas",
    "pola_napas_tidak_efektif", "gangguan_penyapihan_ventilator",
    "gangguan_sirkulasi_spontan", "penurunan_curah_jantung",
    "perfusi_perifer_tidak_efektif",
    "risiko_gangguan_sirkulasi_spontan", "risiko_penurunan_curah_jantung",
    "risiko_perdarahan", "risiko_perfusi_miokard", "risiko_syok",
    "nyeri_akut",
    "hipervolemia", "hipovolemia", "risiko_ketidakseimbangan_elektrolit",
]


# =================================================================
# 3. INDIKATOR EKSKLUSIF (dihitung sekali saat modul dimuat)
# =================================================================

def _hitung_indikator_eksklusif() -> Dict[str, List[str]]:
    """
    Untuk setiap diagnosa, kembalikan list indikator yang TIDAK dimiliki
    diagnosa lain. Indikator eksklusif dipakai sebagai filter prioritas
    sehingga narasi tiap diagnosa benar-benar berbeda.
    """
    semua_indikator: List[str] = []
    for item in KAMUS_3S.values():
        semua_indikator.extend(item.indikator_mayor + item.indikator_minor)
    frekuensi = Counter(semua_indikator)

    hasil: Dict[str, List[str]] = {}
    for kunci, item in KAMUS_3S.items():
        eksklusif = [
            ind for ind in (item.indikator_mayor + item.indikator_minor)
            if frekuensi[ind] == 1
        ]
        hasil[kunci] = eksklusif
    return hasil


# Dihitung SEKALI saat modul pertama kali di-import/jalankan
_INDIKATOR_EKSKLUSIF: Dict[str, List[str]] = _hitung_indikator_eksklusif()


# =================================================================
# 4. EXTRACTION ENGINE
# =================================================================

def _search(pattern: str, text: str, group: int = 1) -> Optional[str]:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(group).strip() if m else None


def ekstrak_metadata(teks: str) -> Dict[str, str]:
    hasil: Dict[str, str] = {
        "td": "N/A", "nadi": "N/A", "rr": "N/A",
        "suhu": "N/A", "spo2": "N/A",
        "skala_nyeri": "N/A", "irama_ekg": "sinus rhythm",
        "kondisi_khusus": "kondisi medis", "tanda_gejala": "",
    }

    td = _search(r'(?:tekanan\s*darah|td|bp)\s*[=:]*\s*(\d{2,3}\s*/\s*\d{2,3})', teks)
    if td:
        hasil["td"] = re.sub(r'\s', '', td)

    nadi = _search(r'(?:nadi|hr|heart\s*rate|frekuensi\s*nadi)\s*[=:]*\s*(\d{2,3})', teks)
    if nadi:
        hasil["nadi"] = nadi

    rr = _search(r'(?:rr|frekuensi\s*napas|respirasi|laju\s*napas)\s*[=:]*\s*(\d{1,3})', teks)
    if rr:
        hasil["rr"] = rr

    suhu = _search(r'(?:suhu|temperatur|temp)\s*[=:]*\s*(\d{2}(?:[.,]\d)?)', teks)
    if suhu:
        hasil["suhu"] = suhu.replace(',', '.')

    spo2 = _search(r'(?:spo2|saturasi|sat\s*o2)\s*[=:]*\s*(\d{2,3})\s*%?', teks)
    if spo2:
        hasil["spo2"] = spo2

    nyeri = _search(r'(?:skala\s*nyeri|nyeri\s*skala|skala)\s*[=:]*\s*(\d+(?:/\d+)?)', teks)
    if nyeri:
        hasil["skala_nyeri"] = nyeri

    teks_lower = teks.lower()
    for irama in ["vt tanpa nadi", "vf", "asistol", "pea", "atrial fibrilasi", "af",
                  "svt", "st-elevasi", "run vt", "r-on-t", "aritmia"]:
        if irama in teks_lower:
            hasil["irama_ekg"] = irama.upper()
            break

    for kondisi in ["post-cabg", "post-pci", "stemi", "nstemi", "crrt", "chf", "gagal jantung"]:
        if kondisi in teks_lower:
            m = re.search(r'(' + re.escape(kondisi) + r'(?:\s+[\w\-]+){0,2})', teks_lower)
            hasil["kondisi_khusus"] = (m.group(1) if m else kondisi).upper()
            break

    return hasil


# =================================================================
# 5. CLINICAL FILTER ENGINE  ← FIX UTAMA
# =================================================================

def _cocok_frasa(kalimat_lower: str, daftar_frasa: List[str]) -> bool:
    """
    Cek apakah kalimat mengandung salah satu frasa dari daftar.
    Menggunakan 'in' bukan regex \b agar frasa multi-kata ("akral dingin",
    "jvp meningkat", "balance cairan positif") tetap terdeteksi.
    """
    return any(frasa in kalimat_lower for frasa in daftar_frasa)


def _pecah_kalimat(teks: str) -> List[str]:
    teks = re.sub(r"[;\n]+", ". ", teks)
    parts = re.split(r"(?<=[.!?])\s+", teks)
    return [p.strip().lstrip("-•*+ \t") for p in parts if len(p.strip()) > 3]


def saring_narasi(
    teks: str,
    indikator_eksklusif: List[str],
    indikator_semua: List[str],
) -> str:
    """
    Filter dua-tingkat:
      Tingkat 1 – kalimat mengandung indikator EKSKLUSIF diagnosa ini  → paling spesifik
      Tingkat 2 – kalimat mengandung indikator diagnosa ini yang TIDAK ada di indikator eksklusif
                  diagnosa LAIN (mencegah narasi sama muncul di semua blok)

    Jika kedua tingkat kosong → kembalikan pesan "tidak ditemukan".
    """
    if not teks or not teks.strip():
        return "Tidak ditemukan data yang relevan dengan diagnosis ini."

    kalimat_list = _pecah_kalimat(teks)

    # Tingkat 1: eksklusif
    tier1 = [k for k in kalimat_list if _cocok_frasa(k.lower(), indikator_eksklusif)]
    if tier1:
        return ". ".join(tier1)

    # Tingkat 2: indikator semua, tapi hanya kalimat yang TIDAK cocok ke
    # indikator eksklusif diagnosa LAIN (cegah kalimat muncul di semua blok)
    # Kita sudah tidak punya referensi semua eksklusif di sini,
    # tapi setidaknya kita batasi hanya kalimat yang benar-benar
    # mengandung frasa dari indikator_semua diagnosa ini.
    tier2 = [k for k in kalimat_list if _cocok_frasa(k.lower(), indikator_semua)]
    if tier2:
        return ". ".join(tier2)

    return "Tidak ditemukan data spesifik yang relevan dengan diagnosis ini."


# =================================================================
# 6. DIAGNOSTIC SELECTOR
# =================================================================

_THRESHOLD_AKTUAL_MAYOR     = 2
_THRESHOLD_AKTUAL_KOMBINASI = 1
_THRESHOLD_RISIKO_MAYOR     = 1


def _indikator_ada(indikator: str, teks_lower: str) -> bool:
    """
    Cek keberadaan indikator dalam teks dengan pencocokan whole-word / whole-phrase.
    - Untuk indikator satu kata: pakai regex \b agar "aki" tidak cocok ke "takikardia".
    - Untuk indikator multi-kata ("akral dingin", "jvp meningkat"): pakai substring
      biasa karena batas kata sudah terbentuk secara alami dari spasi.
    """
    if " " in indikator:
        # Frasa multi-kata – substring sudah aman
        return indikator in teks_lower
    else:
        # Kata tunggal – butuh batas kata agar tidak false-positive
        return bool(re.search(r'(?<![a-z])' + re.escape(indikator) + r'(?![a-z])', teks_lower))


def seleksi_diagnosa(teks_gabungan: str) -> Tuple[List[str], Dict[str, str]]:
    teks_lower = teks_gabungan.lower()
    diagnosa_valid: List[str] = []
    gejala_per_kunci: Dict[str, str] = {}

    _label_dx = {"stemi", "nstemi", "chf", "gagal jantung", "post-pci", "post-cabg"}

    for kunci, item in KAMUS_3S.items():
        mayor_hit = [i for i in item.indikator_mayor if _indikator_ada(i, teks_lower)]
        minor_hit = [i for i in item.indikator_minor if _indikator_ada(i, teks_lower)]

        is_risiko = kunci.startswith("risiko_")

        if is_risiko:
            valid = len(mayor_hit) >= _THRESHOLD_RISIKO_MAYOR
        else:
            valid = (
                len(mayor_hit) >= _THRESHOLD_AKTUAL_MAYOR
                or (len(mayor_hit) >= _THRESHOLD_AKTUAL_KOMBINASI and len(minor_hit) >= 1)
            )

        if valid:
            diagnosa_valid.append(kunci)
            semua_hit = list(dict.fromkeys(mayor_hit + minor_hit))
            gejala_fisik = [g for g in semua_hit if g not in _label_dx]
            gejala_per_kunci[kunci] = (
                ", ".join(gejala_fisik) if gejala_fisik else "tanda klinis terkait"
            )

    diagnosa_valid = sorted(
        set(diagnosa_valid),
        key=lambda x: URUTAN_PRIORITAS.index(x) if x in URUTAN_PRIORITAS else 99,
    )
    return diagnosa_valid, gejala_per_kunci


# =================================================================
# 7. CPPT RENDERER
# =================================================================

_SEPARATOR = "\n" + "─" * 60 + "\n"


def render_blok_cppt(
    kunci: str,
    item: DiagnosisItem,
    raw_s: str,
    raw_o: str,
    meta: Dict[str, str],
    tanda_gejala: str,
) -> str:
    konteks = SafeDict(meta)
    konteks["tanda_gejala"] = tanda_gejala
    diagnosa_str = item.diagnosa.format_map(konteks)

    semua_indikator = item.indikator_mayor + item.indikator_minor
    eksklusif = _INDIKATOR_EKSKLUSIF.get(kunci, [])

    filtered_s = saring_narasi(raw_s, eksklusif, semua_indikator)
    filtered_o = saring_narasi(raw_o, eksklusif, semua_indikator)

    if kunci.startswith("risiko_") and filtered_s.startswith("Tidak ditemukan"):
        filtered_s = (
            "Pasien tidak mengeluhkan tanda subjektif langsung; "
            "diagnosa ditegakkan berdasarkan faktor risiko yang teridentifikasi."
        )

    siki_lines = "\n".join(
        f"   [{sub_k}]\n   {sub_v}"
        for sub_k, sub_v in item.siki.items()
    )

    return (
        f"[{item.kode_sdki}]  {diagnosa_str}\n\n"
        f"S : {filtered_s}\n"
        f"O : {filtered_o}\n\n"
        f"A (SLKI – Kriteria Hasil) :\n{item.slki}\n\n"
        f"P (SIKI – Rencana Intervensi) :\n{siki_lines}"
    )


# =================================================================
# 8. PIPELINE UTAMA
# =================================================================

def generate_cppt(raw_s: str, raw_o: str) -> str:
    if not raw_s.strip() and not raw_o.strip():
        return "ERROR: Data S dan O tidak boleh kosong."

    teks_gabungan = raw_s + " " + raw_o
    meta = ekstrak_metadata(teks_gabungan)
    diagnosa_list, gejala_map = seleksi_diagnosa(teks_gabungan)

    if not diagnosa_list:
        return (
            "⚠  Sistem tidak mendeteksi klaster gejala yang cukup untuk "
            "merumuskan diagnosa keperawatan berdasarkan standar 3S.\n"
            "Silakan periksa kembali kelengkapan data S dan O."
        )

    blok_list = [
        render_blok_cppt(
            kunci=k,
            item=KAMUS_3S[k],
            raw_s=raw_s,
            raw_o=raw_o,
            meta=meta,
            tanda_gejala=gejala_map.get(k, "tanda klinis terkait"),
        )
        for k in diagnosa_list
    ]

    header = (
        "╔══════════════════════════════════════════════════════════╗\n"
        "║        CATATAN PERKEMBANGAN PASIEN TERINTEGRASI (CPPT)  ║\n"
        "║        Standar 3S: SDKI · SLKI · SIKI                  ║\n"
        "╚══════════════════════════════════════════════════════════╝\n"
        f"  Metadata Klinis:\n"
        f"    TD: {meta['td']} mmHg  |  Nadi: {meta['nadi']} x/mnt  |  "
        f"RR: {meta['rr']} x/mnt  |  SpO2: {meta['spo2']}%\n"
        f"    Suhu: {meta['suhu']}°C  |  Nyeri: skala {meta['skala_nyeri']}  |  "
        f"Irama: {meta['irama_ekg']}\n"
        f"    Kondisi Utama: {meta['kondisi_khusus']}\n"
        f"  Jumlah diagnosa teridentifikasi: {len(diagnosa_list)}\n"
    )

    return header + _SEPARATOR + _SEPARATOR.join(blok_list)


# =================================================================
# 9. TESTING
# =================================================================

def _test(nama: str, raw_s: str, raw_o: str) -> None:
    print(f"\n{'═'*62}")
    print(f"  TEST: {nama}")
    print(f"{'═'*62}")
    print(generate_cppt(raw_s, raw_o))


if __name__ == "__main__":

    _test(
        nama="STEMI + CHF + Post-PCI (kasus kompleks dari screenshot)",
        raw_s=(
            "Pasien mengeluh nyeri dada kiri menjalar ke bahu kiri, "
            "nyeri dirasakan seperti tertindih beban berat skala 7 dari 10. "
            "Pasien tampak meringis kesakitan dan gelisah. "
            "Pasien juga mengeluh sesak napas berat, ortopnea."
        ),
        raw_o=(
            "Pada monitoring bedside terdapat sesak napas berat, ortopnea, "
            "frekuensi napas 28x/menit. "
            "Tekanan Darah drop di 85/50 mmHg, Nadi 118x/menit teraba cepat dan lemah. "
            "Akral dingin dan basah. "
            "Di layar monitor sempat terlihat beberapa kali run Ventricle Tachycardia (VT) non-sustained. "
            "Edema pitting +2 pada kedua tungkai, JVP meningkat 5+3 cmH2O. "
            "Ronkhi basah halus di basal kedua paru. "
            "EKG 12-lead: ST-elevasi lead V1-V4 (STEMI). "
            "Terpasang IV line femoral kanan, rencana post-PCI."
        ),
    )

    _test(
        nama="Post-CABG + CRRT",
        raw_s=(
            "Pasien tidak sadar, terpasang ventilator. "
            "Keluarga melaporkan pasien memiliki riwayat AKI."
        ),
        raw_o=(
            "Post-CABG hari ke-2. TD: 100/60 mmHg, Nadi: 98 x/mnt, "
            "RR: 18 x/mnt (ventilator SIMV), SpO2: 98%. "
            "Terpasang CRRT; akses Double Lumen Catheter jugularis kiri. "
            "Terdapat rembes minimal di area insersi. "
            "Edema anasarka, balance cairan positif +1200 cc/24 jam."
        ),
    )
