"""Şartname klasöründeki her belgede kodla yapılan çıkarımları gösterir. Model çağırmaz, ücretsizdir.
Her belge için: madde sayısı, istenen belgeler maddesi, bentler, iş deneyim kararı ve dayanağı, elenen kurallar,
diğer deneyim şartları ve Madde 7 dışında 'deneyim' geçen maddeler."""
import sys                                                          # klasör yolunu komut satırından almak için
from pathlib import Path                                            # klasördeki dosyaları gezmek için
import rag                                                          # belge okuma ve kodla çıkarım fonksiyonları

klasor = Path(sys.argv[1] if len(sys.argv) > 1 else "belgeler/sartnameler")
for yol in sorted(klasor.glob("*")):
    if yol.suffix.lower() not in (".pdf", ".docx"):                 # sadece şartname dosyaları
        continue
    print("=" * 100)
    try:
        metin, bolumler = rag._metin_ve_bolumler(str(yol))          # API çağırmadan okur
    except Exception as hata:
        print(f"{yol.name}: OKUNAMADI ({type(hata).__name__}: {hata})")
        continue
    madde = sum(1 for b in bolumler if "Madde" in b["etiket"])
    print(f"{yol.name}: {len(metin):,} karakter, {len(bolumler)} bölüm ({madde} madde), "
          f"{'tamamı modele gider' if len(metin) <= rag.TAM_SINIR else 'BÜYÜK: seçilen maddeler gider'}")
    if len(metin.strip()) < 200:
        print("  !! Metin yok: taranmış (resim) PDF olabilir")
        continue
    istenen = rag.istenen_belgeler(bolumler)
    if not istenen:
        print("  !! İstenen belgeler maddesi BULUNAMADI. Madde başlıkları:")
        for b in bolumler[:40]:
            print("     -", b["etiket"][:90])
        continue
    print(f"  İstenen belgeler: {istenen['etiket']}")
    print("  Bentler:", ", ".join(x["bent"] for x in istenen["maddeler"]))
    durum, bentler = rag.is_deneyim_durumu(istenen)                 # kodun iş deneyimi tespiti ve dayandığı hüküm
    etiket = {"isteniyor": "İSTENİYOR", "istenmiyor": "İSTENMİYOR"}.get(durum, "?")   # Türkçe büyük harf (upper() 'İ'yi bozar)
    print(f"  İş deneyim belgesi: {etiket}")
    for x in bentler:
        print(f"     dayanak {x['bent']}: {x['metin'][:260]}")
    for x in istenen["maddeler"]:                                   # deneyim geçen ama belgeleme kuralı diye elenen bentler
        if rag._iceriyor(x["metin"], rag.IS_DENEYIM_BELGESI) and rag._basliyor(x["metin"], rag.KURAL_BASI):
            print(f"     elenen kural {x['bent']}: {x['metin'][:120]}")
    for x in rag.deneyim_sartlari(istenen):
        print(f"  Diğer deneyim şartı: {x['bent']} {x['metin'][:160]}")
    madde7 = istenen["etiket"].split(",")[0]                        # Madde 7 dışında "deneyim" geçen maddeler
    disari = [b["etiket"][:70] for b in bolumler if not b["etiket"].startswith(madde7)
              and rag._iceriyor(b["metin"], rag.IS_DENEYIM_BELGESI)]
    if disari:
        print("  Madde 7 dışında deneyim geçen:", " | ".join(disari))