import argparse                                                     # komut satırından örnek sayısını almak için
import random                                                       # rastgele kayıt seçmek için
import re                                                           # PDF metninde kalıp aramak için
from pathlib import Path                                            # dosya yolları için
from pypdf import PdfReader                                         # PDF'ten metin çıkarmak için
import bulten                                                       # okuyucunun kalıplarını (ILAN, UST_BILGI) aynen kullanmak için
import veri                                                         # veritabanı bağlantısı

KLASOR = Path("belgeler/bulten")                                    # PDF'lerin olduğu klasör
RAPOR = KLASOR / "denetim_raporu.txt"                               # rapor dosyası (bu klasör gitignore'da, repoya girmez)
ASIL = {"ilanlar": "İHALE İLANLARI", "sonuclar": "İHALE SONUÇLARININ İLANLARI"}   # sadece asıl kayıtlar denetlenir
KOD = {"Mal": "MAL", "Hizmet": "HIZMET", "Yapım": "YAPIM", "Danışmanlık": "DANISMANLIK"}   # dosya adındaki tür kodu
# Denetlenecek gruplar: (tablo, tür, başlangıç, bitiş, grup adı). Eski biçimli Mal günleri ayrı grup, yoksa rastgele seçime hiç girmezler.
GRUPLAR = [(t, tur, "2026-06-01", "2099-01-01", f"{t} {tur} 2026") for t in ASIL for tur in KOD] + \
          [(t, "Mal", "2000-01-01", "2026-06-01", f"{t} Mal eski örnek günler") for t in ASIL]
# Mantık kontrolleri: (tablo, açıklama, SQL koşulu). Sayı çıkması hata demek değil; örneklere bakıp karar vereceğiz.
KONTROLLER = [
    ("sonuclar", "Geçerli teklif > toplam teklif", "gecerli_teklif > teklif_sayisi"),
    ("sonuclar", "Sözleşme tarihi < ihale tarihi", "sozlesme_tarihi < ihale_tarihi"),
    ("sonuclar", "İhale tarihi > bülten tarihi", "ihale_tarihi > bulten_tarihi"),
    ("sonuclar", "Sözleşme tarihi > bülten tarihi", "sozlesme_tarihi > bulten_tarihi"),
    ("sonuclar", "Sözleşme bedeli boş ya da sıfır", "sozlesme_bedeli IS NULL OR sozlesme_bedeli <= 0"),
    ("sonuclar", "Bedel/yaklaşık < %20 ya da > %150 (tek sözleşmeli, TL)",
     "kisimli = 0 AND para_birimi = 'TRY' AND yaklasik_maliyet > 0 AND "
     "(sozlesme_bedeli < 0.2 * yaklasik_maliyet OR sozlesme_bedeli > 1.5 * yaklasik_maliyet)"),
    ("sonuclar", "Teklif sayısı 0 ama yüklenici var", "teklif_sayisi = 0 AND yuklenici IS NOT NULL"),
    ("ilanlar", "İhale tarihi < bülten tarihi", "substr(ihale_tarihi, 1, 10) < bulten_tarihi"),
    ("ilanlar", "Geçici teminat %3 değil", "gecici_teminat IS NOT NULL AND gecici_teminat <> 3"),
    ("ilanlar", "Geçerlilik < 30 ya da > 365 gün", "gecerlilik_gun < 30 OR gecerlilik_gun > 365"),
]


def satirlar(db, sql, parametreler=()):
    """Sorgu sonucunu {sütun adı: değer} sözlükleri listesi olarak döndürür."""
    imlec = db.execute(sql, parametreler)
    adlar = [d[0] for d in imlec.description]                       # sütun adları
    return [dict(zip(adlar, satir)) for satir in imlec.fetchall()]


def pdf_adi(kayit, tablo):
    """Kaydın geldiği PDF'in adı: 2026-07-14 + Mal + sonuç -> BULTEN_14072026_MAL_SONUC.pdf"""
    y, a, g = kayit["bulten_tarihi"].split("-")
    return f"BULTEN_{g}{a}{y}_{KOD[kayit['tur']]}{'_SONUC' if tablo == 'sonuclar' else ''}.pdf"


_onbellek = {}                                                      # aynı PDF'i iki kez okumamak için
def bloklar(pdf):
    """PDF'i okuyucuyla aynı şekilde ilanlara böler: {(sıra, İKN): ilan metni}."""
    if pdf not in _onbellek:
        metin = "\n".join(s.extract_text() or "" for s in PdfReader(KLASOR / pdf).pages)
        metin = bulten.UST_BILGI.sub("\n", metin)                   # sayfa üst bilgilerini sil (okuyucuyla aynı)
        bas = [m.start() for m in re.finditer(r"A- 4734 SAYILI KANUN KAPSAMINDAKİ İLANLAR", metin)]
        metin = metin[bas[1]:] if len(bas) > 1 else metin           # içindekileri atla (okuyucuyla aynı)
        es = list(bulten.ILAN.finditer(metin))
        sozluk = {}
        for i, e in enumerate(es):
            son = es[i + 1].start() if i + 1 < len(es) else len(metin)
            sozluk.setdefault((int(e.group(1)), e.group(2)), metin[e.start():son])
        _onbellek[pdf] = sozluk
    return _onbellek[pdf]


def kisalt(blok, tablo):
    """Sonuç ilanı kısa, olduğu gibi kalır. Uzun ihale ilanında başı + tarih/teminat/geçerlilik satırları kalır."""
    blok = re.sub(r"\n\s*\n+", "\n", blok).strip()                  # boş satırları at
    if tablo == "sonuclar" or len(blok) <= 2500:
        return blok
    sat = blok.split("\n")
    ekler = ["\n".join(sat[max(i - 1, 0):i + 2]) for i, s in enumerate(sat)
             if re.search(r"teminat|geçerlilik|[Tt]arihi?\s+ve\s+[Ss]aati", s)]   # ilgili satır + bir önceki + bir sonraki
    return blok[:2000] + "\n[...]\n" + "\n[...]\n".join(ekler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--adet", type=int, default=8, help="her gruptan kaç kayıt")
    ap.add_argument("--tohum", type=int, default=42, help="aynı tohum = her çalıştırmada aynı örnekler")
    arg = ap.parse_args()
    db = veri.baglan()
    rastgele = random.Random(arg.tohum)

    with open(RAPOR, "w", encoding="utf-8") as f:
        # ---------- 1. Mantık kontrolleri ----------
        f.write("=== 1. MANTIK KONTROLLERİ (tüm asıl kayıtlar) ===\n")
        print("=== Mantık kontrolleri ===")
        for tablo, ad, kosul in KONTROLLER:
            toplam = db.execute(f"SELECT COUNT(*) FROM {tablo} WHERE ilan_turu = ?", (ASIL[tablo],)).fetchone()[0]
            bulunan = satirlar(db, f"SELECT bulten_tarihi, tur, sira, ikn FROM {tablo} "
                                   f"WHERE ilan_turu = ? AND ({kosul})", (ASIL[tablo],))
            satir = f"{tablo:9} {ad:<55} {len(bulunan):>6} / {toplam}  (%{100 * len(bulunan) / toplam:.2f})"
            print(satir)
            f.write(satir + "\n")
            for o in bulunan[:3]:                                   # her kontrolden 3 örnek, PDF'ten bakmak için
                f.write(f"            örnek: {o['bulten_tarihi']} {o['tur']} sıra {o['sira']} İKN {o['ikn']}\n")

        # ---------- 2. Örnek denetimi ----------
        f.write("\n=== 2. ÖRNEK DENETİMİ: okunan alanlar ve PDF'teki asıl metin ===\n")
        print("\n=== Örnek denetimi (her yeni PDF birkaç saniye sürer) ===")
        no = 0
        for tablo, tur, bas, son, grup in GRUPLAR:
            anahtarlar = db.execute(f"SELECT bulten_tarihi, tur, ilan_turu, sira FROM {tablo} "
                                    "WHERE tur = ? AND ilan_turu = ? AND bulten_tarihi >= ? AND bulten_tarihi < ? "
                                    "ORDER BY bulten_tarihi, sira", (tur, ASIL[tablo], bas, son)).fetchall()
            for anahtar in rastgele.sample(anahtarlar, min(arg.adet, len(anahtarlar))):
                no += 1
                kayit = satirlar(db, f"SELECT * FROM {tablo} WHERE bulten_tarihi = ? AND tur = ? "
                                     "AND ilan_turu = ? AND sira = ?", tuple(anahtar))[0]
                pdf = pdf_adi(kayit, tablo)
                print(f"[{no}] {grup}: {pdf} sıra {kayit['sira']}")
                f.write(f"\n{'=' * 90}\n#{no}  {grup}  |  {pdf}  |  sıra {kayit['sira']}  |  İKN {kayit['ikn']}\n")
                f.write("--- OKUNAN ALANLAR ---\n")
                for alan, deger in kayit.items():
                    if alan not in veri.ANAHTAR:                    # tarih, tür, bölüm, sıra zaten başlıkta yazıyor
                        f.write(f"  {alan:<17}: {deger}\n")
                f.write("--- PDF'TEKİ ASIL METİN ---\n")
                try:
                    blok = bloklar(pdf).get((kayit["sira"], kayit["ikn"]))
                    f.write(kisalt(blok, tablo) + "\n" if blok else "  (ilan PDF'te bulunamadı)\n")
                except FileNotFoundError:
                    f.write("  (PDF dosyası yok)\n")

    print(f"\nRapor yazıldı: {RAPOR}")