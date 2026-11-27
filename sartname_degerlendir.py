import json                                                         # test setini okumak, sonucu yazmak için
import re                                                           # cevaptaki madde numaralarını bulmak için
from rag import belge_parcala, veritabani_kur, cevapla

KLASOR = "belgeler/sartnameler"                                     # 10 şartnamenin durduğu klasör
BULAMADIM = ["bulamadım", "bulunamadı", "bulunmamaktadır", "yeralmıyor", "yeralmamaktadır"]   # "belgede yok" demenin yolları


def sadele(metin):
    """Karşılaştırma için: Türkçe küçük harf, boşluk ve kesme işaretleri yok. '% 3' -> '%3'."""
    metin = metin.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[\s'’]", "", metin)


def maddeleri_bul(cevap):
    """Cevapta kaynak olarak geçen madde numaraları: 'Madde 24' -> {24}."""
    return {int(n) for n in re.findall(r"madde\s*(\d+)", cevap, re.IGNORECASE)}


test_seti = json.load(open("sartname_test_seti.json", encoding="utf-8"))
toplam = kaynak_dogru = icerik_dogru = 0
rapor = []

for belge in test_seti:
    yol = f"{KLASOR}/{belge['belge']}"
    parcalar = belge_parcala(yol)                                   # yeni parçalayıcı
    db = veritabani_kur(parcalar, ad=belge["belge"][:3])            # hafızada, her belgeye ayrı koleksiyon (s01, s02...)
    b_toplam = b_dogru = 0
    print(f"\n=== {belge['belge']}  ({len(parcalar)} parça)")

    for t in belge["sorular"]:
        cevap = cevapla(db, t["soru"])
        if t["madde"] is None:                                      # belgede olmayan soru: "bulamadım" demeli
            k = i = any(x in sadele(cevap) for x in BULAMADIM)
        else:
            k = t["madde"] in maddeleri_bul(cevap)                  # doğru maddeyi kaynak gösterdi mi
            i = any(sadele(x) in sadele(cevap) for x in t["kelime"])   # doğru bilgiyi söyledi mi
        dogru = k and i                                             # ikisi birden olmalı
        toplam += 1; b_toplam += 1
        kaynak_dogru += k; icerik_dogru += i; b_dogru += dogru
        print(f"{'✓' if dogru else '✗'} [kaynak {'✓' if k else '✗'} | bilgi {'✓' if i else '✗'}] {t['soru']}")
        if not dogru:
            print("   CEVAP:", cevap.replace("\n", " ")[:250])
        rapor.append({"belge": belge["belge"], "soru": t["soru"], "beklenen_madde": t["madde"],
                      "kaynak": k, "bilgi": i, "cevap": cevap})
    print(f"--> {b_dogru}/{b_toplam}")

tam = sum(r["kaynak"] and r["bilgi"] for r in rapor)
print(f"\nTOPLAM: {tam}/{toplam} (%{100 * tam // toplam})  |  kaynak doğru: {kaynak_dogru}  |  bilgi doğru: {icerik_dogru}")
json.dump(rapor, open("sartname_sonuc.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)