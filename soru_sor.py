from rag import veritabani_ac, cevapla              # işin asıl kısmı rag.py'de

db = veritabani_ac("vektor_db")                     # belge_yukle.py'nin kaydettiği veritabanını aç

while True:                                         # sen çıkana kadar soru sormaya devam et
    soru = input("\nSoru (çıkmak için boş bırak): ").strip()
    if not soru:
        break
    print(cevapla(db, soru))