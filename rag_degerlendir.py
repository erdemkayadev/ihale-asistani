import json                                   # test setini okumak için
import re                                     # metin içinde kalıp (madde numarası) aramak için
from pathlib import Path                      # dosya yolu için
from rag import veritabani_ac, cevapla        # YENİ
db = veritabani_ac("vektor_db")               # YENİ: kayıtlı veritabanını aç                  # soru_sor.py'deki cevaplama fonksiyonunu kullan


def sadelestir(metin):
    """Türkçe İ/I sorununu çözüp her şeyi küçük harfe çevirir."""
    return metin.replace("İ", "i").replace("I", "ı").lower()


def maddeleri_bul(cevap):
    """Cevapta geçen madde numaralarını bulur: 'Madde 33', '33. madde', '33 üncü madde' gibi."""
    metin = sadelestir(cevap)
    sayilar = re.findall(r"madde\s*(\d+)", metin)                                      # "madde 33"
    sayilar += re.findall(r"(\d+)\s*(?:\.|['’]?\s*(?:inci|ıncı|uncu|üncü|nci|ncı|ncu|ncü))\s*madde", metin)  # "33. madde", "33 üncü madde"
    return {int(s) for s in sayilar}                                                   # tekrarları at, sayıya çevir


test_seti = json.loads(Path("rag_test_seti.json").read_text(encoding="utf-8"))  # soruları ve beklenen maddeleri oku

dogru = 0
for i, test in enumerate(test_seti, start=1):         # her soruyu sırayla, 1'den başlayan numarayla gez
    cevap = cevapla(db, test["soru"])                      # sistemimize soruyu sor
    if test["madde"] is None:                          # belgede OLMAYAN soru: "bulamadım" demeli
        basarili = "bulamadım" in sadelestir(cevap)
        beklenen = "Belgede bulamadım"
    else:                                              # belgede OLAN soru: doğru madde cevapta geçmeli
        basarili = test["madde"] in maddeleri_bul(cevap)
        beklenen = f"Madde {test['madde']}"

    if basarili:
        dogru += 1
        print(f"✓ {i}. {test['soru']}")
    else:
        print(f"✗ {i}. {test['soru']}  (beklenen: {beklenen})")
        print(f"     cevap: {cevap[:200]}")             # yanlışsa cevabın ilk 200 karakterini göster

oran = dogru / len(test_seti) * 100
print(f"\nSonuç: {dogru}/{len(test_seti)} doğru (%{oran:.1f})")