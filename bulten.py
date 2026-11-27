import re                                                           # bültendeki kalıpları yakalamak için
import sys                                                          # komut satırından dosya adı almak için
from pathlib import Path                                            # klasördeki PDF'leri listelemek için
from pypdf import PdfReader                                         # PDF'ten metin çıkarmak için
import veri                                                         # veritabanı işleri (tüm SQL orada)

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


def _tam(metin):
    """'12' -> 12, boşsa None."""
    return int(metin) if metin and metin.isdigit() else None


def _iso(tarih):
    """'14.10.2026' -> '2026-10-14' (sıralanabilir tarih biçimi)."""
    return f"{tarih[6:10]}-{tarih[3:5]}-{tarih[0:2]}" if tarih else None


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
        kayit = {**bilgi, "sira": int(e.group(1)), "ikn": e.group(2), "ilan_turu": once[-1] if once else "BİLİNMİYOR"}
        kayit.update(_sonuc_alanlari(blok) if bilgi["sonuc"] else _ilan_alanlari(blok))
        if bilgi["sonuc"] and "-" in kayit["ikn"]:                  # "2024/1406909-4": kısım numaralı İKN
            kayit["kisimli"] = 1
        kayitlar.append(kayit)
    return bilgi, kayitlar


def _ilk(blok, *desenler):
    """Desenleri sırayla dener, ilk bulunan değeri döndürür (bülten biçimi yıllar içinde değişti)."""
    for desen in desenler:
        deger = _alan(blok, desen)
        if deger:
            return deger
    return None


def _baslik_idaresi(blok):
    """Eski bültenlerde idare adı, ilan başlığının hemen altında yazar:
    '1.  2022/10133  AKARYAKIT SATIN ALINACAKTIR' / 'TARIM VE ORMAN ... MÜDÜRLÜĞÜ' / boş satır."""
    ust = re.split(r"\n[ \t]*\n", blok, maxsplit=1)[0].split("\n")    # ilk boş satıra kadar olan satırlar
    for i, satir in enumerate(ust):
        if re.search(r"(?:CAKTIR|CEKTİR)\.?\s*$", satir):          # başlık "...ALINACAKTIR" ile biter
            return " ".join(s.strip() for s in ust[i + 1:]).strip() or None
    return None


def _ilan_alanlari(blok):
    """Yeni ihale ilanından alanları çıkarır. 2022'den bugüne üç farklı biçim desteklenir."""
    adres = _ilk(blok, r"1\.2\.\s*Adresi\s*:\s*(.*?)\n\s*1\.3\.",                       # 2025 sonu ve sonrası
                 r"İdarenin\s*\n.*?[ab]\)\s*Adresi\s*:\s*(.*?)\n\s*[b-d]\)")            # daha eski bültenler
    konu = _ilk(blok, r"3\.1\.?\s*Adı\s*:\s*(.*?)\n\s*3\.2\.",
                r"İhale konusu[^\n]*\n\s*a\)\s*Adı\s*:\s*(.*?)\n\s*b\)")
    if not konu:                                                    # en eski biçim: "Akaryakıt mal alımı 4734 sayılı ..."
        cumle = _alan(re.split(r"\n[ \t]*\n", blok, maxsplit=1)[-1], r"^\s*(.*?)\s+4734\s+sayılı")
        konu = re.sub(r"\s+(?:mal\s+alımı|hizmet\s+alımı|yapım\s+işi|alımı)$", "", cumle or "") or None
    tarih = re.search(r"[Tt]arih(?:i)?\s+ve\s+[Ss]aati\s*:\s*(\d\d)\.(\d\d)\.(\d{4})\s*-\s*(\d\d)[:.](\d\d)", blok)
    return {
        "idare": _ilk(blok, r"1\.1\.\s*Adı\s*:\s*(.*?)\n\s*1\.2\.",
                      r"İdarenin\s*\n\s*a\)\s*Adı\s*:\s*(.*?)\n\s*b\)") or _baslik_idaresi(blok),
        "il": _alan(adres or "", r"/\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)\s*$"),  # adresin sonundaki "/İstanbul"
        "konu": konu,
        "ihale_tarihi": f"{tarih[3]}-{tarih[2]}-{tarih[1]} {tarih[4]}:{tarih[5]}" if tarih else None,   # "2026-12-22 10:00"
        "usul": _alan(blok, r"(açık\s+ihale|belli\s+istekliler\s+arasında\s+ihale|pazarlık)\s+usulü\s+ile\s+ihale"),
        "gecici_teminat": _tam(_alan(blok, r"teklif\s+ettikleri\s+bedelin\s*%\s*(\d+)")),
        "gecerlilik_gun": _tam(_alan(blok, r"geçerlilik\s+süresi,\s+ihale\s+tarihinden\s+itibaren\s*(\d+)")),
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
        "ihale_tarihi": _iso(_alan(blok, r"1-\s*İhalenin\s*\n\s*a\)\s*Tarihi\s*:\s*(\d\d\.\d\d\.\d{4})")),
        "usul": _alan(blok, r"Usulü\s*:\s*([^\n]+)"),
        "yaklasik_maliyet": _sayi(_alan(blok, r"Yaklaşık Maliyeti[ \t]*:[ \t]*([\d.,]+)[ \t]*[A-Z]{3}")),   # boşsa alt satıra kaymasın
        # Kısımlı ihalede yaklaşık maliyet tüm ihalenin, sözleşme bedeli tek kısmındır: oran hesaplanmamalı
        "kisimli": int("Kısımlarının" in blok),
        "dokuman_indiren": _tam(_alan(blok, r"indiren sayısı\s*:[ \t]*(\d+)[ \t]*\n")),
        "teklif_sayisi": _tam(_alan(blok, r"Toplam Teklif Sayısı[ \t]*:[ \t]*(\d+)")),
        "gecerli_teklif": _tam(_alan(blok, r"Geçerli Teklif Sayısı[ \t]*:[ \t]*(\d+)")),
        "sozlesme_bedeli": _sayi(bedel.group(1)) if bedel else None,
        "para_birimi": bedel.group(2) if bedel else None,
        "sozlesme_tarihi": _iso(_alan(blok, r"4-\s*Sözleşmenin\s*\n\s*a\)\s*Tarihi\s*:\s*(\d\d\.\d\d\.\d{4})")),
        "yuklenici": _alan(blok, r"Yüklenici(?:si)?\s*:\s*(.*?)\n\s*[e-g]\)"),     # "Yüklenicisi" ya da "Yüklenici"
    }


# ---------- Ana program ----------

if __name__ == "__main__":
    klasor = Path(sys.argv[1] if len(sys.argv) > 1 else "belgeler/bulten")   # varsayılan: belgeler/bulten
    pdfler = sorted(klasor.glob("*.pdf"))
    yeni = [p for p in pdfler if not veri.islenmis_mi(p.name)]      # sadece daha önce okunmamış olanlar
    print(f"{len(pdfler)} PDF var, {len(pdfler) - len(yeni)} tanesi daha önce okunmuş, {len(yeni)} yeni.")
    for sira, pdf in enumerate(yeni, start=1):
        try:
            bilgi, kayitlar = bulteni_oku(pdf)
        except Exception as hata:                                   # bozuk ya da farklı biçimli PDF: atla, devam et
            print(f"[{sira}/{len(yeni)}] {pdf.name}: OKUNAMADI ({hata})")
            continue
        veri.kayitlari_yaz("sonuclar" if bilgi["sonuc"] else "ilanlar", kayitlar)
        veri.islendi_isaretle(pdf.name, bilgi["bulten_tarihi"], bilgi["tur"], bilgi["sonuc"], len(kayitlar))
        print(f"[{sira}/{len(yeni)}] {pdf.name}: {bilgi['tur']} {'sonuç' if bilgi['sonuc'] else 'ilan'} bülteni, "
              f"{bilgi['bulten_tarihi']}, {len(kayitlar)} kayıt")