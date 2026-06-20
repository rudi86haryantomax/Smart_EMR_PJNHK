from fastapi import APIRouter, HTTPException, status
from app.schemas.emr_schema import (
    ExtractionRequest, 
    ExtractionResponse, 
    CPPTAnalysisRequest, 
    CPPTAnalysisResponse
)
from app.services.ai_extractor import extract_clinical_data
from app.services.cdss_engine import analyze_clinical_trends

router = APIRouter()

# ========================================================
# 1. ENDPOINT INTEGRASI LLM (Qwen 2.5:1.5b)
# ========================================================
@router.post("/extract", response_model=ExtractionResponse)
async def extract_emr(payload: ExtractionRequest):
    # Ambil teks dari key mana pun yang tersedia di request
    clinical_text = payload.text or payload.text_raw or payload.teks_raw
    
    if not clinical_text:
        raise HTTPException(status_code=400, detail="Catatan klinis kosong atau tidak ditemukan")
        
    try:
        # Jalankan ekstraksi menggunakan local LLM Qwen 2.5 (1.5B)
        result = extract_clinical_data(clinical_text)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========================================================
# 2. ENDPOINT CDSS (Rule-Based & Analisis Nilai Kritis Lab)
# ========================================================
@router.post(
    "/analyze-cppt", 
    response_model=CPPTAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analisis Teks CPPT untuk Deteksi Nilai Kritis & Rekomendasi 3S"
)
async def analyze_cppt_endpoint(payload: CPPTAnalysisRequest):
    if not payload.clinical_note:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Catatan klinis tidak boleh kosong."
        )
        
    try:
        # Jalankan engine pendeteksi nilai kritis dari cdss_engine
        result = analyze_clinical_trends(payload.clinical_note)
        
        if result["status"] == "empty":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Catatan klinis kosong."
            )
            
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kegagalan pada AI Engine: {str(e)}"
        )