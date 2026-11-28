"""Ajanın veritabanı araçları. Model SQL yazmaz; sadece bu fonksiyonları parametreyle çağırır.
Halüsinasyona karşı kurallar kodun içinde: kapsam kontrolü, kısımlı ihale kuralı, şüpheli kayıt ayıklama, az veri uyarısı."""
import statistics                                                    # ortanca (medyan) ve çeyrekler için
from datetime import datetime                                       # "henüz teklif tarihi geçmemiş" ilanlar için
from bulten import ILLER                                            # 81 il listesi (tek yerde tutulur)
import veri                                                         # tüm SQL veri.py'den geçer

TAM_AY_ESIGI = 15            # bir ay en az bu kadar günün bülteni okunmuşsa istatistiğe girer
AZ_VERI = 20                 # bundan az kayda dayanan oranlarda "az veri" uyarısı
ORAN_ALT, ORAN_UST = 0.2, 1.5    # bedel / yaklaşık maliyet bu aralığın dışındaysa kaynakta şüpheli değer
ORNEK_SAYISI = 5             # cevapta gösterilecek örnek İKN sayısı (kullanıcı EKAP'tan doğrulayabilsin)
LISTE_SINIRI = 20            # listelerde en fazla kaç satır döner
ASIL_SONUC = "İHALE SONUÇLARININ İLANLARI"
ASIL_ILAN = "İHALE İLANLARI"
TURLER = ["Mal", "Hizmet", "Yapım", "Danışmanlık"]
# Usul adı iki tabloda farklı yazılıyor ("açık ihale" / "Açık", "pazarlık" / "Pazarlık (MD 21 F)"); hepsi bu başlangıçlarla eşleşir
USULLER = {"açık": "açık%", "pazarlık": "pazarlık%", "belli istekliler": "belli%"}


# ---------- yardımcılar ----------

def _araliklar(aylar):
    """['2024-06','2024-07','2024-09'] -> '2024-06 → 2024-07, 2024-09' (modele kısa metin gitsin)."""
    if not aylar:
        return "yok"
    sayi = lambda ay: int(ay[:4]) * 12 + int(ay[5:7])               # ayı tek bir sayıya çevir: ardışıklığı anlamak için
    parcalar, bas, onceki = [], aylar[0], aylar[0]
    for ay in aylar[1:] + [None]:
        if ay is None or sayi(ay) != sayi(onceki) + 1:              # ardışıklık bozuldu: bir aralığı kapat
            parcalar.append(bas if bas == onceki else f"{bas} → {onceki}")
            bas = ay
        onceki = ay or onceki
    return ", ".join(parcalar)


def kapsam():
    """Hangi aylar istatistik için yeterince okunmuş? Araçlar her hesaptan önce buna bakar."""
    satirlar = veri.sorgu("SELECT substr(bulten_tarihi, 1, 7), COUNT(DISTINCT bulten_tarihi) "
                          "FROM islenen_dosyalar GROUP BY 1 ORDER BY 1")
    tam = [ay for ay, gun in satirlar if gun >= TAM_AY_ESIGI]
    eksik = [ay for ay, gun in satirlar if gun < TAM_AY_ESIGI]
    son = veri.sorgu("SELECT MAX(bulten_tarihi) FROM islenen_dosyalar")[0][0]
    return {"tam_aylar": tam, "eksik_aylar": eksik, "son_bulten": son,
            "ozet": f"Tam okunmuş aylar: {_araliklar(tam)}. Eksik aylar: {_araliklar(eksik)}. Son bülten: {son}."}


def _il_bul(il):
    """'izmir' / 'İZMİR' -> 'İzmir'. 81 il dışındaysa None."""
    for ad in ILLER:
        if veri.tr_kucuk(ad) == veri.tr_kucuk(il.strip()):
            return ad
    return None


def _ayir(metin):
    """'perde, stor perde' ya da ['perde','stor perde'] -> ['perde', 'stor perde'] (arama biçimine çevrilmiş)."""
    liste = metin if isinstance(metin, list) else str(metin).split(",")
    return [veri.arama_metni(k).strip() for k in liste if k and veri.arama_metni(k).strip()]


def _kelimeler(metin, kelime_basi=False):
    """LIKE kalıpları. kelime_basi=True: '% perde%' (kelime 'perde' ile başlamalı; 'stor perde' gibi ifade de olur);
    False: '%perde%' (metnin herhangi bir yeri: idare ve firma adı için)."""
    on = "% " if kelime_basi else "%"
    return [f"{on}{k}%" for k in _ayir(metin)]


def _eslesen(konu, kelimeler):
    """Konuda hangi arama kelimesi geçti? Yanlış eşleşmeyi (ör. 'stor' -> 'storage') görünür kılmak için."""
    metin = veri.arama_metni(konu) or ""
    return next((k for k in _ayir(kelimeler) if f" {k}" in metin), None) if kelimeler else None


def _ihale_kodu(ikn):
    """'2024/1406909-4' -> '2024/1406909': kısımlı ihalenin kısımları tek ihale sayılsın."""
    return ikn.split("-")[0]


def _tur_bul(tur):
    """'mal' / 'YAPIM' / 'yapim' / 'danismanlik' -> 'Mal' / 'Yapım' / 'Danışmanlık'. Bulunamazsa None."""
    sade = lambda s: veri.tr_kucuk(s).translate(str.maketrans("çğıöşü", "cgiosu"))   # Türkçe harfsiz yazımı da kabul et
    return next((t for t in TURLER if sade(t) == sade(tur.strip())), None)


def _filtre(tablo, tur=None, usul=None, il=None, konu=None, idare=None, yuklenici=None, haric=None):
    """Ortak WHERE parçalarını kurar. Döner: (koşullar, parametreler, hata, notlar)."""
    kosul, param, notlar = [], [], []
    if tur:
        if not _tur_bul(tur):
            return None, None, f"Geçersiz tür: {tur}. Seçenekler: {', '.join(TURLER)}", notlar
        kosul.append("tur = ?"); param.append(_tur_bul(tur))
    if usul:
        if usul not in USULLER:
            return None, None, f"Geçersiz usul: {usul}. Seçenekler: {', '.join(USULLER)}", notlar
        kosul.append("tr_kucuk(usul) LIKE ?"); param.append(USULLER[usul])
    if konu:
        k = _kelimeler(konu, kelime_basi=True)                      # kelimelerden HERHANGİ biri, kelime başında geçsin (VEYA)
        kosul.append("(" + " OR ".join("arama_metni(konu) LIKE ?" for _ in k) + ")"); param += k
    if haric:
        for k in _kelimeler(haric, kelime_basi=True):               # bu kelimelerden biri geçen konular atılır ('storage' gibi)
            kosul.append("arama_metni(konu) NOT LIKE ?"); param.append(k)
    if idare:
        kosul.append("arama_metni(idare) LIKE ?"); param += _kelimeler(idare)[:1]
    if yuklenici and tablo == "sonuclar":
        kosul.append("arama_metni(yuklenici) LIKE ?"); param += _kelimeler(yuklenici)[:1]
    if il:
        ad = _il_bul(il)
        if not ad:
            return None, None, f"Geçersiz il: {il}", notlar
        if tablo == "ilanlar":
            kosul.append("il = ?"); param.append(ad)
        else:
            # Sonuç ilanında il yazmıyor. İl iki yoldan bulunur: aynı İKN'nin ihale ilanı, ya da ilanlarda
            # SADECE bu ilde görünen bir idare adı ("İL SAĞLIK MÜDÜRLÜĞÜ" gibi birçok ilde olan adlar kullanılmaz).
            kosul.append("(ikn IN (SELECT ikn FROM ilanlar WHERE il = ?) OR tr_kucuk(idare) IN ("
                         "SELECT k FROM (SELECT tr_kucuk(idare) AS k, COUNT(DISTINCT il) AS n, MAX(il) AS i "
                         "FROM ilanlar WHERE il IS NOT NULL AND idare IS NOT NULL GROUP BY k) WHERE n = 1 AND i = ?))")
            param += [ad, ad]
            notlar.append("Sonuç ilanlarında il yazmadığı için il, idarenin ihale ilanından bulundu; "
                          "eşleşmeyen sonuçlar bu sayıya dahil değil, gerçek sayı daha yüksek olabilir.")
    return kosul, param, None, notlar


def _donem(baslangic, bitis):
    """İstenen tarih aralığındaki TAM ayları seçer. Döner: (aylar, uyarılar)."""
    k = kapsam()
    bas, son = (baslangic or "0000-00")[:7], (bitis or "9999-99")[:7]
    aylar = [ay for ay in k["tam_aylar"] if bas <= ay <= son]
    eksik = [ay for ay in k["eksik_aylar"] if bas <= ay <= son]
    uyari = [f"Şu aylar eksik okunduğu için hesaba katılmadı: {_araliklar(eksik)}."] if eksik else []
    return aylar, uyari


def _oran(r):
    """Kazanan teklif / yaklaşık maliyet. Döner: (oran ya da None, neden)."""
    bedel, yaklasik, kisim, kisimli, para = r["sozlesme_bedeli"], r["yaklasik_maliyet"], r["kisim_yaklasik"], r["kisimli"], r["para_birimi"]
    if para != "TRY" or not bedel:
        return None, "doviz_ya_da_bedel_yok"
    if not kisimli and yaklasik:
        oran = bedel / yaklasik
    elif kisimli and kisim and yaklasik and 0 < kisim < yaklasik - 0.01:
        oran = bedel / kisim                                        # kısmın kendi yaklaşık maliyeti biliniyor
    else:
        return None, "kisimli_kisim_maliyeti_yok"                   # tüm ihalenin maliyetiyle tek kısmın bedeli kıyaslanamaz
    if not ORAN_ALT <= oran <= ORAN_UST:
        return None, "supheli_oran"                                 # kaynakta tutarsız giriş (ör. aylık maliyet, toplam bedel)
    return oran, None


SONUC_ALANLARI = ["ikn", "idare", "konu", "bulten_tarihi", "ihale_tarihi", "usul", "tur", "yaklasik_maliyet",
                  "kisim_yaklasik", "kisimli", "sozlesme_bedeli", "para_birimi", "teklif_sayisi", "yuklenici"]


def _sonuclari_getir(kosul, param, aylar=None, sinir=None):
    """Asıl sonuç kayıtlarını sözlük listesi olarak getirir."""
    kosul = [f"ilan_turu = '{ASIL_SONUC}'"] + kosul
    if aylar is not None:
        kosul.append("substr(bulten_tarihi, 1, 7) IN (" + ",".join("?" * len(aylar)) + ")"); param = param + aylar
    sql = (f"SELECT {', '.join(SONUC_ALANLARI)} FROM sonuclar WHERE {' AND '.join(kosul)} "
           f"ORDER BY bulten_tarihi DESC" + (f" LIMIT {int(sinir)}" if sinir else ""))
    return [dict(zip(SONUC_ALANLARI, s)) for s in veri.sorgu(sql, param)]


# ---------- ajanın çağıracağı araçlar ----------

def sonuc_istatistigi(tur=None, usul=None, il=None, konu=None, haric=None, idare=None, baslangic=None, bitis=None):
    """Sonuçlanmış ihalelerin özeti: kazanan teklif / yaklaşık maliyet (ortanca, sözleşme bazında),
    teklif sayısı ve tek teklif oranı (ihale bazında: kısımlı ihale bir kez sayılır)."""
    kosul, param, hata, notlar = _filtre("sonuclar", tur, usul, il, konu, idare, haric=haric)
    if hata:
        return {"hata": hata}
    aylar, uyarilar = _donem(baslangic, bitis)
    if not aylar:
        return {"veri_yok": "İstenen dönemde tam okunmuş ay yok.", "kapsam": kapsam()["ozet"]}
    kayitlar = _sonuclari_getir(kosul, param, aylar)
    if not kayitlar:
        return {"veri_yok": "Bu filtrelere uyan sonuçlanmış ihale bulunamadı.", "donem": _araliklar(aylar)}

    oranlar, dislanan = [], {"doviz_ya_da_bedel_yok": 0, "kisimli_kisim_maliyeti_yok": 0, "supheli_oran": 0}
    for r in kayitlar:
        oran, neden = _oran(r)
        r["oran"] = oran
        if oran is None:
            dislanan[neden] += 1
        else:
            oranlar.append(oran)
    ihale_teklif = {}                                               # İKN -> teklif sayısı (kısımlar tek ihale)
    for r in kayitlar:
        if r["teklif_sayisi"] is not None:
            kod = _ihale_kodu(r["ikn"])
            ihale_teklif[kod] = max(ihale_teklif.get(kod, 0), r["teklif_sayisi"])
    teklifler = list(ihale_teklif.values())

    sonuc = {
        "donem": _araliklar(aylar),
        "sozlesme_sayisi": len(kayitlar),
        "ihale_sayisi": len({_ihale_kodu(r["ikn"]) for r in kayitlar}),
        "oran_hesaplanan_kayit": len(oranlar),
        "oran_ortanca_yuzde": round(100 * statistics.median(oranlar), 1) if oranlar else None,
        "oran_ceyrekler_yuzde": [round(100 * q, 1) for q in statistics.quantiles(oranlar, n=4)][0::2] if len(oranlar) >= 4 else None,
        "teklif_sayisi_ortanca": statistics.median(teklifler) if teklifler else None,
        "tek_teklif_yuzde": round(100 * sum(t == 1 for t in teklifler) / len(teklifler), 1) if teklifler else None,
        "oran_disi_birakilan": dislanan,
        "ornekler": [{"ikn": r["ikn"], "idare": r["idare"], "konu": (r["konu"] or "")[:80], "sonuc_ilani": r["bulten_tarihi"],
                      "eslesen_kelime": _eslesen(r["konu"], konu),
                      "oran_yuzde": round(100 * r["oran"], 1) if r["oran"] else None} for r in kayitlar[:ORNEK_SAYISI]],
        "uyarilar": uyarilar + notlar,
        "not": "Oran = kazanan teklif / yaklaşık maliyet, ortanca, sözleşme bazında. Kısımlı ihalelerde sadece kısmın "
               "kendi maliyeti biliniyorsa hesaplanır. Teklif sayısı ve tek teklif oranı ihale bazında. Tutarlar nominal TL.",
    }
    if len(oranlar) < AZ_VERI:
        sonuc["uyarilar"].append(f"Oran sadece {len(oranlar)} kayda dayanıyor; temkinli yorumlanmalı.")
    return sonuc


def sonuc_listesi(tur=None, usul=None, il=None, konu=None, haric=None, idare=None, yuklenici=None, baslangic=None, bitis=None):
    """Tek tek sonuçlanmış ihaleler (en yeniden eskiye): kim, ne kadara, kaç teklifle kazandı."""
    kosul, param, hata, notlar = _filtre("sonuclar", tur, usul, il, konu, idare, yuklenici, haric)
    if hata:
        return {"hata": hata}
    if baslangic:
        kosul.append("bulten_tarihi >= ?"); param.append(baslangic)
    if bitis:
        kosul.append("bulten_tarihi <= ?"); param.append(bitis)
    toplam = veri.sorgu(f"SELECT COUNT(*) FROM sonuclar WHERE ilan_turu = '{ASIL_SONUC}'"
                        + "".join(f" AND {k}" for k in kosul), param)[0][0]
    kayitlar = _sonuclari_getir(kosul, param, sinir=LISTE_SINIRI)
    liste = []
    for r in kayitlar:
        oran, neden = _oran(r)
        liste.append({"ikn": r["ikn"], "idare": r["idare"], "konu": (r["konu"] or "")[:100],
                      "eslesen_kelime": _eslesen(r["konu"], konu), "usul": r["usul"],
                      "sonuc_ilani": r["bulten_tarihi"], "yuklenici": r["yuklenici"],
                      "sozlesme_bedeli": r["sozlesme_bedeli"], "para_birimi": r["para_birimi"],
                      "teklif_sayisi": r["teklif_sayisi"],
                      "oran_yuzde": round(100 * oran, 1) if oran else None,
                      "oran_yok_nedeni": neden})
    uyarilar = notlar + (["Bazı aylar eksik okundu; liste tam olmayabilir."] if kapsam()["eksik_aylar"] else [])
    return {"toplam_eslesen": toplam, "gosterilen": len(liste), "sonuclar": liste, "uyarilar": uyarilar}


def acik_ihaleler(tur=None, usul=None, il=None, konu=None, haric=None, idare=None):
    """Teklif tarihi henüz geçmemiş ihale ilanları (en yakın tarihten başlayarak)."""
    kosul, param, hata, notlar = _filtre("ilanlar", tur, usul, il, konu, idare, haric=haric)
    if hata:
        return {"hata": hata}
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M")
    kosul = [f"ilan_turu = '{ASIL_ILAN}'", "ihale_tarihi >= ?"] + kosul
    param = [simdi] + param
    alanlar = ["ikn", "tur", "idare", "il", "konu", "ihale_tarihi", "usul", "bulten_tarihi"]
    satirlar = veri.sorgu(f"SELECT {', '.join(alanlar)} FROM ilanlar WHERE {' AND '.join(kosul)} "
                          f"ORDER BY ihale_tarihi", param)
    tekil = {}
    for s in satirlar:                                              # aynı İKN birden fazla bültende olabilir: birini tut
        tekil.setdefault(s[0], dict(zip(alanlar, s)))
        tekil[s[0]]["eslesen_kelime"] = _eslesen(s[4], konu)
    ilanlar = list(tekil.values())[:LISTE_SINIRI]
    if ilanlar:                                                     # takip edilecek ihalede düzeltme ya da iptal ilanı var mı?
        ikn_listesi = [i["ikn"] for i in ilanlar]
        degisen = veri.sorgu("SELECT ikn, ilan_turu FROM ilanlar WHERE ikn IN (" + ",".join("?" * len(ikn_listesi)) + ") "
                             "AND ilan_turu IN ('İHALE DÜZELTME İLANLARI', 'İHALE İPTAL İLANLARI')", ikn_listesi)
        for ikn, tur_adi in degisen:
            tekil[ikn]["uyari"] = "İPTAL EDİLMİŞ" if "İPTAL" in tur_adi else "Düzeltme ilanı var, EKAP'tan kontrol et"
    return {"toplam_eslesen": len(tekil), "gosterilen": len(ilanlar), "ilanlar": ilanlar,
            "son_bulten": kapsam()["son_bulten"], "uyarilar": notlar}