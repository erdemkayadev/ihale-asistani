"""Kamu İhale Bülteni arşivinden PDF indirir (ekap.kik.gov.tr, herkese açık, giriş gerekmez).
Örnek:  python bulten_indir.py --baslangic 01.09.2026 --bitis 27.11.2026"""
import argparse                                                     # komut satırı seçenekleri (--baslangic ...)
import html                                                         # "&amp;" gibi HTML kaçışlarını çözmek için
import io                                                           # indirilen ZIP'i diske yazmadan açmak için
import json                                                         # bülten çıkmayan günlerin listesini saklamak için
import re                                                           # sayfadaki form alanlarını bulmak için
import ssl                                                          # güvenli bağlantı (HTTPS) ayarları için
import subprocess                                                   # indirme bitince bulten.py'yi çalıştırmak için
import sys
import time                                                         # istekler arasında beklemek için
import zipfile                                                      # gelen ZIP'in içindeki PDF'leri çıkarmak için
from datetime import date, datetime, timedelta
from pathlib import Path

import requests                                                     # siteye istek atmak için
from requests.adapters import HTTPAdapter                           # bağlantı ayarını değiştirmek için

URL = "https://ekap.kik.gov.tr/ekap/ilan/bultenindirme.aspx"
KLASOR = Path("belgeler/bulten")
BOS_GUNLER = KLASOR / "_bulten_yok.json"                            # tatil/hafta içi bülten çıkmayan günler: tekrar denenmez
BEKLEME = 3                                                         # her istekten sonra kaç saniye beklensin (sunucuyu yormamak için)
TURLER = {"Mal": "MAL", "Hizmet": "HIZMET", "Yapım": "YAPIM", "Danışmanlık": "DANISMANLIK"}   # sitedeki ad -> dosya adı


class EskiSunucuUyumlu(HTTPAdapter):
    """EKAP sunucusu eski şifreleme yöntemleri kullanıyor; Python'un yeni sürümü bunları varsayılan olarak reddediyor.
    Burada o yöntemlere izin veriyoruz. Sertifika doğrulaması AÇIK kalıyor: doğru siteye bağlandığımızdan eminiz."""
    def init_poolmanager(self, *args, **kwargs):
        ayar = ssl.create_default_context()                         # varsayılan: sertifika kontrolü açık
        ayar.set_ciphers("DEFAULT:@SECLEVEL=1")                     # eski şifreleme yöntemlerine izin ver
        ayar.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)   # eski sunucu el sıkışmasına izin ver
        kwargs["ssl_context"] = ayar
        return super().init_poolmanager(*args, **kwargs)


def gizli_alanlar(sayfa):
    """Sayfadaki tüm gizli form alanlarını (__VIEWSTATE, __PIT ...) {ad: değer} olarak döndürür."""
    alanlar = {}
    for etiket in re.findall(r"<input[^>]+>", sayfa):
        if 'type="hidden"' not in etiket:
            continue
        ad = re.search(r'name="([^"]+)"', etiket)
        deger = re.search(r'value="([^"]*)"', etiket)
        if ad:
            alanlar[html.unescape(ad.group(1))] = html.unescape(deger.group(1)) if deger else ""
    return alanlar


def tur_secenekleri(sayfa):
    """İhale türü açılır listesindeki seçenekler: {'Mal': '1', ...}"""
    liste = re.search(r'<select[^>]*ddlstBxIhaleTur[^>]*>(.*?)</select>', sayfa, re.S)
    if not liste:
        return {}
    return {html.unescape(yazi).strip(): deger
            for deger, yazi in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', liste.group(1), re.S)}


def oturum_ac():
    """Tarayıcı gibi davranan bir oturum: çerezleri taşır, eski sunucuyla uyumlu bağlanır."""
    oturum = requests.Session()
    oturum.headers["User-Agent"] = "Mozilla/5.0 (ihale-asistani; bulten arsivi)"
    oturum.mount("https://", EskiSunucuUyumlu())
    return oturum


def bulten_indir(oturum, gun, tur):
    """Tek bir gün + tür için bülteni indirir. Döner: indirilen PDF adları ([] = o gün bülten yok)."""
    sayfa = oturum.get(URL, timeout=30)                             # 1) sayfayı aç: her seferinde taze gizli alanlar
    sayfa.raise_for_status()
    time.sleep(BEKLEME)
    form = {**gizli_alanlar(sayfa.text),                            # 2) butona basmış gibi formu gönder
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$btnYukle",
            "__EVENTARGUMENT": "",
            "ctl00$ContentPlaceHolder1$ddlstBxIhaleTur": tur_secenekleri(sayfa.text)[tur],
            "ctl00$ContentPlaceHolder1$etBultenTarihi$EkapTakvimTextBox_etBultenTarihi": gun.strftime("%d.%m.%Y")}
    yanit = oturum.post(URL, data=form, timeout=120)
    yanit.raise_for_status()
    time.sleep(BEKLEME)

    if not yanit.content.startswith(b"PK"):                         # ZIP gelmedi: sayfa döndü = o gün bülten yok
        return []
    kayitlar = []
    with zipfile.ZipFile(io.BytesIO(yanit.content)) as arsiv:       # 3) ZIP'teki PDF'leri klasöre çıkar
        for ad in arsiv.namelist():
            dosya = Path(ad).name                                   # sadece dosya adı: ZIP içindeki klasör yollarına güvenme
            if dosya.lower().endswith(".pdf"):
                (KLASOR / dosya).write_bytes(arsiv.read(ad))
                kayitlar.append(dosya)
    return kayitlar


def is_gunleri(baslangic, bitis):
    """Başlangıç-bitiş arası hafta içi günler, en yeniden en eskiye."""
    gun = bitis
    while gun >= baslangic:
        if gun.weekday() < 5:                                       # 0-4: Pazartesi-Cuma
            yield gun
        gun -= timedelta(days=1)


if __name__ == "__main__":
    tarih = lambda s: datetime.strptime(s, "%d.%m.%Y").date()       # "27.11.2026" -> tarih
    secim = argparse.ArgumentParser(description="Kamu İhale Bülteni arşivinden PDF indirir.")
    secim.add_argument("--baslangic", type=tarih, default=date.today(), help="GG.AA.YYYY (varsayılan: bugün)")
    secim.add_argument("--bitis", type=tarih, default=date.today(), help="GG.AA.YYYY (varsayılan: bugün)")
    secim.add_argument("--turler", default=",".join(TURLER), help="virgülle: Mal,Hizmet,Yapım,Danışmanlık")
    secim.add_argument("--okuma", action="store_true", help="indirme bitince bulten.py ile veritabanına da yaz")
    ayar = secim.parse_args()

    KLASOR.mkdir(parents=True, exist_ok=True)
    bos = set(json.loads(BOS_GUNLER.read_text(encoding="utf-8"))) if BOS_GUNLER.exists() else set()
    oturum = oturum_ac()
    ardisik_hata = 0
    for gun in is_gunleri(ayar.baslangic, ayar.bitis):
        for tur in ayar.turler.split(","):
            anahtar = f"{gun:%d%m%Y}_{TURLER[tur]}"                  # örn. "27112026_MAL"
            if (KLASOR / f"BULTEN_{anahtar}.pdf").exists() or anahtar in bos:
                continue                                            # zaten indirilmiş ya da o gün bülten yok
            try:
                pdfler = bulten_indir(oturum, gun, tur)
                ardisik_hata = 0
            except Exception as hata:                               # bağlantı sorunu: say, 3 kez üst üste olursa dur
                ardisik_hata += 1
                print(f"{gun:%d.%m.%Y} {tur}: HATA ({hata})")
                if ardisik_hata >= 3:
                    sys.exit("Üst üste 3 hata: durduruldu. Biraz bekleyip tekrar çalıştırın, kaldığı yerden devam eder.")
                time.sleep(BEKLEME * 10)
                continue
            if pdfler:
                print(f"{gun:%d.%m.%Y} {tur}: {', '.join(pdfler)}")
            else:
                print(f"{gun:%d.%m.%Y} {tur}: bülten yok")
                if gun < date.today():                              # bugünün bülteni henüz yayımlanmamış olabilir
                    bos.add(anahtar)
                    BOS_GUNLER.write_text(json.dumps(sorted(bos), ensure_ascii=False), encoding="utf-8")

    if ayar.okuma:
        subprocess.run([sys.executable, "bulten.py"])               # yeni PDF'leri veritabanına yaz