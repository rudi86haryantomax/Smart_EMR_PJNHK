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
            'critical_low': (0, 90),
            'normal': (90, 180),
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
        'ejection_fraction': ['D.0008', 'D.0022'],  
        'troponin': ['D.0015', 'D.0008'],           
        'bnp': ['D.0008', 'D.0022'],                
        'heart_rate': ['D.0008', 'D.0015', 'D.0023'], 
        'systolic_bp': ['D.0008', 'D.0023'],        
        'lactate': ['D.0009', 'D.0008', 'D.0003']   
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
            r'(?i)denyut\s+jantung[:\s]*(\d+)'
        ],
        'systolic_bp': [
            r'(?i)(?:bp|blood\s+pressure|tekanan\s+darah)[:\s]*(\d+)\s*/\s*\d+',
            r'(?i)sistolik[:\s]*(\d+)'
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
        }
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
            'hipotensi', 'poor perfusion', 'perfusi buruk', 'cold extremities'
        ]
        text_lower = text.lower()
        has_shock_indicators = any(contains_keyword(text_lower, kw) for kw in shock_keywords)
        
        # Additional check: SBP < 90 + tachycardia
        if 'systolic_bp' in numeric_findings and 'heart_rate' in numeric_findings:
            if numeric_findings['systolic_bp'] < 90 and numeric_findings['heart_rate'] > 100:
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
        """Detect if patient is on ECMO/VAD support"""
        support_keywords = ['ecmo', 'vad', 'impella', 'tandem heart', 'mechanical support']
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
            
            if is_hf:
                # Boost untuk D.0008, D.0022, D.0003, D.0005 pada HF
                if diagnosis_code in ['D.0008', 'D.0022', 'D.0003', 'D.0005']:
                    cardiac_boost += 2
            
            if is_acs:
                # Boost untuk D.0015, D.0008 pada ACS
                if diagnosis_code in ['D.0015', 'D.0008']:
                    cardiac_boost += 2
            
            if is_shock:
                # Boost untuk D.0008, D.0009 pada shock
                if diagnosis_code in ['D.0008', 'D.0009']:
                    cardiac_boost += 3
            
            if is_on_support:
                # Boost untuk D.0012 pada ECMO (anticoagulation risk)
                if diagnosis_code == 'D.0012':
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
    """Main function yang compatible dengan existing Streamlit dashboard"""
    if not s_text or not o_text:
        return {
            "status": "empty",
            "analisis": "",
            "recommendations": [],
            "numeric_findings": {},
            "clinical_context": {}
        }
    
    result = ImprovedCDSSEngine.analyze(s_text, o_text)
    return result