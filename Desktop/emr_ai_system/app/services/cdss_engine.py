"""
═══════════════════════════════════════════════════════════════════════════════
SMART CAREPLAN - IMPROVED CDSS ENGINE v2.1
═══════════════════════════════════════════════════════════════════════════════

IMPROVEMENTS:
✓ Weighted Scoring System (bukan simple threshold)
✓ Comprehensive Keyword Expansion (abbreviations, variations)
✓ Numeric Value Parsing & Clinical Interpretation
✓ Cardiac-Specific Rule Set (post-op, HF, ACS, shock, mechanical complications)
✓ Negation Detection & Context Awareness
✓ Diagnostic Interaction Rules
✓ Explicit Per-Diagnosis Numeric Boost Mapping (Prevent Overshoot v2.1)
"""

import re
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache


@lru_cache(maxsize=512)
def _compiled_keyword_pattern(keyword: str) -> "re.Pattern":
    """Compile & cache regex word-boundary untuk satu keyword."""
    return re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)


def contains_keyword(text: str, keyword: str) -> bool:
    """
    Cek apakah `keyword` muncul di `text` sebagai kata/frasa utuh
    (word-boundary), bukan sekadar substring.

    Contoh: contains_keyword("pola napas efektif", "ef") -> False
            contains_keyword("EF 35%", "ef")              -> True
    """
    return _compiled_keyword_pattern(keyword).search(text) is not None


def find_keyword_positions(text: str, keyword: str) -> List[int]:
    """Kembalikan SEMUA posisi awal kemunculan keyword (word-boundary)."""
    return [m.start() for m in _compiled_keyword_pattern(keyword).finditer(text)]


@dataclass
class NumericFinding:
    """Data class untuk menyimpan numeric findings"""
    parameter: str
    value: float
    unit: str
    clinical_level: str  # 'critical', 'high', 'moderate', 'normal'
    boost_score: int


class ClinicalValueLevel(Enum):
    """Enum untuk clinical interpretation levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MODERATE = "moderate"
    NORMAL = "normal"


class NumericValueParser:
    """Parser untuk extract dan interpret numeric clinical values (Fixed Thresholds)"""
    
    # Perbaikan rentang threshold agar nilai rendah/normal tidak terbaca sebagai critical tinggi
    THRESHOLDS = {
        'spo2': {
            'critical': (0, 85),
            'high': (85, 90),
            'moderate': (90, 94),
            'normal': (94, 101)
        },
        'hemoglobin': {
            'critical': (0, 7),
            'high': (7, 10),
            'moderate': (10, 13),
            'normal': (13, 25)
        },
        'ejection_fraction': {
            'critical': (0, 30),      # EF < 30% baru critical
            'high': (30, 40),
            'moderate': (40, 50),
            'normal': (50, 101)
        },
        'troponin': {
            'normal': (0, 0.04),
            'moderate': (0.04, 0.1),
            'high': (0.1, 0.5),
            'critical': (0.5, float('inf')) # Troponin tinggi baru critical
        },
        'bnp': {
            'normal': (0, 100),
            'moderate': (100, 200),
            'high': (200, 500),
            'critical': (500, float('inf')) # BNP > 500 baru critical
        },
        'heart_rate': {
            'critical_low': (0, 40),
            'normal': (40, 120),
            'high': (120, 300)
        },
        'systolic_bp': {
            'critical_low': (0, 91),   # FIX: TD=90 sebelumnya jatuh ke 'normal' (off-by-one)
            'normal': (91, 180),
            'high': (180, 300)
        },
        'lactate': {
            'normal': (0, 1),
            'moderate': (1, 2),
            'high': (2, 4),
            'critical': (4, float('inf'))
        }
    }
    
    BOOST_SCORES = {
        'ejection_fraction': {'critical': 5, 'high': 4, 'moderate': 2},
        'spo2': {'critical': 5, 'high': 3, 'moderate': 1},
        'hemoglobin': {'critical': 5, 'high': 3, 'moderate': 1},
        'troponin': {'critical': 5, 'high': 4, 'moderate': 2},
        'bnp': {'critical': 5, 'high': 3, 'moderate': 1},
        'heart_rate': {'critical_low': 4, 'high': 3},
        'systolic_bp': {'critical_low': 5, 'high': 2},
        'lactate': {'critical': 5, 'high': 3, 'moderate': 1}
    }
    
    # Pemetaan eksplisit per-diagnosis untuk menghindari global overshoot (v2.1)
    PARAM_TO_DIAGNOSIS = {
        'spo2': ['D.0003', 'D.0005'],
        'hemoglobin': ['D.0012', 'D.0009'],
        'ejection_fraction': ['D.0008', 'D.0022', 'D.0011', 'D.0016'],
        'troponin': ['D.0015', 'D.0008', 'D.0016'],
        'bnp': ['D.0008', 'D.0022', 'D.0016'],
        'heart_rate': ['D.0008', 'D.0015', 'D.0023', 'D.0109', 'D.0011'],
        'systolic_bp': ['D.0008', 'D.0023', 'D.0109', 'D.0011'],
        # PATCH (2026-06): lactate kini juga boost D.0109 (Risiko Syok) --
        # laktat meningkat adalah penanda hipoperfusi jaringan paling
        # langsung dipakai secara klinis untuk identifikasi syok.
        'lactate': ['D.0009', 'D.0008', 'D.0003', 'D.0109']
    }
    
    REGEX_PATTERNS = {
        'spo2': [
            r'(?i)(?:spo2|sp\.?o2|o2\s+sat)[:\s]*(\d+)\s*%?',
            r'(?i)saturasi\s*[:\s]*(\d+)\s*%?'
        ],
        'ejection_fraction': [
            r'(?i)(?:ef|ejection\s+fraction)[:\s]*(\d+)\s*%?',
            r'(?i)fraksi\s+ejeksi[:\s]*(\d+)\s*%?'
        ],
        'hemoglobin': [
            r'(?i)(?:hb|hemoglobin)[:\s]*(\d+(?:\.\d+)?)\s*(?:g|mg)?',
            r'(?i)hemoglobin[:\s]*(\d+(?:\.\d+)?)'
        ],
        'heart_rate': [
            r'(?i)(?:hr|heart\s+rate)[:\s]*(\d+)\s*(?:bpm)?',
            r'(?i)denyut\s+jantung[:\s]*(\d+)',
            r'(?i)\bnadi\b[:\s]*(\d+)'  # FIX: singkatan ICU "Nadi" (paling umum dipakai perawat, sebelumnya tidak terbaca)
        ],
        'systolic_bp': [
            r'(?i)(?:bp|blood\s+pressure|tekanan\s+darah)[:\s]*(\d+)\s*/\s*\d+',
            r'(?i)sistolik[:\s]*(\d+)',
            r'(?i)\btd\b[:\s]*(\d+)\s*/\s*\d+'  # FIX: singkatan ICU "TD" (sebelumnya tidak terbaca)
        ],
        'troponin': [
            r'(?i)troponin\s+[it][:\s]*(\d+(?:\.\d+)?)',
            r'(?i)troponin[:\s]*(\d+(?:\.\d+)?)'
        ],
        'bnp': [
            r'(?i)(?:bnp|b-type\s+natriuretic\s+peptide)[:\s]*(\d+(?:\.\d+)?)',
            r'(?i)nt[:-]?pro[:-]?bnp[:\s]*(\d+(?:\.\d+)?)'
        ],
        'lactate': [
            r'(?i)lactate\s*[:\s]*(\d+(?:\.\d+)?)',
            r'(?i)asam\s+laktat[:\s]*(\d+(?:\.\d+)?)'
        ]
    }
    
    @staticmethod
    def extract_numeric_findings(text: str) -> Dict[str, float]:
        """Extract numeric values dari text input"""
        findings = {}
        text_lower = text.lower()
        
        for param, patterns in NumericValueParser.REGEX_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text_lower)
                if match:
                    try:
                        value = float(match.group(1)) if '.' in match.group(1) else int(match.group(1))
                        findings[param] = value
                        break  # Use first match
                    except (ValueError, IndexError):
                        continue
        
        return findings
    
    @staticmethod
    def interpret_findings(findings: Dict[str, float]) -> Dict[str, str]:
        """Interpret numeric findings ke clinical levels"""
        interpretation = {}
        
        for param, value in findings.items():
            if param in NumericValueParser.THRESHOLDS:
                thresholds = NumericValueParser.THRESHOLDS[param]
                
                for level, (low, high) in thresholds.items():
                    if low <= value < high:
                        interpretation[param] = level
                        break
        
        return interpretation
    
    @staticmethod
    def get_boost_score(param: str, level: str) -> int:
        """Get boost score untuk diagnosis berdasarkan numeric findings"""
        boosts = NumericValueParser.BOOST_SCORES
        return boosts.get(param, {}).get(level, 0)


class NegationDetector:
    """Detect negations dalam clinical text untuk avoid false positives"""

    NEGATION_WORDS = [
        'tidak', 'tiada', 'bukan', 'tanpa',
        'no', 'without', 'absent', 'absence',
        'deny', 'denies', 'denied',
        'negative for', 'tidak ada', 'tidak terdapat', 'rule out',
    ]

    LOOKBACK_WORDS = 4

    @staticmethod
    def has_negation(text: str, keyword: str) -> bool:
        positions = find_keyword_positions(text, keyword)
        if not positions:
            return False

        any_non_negated_occurrence = False
        for keyword_pos in positions:
            preceding_words = re.findall(r'\w+', text[:keyword_pos].lower())
            window = preceding_words[-NegationDetector.LOOKBACK_WORDS:]
            window_text = ' '.join(window)

            is_negated_here = any(
                contains_keyword(window_text, neg) if ' ' not in neg
                else neg in window_text
                for neg in NegationDetector.NEGATION_WORDS
            )
            if not is_negated_here:
                any_non_negated_occurrence = True
                break

        # Negated overall hanya jika TIDAK ADA kemunculan yang bersih
        return not any_non_negated_occurrence
    
    @staticmethod
    def check_descriptor(text: str, finding: str) -> str:
        """Check if finding has positive/negative/neutral descriptor"""
        finding_lower = text.lower()
        
        # Define descriptors
        positive_descriptors = [
            'hangat', 'warm', 'kuat', 'strong', 'baik', 'good',
            'normal', 'membaik', 'improving', 'efektif', 'effective',
            '<2', 'crt <2', 'crt<2'
        ]
        
        negative_descriptors = [
            'dingin', 'cold', 'lemah', 'weak', 'buruk', 'bad',
            'abnormal', 'memburuk', 'worsening', 'tidak efektif',
            '>2', '>3', 'crt >2', 'crt >3', 'sianosis', 'cyanosis'
        ]
        
        # Check descriptors
        for desc in negative_descriptors:
            if desc in finding_lower:
                return 'negative'
        
        for desc in positive_descriptors:
            if desc in finding_lower:
                return 'positive'
        
        return 'neutral'


class WeightedKeywordScorer:
    """Scoring system dengan weighted keywords"""
    
    # Define comprehensive keyword list untuk setiap diagnosa dengan weights
    DIAGNOSIS_KEYWORDS = {
        'D.0001': {  # Bersihan Jalan Napas Tidak Efektif
            'high_priority': {
                'sekret': 5, 'sputum': 5, 'dahak': 5, 'suara napas tambahan': 5,
                'ronki': 5, 'wheezing': 5, 'batuk tidak efektif': 5
            },
            'medium_priority': {
                'batuk': 3, 'sputum berlebih': 3, 'lendir': 3, 'stridor': 3,
                'tersedak': 3, 'airway': 3
            },
            'low_priority': {
                'napas': 1, 'respiratory': 1
            },
            'threshold': 6,
            'high_priority_threshold': 3
        },
        'D.0003': {  # Gangguan Pertukaran Gas
            'high_priority': {
                'spo2': 5, 'saturasi': 5, 'hipoksia': 5, 'pco2': 5, 'po2': 5,
                'sianosis': 5, 'agd': 5, 'asidosis': 5
            },
            'medium_priority': {
                'konfusi': 3, 'gelisah': 3, 'restless': 3, 'hiperkapnia': 3
            },
            'low_priority': {
                'gaseous': 1, 'exchange': 1
            },
            'threshold': 6,
            'high_priority_threshold': 3
        },
        'D.0005': {  # Pola Napas Tidak Efektif
            'high_priority': {
                'takipnea': 5, 'bradipnea': 5, 'dispnea': 5, 'sesak napas': 5,
                'napas cepat': 5, 'napas dangkal': 5
            },
            'medium_priority': {
                'penggunaan otot bantu napas': 3, 'cuping hidung': 3, 'dyspnea': 3
            },
            'low_priority': {
                'pola': 1, 'pattern': 1
            },
            'threshold': 6,
            'high_priority_threshold': 3
        },
        'D.0008': {  # Penurunan Curah Jantung
            'high_priority': {
                'ef': 5, 'ejection fraction': 5, 'cardiogenic shock': 5,
                'hipotensi': 5, 'hipotensif': 5, 'td rendah': 5,
                'takikardia': 5, 'tachycardia': 5, 'bnp': 5,
                'curah jantung menurun': 5
            },
            'medium_priority': {
                'orthopnea': 3, 'pnd': 3, 'paroxysmal nocturnal dyspnea': 3,
                'edema paru': 3, 'pulmonary edema': 3, 'ronki': 3,
                'jvp tinggi': 3, 'cvp tinggi': 3, 'murmur': 3,
                's3': 3, 'gallop': 3, 'hepatomegali': 3, 'chf': 3,
                'gagal jantung': 3, 'heart failure': 3, 'ahf': 3
            },
            'low_priority': {
                'nyeri dada': 1, 'jantung': 1, 'cardiac': 1, 'aritmia': 1
            },
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0009': {  # Perfusi Perifer Tidak Efektif
            'high_priority': {
                'akral dingin': 5, 'cold extremities': 5, 'crt >2': 5, 'crt >3': 5,
                'capillary refill': 5, 'nadi lemah': 5, 'weak pulse': 5
            },
            'medium_priority': {
                'akral': 3, 'pale': 3, 'pucat': 3, 'edema perifer': 3,
                'sianosis perifer': 3, 'varises': 3, 'claudication': 3
            },
            'low_priority': {
                'perifer': 1, 'ekstremitas': 1, 'extremity': 1
            },
            'threshold': 6,
            'high_priority_threshold': 3
        },
        'D.0012': {  # Risiko Perdarahan
            'high_priority': {
                'perdarahan': 5, 'bleeding': 5, 'hemoglobin rendah': 5,
                'hb turun': 5, 'hb <': 5, 'trombositopenia': 5, 'plt rendah': 5
            },
            'medium_priority': {
                'post operasi': 3, 'post-op': 3, 'post-cabg': 3, 'postop': 3,
                'post-pci': 3, 'post-valve': 3, 'antikoagulan': 3, 'heparin': 3,
                'antiplatelet': 3, 'drain': 3, 'luka operasi': 3, 'surgical': 3,
                'koagulopati': 3, 'inr': 3, 'pt inr': 3
            },
            'low_priority': {
                'operasi': 1, 'luka': 1, 'surgery': 1
            },
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0015': {  # Risiko Perfusi Miokard Tidak Efektif
            'high_priority': {
                'iskemia': 5, 'acs': 5, 'stemi': 5, 'nstemi': 5,
                'angina': 5, 'troponin': 5, 'st elevasi': 5, 'st depresi': 5,
                'nyeri dada menjalar': 5
            },
            'medium_priority': {
                'diaphoresis': 3, 'cold sweat': 3, 'keringat dingin': 3,
                'nyeri dada kiri': 3, 'chest pain': 3, 'palpitasi': 3
            },
            'low_priority': {
                'miokard': 1, 'myocardial': 1
            },
            'threshold': 6,
            'high_priority_threshold': 3
        },
        'D.0022': {  # Hipervolemia
            'high_priority': {
                'edema': 5, 'asites': 5, 'edema paru': 5, 'overload cairan': 5,
                'bb naik cepat': 5, 'weight gain rapid': 5, 'jvp meningkat': 5,
                'jvp tinggi': 5, 'cvp tinggi': 5
            },
            'medium_priority': {
                'ronki basah': 3, 'crackles': 3, 'pulmonary edema': 3,
                'edema perifer': 3, 'oliguria': 3, 'breathlessness': 3
            },
            'low_priority': {
                'cairan': 1, 'fluid': 1, 'overload': 1
            },
            'threshold': 7,
            'high_priority_threshold': 4
        },
        'D.0023': {  # Hipovolemia
            'high_priority': {
                'dehidrasi': 5, 'dehydration': 5, 'turgor lambat': 5,
                'turgor menurun': 5, 'mukosa kering': 5, 'dry mucous': 5,
                'oliguria': 5
            },
            'medium_priority': {
                'muntah': 3, 'diare': 3, 'diarrhea': 3, 'haus': 3,
                'nadi lemah': 3, 'nadi cepat': 3, 'hipotensi ortostatik': 3
            },
            'low_priority': {
                'lemas': 1, 'weakness': 1
            },
            'threshold': 6,
            'high_priority_threshold': 3
        },

        # =====================================================================
        # PATCH (2026-06): 36 diagnosis baru ditambahkan agar setara cakupan
        # local_cdss_rule_engine() (49 rule blok, 45 kode unik) di dashboard.py.
        # Bobot keyword di-set 4 (high_priority) dengan threshold=8 -- ini
        # secara matematis SETARA aturan asli "minimal 2 kata kunci cocok"
        # di local_cdss_rule_engine, sambil otomatis mewarisi semua kapabilitas
        # v2.0 yang TIDAK dimiliki engine lokal: negation detection (mis. teks
        # "tidak ada edema" tidak akan salah trigger), weighted ranking/priority,
        # dan sinergi dengan numeric findings (lab/TTV) bila relevan.
        # =====================================================================
        'D.0006': {  # Risiko Aspirasi
            'high_priority': {
                'disfagia': 4, 'kesulitan menelan': 4, 'penurunan refleks menelan': 4, 'ngt': 4,
                'sonde': 4, 'penurunan kesadaran': 4, 'trakeostomi': 4, 'muntah berulang': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0017': {  # Risiko Perfusi Serebral Tidak Efektif
            'high_priority': {
                'stroke': 4, 'tia': 4, 'penurunan kesadaran': 4, 'hemiparesis': 4, 'afasia': 4, 'tekanan intrakranial': 4,
                'tic': 4, 'gcs menurun': 4, 'papil edema': 4, 'carotid stenosis': 4, 'atrial fibrilasi': 4,
                'emboli serebral': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0020': {  # Defisit Nutrisi
            'high_priority': {
                'berat badan turun': 4, 'bb turun': 4, 'bmi rendah': 4, 'malnutrisi': 4, 'anoreksia': 4,
                'mual': 4, 'tidak mau makan': 4, 'albumin rendah': 4, 'protein rendah': 4, 'kachexia': 4,
                'penurunan nafsu makan': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0025': {  # Ketidakstabilan Kadar Glukosa Darah
            'high_priority': {
                'hipoglikemia': 4, 'hiperglikemia': 4, 'gula darah': 4, 'gdp': 4, 'gds': 4, 'hba1c': 4,
                'diabetes': 4, 'dm': 4, 'dextrose': 4, 'pusing gula': 4, 'keringat gula': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0039': {  # Gangguan Eliminasi Urin
            'high_priority': {
                'urin sedikit': 4, 'oliguria': 4, 'anuria': 4, 'retensi urin': 4, 'disuria': 4, 'urgensi': 4,
                'frekuensi bak': 4, 'kateter urin': 4, 'kreatinin meningkat': 4, 'gagal ginjal': 4,
                'azotemia': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0038': {  # Konstipasi
            'high_priority': {
                'konstipasi': 4, 'bab keras': 4, 'tidak bab': 4, 'susah bab': 4, 'feses keras': 4,
                'distensi abdomen': 4, 'kembung': 4, 'perut keras': 4, 'ileus': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0040': {  # Diare
            'high_priority': {
                'diare': 4, 'bab cair': 4, 'bab >3x': 4, 'feses cair': 4, 'mencret': 4, 'gastroenteritis': 4,
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0056': {  # Intoleransi Aktivitas
            'high_priority': {
                'sesak saat aktivitas': 4, 'lelah': 4, 'lemah': 4, 'tidak mampu beraktivitas': 4,
                'dyspnea on effort': 4, 'doe': 4, 'aktivitas terbatas': 4, 'toleransi rendah': 4,
                'kelelahan ekstrem': 4, 'kapasitas fungsional rendah': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0055': {  # Gangguan Pola Tidur
            'high_priority': {
                'insomnia': 4, 'tidak bisa tidur': 4, 'sering terbangun': 4, 'tidur tidak nyenyak': 4,
                'gelisah malam': 4, 'gangguan tidur': 4, 'nyeri mengganggu tidur': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0057': {  # Keletihan
            'high_priority': {
                'kelelahan kronis': 4, 'fatigue': 4, 'tidak bertenaga': 4, 'exhausted': 4, 'lemah berat': 4,
                'tidak mampu konsentrasi karena lelah': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0062': {  # Gangguan Komunikasi Verbal
            'high_priority': {
                'afasia': 4, 'tidak bisa bicara': 4, 'bicara pelo': 4, 'disartria': 4, 'sulit berkomunikasi': 4,
                'gagap baru': 4, 'pasca stroke bicara': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0063': {  # Konfusi Akut
            'high_priority': {
                'bingung': 4, 'disorientasi': 4, 'konfusi': 4, 'delirium': 4, 'gelisah tanpa sebab': 4,
                'agitasi': 4, 'tidak kenal keluarga': 4, 'halusinasi': 4, 'bingung waktu tempat': 4,
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0077': {  # Nyeri Akut
            'high_priority': {
                'nyeri': 4, 'sakit': 4, 'pain': 4, 'vas': 4, 'nrs': 4, 'visual analogue scale': 4,
                'nyeri dada': 4, 'nyeri perut': 4, 'nyeri kepala': 4, 'nyeri luka': 4, 'nyeri post op': 4,
                'nyeri saat napas': 4, 'nyeri sternotomi': 4, 'nyeri luka sternum': 4, 'nyeri pasca cabg': 4,
                'nyeri pasca operasi jantung': 4, 'nyeri post operasi jantung': 4, 'nyeri sternum': 4,
                'nyeri dada post op': 4, 'nyeri thoracotomy': 4, 'nyeri insisi dada': 4, 'nyeri pasca torakotomi': 4,
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0078': {  # Nyeri Kronis
            'high_priority': {
                'nyeri kronis': 4, 'nyeri berulang': 4, 'nyeri >3 bulan': 4, 'nyeri persisten': 4,
                'neuropati': 4, 'nyeri neuropatik': 4, 'nyeri sudah lama': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0080': {  # Ansietas
            'high_priority': {
                'cemas': 4, 'khawatir': 4, 'takut': 4, 'ansietas': 4, 'gelisah': 4, 'panik': 4, 'jantung berdebar karena takut': 4,
                'tidak tenang': 4, 'anxious': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0129': {  # Gangguan Integritas Kulit/Jaringan
            'high_priority': {
                'luka': 4, 'dekubitus': 4, 'kemerahan kulit': 4, 'gesekan': 4, 'tirah baring': 4,
                'bedrest lama': 4, 'luka tekan': 4, 'pressure injury': 4, 'pressure ulcer': 4,
                'luka bakar': 4, 'lecet': 4, 'bulla': 4, 'vesikel': 4, 'ulkus': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0142': {  # Risiko Infeksi
            'high_priority': {
                'iv line': 4, 'kateter': 4, 'operasi': 4, 'leukosit tinggi': 4, 'demam': 4, 'suhu >38': 4,
                'suhu >38.5': 4, 'pneumonia': 4, 'invasif': 4, 'wbc tinggi': 4, 'infeksi': 4, 'sepsis': 4,
                'iak': 4, 'isk': 4, 'hap': 4, 'vap': 4, 'clabsi': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0136': {  # Risiko Jatuh
            'high_priority': {
                'risiko jatuh': 4, 'morse fall': 4, 'lansia': 4, 'lemah': 4, 'vertigo': 4, 'pusing berdiri': 4,
                'riwayat jatuh': 4, 'sedatif': 4, 'diuretik malam': 4, 'balance terganggu': 4,
                'gaya berjalan tidak stabil': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0131': {  # Hipotermia
            'high_priority': {
                'hipotermia': 4, 'suhu rendah': 4, 'suhu <36': 4, 'suhu <35': 4, 'menggigil berat': 4,
                'kedinginan': 4, 'cold exposure': 4, 'akral sangat dingin': 4, 'perioperatif hipotermia': 4,
                'post bypass hipotermia': 4, 'ttm': 4, 'target temperature management': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0109': {  # Risiko Syok
            'high_priority': {
                'syok': 4, 'hipotensi berat': 4, 'td sistolik <90': 4, 'td <90/60': 4, 'map rendah': 4,
                'map <65': 4, 'tachycardia kompensasi': 4, 'oliguria syok': 4, 'mottling': 4, 'nadi filiform': 4,
                'capillary refill >3': 4, 'lactic acidosis': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0004': {  # Gangguan Ventilasi Spontan
            'high_priority': {
                'ventilasi mekanik': 4, 'tidak bisa napas spontan': 4, 'apnea': 4, 'apnoe': 4,
                'gagal napas': 4, 'respiratory failure': 4, 'fio2 tinggi': 4, 'peep': 4, 'mode vc': 4,
                'mode pc': 4, 'ards': 4, 'kelelahan otot napas': 4, 'tidak bisa lepas ventilator': 4,
                'tergantung ventilator': 4, 'drive napas hilang': 4, 'co2 retensi berat': 4,
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0002': {  # Gangguan Penyapihan Ventilator
            'high_priority': {
                'penyapihan': 4, 'weaning': 4, 'sapih ventilator': 4, 'trial sbt': 4, 'sbt': 4, 'spontaneous breathing trial': 4,
                'gagal weaning': 4, 'ekstubasi': 4, 'rencana ekstubasi': 4, 'psmv': 4, 'psv mode': 4,
                'cpap mode': 4, 'rapid shallow breathing': 4, 'rsbi': 4, 'tidak toleran sbt': 4,
                'distress pasca ekstubasi': 4, 'reintubasi': 4, 'post extubation stridor': 4,
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0007': {  # Gangguan Sirkulasi Spontan
            'high_priority': {
                'henti jantung': 4, 'cardiac arrest': 4, 'vf': 4, 'ventrikel fibrilasi': 4, 'pulseless vt': 4,
                'vt tanpa nadi': 4, 'asistol': 4, 'pea': 4, 'rosc': 4, 'return of spontaneous circulation': 4,
                'post rosc': 4, 'rjp': 4, 'resusitasi': 4, 'defibrilasi': 4, 'defibrillasi': 4, 'aed': 4,
                'tidak ada nadi': 4, 'henti nafas dan jantung': 4, 'dnr': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0010': {  # Risiko Gangguan Sirkulasi Spontan
            'high_priority': {
                'risiko henti jantung': 4, 'aritmia mengancam': 4, 'vt stabil': 4, 'blok av derajat 3': 4,
                'blok av total': 4, 'av block komplit': 4, 'qt memanjang': 4, 'lqts': 4, 'torsades': 4,
                'torsade de pointes': 4, 'bradikardi berat': 4, 'hr < 40': 4, 'bradikardia simtomatik': 4,
                'sinkop berulang': 4, 'pre-arrest': 4, 'deteriorasi klinis cepat': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0011': {  # Risiko Penurunan Curah Jantung
            'high_priority': {
                'risiko penurunan curah jantung': 4, 'ef borderline': 4, 'ef 40': 4, 'disfungsi diastolik': 4,
                'hipertrofi ventrikel': 4, 'hipertensi tidak terkontrol': 4, 'stenosis aorta berat': 4,
                'regurgitasi mitral': 4, 'mr berat': 4, 'as berat': 4, 'kardiomiopati': 4, 'amyloidosis jantung': 4,
                'miokarditis': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0014': {  # Gangguan Perfusi Serebral
            'high_priority': {
                'stroke iskemik': 4, 'stroke hemoragik': 4, 'ich': 4, 'sah': 4, 'penurunan gcs': 4,
                'gcs turun': 4, 'hemiplegia': 4, 'hemiparesis aktual': 4, 'afasia aktual': 4, 'disartria berat': 4,
                'hemianopia': 4, 'ptosis mendadak': 4, 'deviasi mata': 4, 'babinski positif': 4,
                'perdarahan intra serebral': 4, 'infark serebri': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0013': {  # Risiko Perfusi Pulmonal Tidak Efektif
            'high_priority': {
                'emboli paru': 4, 'pulmonary embolism': 4, 'pe': 4, 'tromboemboli': 4, 'dvt': 4,
                'deep vein thrombosis': 4, 'nyeri dada pleuritik': 4, 'hemoptisis': 4, 'tachycardia tanpa sebab': 4,
                'd-dimer tinggi': 4, 'troponin naik pe': 4, 'strain ventrikel kanan': 4, 's1q3t3': 4,
                'hipoksia mendadak': 4, 'wells score': 4, 'right heart strain': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0016': {  # Gangguan Status Kardiopulmonal
            'high_priority': {
                'gagal napas dan jantung bersamaan': 4, 'acute cor pulmonale': 4, 'right heart failure akut': 4,
                'pulmonary hypertension krisis': 4, 'hipertensi pulmonal berat': 4, 'phtn': 4,
                'svri tinggi': 4, 'pvri tinggi': 4, 'right ventricular failure': 4, 'rvf': 4, 'hepatojugular reflux': 4,
                'pericardial effusion': 4, 'tamponade': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0037': {  # Risiko Ketidakseimbangan Elektrolit
            'high_priority': {
                'hipokalemia': 4, 'hiperkalemia': 4, 'kalium rendah': 4, 'kalium tinggi': 4, 'k+ rendah': 4,
                'k+ tinggi': 4, 'k <3': 4, 'k >5.5': 4, 'k <3.5': 4, 'k >6': 4, 'kelemahan otot': 4,
                'kram otot': 4, 'aritmia elektrolit': 4, 'gelombang u': 4, 'gelombang t tinggi': 4,
                'peaked t wave': 4, 'serum kalium': 4, 'hipokalemi': 4, 'hiperkalemi': 4, 'hiponatremia': 4,
                'hipernatremia': 4, 'natrium rendah': 4, 'natrium tinggi': 4, 'na rendah': 4, 'na tinggi': 4,
                'na <130': 4, 'na >150': 4, 'siadh': 4, 'dilusi natrium': 4, 'sodium rendah': 4,
                'hiponatrem': 4, 'hipernatrem': 4, 'penurunan kesadaran elektrolit': 4, 'kejang elektrolit': 4,
                'hipomagnesemia': 4, 'magnesium rendah': 4, 'mg rendah': 4, 'mg <1.5': 4, 'hipofosfatemia': 4,
                'fosfat rendah': 4, 'po4 rendah': 4, 'refeeding syndrome': 4, 'aritmia refrakter': 4,
                'prolonged qt': 4, 'tetani': 4, 'kram halus': 4, 'tanda chvostek': 4, 'tanda trousseau': 4,
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0021': {  # Ketidakseimbangan Asam-Basa
            'high_priority': {
                'asidosis metabolik': 4, 'alkalosis metabolik': 4, 'asidosis respiratorik': 4,
                'alkalosis respiratorik': 4, 'ph rendah': 4, 'ph tinggi': 4, 'ph <7.35': 4, 'ph >7.45': 4,
                'bikarbonat rendah': 4, 'bikarbonat tinggi': 4, 'be negatif': 4, 'base excess negatif': 4,
                'laktat tinggi': 4, 'lactat': 4, 'lactic': 4, 'agd asidosis': 4, 'agd alkalosis': 4,
                'ketoasidosis': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0130': {  # Hipertermia
            'high_priority': {
                'demam tinggi': 4, 'hipertermia': 4, 'suhu >39': 4, 'suhu 40': 4, 'suhu 39': 4, 'fever': 4,
                'suhu tubuh meningkat': 4, 'panas tinggi': 4, 'hiperpireksia': 4, 'demam pasca operasi': 4,
                'demam hari ke': 4, 'febris': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0143': {  # Infeksi (Sepsis)
            'high_priority': {
                'sepsis': 4, 'septik': 4, 'septic shock': 4, 'syok sepsis': 4, 'pct tinggi': 4, 'procalcitonin tinggi': 4,
                'qsofa': 4, 'sofa score': 4, 'bakteremia': 4, 'infeksi sistemik': 4, 'wbc >12000': 4,
                'wbc <4000': 4, 'bandemia': 4, 'kultur positif': 4, 'bacteremia': 4, 'fungemia': 4,
                'candida sistemik': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0054': {  # Gangguan Mobilitas Fisik
            'high_priority': {
                'tidak bisa bergerak': 4, 'hemiplegia': 4, 'paraplegia': 4, 'tetraplegia': 4, 'kelemahan anggota gerak': 4,
                'imobilisasi': 4, 'bedrest total': 4, 'kekuatan otot menurun': 4, 'tidak bisa berdiri sendiri': 4,
                'kekuatan otot 0': 4, 'kekuatan otot 1': 4, 'kekuatan otot 2': 4, 'pasca operasi mobilitas': 4,
                'post stroke mobilitas': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0058': {  # Hambatan Ambulasi
            'high_priority': {
                'tidak bisa berjalan': 4, 'kesulitan berjalan': 4, 'gaya berjalan terganggu': 4,
                'membutuhkan alat bantu jalan': 4, 'walker': 4, 'kruk': 4, 'tripod': 4, 'kursi roda': 4,
                'pasca amputasi': 4, 'luka kaki diabetik': 4, 'neuropati perifer berat': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0082': {  # Gangguan Citra Tubuh
            'high_priority': {
                'tidak menerima kondisi': 4, 'malu dengan kondisi fisik': 4, 'menolak melihat luka': 4,
                'cemas dengan perubahan tubuh': 4, 'tidak percaya diri': 4, 'merasa tidak sempurna': 4,
                'pasca amputasi perasaan': 4, 'scar sternotomi': 4, 'bekas operasi': 4, 'stoma': 4,
                'body image terganggu': 4
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
        'D.0087': {  # Ketidakberdayaan
            'high_priority': {
                'merasa tidak berguna': 4, 'putus asa': 4, 'tidak ada harapan': 4, 'menyerah': 4,
                'tidak mau berobat': 4, 'menolak terapi': 4, 'pasrah berlebihan': 4, 'depresi berat klinis': 4,
                'tidak mau makan karena putus asa': 4, 'tidak mau rehabilitasi': 4, 'niat bunuh diri': 4,
            },
            'medium_priority': {},
            'low_priority': {},
            'threshold': 8,
            'high_priority_threshold': 4
        },
    }
    
    @staticmethod
    def calculate_score(text: str, diagnosis_code: str) -> Tuple[int, bool]:
        """
        Calculate score untuk diagnosis dengan weighted keywords
        Returns: (score, has_high_priority)
        """
        if diagnosis_code not in WeightedKeywordScorer.DIAGNOSIS_KEYWORDS:
            return 0, False
        
        diagnosis_info = WeightedKeywordScorer.DIAGNOSIS_KEYWORDS[diagnosis_code]
        text_lower = text.lower()
        
        score = 0
        has_high_priority = False
        
        # Check high priority keywords
        for keyword, weight in diagnosis_info['high_priority'].items():
            if contains_keyword(text_lower, keyword):
                if not NegationDetector.has_negation(text_lower, keyword):
                    score += weight
                    has_high_priority = True
        
        # Check medium priority keywords
        for keyword, weight in diagnosis_info['medium_priority'].items():
            if contains_keyword(text_lower, keyword):
                if not NegationDetector.has_negation(text_lower, keyword):
                    score += weight
        
        # Check low priority keywords
        for keyword, weight in diagnosis_info['low_priority'].items():
            if contains_keyword(text_lower, keyword):
                if not NegationDetector.has_negation(text_lower, keyword):
                    score += weight
        
        return score, has_high_priority


class CardiacSpecificRules:
    """Rule set khusus untuk cardiac conditions"""
    
    @staticmethod
    def is_postoperative_cardiac(text: str) -> bool:
        """Detect if case is post-operative cardiac"""
        postop_keywords = [
            'post operasi', 'post-op', 'postop', 'post-cabg', 'cabg',
            'post-pci', 'post pci', 'post-valve', 'bypass', 'graft',
            'svg', 'lima', 'imainto', 'off-pump', 'on-pump'
        ]
        text_lower = text.lower()
        return any(contains_keyword(text_lower, kw) for kw in postop_keywords)
    
    @staticmethod
    def is_acute_decompensated_hf(text: str, numeric_findings: Dict) -> bool:
        """Detect acute decompensated heart failure pattern"""
        hf_keywords = ['orthopnea', 'pnd', 'edema paru', 'pulmonary edema', 'chf', 'ahf']
        text_lower = text.lower()
        has_hf_indicators = any(contains_keyword(text_lower, kw) for kw in hf_keywords)
        
        # Additional check: EF < 40%
        if 'ejection_fraction' in numeric_findings:
            if numeric_findings['ejection_fraction'] < 40:
                has_hf_indicators = True
        
        return has_hf_indicators
    
    @staticmethod
    def is_acute_coronary_syndrome(text: str, numeric_findings: Dict) -> bool:
        """Detect acute coronary syndrome pattern"""
        acs_keywords = ['acs', 'stemi', 'nstemi', 'st elevasi', 'st depresi', 'angina']
        text_lower = text.lower()
        has_acs_indicators = any(contains_keyword(text_lower, kw) for kw in acs_keywords)
        
        # Additional check: Troponin elevation
        if 'troponin' in numeric_findings:
            if numeric_findings['troponin'] > 0.04:
                has_acs_indicators = True
        
        return has_acs_indicators
    
    @staticmethod
    def is_cardiogenic_shock(text: str, numeric_findings: Dict) -> bool:
        """Detect cardiogenic shock pattern"""
        shock_keywords = [
            'cardiogenic shock', 'syok kardiogenik', 'shock', 'hypotension',
            'hipotensi', 'poor perfusion', 'perfusi buruk', 'cold extremities',
            # FIX: dukungan sirkulasi mekanik & vasopresor/inotropik dosis tinggi
            # adalah indikator klinis kuat syok kardiogenik refrakter, sebelumnya
            # sama sekali tidak dikenali oleh engine.
            'iabp', 'intra-aortic balloon', 'norepinephrine', 'norepinefrin',
            'vasopressin', 'farpresin', 'adrenalin', 'epinephrine',
            'dobutamin', 'dobutamine', 'milrinone'
        ]
        text_lower = text.lower()
        has_shock_indicators = any(contains_keyword(text_lower, kw) for kw in shock_keywords)

        # Additional check: SBP <= 90 + tachycardia
        # FIX: boundary diperbaiki dari "< 90" menjadi "<= 90" (TD 90 tetap signifikan
        # secara klinis terutama disertai takikardia berat / aritmia)
        if 'systolic_bp' in numeric_findings and 'heart_rate' in numeric_findings:
            if numeric_findings['systolic_bp'] <= 90 and numeric_findings['heart_rate'] > 100:
                has_shock_indicators = True

        # FIX: ≥2 obat vasopressor/inotropik berbeda = indikator kuat syok refrakter
        pressor_drugs = ['norepinephrine', 'norepinefrin', 'vasopressin', 'farpresin',
                          'adrenalin', 'epinephrine', 'dobutamin', 'dobutamine', 'milrinone']
        pressor_count = sum(1 for kw in pressor_drugs if contains_keyword(text_lower, kw))
        if pressor_count >= 2:
            has_shock_indicators = True

        return has_shock_indicators
    
    @staticmethod
    def is_mechanical_complication(text: str) -> bool:
        """Detect mechanical complications (VSD, MR, tamponade, free wall rupture)"""
        complication_keywords = [
            'vsd', 'ventricular septal defect', 'murmur',
            'mitral regurgitation', 'mr', 'papillary muscle', 'tamponade',
            'free wall rupture', 'ruptur', 'mechanical complication'
        ]
        text_lower = text.lower()
        return any(contains_keyword(text_lower, kw) for kw in complication_keywords)
    
    @staticmethod
    def is_on_ecmo_vad(text: str) -> bool:
        """Detect if patient is on ECMO/VAD/IABP support"""
        support_keywords = [
            'ecmo', 'vad', 'impella', 'tandem heart', 'mechanical support',
            'iabp', 'intra-aortic balloon', 'intra aortic balloon',
            'balon pompa intra aorta'  # FIX: IABP sebelumnya tidak terdeteksi sama sekali
        ]
        text_lower = text.lower()
        return any(contains_keyword(text_lower, kw) for kw in support_keywords)


@dataclass
class DiagnosisRecommendation:
    """Data class untuk diagnosis recommendation"""
    code: str
    name: str
    score: int
    priority: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    has_high_priority_keyword: bool
    numeric_findings_boost: int
    cardiac_context_boost: int


class ImprovedCDSSEngine:
    """Main CDSS Engine dengan semua improvements"""
    
    # Master data diagnosa
    MASTER_DIAGNOSES = {
        'D.0001': {
            'name': 'Bersihan Jalan Napas Tidak Efektif',
            'luaran': 'Bersihan Jalan Napas Meningkat (L.01001)'
        },
        'D.0003': {
            'name': 'Gangguan Pertukaran Gas',
            'luaran': 'Pertukaran Gas Meningkat (L.01003)'
        },
        'D.0005': {
            'name': 'Pola Napas Tidak Efektif',
            'luaran': 'Pola Napas Membaik (L.01004)'
        },
        'D.0008': {
            'name': 'Penurunan Curah Jantung',
            'luaran': 'Curah Jantung Meningkat (L.02008)'
        },
        'D.0009': {
            'name': 'Perfusi Perifer Tidak Efektif',
            'luaran': 'Perfusi Perifer Meningkat (L.02011)'
        },
        'D.0012': {
            'name': 'Risiko Perdarahan',
            'luaran': 'Tingkat Perdarahan Menurun (L.02012)'
        },
        'D.0015': {
            'name': 'Risiko Perfusi Miokard Tidak Efektif',
            'luaran': 'Perfusi Miokard Meningkat (L.02010)'
        },
        'D.0022': {
            'name': 'Hipervolemia',
            'luaran': 'Status Cairan Membaik (L.03028)'
        },
        'D.0023': {
            'name': 'Hipovolemia',
            'luaran': 'Status Cairan Membaik (L.03028)'
        },

        # =====================================================================
        # PATCH (2026-06): Pelengkapan MASTER_DIAGNOSES dari 9 -> 45 kode unik,
        # setara cakupan local_cdss_rule_engine() (49 rule blok) di dashboard.py.
        # Sebelumnya, diagnosis berikut HANYA bisa muncul lewat fallback mentah
        # (keyword-only, tanpa scoring/numeric/negation) karena tidak terdaftar
        # di engine utama (v2.0) -- termasuk D.0109 (Risiko Syok) & D.0011
        # (Risiko Penurunan Curah Jantung) yang krusial untuk kasus syok kardiak.
        #
        # Catatan penggabungan kode (lihat juga DIAGNOSIS_KEYWORDS):
        #  - D.0037 di lokal punya 3 blok terpisah (Kalium/Natrium/Mg-Fosfat) ->
        #    digabung jadi 1 diagnosis "Risiko Ketidakseimbangan Elektrolit"
        #    dengan keyword gabungan (SDKI memang satu kode untuk ketiganya).
        #  - D.0077 di lokal punya 2 blok (nyeri umum + nyeri kardiak/sternotomi)
        #    -> digabung jadi 1 (sama-sama "Nyeri Akut").
        #  - D.0131 di lokal KOLISI: dipakai untuk 2 diagnosis BERBEDA
        #    ("Risiko Komplikasi Pascabedah" dan "Hipotermia"). Kode D.0131
        #    yang benar menurut SDKI adalah Hipotermia, jadi itu yang dipakai
        #    di sini. "Risiko Komplikasi Pascabedah" BUKAN label SDKI baku dan
        #    substansinya sudah tercakup diagnosis lain (D.0012 Perdarahan,
        #    D.0142 Infeksi, D.0001/0003/0005 respirasi, D.0008/0011 kardiak)
        #    -- lihat dashboard.py untuk perbaikan kode di sisi local engine.
        # =====================================================================
        'D.0006': {
            'name': 'Risiko Aspirasi',
            'luaran': 'Tingkat Aspirasi Menurun (L.01006)'
        },
        'D.0017': {
            'name': 'Risiko Perfusi Serebral Tidak Efektif',
            'luaran': 'Perfusi Serebral Meningkat (L.02014)'
        },
        'D.0020': {
            'name': 'Defisit Nutrisi',
            'luaran': 'Status Nutrisi Membaik (L.03030)'
        },
        'D.0025': {
            'name': 'Ketidakstabilan Kadar Glukosa Darah',
            'luaran': 'Kestabilan Kadar Glukosa Darah Membaik (L.03022)'
        },
        'D.0039': {
            'name': 'Gangguan Eliminasi Urin',
            'luaran': 'Eliminasi Urin Membaik (L.04034)'
        },
        'D.0038': {
            'name': 'Konstipasi',
            'luaran': 'Eliminasi Fekal Membaik (L.04033)'
        },
        'D.0040': {
            'name': 'Diare',
            'luaran': 'Eliminasi Fekal Membaik (L.04033)'
        },
        'D.0056': {
            'name': 'Intoleransi Aktivitas',
            'luaran': 'Toleransi Aktivitas Meningkat (L.05047)'
        },
        'D.0055': {
            'name': 'Gangguan Pola Tidur',
            'luaran': 'Status Tidur Membaik (L.05045)'
        },
        'D.0057': {
            'name': 'Keletihan',
            'luaran': 'Konservasi Energi Meningkat (L.05040)'
        },
        'D.0062': {
            'name': 'Gangguan Komunikasi Verbal',
            'luaran': 'Komunikasi Verbal Meningkat (L.13118)'
        },
        'D.0063': {
            'name': 'Konfusi Akut',
            'luaran': 'Orientasi Kognitif Meningkat (L.09082)'
        },
        'D.0077': {
            'name': 'Nyeri Akut',
            'luaran': 'Tingkat Nyeri Menurun (L.08066)'
        },
        'D.0078': {
            'name': 'Nyeri Kronis',
            'luaran': 'Tingkat Nyeri Menurun (L.08066)'
        },
        'D.0080': {
            'name': 'Ansietas',
            'luaran': 'Tingkat Ansietas Menurun (L.09093)'
        },
        'D.0129': {
            'name': 'Gangguan Integritas Kulit/Jaringan',
            'luaran': 'Integritas Kulit dan Jaringan Meningkat (L.14125)'
        },
        'D.0142': {
            'name': 'Risiko Infeksi',
            'luaran': 'Tingkat Infeksi Menurun (L.14137)'
        },
        'D.0136': {
            'name': 'Risiko Jatuh',
            'luaran': 'Tingkat Jatuh Menurun (L.14138)'
        },
        'D.0131': {
            'name': 'Hipotermia',
            'luaran': 'Termoregulasi Membaik (L.14134)'
        },
        'D.0109': {
            'name': 'Risiko Syok',
            'luaran': 'Status Kardiopulmonal Membaik (L.02016)'
        },
        'D.0004': {
            'name': 'Gangguan Ventilasi Spontan',
            'luaran': 'Ventilasi Spontan Meningkat (L.01007)'
        },
        'D.0002': {
            'name': 'Gangguan Penyapihan Ventilator',
            'luaran': 'Penyapihan Ventilator Meningkat (L.01002)'
        },
        'D.0007': {
            'name': 'Gangguan Sirkulasi Spontan',
            'luaran': 'Sirkulasi Spontan Meningkat (L.02015)'
        },
        'D.0010': {
            'name': 'Risiko Gangguan Sirkulasi Spontan',
            'luaran': 'Sirkulasi Spontan Meningkat (L.02015)'
        },
        'D.0011': {
            'name': 'Risiko Penurunan Curah Jantung',
            'luaran': 'Curah Jantung Meningkat (L.02008)'
        },
        'D.0014': {
            'name': 'Gangguan Perfusi Serebral',
            'luaran': 'Perfusi Serebral Meningkat (L.02014)'
        },
        'D.0013': {
            'name': 'Risiko Perfusi Pulmonal Tidak Efektif',
            'luaran': 'Perfusi Pulmonal Meningkat (L.02013)'
        },
        'D.0016': {
            'name': 'Gangguan Status Kardiopulmonal',
            'luaran': 'Status Kardiopulmonal Membaik (L.02016)'
        },
        'D.0037': {
            'name': 'Risiko Ketidakseimbangan Elektrolit',
            'luaran': 'Keseimbangan Elektrolit Membaik (L.03021)'
        },
        'D.0021': {
            'name': 'Ketidakseimbangan Asam-Basa',
            'luaran': 'Keseimbangan Asam Basa Membaik (L.02009)'
        },
        'D.0130': {
            'name': 'Hipertermia',
            'luaran': 'Termoregulasi Membaik (L.14134)'
        },
        'D.0143': {
            'name': 'Infeksi (Sepsis)',
            'luaran': 'Tingkat Infeksi Menurun (L.14137)'
        },
        'D.0054': {
            'name': 'Gangguan Mobilitas Fisik',
            'luaran': 'Mobilitas Fisik Meningkat (L.05042)'
        },
        'D.0058': {
            'name': 'Hambatan Ambulasi',
            'luaran': 'Ambulasi Meningkat (L.05001)'
        },
        'D.0082': {
            'name': 'Gangguan Citra Tubuh',
            'luaran': 'Citra Tubuh Meningkat (L.09067)'
        },
        'D.0087': {
            'name': 'Ketidakberdayaan',
            'luaran': 'Tingkat Stres Menurun (L.09092)'
        }
    }
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize input text"""
        text = text.lower().strip()
        text = re.sub(r'#{1,3}\s?', '', text)
        text = re.sub(r'\*\*', '', text)
        text = re.sub(r'^- ', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n\s*\n', ' ', text)
        return text
    
    @staticmethod
    def analyze(s_text: str, o_text: str) -> Dict:
        """
        Main analysis function
        Input: Subjective (S) and Objective (O) data
        Output: Ranked list of diagnoses dengan reasoning
        """
        # Normalize input
        combined_text = ImprovedCDSSEngine.normalize_text(s_text + " " + o_text)
        
        # Step 1: Extract numeric findings
        numeric_findings = NumericValueParser.extract_numeric_findings(combined_text)
        numeric_interpretation = NumericValueParser.interpret_findings(numeric_findings)
        
        # Step 2: Determine clinical context
        is_postop = CardiacSpecificRules.is_postoperative_cardiac(combined_text)
        is_hf = CardiacSpecificRules.is_acute_decompensated_hf(combined_text, numeric_findings)
        is_acs = CardiacSpecificRules.is_acute_coronary_syndrome(combined_text, numeric_findings)
        is_shock = CardiacSpecificRules.is_cardiogenic_shock(combined_text, numeric_findings)
        has_mech_comp = CardiacSpecificRules.is_mechanical_complication(combined_text)
        is_on_support = CardiacSpecificRules.is_on_ecmo_vad(combined_text)
        
        # Step 3: Calculate diagnosis scores
        recommendations = []
        
        for diagnosis_code, diagnosis_info in ImprovedCDSSEngine.MASTER_DIAGNOSES.items():
            # Get base score dari keyword matching
            base_score, has_high_priority = WeightedKeywordScorer.calculate_score(
                combined_text, 
                diagnosis_code
            )
            
            # Get numeric boost SECARA SPESIFIK per diagnosa (Mencegah global overshoot v2.1)
            numeric_boost = 0
            for param, level in numeric_interpretation.items():
                allowed_diagnoses = NumericValueParser.PARAM_TO_DIAGNOSIS.get(param, [])
                if diagnosis_code in allowed_diagnoses:
                    numeric_boost += NumericValueParser.get_boost_score(param, level)
            
            # Get cardiac context boost
            cardiac_boost = 0
            if is_postop:
                # Boost untuk D.0012 (Perdarahan) pada post-op
                if diagnosis_code == 'D.0012':
                    cardiac_boost += 3
                # PATCH: post-op cardiac juga relevan untuk risiko infeksi
                if diagnosis_code == 'D.0142':
                    cardiac_boost += 2
            
            if is_hf:
                # Boost untuk D.0008, D.0022, D.0003, D.0005 pada HF
                if diagnosis_code in ['D.0008', 'D.0022', 'D.0003', 'D.0005']:
                    cardiac_boost += 2
                # PATCH: HF dekompensata juga relevan untuk status kardiopulmonal
                if diagnosis_code == 'D.0016':
                    cardiac_boost += 2
            
            if is_acs:
                # Boost untuk D.0015, D.0008 pada ACS
                if diagnosis_code in ['D.0015', 'D.0008']:
                    cardiac_boost += 2
            
            if is_shock:
                # Boost untuk D.0008, D.0009 pada shock
                if diagnosis_code in ['D.0008', 'D.0009']:
                    cardiac_boost += 3
                # PATCH: syok kardiogenik secara klinis = Risiko Syok (D.0109)
                # & Risiko Penurunan Curah Jantung (D.0011) -- sebelumnya kedua
                # diagnosis ini tidak ada sama sekali di v2.0 (lihat patch
                # MASTER_DIAGNOSES di atas) sehingga tidak pernah terdeteksi.
                if diagnosis_code in ['D.0109', 'D.0011', 'D.0016']:
                    cardiac_boost += 3
            
            if is_on_support:
                # Boost untuk D.0012 pada ECMO/IABP (risiko perdarahan kanulasi/sheath)
                if diagnosis_code == 'D.0012':
                    cardiac_boost += 2
                # PATCH: dukungan mekanis (ECMO/VAD/IABP) = indikator independen
                # disfungsi sirkulasi berat, relevan untuk D.0011 & D.0109
                if diagnosis_code in ['D.0011', 'D.0109']:
                    cardiac_boost += 2
            
            # Calculate final score
            final_score = base_score + numeric_boost + cardiac_boost
            
            # Determine threshold untuk diagnosis
            if diagnosis_code in WeightedKeywordScorer.DIAGNOSIS_KEYWORDS:
                threshold = WeightedKeywordScorer.DIAGNOSIS_KEYWORDS[diagnosis_code]['threshold']
            else:
                threshold = 6
            
            # Check if diagnosis should appear
            should_appear = (
                (final_score >= threshold and has_high_priority) or
                final_score >= (threshold + 2)
            )
            
            if should_appear:
                # Determine priority level
                if final_score >= 15:
                    priority = 'CRITICAL'
                elif final_score >= 10:
                    priority = 'HIGH'
                elif final_score >= 8:
                    priority = 'MEDIUM'
                else:
                    priority = 'LOW'
                
                rec = DiagnosisRecommendation(
                    code=diagnosis_code,
                    name=diagnosis_info['name'],
                    score=final_score,
                    priority=priority,
                    has_high_priority_keyword=has_high_priority,
                    numeric_findings_boost=numeric_boost,
                    cardiac_context_boost=cardiac_boost
                )
                recommendations.append(rec)
        
        # Step 4: Apply diagnostic interactions
        recommendations = ImprovedCDSSEngine._apply_diagnostic_interactions(
            recommendations, 
            is_postop, is_hf, is_acs, is_shock, has_mech_comp, is_on_support
        )
        
        # Step 5: Sort by priority dan score
        priority_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
        recommendations.sort(
            key=lambda x: (priority_order[x.priority], -x.score)
        )

        # Ringkasan satu-baris ('analisis')
        analisis_summary = ImprovedCDSSEngine._build_analisis_summary(
            recommendations, is_postop, is_hf, is_acs, is_shock,
            has_mech_comp, is_on_support
        )
        
        return {
            'status': 'success',
            'analisis': analisis_summary,
            'numeric_findings': numeric_findings,
            'numeric_interpretation': numeric_interpretation,
            'clinical_context': {
                'is_postoperative_cardiac': is_postop,
                'is_acute_decompensated_hf': is_hf,
                'is_acute_coronary_syndrome': is_acs,
                'is_cardiogenic_shock': is_shock,
                'has_mechanical_complication': has_mech_comp,
                'is_on_ecmo_vad': is_on_support
            },
            'recommendations': [
                {
                    'code': r.code,
                    'name': r.name,
                    'score': r.score,
                    'priority': r.priority,
                    'base_keywords': r.has_high_priority_keyword,
                    'numeric_boost': r.numeric_findings_boost,
                    'cardiac_context_boost': r.cardiac_context_boost,
                    'luaran': ImprovedCDSSEngine.MASTER_DIAGNOSES[r.code]['luaran']
                }
                for r in recommendations
            ]
        }

    @staticmethod
    def _build_analisis_summary(
        recommendations: List[DiagnosisRecommendation],
        is_postop: bool, is_hf: bool, is_acs: bool, is_shock: bool,
        has_mech_comp: bool, is_on_support: bool
    ) -> str:
        """Bangun ringkasan satu-baris dari hasil analisis untuk panel alert."""
        context_flags = []
        if is_shock:
            context_flags.append("Kecurigaan Syok Kardiogenik")
        if has_mech_comp:
            context_flags.append("Kecurigaan Komplikasi Mekanik")
        if is_acs:
            context_flags.append("Pola ACS/Iskemia")
        if is_hf:
            context_flags.append("Pola Gagal Jantung Akut")
        if is_postop:
            context_flags.append("Post-operatif Kardiak")
        if is_on_support:
            context_flags.append("Dalam Dukungan ECMO/VAD")

        critical_dx = [r for r in recommendations if r.priority == 'CRITICAL']
        high_dx = [r for r in recommendations if r.priority == 'HIGH']

        parts = []
        if context_flags:
            parts.append("Konteks: " + ", ".join(context_flags))
        if critical_dx:
            names = ", ".join(f"{r.code} ({r.name})" for r in critical_dx)
            parts.append(f"Prioritas KRITIS: {names}")
        elif high_dx:
            names = ", ".join(f"{r.code} ({r.name})" for r in high_dx)
            parts.append(f"Prioritas TINGGI: {names}")

        if not parts:
            return ""
        return " | ".join(parts)
    
    @staticmethod
    def _apply_diagnostic_interactions(
        recommendations: List[DiagnosisRecommendation],
        is_postop: bool,
        is_hf: bool,
        is_acs: bool,
        is_shock: bool,
        has_mech_comp: bool,
        is_on_support: bool
    ) -> List[DiagnosisRecommendation]:
        """Apply diagnostic interaction rules"""
        current_codes = {r.code for r in recommendations}
        
        # Rule 1: Curah Jantung berat → add Perfusi Perifer jika belum ada
        if 'D.0008' in current_codes:
            d0008 = next(r for r in recommendations if r.code == 'D.0008')
            if d0008.score >= 12 and 'D.0009' not in current_codes:
                new_rec = DiagnosisRecommendation(
                    code='D.0009',
                    name='Perfusi Perifer Tidak Efektif',
                    score=8,
                    priority='HIGH',
                    has_high_priority_keyword=False,
                    numeric_findings_boost=0,
                    cardiac_context_boost=3
                )
                recommendations.append(new_rec)
        
        # Rule 2: Post-op cardiac → D.0012 adalah MANDATORY
        if is_postop and 'D.0012' not in current_codes:
            new_rec = DiagnosisRecommendation(
                code='D.0012',
                name='Risiko Perdarahan',
                score=10,
                priority='HIGH',
                has_high_priority_keyword=True,
                numeric_findings_boost=0,
                cardiac_context_boost=4
            )
            recommendations.append(new_rec)
        
        # Rule 3: Hipervolemia + pulmonary edema → add D.0003 & D.0005
        if 'D.0022' in current_codes and ('D.0003' not in current_codes or 'D.0005' not in current_codes):
            d0022 = next(r for r in recommendations if r.code == 'D.0022')
            if d0022.score >= 8:
                if 'D.0003' not in current_codes:
                    rec_d0003 = DiagnosisRecommendation(
                        code='D.0003',
                        name='Gangguan Pertukaran Gas',
                        score=7,
                        priority='MEDIUM',
                        has_high_priority_keyword=False,
                        numeric_findings_boost=0,
                        cardiac_context_boost=2
                    )
                    recommendations.append(rec_d0003)
                
                if 'D.0005' not in current_codes:
                    rec_d0005 = DiagnosisRecommendation(
                        code='D.0005',
                        name='Pola Napas Tidak Efektif',
                        score=7,
                        priority='MEDIUM',
                        has_high_priority_keyword=False,
                        numeric_findings_boost=0,
                        cardiac_context_boost=2
                    )
                    recommendations.append(rec_d0005)
        
        return recommendations


def analyze_clinical_trends_improved(s_text: str, o_text: str) -> Dict:
    """
    Main function yang compatible dengan existing Streamlit dashboard.

    FIX (akar masalah "hanya 1 diagnosa muncul"): guard sebelumnya
    mensyaratkan S **dan** O harus sama-sama terisi ("if not s_text or
    not o_text"). Pada kasus pasien tersedasi/terintubasi/IABP (persis
    seperti kasus di screenshot), kolom S (Subjektif) SECARA KLINIS WAJAR
    kosong karena pasien tidak bisa menyampaikan keluhan -- sementara
    kolom O (Objektif) berisi data vital yang sangat kritis (TD, HR,
    vasopresor, IABP, oliguria, ventilator). Guard lama membuat seluruh
    engine v2.0 (weighted scoring + numeric parsing + cardiac rules)
    di-skip total dan mengembalikan 0 rekomendasi -- meskipun datanya
    sangat layak dianalisis. Akibatnya pipeline di dashboard.py jatuh ke
    fallback yang lebih lemah (API eksternal tidak lengkap / local
    keyword fallback), yang hanya berhasil meloloskan 1 diagnosa.

    Sekarang engine tetap berjalan selama SALAH SATU dari S atau O
    terisi; hanya menolak jika KEDUANYA benar-benar kosong.
    """
    s_text = s_text or ""
    o_text = o_text or ""

    if not s_text.strip() and not o_text.strip():
        return {
            "status": "empty",
            "analisis": "",
            "recommendations": [],
            "numeric_findings": {},
            "clinical_context": {}
        }
    
    result = ImprovedCDSSEngine.analyze(s_text, o_text)
    return result