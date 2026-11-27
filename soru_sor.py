import os                                                           # .env'deki değerlere erişmek için
from dotenv import load_dotenv                                      # .env dosyasını okuyacak araç
from openai import OpenAI                                           # cevabı yazacak model
from langchain_openai import OpenAIEmbeddings                       # soruyu sayıya çevirmek için
from langchain_chroma import Chroma                                 # kayıtlı veritabanını açmak için

load_dotenv()                                                       # .env'i yükle
client = OpenAI()                                                   # OpenAI bağlantısı
db = Chroma(persist_directory="vektor_db",
            embedding_function=OpenAIEmbeddings(model=os.getenv("EMBED_MODEL")))  # belge_yukle.py'nin kaydettiği veritabanı

TALIMAT = """Sen bir kamu ihale mevzuatı asistanısın. Soruyu SADECE sana verilen belge parçalarına dayanarak cevapla.
Her bilginin sonuna kaynağını parçanın başındaki etiketle [Madde X, Sayfa Y] şeklinde yaz.
Cevap parçalarda yoksa sadece "Belgede bu bilgiyi bulamadım." yaz. Tahmin etme."""


def soruyu_duzelt(soru):
    """Aramadan önce yazım hatalarını ve eksik Türkçe karakterleri düzeltir."""
    cevap = client.responses.create(
        model=os.getenv("MODEL"),
        instructions="Kullanıcının sorusundaki yazım hatalarını ve eksik Türkçe karakterleri düzelt. "
                     "Anlamı değiştirme, soruya cevap verme. Sadece düzeltilmiş soruyu yaz.",
        input=soru,
    )
    return cevap.output_text.strip()                                # baştaki/sondaki boşlukları sil


def cevapla(soru):
    duzgun_soru = soruyu_duzelt(soru)                               # önce soruyu düzelt
    print(f"(Aranan: {duzgun_soru})")                               # neyi aradığımızı ekranda göster
    parcalar = db.similarity_search(duzgun_soru, k=8)               # düzeltilmiş soruya anlamca en yakın 8 parçayı bul
    baglam = "\n\n".join(                                           # parçaları sayfa numarasıyla tek metinde birleştir
        f"[{p.metadata['madde']}, Sayfa {p.metadata['sayfa']}]\n{p.page_content}" for p in parcalar   # her parçanın madde ve sayfa etiketi
    )
    cevap = client.responses.create(
        model=os.getenv("MODEL"),
        instructions=TALIMAT,
        input=f"BELGE PARÇALARI:\n{baglam}\n\nSORU: {duzgun_soru}",    # model hem parçaları hem soruyu görür
    )
    return cevap.output_text


if __name__ == "__main__":
    while True:                                                     # sen çıkana kadar soru sormaya devam et
        soru = input("\nSoru (çıkmak için boş bırak): ").strip()
        if not soru:                                                # boş Enter'a basılırsa döngüden çık
            break
        print(cevapla(soru))