import re                                                           # bültendeki kalıpları yakalamak için
import sqlite3                                                      # kayıtları tek bir veritabanı dosyasına yazmak için
import sys                                                          # komut satırından dosya adı almak için
from pathlib import Path                                            # klasördeki PDF'leri listelemek için
from pypdf import PdfReader                                         # PDF'ten metin çıkarmak için

AYLAR = {"OCAK": 1, "ŞUBAT": 2, "MART": 3, "NİSAN": 4, "MAYIS": 5, "HAZİRAN": 6, "TEMMUZ": 7,
         "AĞUSTOS": 8, "EYLÜL": 9, "EKİM": 10, "KASIM": 11, "ARALIK": 12}
# Her ilanın ilk satırı: "12.   2026/2283499       AĞ ANAHTARI ..." (içindekilerdeki "....... 7" satırları hariç)
ILAN = re.compile(r"^[ \t]*(\d+)\.\s+(\d{4}/\d+(?:-\d+)?)[ \t]{3,}(?![^\n]*\.{5})", re.M)   # "-3": kısım numarası
# Bölüm başlıkları: "2. İHALE İLANLARI", "4. İHALE İPTAL İLANLARI", "1. İSTİSNA SONUÇLARININ İLANLARI" ...
BOLUM = re.compile(r"^[ \t]*\d+\.[ \t]+((?:İHALE|ÇERÇEVE|İSTİSNA|KAPSAM DIŞI)[A-ZÇĞİÖŞÜ ]+?)[ \t]*$", re.M)
# Her sayfanın üstündeki 3-4 satırlık başlık: "KAMU İHALE BÜLTENİ ... www.kik.gov.tr  17"
UST_BILGI = re.compile(r"KAMU İHALE BÜLTENİ[^\n]*\n[^\n]*BÜLTENİ[^\n]*\n[^\n]*\n[^\n]*www\.kik\.gov\.tr[ \t]*\d+[ \t]*\n")


def _alan(blok, desen):
    """Desene uyan ilk değeri, satır sonlarını tek boşluğa çevirerek döndürür. Yoksa None."""
    m = re.search(desen, blok, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip(" :") if m else None


def _sayi(metin):
    """'3.170.000,00' -> 3170000.0 (Türkçe sayı yazımı)."""
    try:
        return float(metin.replace(".", "").replace(",", "."))
    except (AttributeError, ValueError):
        return None


def bulteni_oku(yol):
    """Bülten PDF'ini okur. Döner: (bilgi sözlüğü, kayıt listesi)."""
    metin = "\n".join(s.extract_text() or "" for s in PdfReader(yol).pages)
    g, ay, yil = re.search(r"(\d{1,2}) ([A-ZÇĞİÖŞÜ]+) (\d{4}) – Sayı", metin).groups()
    ust = metin[:3000]
    bilgi = {
        "bulten_tarihi": f"{yil}-{AYLAR[ay]:02d}-{int(g):02d}",       # "27 KASIM 2026" -> "2026-11-27"
        "tur": ("Yapım" if "YAPIM İŞLERİ" in ust else "Danışmanlık" if "DANIŞMANLIK" in ust
                else "Hizmet" if "HİZMET ALIMI" in ust else "Mal"),
        "sonuc": "Sonuç İlanları" in ust,                           # sonuç bülteni mi, ilan bülteni mi
    }
    metin = UST_BILGI.sub("\n", metin)                              # sayfa üst bilgilerini sil
    bas = [m.start() for m in re.finditer(r"A- 4734 SAYILI KANUN KAPSAMINDAKİ İLANLAR", metin)]
    metin = metin[bas[1]:] if len(bas) > 1 else metin               # 1. geçiş içindekiler, 2. geçişten itibaren ilanlar

    bolumler = [(m.start(), m.group(1).strip()) for m in BOLUM.finditer(metin)]
    es = list(ILAN.finditer(metin))
    kayitlar = []
    for i, e in enumerate(es):
        blok = metin[e.start(): es[i + 1].start() if i + 1 < len(es) else len(metin)]
        once = [ad for konum, ad in bolumler if konum < e.start()]  # bu ilandan önceki son bölüm başlığı
        kayit = {**bilgi, "sira": int(e.group(1)), "ikn": e.group(2), "ilan_turu": once[-1] if once else None}
        kayit.update(_sonuc_alanlari(blok) if bilgi["sonuc"] else _ilan_alanlari(blok))
        kayitlar.append(kayit)
    return bilgi, kayitlar


def _ilan_alanlari(blok):
    """Yeni ihale ilanından alanları çıkarır."""
    adres = _alan(blok, r"1\.2\.\s*Adresi\s*:\s*(.*?)\n\s*1\.3\.")
    return {
        "idare": _alan(blok, r"1\.1\.\s*Adı\s*:\s*(.*?)\n\s*1\.2\."),
        "il": _alan(adres or "", r"/\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)\s*$"),  # adresin sonundaki "/İstanbul"
        "ihale_tarihi": _alan(blok, r"2\.1\.\s*Tarih ve Saati\s*:\s*(\d\d\.\d\d\.\d{4}\s*-\s*\d\d:\d\d)"),
        "konu": _alan(blok, r"3\.1\.?\s*Adı\s*:\s*(.*?)\n\s*3\.2\."),
        "usul": _alan(blok, r"(açık\s+ihale|belli\s+istekliler\s+arasında\s+ihale|pazarlık)\s+usulü\s+ile\s+ihale"),
        "gecici_teminat": _alan(blok, r"teklif\s+ettikleri\s+bedelin\s*%\s*(\d+)"),
        "gecerlilik_gun": _alan(blok, r"geçerlilik\s+süresi,\s+ihale\s+tarihinden\s+itibaren\s*(\d+)"),
    }


def _sonuc_idaresi(blok):
    """Sonuç ilanının başı: 'başlık + aynı başlık tekrar + idare adı'. Tekrarlanan başlığı atıp idareyi bırakır.
    Satır sonlarında kelimeler bölünebildiği için ("YAN" / "GIN") karşılaştırma boşluksuz yapılır."""
    bas = re.sub(r"^\s*\d+\.\s+\S+", "", blok[:blok.find("hale kayıt numarası")])[:-1]   # sıra no ve İKN'yi at
    sikisik = re.sub(r"\s", "", bas)                                # tüm boşluklar silinmiş hali
    for n in range(len(sikisik) // 2, 0, -1):                       # en uzun tekrar eden başlığı ara
        if sikisik[:n] == sikisik[n:2 * n]:
            sayac = 0
            for konum, harf in enumerate(bas):                      # 2n. dolu harfin asıl metindeki yerini bul
                if not harf.isspace():
                    sayac += 1
                    if sayac == 2 * n:
                        return re.sub(r"\s+", " ", bas[konum + 1:]).strip() or None
    return None


def _sonuc_alanlari(blok):
    """Sonuç ilanından alanları çıkarır."""
    bedel = re.search(r"b\)\s*Bedeli[ \t]*:[ \t]*([\d.,]+)[ \t]*([A-Z]{3})", blok)
    return {
        "idare": _sonuc_idaresi(blok),
        "konu": _alan(blok, r"2-\s*İhale konusu[^\n]*\n\s*a\)\s*Adı\s*:\s*(.*?)\n\s*[b3]"),
        "ihale_tarihi": _alan(blok, r"1-\s*İhalenin\s*\n\s*a\)\s*Tarihi\s*:\s*(\d\d\.\d\d\.\d{4})"),
        "usul": _alan(blok, r"Usulü\s*:\s*([^\n]+)"),
        "yaklasik_maliyet": _sayi(_alan(blok, r"Yaklaşık Maliyeti[ \t]*:[ \t]*([\d.,]+)[ \t]*[A-Z]{3}")),   # boşsa alt satıra kaymasın
        # Kısımlı ihalede yaklaşık maliyet tüm ihalenin, sözleşme bedeli tek kısmındır: oran hesaplanmamalı
        "kisimli": int("Kısımlarının" in blok),
        "dokuman_indiren": _alan(blok, r"indiren sayısı\s*:[ \t]*(\d+)[ \t]*\n"),
        "teklif_sayisi": _alan(blok, r"Toplam Teklif Sayısı[ \t]*:[ \t]*(\d+)"),
        "gecerli_teklif": _alan(blok, r"Geçerli Teklif Sayısı[ \t]*:[ \t]*(\d+)"),
        "sozlesme_bedeli": _sayi(bedel.group(1)) if bedel else None,
        "para_birimi": bedel.group(2) if bedel else None,
        "sozlesme_tarihi": _alan(blok, r"4-\s*Sözleşmenin\s*\n\s*a\)\s*Tarihi\s*:\s*(\d\d\.\d\d\.\d{4})"),
        "yuklenici": _alan(blok, r"Yüklenici(?:si)?\s*:\s*(.*?)\n\s*[e-g]\)"),     # "Yüklenicisi" ya da "Yüklenici"
    }


# ---------- Veritabanı ----------

ILAN_ALANLARI = ["bulten_tarihi", "tur", "ilan_turu", "sira", "ikn", "idare", "il", "konu", "ihale_tarihi",
                 "usul", "gecici_teminat", "gecerlilik_gun"]
SONUC_ALANLARI = ["bulten_tarihi", "tur", "ilan_turu", "sira", "ikn", "idare", "konu", "ihale_tarihi", "usul",
                  "yaklasik_maliyet", "kisimli", "dokuman_indiren", "teklif_sayisi", "gecerli_teklif", "sozlesme_bedeli",
                  "para_birimi", "sozlesme_tarihi", "yuklenici"]


def veritabanina_yaz(kayitlar, sonuc, db_yolu="ihaleler.db"):
    """Kayıtları SQLite'a yazar. Aynı bülten tekrar okunursa kayıtlar çoğalmaz, üzerine yazılır."""
    tablo, alanlar = ("sonuclar", SONUC_ALANLARI) if sonuc else ("ilanlar", ILAN_ALANLARI)
    db = sqlite3.connect(db_yolu)
    db.execute(f"CREATE TABLE IF NOT EXISTS {tablo} ({', '.join(alanlar)}, "
               f"PRIMARY KEY (bulten_tarihi, tur, ilan_turu, sira))")   # bir kaydın benzersiz kimliği
    db.executemany(f"INSERT OR REPLACE INTO {tablo} VALUES ({', '.join('?' * len(alanlar))})",
                   [[k.get(a) for a in alanlar] for k in kayitlar])
    db.commit()
    db.close()


if __name__ == "__main__":
    klasor = Path(sys.argv[1] if len(sys.argv) > 1 else "belgeler/bulten")   # varsayılan: belgeler/bulten
    for pdf in sorted(klasor.glob("*.pdf")):
        bilgi, kayitlar = bulteni_oku(pdf)
        veritabanina_yaz(kayitlar, bilgi["sonuc"])
        print(f"{pdf.name}: {bilgi['tur']} {'sonuç' if bilgi['sonuc'] else 'ilan'} bülteni, "
              f"{bilgi['bulten_tarihi']}, {len(kayitlar)} kayıt")