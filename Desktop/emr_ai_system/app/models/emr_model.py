from sqlalchemy import Column, Integer, String, Text
from app.config.database import Base

class EMRRecord(Base):
    __tablename__ = "emr_records"

    # Kolom ID sebagai kunci utama
    id = Column(Integer, primary_key=True, index=True)
    
    # Kolom untuk data yang diekstrak oleh AI
    keluhan_utama = Column(String, nullable=True)
    diagnosa = Column(String, nullable=True)
    tindakan = Column(String, nullable=True)
    
    # Kolom untuk menyimpan teks mentah aslinya
    raw_text = Column(Text, nullable=True)