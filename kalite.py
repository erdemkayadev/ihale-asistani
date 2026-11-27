"""Veri kalitesi raporu: her tür ve ay için asıl ilan/sonuç kayıtlarında boş kalan alanları sayar.
Yeni bülten biçimi çıkarsa ya da okuma bozulursa ilk burada görünür."""
from veri import sorgu

ILLER = set("""Adana Adıyaman Afyonkarahisar Ağrı Aksaray Amasya Ankara Antalya Ardahan Artvin Aydın Balıkesir Bartın
Batman Bayburt Bilecik Bingöl Bitlis Bolu Burdur Bursa Çanakkale Çankırı Çorum Denizli Diyarbakır Düzce Edirne Elazığ
Erzincan Erzurum Eskişehir Gaziantep Giresun Gümüşhane Hakkari Hatay Iğdır Isparta İstanbul İzmir Kahramanmaraş Karabük
Karaman Kars Kastamonu Kayseri Kilis Kırıkkale Kırklareli Kırşehir Kocaeli Konya Kütahya Malatya Manisa Mardin Mersin
Muğla Muş Nevşehir Niğde Ordu Osmaniye Rize Sakarya Samsun Şanlıurfa Siirt Sinop Şırnak Sivas Tekirdağ Tokat Trabzon
Tunceli Uşak Van Yalova Yozgat Zonguldak""".split())                  # 81 il: adresten çıkan il adını doğrulamak için

KONTROL = {
    "ilanlar": ("İHALE İLANLARI", ["idare", "il", "konu", "ihale_tarihi", "usul", "gecici_teminat", "gecerlilik_gun"]),
    "sonuclar": ("İHALE SONUÇLARININ İLANLARI", ["idare", "konu", "ihale_tarihi", "usul", "yaklasik_maliyet",
                                                 "teklif_sayisi", "sozlesme_bedeli", "yuklenici"]),
}

for tablo, (asil, alanlar) in KONTROL.items():
    print(f"\n=== {tablo}: boş alan yüzdesi (sadece asıl kayıtlar: {asil}) ===")
    print(f"{'tür':12} {'ay':8} {'kayıt':>6}  " + "  ".join(f"{a[:9]:>9}" for a in alanlar))
    bos = ", ".join(f"SUM({a} IS NULL OR {a} = '')" for a in alanlar)   # her alan için boş sayısı
    for tur, ay, adet, *boslar in sorgu(f"SELECT tur, substr(bulten_tarihi, 1, 7), COUNT(*), {bos} FROM {tablo} "
                                       f"WHERE ilan_turu = ? GROUP BY 1, 2 ORDER BY 1, 2", (asil,)):
        print(f"{tur:12} {ay:8} {adet:6}  " + "  ".join(f"{100 * b / adet:8.1f}%" for b in boslar))

iller = sorgu("SELECT il, COUNT(*) FROM ilanlar WHERE ilan_turu = 'İHALE İLANLARI' GROUP BY il")
gecersiz = sorted(((il, n) for il, n in iller if il not in ILLER), key=lambda x: -x[1])
print(f"\n81 il listesinde olmayan il değerleri: {sum(n for _, n in gecersiz)} kayıt -> {gecersiz[:10]}")