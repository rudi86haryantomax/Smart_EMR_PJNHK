from pydantic import BaseModel
from typing import Optional, Any, List, Dict

# ========================================================
# 1. SKEMA EKSISTING ANDA (Pertahankan agar sistem tidak break)
# ========================================================
class ExtractionRequest(BaseModel):
    text: Optional[str] = None
    text_raw: Optional[str] = None
    teks_raw: Optional[str] = None

class ExtractionResponse(BaseModel):
    # Menggunakan Any agar fleksibel menerima teks
    diagnosa_keperawatan: Any
    luaran_keperawatan: Any
    rencana_intervensi: Any


# ========================================================
# 2. SKEMA BARU (Untuk Integrasi CDSS Nilai Kritis Lab)
# ========================================================
class CPPTAnalysisRequest(BaseModel):
    clinical_note: str

class CDSSAlert(BaseModel):
    parameter: str
    value: float
    unit: str
    condition: str
    sdki: str
    slki: str
    siki: Dict[str, List[str]]

class CPPTAnalysisResponse(BaseModel):
    status: str
    extracted_labs: Dict[str, float]
    alerts: List[CDSSAlert]