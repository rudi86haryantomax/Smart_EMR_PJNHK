import os
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter # Perhatikan perubahan path ini
from langchain_chroma import Chroma
from langchain_community.embeddings import SentenceTransformerEmbeddings

# Folder tempat menyimpan PDF
guidelines_path = "app/data/guidelines"

def ingest_pdfs():
    documents = []
    # Membaca semua file PDF di folder
    for file in os.listdir(guidelines_path):
        if file.endswith(".pdf"):
            full_path = os.path.join(guidelines_path, file)
            print(f"Sedang memproses: {file}...")
            loader = PyPDFLoader(full_path)
            documents.extend(loader.load())

    # Memecah dokumen menjadi potongan kecil
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    texts = text_splitter.split_documents(documents)

    # Mengubah teks menjadi vektor
    embedding_function = SentenceTransformerEmbeddings(model_name="all-MiniLM-L6-v2")

    # Menyimpan ke database vektor
    db = Chroma.from_documents(texts, embedding_function, persist_directory="./chroma_db")
    print(f"Berhasil mengindeks {len(texts)} potongan teks dari pedoman klinis!")

if __name__ == "__main__":
    ingest_pdfs()