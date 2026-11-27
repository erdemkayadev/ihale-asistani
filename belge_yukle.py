import os                                                           # .env'deki değerlere erişmek için
from dotenv import load_dotenv                                      # .env dosyasını okuyacak araç
from langchain_community.document_loaders import PyPDFLoader        # PDF'i sayfa sayfa okur
from langchain_text_splitters import RecursiveCharacterTextSplitter # metni küçük parçalara böler
from langchain_openai import OpenAIEmbeddings                       # metni sayıya çevirir (embedding)
from langchain_chroma import Chroma                                 # vektör veritabanı

load_dotenv()                                                       # .env'i yükle

sayfalar = PyPDFLoader("belgeler/4734.pdf").load()                  # PDF'teki her sayfa bir belge olur
print(f"{len(sayfalar)} sayfa okundu")

bolucu = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)  # 1000 karakterlik parçalar, 150 karakter örtüşme
parcalar = bolucu.split_documents(sayfalar)                          # sayfaları parçalara böl (sayfa numarası korunur)
print(f"{len(parcalar)} parçaya bölündü")

embedding = OpenAIEmbeddings(model=os.getenv("EMBED_MODEL"))         # embedding modelini .env'den al
Chroma.from_documents(parcalar, embedding, persist_directory="vektor_db")  # parçaları sayıya çevir, vektor_db klasörüne kaydet
print("Veritabanı hazır: vektor_db/")