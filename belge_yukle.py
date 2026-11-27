import shutil                                       # eski veritabanı klasörünü silmek için
from rag import pdf_parcala, veritabani_kur         # işin asıl kısmı rag.py'de

parcalar = pdf_parcala("belgeler/4734.pdf")         # PDF'i parçala
print(f"{len(parcalar)} parça oluşturuldu")
shutil.rmtree("vektor_db", ignore_errors=True)      # eskisini sil, üst üste eklenmesin
veritabani_kur(parcalar, klasor="vektor_db")        # diske kaydet
print("Veritabanı hazır: vektor_db/")