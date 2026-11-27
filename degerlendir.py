import json                                   # JSON dosyalarını Python sözlüğüne çevirmek için
from pathlib import Path                      # dosya yollarıyla kolay çalışmak için


def sadelestir(metin):
    """İki cevabı karşılaştırmadan önce aynı biçime getirir."""
    if metin is None:                                           # model null döndürdüyse
        return ""                                               # boş metin say
    metin = str(metin).replace("İ", "i").replace("I", "ı")      # Türkçe İ/I'yı önce elle küçült
    return " ".join(metin.lower().split())                      # hepsini küçült, fazla boşlukları sil


# Cevap anahtarını oku
dogrular = json.loads(Path("dogru_cevaplar.json").read_text(encoding="utf-8"))

dogru_sayisi = 0                              # kaç alan doğru çıktı
toplam = 0                                    # kaç alan kontrol edildi

for ilan_adi, beklenen in dogrular.items():   # her ilan için: ilan_adi="ilan1", beklenen={5 alan}
    yol = Path("ciktilar") / f"{ilan_adi}.json"                 # modelin çıktısı: ciktilar/ilan1.json
    cikti = json.loads(yol.read_text(encoding="utf-8"))         # o dosyayı oku, sözlüğe çevir

    for alan, dogru_deger in beklenen.items():                  # o ilanın 5 alanını tek tek gez
        toplam += 1                                             # bir alan daha kontrol ediliyor
        gelen = cikti.get(alan)                                 # modelin cevabı; alan yoksa None
        if sadelestir(gelen) == sadelestir(dogru_deger):        # sadeleştirilmiş halleri aynı mı?
            dogru_sayisi += 1                                   # aynıysa doğru sayısını artır
        else:                                                   # değilse yanlışı ekrana yaz
            print(f"YANLIŞ  {ilan_adi}.{alan}: beklenen '{dogru_deger}', gelen '{gelen}'")

oran = dogru_sayisi / toplam * 100            # yüzde hesabı
print(f"Sonuç: {dogru_sayisi}/{toplam} doğru (%{oran:.1f})")   # .1f = virgülden sonra 1 basamak