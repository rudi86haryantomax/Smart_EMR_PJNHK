from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import traceback

# Pastikan import ini sesuai dengan lokasi fungsi Anda
from app.services.ai_extractor import extract_clinical_data

app = FastAPI()

class RequestTeks(BaseModel):
    text: str

@app.post("/api/v1/extract")
async def extract_data(request: RequestTeks):
    try:
        # Panggil fungsi ekstraksi kilat (0ms)
        hasil = extract_clinical_data(request.text)
        
        # WAJIB: Gunakan JSONResponse untuk mem-bypass validasi response_model Pydantic 
        # yang sering menyebabkan error 500 secara sepihak.
        return JSONResponse(status_code=200, content=hasil)
        
    except Exception as e:
        # Tangkap error di pintu terluar server
        error_log = traceback.format_exc()
        
        # Cetak merah di terminal backend agar Anda bisa melihatnya langsung
        print("\n" + "="*50)
        print("🚨 ERROR FATAL DI ROUTER FASTAPI 🚨")
        print(error_log)
        print("="*50 + "\n")
        
        # Kirim error secara elegan ke Streamlit, bukan sekadar "Internal Server Error"
        return JSONResponse(
            status_code=500,
            content={
                "detail": str(e),
                "traceback": error_log,
                "message": "Terjadi galat kompilasi di tingkat FastAPI."
            }
        )