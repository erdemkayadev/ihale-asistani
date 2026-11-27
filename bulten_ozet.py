import sqlite3                                                      # ihaleler.db'yi okumak için
import statistics                                                   # ortanca (medyan) hesaplamak için

db = sqlite3.connect("ihaleler.db")

print("=== Veritabanında ne var ===")
for tablo in ["ilanlar", "sonuclar"]:
    for tur, adet in db.execute(f"SELECT tur, COUNT(*) FROM {tablo} GROUP BY tur"):
        print(f"{tablo:9} {tur:12} {adet} kayıt")

print("\n=== Yeni ihaleler: en çok ilan çıkan 5 il ===")
for il, adet in db.execute("SELECT il, COUNT(*) FROM ilanlar WHERE ilan_turu = 'İHALE İLANLARI' "
                           "GROUP BY il ORDER BY COUNT(*) DESC LIMIT 5"):
    print(f"{il:12} {adet}")

print("\n=== Sonuçlar: kazanan teklif, yaklaşık maliyetin yüzde kaçı? ===")
print("(Sadece tek sözleşmeli, TL'li ihaleler. Kısımlı ihalelerde iki sayı karşılaştırılamaz.)")
satirlar = db.execute("SELECT tur, usul, sozlesme_bedeli / yaklasik_maliyet, CAST(teklif_sayisi AS INT) "
                      "FROM sonuclar WHERE ilan_turu = 'İHALE SONUÇLARININ İLANLARI' AND kisimli = 0 "
                      "AND para_birimi = 'TRY' AND yaklasik_maliyet > 0").fetchall()
for tur in ["Mal", "Hizmet", "Yapım"]:
    for ad in ["Açık", "Pazarlık"]:
        grup = [s for s in satirlar if s[0] == tur and s[1].startswith(ad)]   # bu tür + bu usul
        if not grup:
            continue
        oran = statistics.median(s[2] for s in grup)                # ortadaki değer: uç değerlerden etkilenmez
        teklif = statistics.median(s[3] for s in grup)
        tek = sum(1 for s in grup if s[3] == 1)                     # tek teklifle sonuçlanan ihale sayısı
        print(f"{tur:7} {ad:9} {len(grup):3} ihale | ortanca %{100 * oran:.0f} | "
              f"ortanca {teklif:g} teklif | {tek} tanesi tek teklifli")