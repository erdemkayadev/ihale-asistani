"""İhale asistanı ajanı. Model soruya göre araç seçer (veritabanı, şartname), sonucu okur ve cevap yazar.
Halüsinasyon önlemleri: model SQL yazmaz, hesap yapmaz; cevaptaki her sayı araç çıktılarında aranır."""
import json                                                         # araç parametreleri ve çıktıları JSON olarak gider gelir
import os                                                           # model adını .env'den almak için
import re                                                           # cevaptaki sayıları bulmak için
from datetime import date                                           # talimata bugünün tarihini yazmak için
from dotenv import load_dotenv                                      # .env dosyasını okumak için
from openai import OpenAI                                           # model
import araclar                                                      # veritabanı araçları
import veri                                                         # arama_metni: Türkçe küçük harf + noktalama temizliği

load_dotenv()
client = OpenAI()
MAKS_TUR = 6                                                        # bir soruda en fazla kaç kez araç çağrılabilir

TALIMAT = """Sen Türkiye kamu ihaleleri için bir asistansın. Kullanıcılar EKAP'tan iş kovalayan firmalar; teknik terim bilmezler.

NE YAPABİLİRSİN (sadece bunlar):
- Sonuçlanmış ihalelerde kazanan teklifin yaklaşık maliyete oranı, teklif sayısı, tek teklif oranı (sonuc_istatistigi)
- Kim kazandı, ne kadara, kaç teklifle (sonuc_listesi); en çok ihale kazanan firmalar (firma_siralamasi)
- Teklif tarihi geçmemiş ihale ilanları (acik_ihaleler); verinin hangi dönemi kapsadığı (kapsam)
- Kullanıcının yüklediği şartnamenin içeriği (sartname_sor)
Bunların dışındaki bir şeyi (İKN ile doküman getirme, EKAP'a giriş, teklif hazırlama, kazanma tahmini) yapamazsın;
"yapabilirim" diye vaat etme, kullanıcıyı da yapamayacağın bir seçime yönlendirme.

DOĞRULUK:
1. Rakam, firma, idare ya da ihale bilgisi gereken HER soruda araç kullan. Kendi bilginden rakam verme.
2. Sayıları araçtaki gibi, rakamla aktar; yanına yazıyla karşılık ekleme ("90 (doksan)" yazma). Yuvarlama, toplama,
   ortalama gibi hiçbir hesap yapma. Tutarları kısaltma ("1.200.000 TL"). Olmayan bir şeye sayı uydurma ("%0", "sıfır"):
   "istenmiyor", "yok" de.
3. Araç veri bulamazsa ya da hata verirse bunu açıkça söyle, tahmin yürütme. Hiçbir araç kapsamıyorsa
   "bu bilgi elimdeki veride yok" de, sayı yazma.
4. Önceki mesajlardaki rakamları tekrar kullanacaksan aracı yeniden çağır.

PARAMETRELER:
5. Soruda iş konusu geçiyorsa (örn. temizlik, perde) onu "konu"ya yaz; geçmiyorsa boş bırak. Soruda geçmeyen filtreyi
   (konu, usul, il, idare) ekleme; program bunları zaten siler.
6. Alan bilgisi: perde/stor/tül/jaluzi ürünü -> "Mal" (yapımda "perde" betonarme duvardır). Yemek, temizlik, güvenlik,
   taşıma -> "Hizmet". Bina, yol, altyapı -> "Yapım". "Güvenlik hizmeti" sorulursa konu "özel güvenlik" olsun
   (sadece "güvenlik" iş güvenliği, bilgi güvenliği gibi alakasız işleri de getirir).
7. Konu araması kelime başına göre çalışır ("stor" -> "storage"). Sonuçlardaki "eslesen_kelime"ye bak; alakasız eşleşme
   varsa "haric" ile çıkarıp aracı tekrar çağır.
8. Tarihler: bugün {bugun}. "Bu ay" = {bu_ay}, "geçen ay" = {gecen_ay}, "bu yıl" = {bu_yil}, "geçen yıl" = {gecen_yil}.
   Kullanıcı dönem söylemediyse tarih verme (araç tüm veriyi kullanır). Soru netse onay sorma, aracı çağır.
{belge}
CEVAP BİÇİMİ (kullanıcı bunu okuyacak):
9. İlk cümle doğrudan cevap olsun (örn. "Ankara'da temizlik ihaleleri yaklaşık maliyetin ortanca %93,7'sine gidiyor.").
10. Ardından kısa bir "Dayanak" satırı: dönem (ay adlarıyla, örn. Haziran–Kasım 2026), kaç ihaleye dayandığı.
11. Sadece kullanıcıyı ilgilendiren uyarıları yaz, sade dille: az veri, il eşleşmesi eksikliği, firma adı birleştirme.
    Eksik okunan ay listelerini, "veri_yok", "araç", "araç çıktısı", parametre adları gibi teknik kelimeleri ASLA yazma.
12. İstatistikte doğrulama için 2-3 örnek İKN ver. Veri yoksa İKN başlığı açma.
13. Liste araçlarının (sonuc_listesi, acik_ihaleler, firma_siralamasi) sonucu kullanıcıya program tarafından ayrıca TABLO
    olarak gösterilir. Satırları tekrar yazma; 2-3 cümle özet yaz. "eslesen_ihale" ihale sayısı, "eslesen_sozlesme"
    sözleşme sayısıdır; karıştırma.
14. Hukuki tavsiye verme. Önemli kararlardan önce EKAP'tan ve ihale dokümanından doğrulanmasını hatırlat (tek cümle).
15. Kısa yaz. Kalın yazıyı en fazla 2-3 yerde kullan. Cevabı "İstersen ... yapayım mı?" gibi bir teklif ya da soruyla
    bitirme; kullanıcı ne isterse kendisi sorar."""

BELGE_VAR = """ŞARTNAME: Kullanıcı "{ad}" adlı belgeyi yükledi. "Bu ihalede", "şartnamede", "belgede", "iş deneyimi",
"teminat", "süre", "yeterlik", "ceza", "ödeme" gibi sorular bu belgeyle ilgilidir: sartname_sor aracını kullan, İKN isteme.
Belgeden doğrulanmış ihale kartı (sorudan önce bilinenler):
{kart}
Şartname cevabı kuralları:
- sartname_sor'un "cevap" alanındaki hükmü değiştirme, ona aykırı ya da ek sonuç çıkarma.
- sartname_sor'un "kaynaklar"ındaki alıntılar ve "istenen_belgeler" listesi kullanıcıya program tarafından ayrıca
  gösterilir; tekrar yazma, "listeleyeyim mi" diye sorma. Listenin varlığını ve öne çıkan 2-3 şartı söylemen yeterli.
  Kısa cevabı yaz ve dayandığı madde etiketini söyle (örn. "Madde 7'ye göre iş deneyim belgesi istenmiyor.").
- "istenen_belgeler" geldiyse listeyi madde madde tekrar yazma, tablodan ya da programdan bahsetme; kaç bent olduğunu
  ve öne çıkan 2-3 yeterlik şartını tek paragrafta söyle.
- Belgedeki bir şartı aktarırken kelimelerini koru; zamanını, kipini, sayısını değiştirme ("aktif olarak hizmet
  vermesi" ile "hizmet vermiş olması" farklı şartlardır). Emin değilsen şartı tırnak içinde aynen yaz.
- "bulundu" false ise: "Belgede bu konuda bir hüküm bulamadım" de ve "okunan" bilgisini aktar ("belgenin tamamı okundu").
  Kendi bilginden ihale mevzuatı yorumu ekleme.
- Kullanıcı "çıkar", "devam", "detay ver" gibi kısa bir takip yazarsa önceki konuyu genişleterek sartname_sor'a
  AÇIK bir soru gönder (örn. "İş deneyimi ve benzer iş tanımıyla ilgili tüm hükümler neler?").
"""
BELGE_YOK = """ŞARTNAME: Kullanıcı belge yüklemedi. Belge içeriği sorarsa sol menüden PDF ya da Word yüklemesini söyle.
"""

_ORTAK = {                                                           # birçok araçta aynı olan parametreler
    "tur": {"type": "string", "enum": ["Mal", "Hizmet", "Yapım", "Danışmanlık"], "description": "İhale türü"},
    "usul": {"type": "string", "enum": ["açık", "pazarlık", "belli istekliler"], "description": "İhale usulü"},
    "il": {"type": "string", "description": "İl adı, örn. Ankara"},
    "konu": {"type": "string", "description": "Konuda geçecek kelime ya da ifadeler, virgülle: 'perde, stor perde, tül'"},
    "haric": {"type": "string", "description": "Konuda geçmemesi gereken kelimeler, virgülle: 'storage, server'"},
    "idare": {"type": "string", "description": "İdare adında geçecek ifade, örn. 'karayolları'"},
}
_TARIH = {
    "baslangic": {"type": "string", "description": "Başlangıç tarihi YYYY-MM-DD"},
    "bitis": {"type": "string", "description": "Bitiş tarihi YYYY-MM-DD"},
}
ARAC_TANIMLARI = [
    {"type": "function", "name": "kapsam",
     "description": "Veritabanında hangi ayların tam okunduğunu ve son bülten tarihini verir. Dönem sorularından önce kullan.",
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "sonuc_istatistigi",
     "description": "Sonuçlanmış ihalelerin özeti: kazanan teklifin yaklaşık maliyete oranı (ortanca, %), teklif sayısı, "
                    "tek teklif oranı. 'Ne kadara gider, kaç teklif gelir, rekabet nasıl' soruları için.",
     "parameters": {"type": "object", "properties": {**_ORTAK, **_TARIH}}},
    {"type": "function", "name": "sonuc_listesi",
     "description": "Tek tek sonuçlanmış ihaleler: kim kazandı, ne kadara, kaç teklifle. En fazla 20 satır, en yeniden eskiye. "
                    "Firma ya da idare geçmişi, örnek ihaleler için.",
     "parameters": {"type": "object", "properties": {**_ORTAK, **_TARIH,
                    "yuklenici": {"type": "string", "description": "Kazanan firma adında geçecek ifade"}}}},
    {"type": "function", "name": "firma_siralamasi",
     "description": "En çok ihale kazanan ilk 10 firma: kazandığı ihale ve sözleşme sayısı, toplam TL bedel, örnek İKN. "
                    "'En çok kim kazanıyor, rakipler kim, pazarın büyük oyuncuları' soruları için.",
     "parameters": {"type": "object", "properties": {**_ORTAK, **_TARIH}}},
    {"type": "function", "name": "acik_ihaleler",
     "description": "Teklif tarihi henüz geçmemiş ihale ilanları, en yakın tarihten başlayarak. En fazla 20 satır.",
     "parameters": {"type": "object", "properties": _ORTAK}},
    {"type": "function", "name": "sartname_sor",
     "description": "SADECE kullanıcının yüklediği ihale dokümanının (idari/teknik şartname) içinde arama yapar. "
                    "Geçmiş ihale sonuçları ya da kazananlar için KULLANMA.",
     "parameters": {"type": "object", "properties": {"soru": {"type": "string", "description": "Belgeye sorulacak soru"}},
                    "required": ["soru"]}},
]
for _t in ARAC_TANIMLARI:                                            # parametreler isteğe bağlı: katı şema doğrulamasını kapat
    _t["strict"] = False


def _araci_calistir(ad, parametreler, belge):
    """Modelin istediği aracı çalıştırır. Döner: JSON'a çevrilebilir sonuç."""
    try:
        if ad == "kapsam":
            return {k: v for k, v in araclar.kapsam().items() if k in ("ozet", "son_bulten")}
        if ad == "sartname_sor":
            if belge is None:
                return {"hata": "Kullanıcı henüz bir ihale dokümanı yüklemedi."}
            import rag                                              # şartname aracı sadece gerekince yüklensin
            return rag.sartname_cevapla(belge, parametreler.get("soru", ""))
        if ad in ("sonuc_istatistigi", "sonuc_listesi", "acik_ihaleler", "firma_siralamasi"):
            return getattr(araclar, ad)(**parametreler)
        return {"hata": f"Bilinmeyen araç: {ad}"}
    except TypeError as hata:                                       # model tanımsız bir parametre gönderdiyse
        return {"hata": f"Geçersiz parametre: {hata}"}
    except Exception as hata:                                       # şartname tarafında API hatası vb.: sohbet çökmesin
        return {"hata": f"İşlem yapılamadı: {type(hata).__name__}"}


# ---------- listeler: tabloyu model değil program basar ----------

LISTE_ARACLARI = {                                                  # araç adı -> (sonuçtaki liste anahtarı, sütunlar)
    "sonuc_listesi": ("sonuclar", [("ikn", "İKN"), ("yuklenici", "Kazanan"), ("sozlesme_bedeli", "Bedel"),
                                   ("oran_yuzde", "Oran %"), ("teklif_sayisi", "Teklif"), ("sonuc_ilani", "Sonuç ilanı"),
                                   ("konu", "Konu"), ("idare", "İdare"), ("usul", "Usul")]),
    "acik_ihaleler": ("ilanlar", [("ikn", "İKN"), ("ihale_tarihi", "Teklif tarihi"), ("konu", "Konu"), ("il", "İl"),
                                  ("idare", "İdare"), ("usul", "Usul"), ("uyari", "Uyarı")]),
    "firma_siralamasi": ("firmalar", [("sira", "Sıra"), ("yuklenici", "Firma"), ("ihale_sayisi", "İhale"),
                                      ("sozlesme_sayisi", "Sözleşme"), ("toplam_bedel_try", "Toplam bedel"),
                                      ("ornek_ikn", "Örnek İKN")]),
    "sartname_sor": ("istenen_belgeler", [("bent", "Bent"), ("kisa", "İstenen belge / şart")]),   # "hangi belgeler" sorusu
}


def _bicim(anahtar, deger, satir):
    """Hücre metni: bedel Türkçe binlik ayraçla ve para birimiyle, boşlar '-'."""
    if deger is None or deger == "":
        return "-"
    if anahtar in ("sozlesme_bedeli", "toplam_bedel_try"):
        metin = f"{deger:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")   # 1234567.5 -> 1.234.567,50
        return f"{metin} {satir.get('para_birimi') or 'TRY'}"
    if anahtar == "oran_yuzde":
        return str(deger).replace(".", ",")
    return str(deger)


def tablo_yap(ad, sonuc):
    """Liste aracının çıktısından tablo satırları: [{"İKN": ..., "Kazanan": ...}]. Liste aracı değilse None."""
    if ad not in LISTE_ARACLARI or not isinstance(sonuc, dict):
        return None
    anahtar, sutunlar = LISTE_ARACLARI[ad]
    return [{baslik: _bicim(k, satir.get(k), satir) for k, baslik in sutunlar} for satir in sonuc.get(anahtar, [])]


# ---------- uydurma filtre koruması ----------

DENETLENEN = ("konu", "usul", "il", "idare", "yuklenici")           # soruda geçmesi gereken parametreler


def _soruda_mi(ifade, metin):
    """İfadedeki kelimelerden birinin kökü (ilk 5 harf) soruda/geçmişte geçiyor mu? 'stor perde' -> 'perdeler' de sayılır."""
    return any((" " + k[:5]) in metin for k in veri.arama_metni(ifade).split())


def filtreleri_temizle(parametreler, metin):
    """Kullanıcının söylemediği konu/usul/il/idare/firma filtrelerini siler. Döner: (temiz parametreler, silinenler)."""
    temiz, silinen = dict(parametreler), []
    for ad in DENETLENEN:
        if ad not in temiz:
            continue
        parcalar = [x.strip() for x in str(temiz[ad]).split(",") if x.strip()]
        kalan = [x for x in parcalar if _soruda_mi(x, metin)]
        silinen += [f"{ad}={x}" for x in parcalar if x not in kalan]
        if kalan:
            temiz[ad] = ", ".join(kalan)
        else:
            del temiz[ad]
    return temiz, silinen


# ---------- rakam kontrolü ----------

SAYI = re.compile(r"\d+(?:[.,]\d+)*")                                # 5.531 / 93,7 / 2026 / 3.300.000,00


def _degerler(metin):
    """Metindeki sayıların olası değerleri: '93,7' -> {93.7}, '5.531' -> {5531, 5.531}, '2026-06' -> {2026, 6}."""
    sonuc = set()
    for s in SAYI.findall(metin):
        sonuc.add(float(s.replace(".", "").replace(",", ".")) if "," in s else float(s.replace(".", "")))  # Türkçe yazım
        if s.count(".") == 1 and "," not in s:
            sonuc.add(float(s))                                     # '93.7' İngilizce yazım da olabilir
    return sonuc


def dogrulanmayan_sayilar(cevap, kaynaklar):
    """Cevapta geçip araç çıktılarında ya da soruda geçmeyen sayılar. Küçük tamsayılar (liste numarası, ay) sayılmaz."""
    izinli = set()
    for k in kaynaklar:
        izinli |= _degerler(k)
    supheli = []
    for s in SAYI.findall(cevap):
        degerler = _degerler(s)
        if any(d <= 12 and d == int(d) for d in degerler):          # 1., 2. gibi madde numaraları, ay numaraları
            continue
        if not any(abs(d - i) < 0.051 for d in degerler for i in izinli) and s not in supheli:
            supheli.append(s)
    return supheli


# ---------- ana fonksiyon ----------

TEKLIF = re.compile(r"\n\s*\n\s*(?:\*\*)?(?:İstersen|İsterseniz|Dilersen|Dilerseniz)\b[^\n]*\s*$")


def teklifi_sil(cevap):
    """Cevabın sonundaki 'İstersen ... yazayım mı?' paragrafını siler (talimata rağmen model eklerse)."""
    return TEKLIF.sub("", (cevap or "").rstrip())


def sor(soru, gecmis=None, belge=None, model=None):
    """Soruyu cevaplar. gecmis: [{"role": "user"/"assistant", "content": "..."}]. belge: rag.belge_hazirla() sonucu.
    Döner: {"cevap", "araclar": [(ad, parametreler)], "tablolar": {araç: satırlar},
            "kaynaklar": [{"etiket", "alinti"}] (şartname alıntıları), "dogrulanmayan": [sayılar]}."""
    girdi = list(gecmis or []) + [{"role": "user", "content": soru}]
    bugun = date.today()
    gecen = date(bugun.year - (bugun.month == 1), (bugun.month - 2) % 12 + 1, 1)      # önceki takvim ayı
    if belge is not None:
        import rag
        belge_notu = BELGE_VAR.format(ad=belge["ad"], kart=rag.kart_metni(belge.get("kart")) or "(kart çıkarılamadı)")
    else:
        belge_notu = BELGE_YOK
    talimat = TALIMAT.format(bugun=bugun.isoformat(), bu_ay=bugun.strftime("%Y-%m"), gecen_ay=gecen.strftime("%Y-%m"),
                             bu_yil=bugun.year, gecen_yil=bugun.year - 1, belge=belge_notu)
    yillar = "yıllar: " + " ".join(str(y) for y in range(2015, bugun.year + 2))   # "geçen yıl" -> 2025 yazabilsin
    kaynaklar, kullanilan, tablolar, alintilar = [soru, talimat, yillar], [], {}, []   # rakam kontrolünün izin verdiği metinler
    araclar_ = [t for t in ARAC_TANIMLARI if belge is not None or t["name"] != "sartname_sor"]   # belge yoksa şartname aracı yok
    kullanici_metni = veri.arama_metni(" ".join([soru] + [m["content"] for m in (gecmis or []) if m["role"] == "user"]))
    for _ in range(MAKS_TUR):
        yanit = client.responses.create(model=model or os.getenv("MODEL"), instructions=talimat,
                                        input=girdi, tools=araclar_)
        cagrilar = [o for o in yanit.output if o.type == "function_call"]
        if not cagrilar:                                            # araç istemedi: cevap hazır
            break
        girdi += yanit.output                                       # modelin araç isteklerini geçmişe ekle
        for c in cagrilar:
            parametreler = json.loads(c.arguments or "{}")
            parametreler = {k: v for k, v in parametreler.items() if v not in (None, "")}   # boş parametreleri at
            silinen = []
            if c.name != "sartname_sor":
                parametreler, silinen = filtreleri_temizle(parametreler, kullanici_metni)   # uydurma filtreleri at
            ham = _araci_calistir(c.name, parametreler, belge)
            if silinen and isinstance(ham, dict):                   # modele neyin neden uygulanmadığını söyle
                ham["uygulanmayan_filtreler"] = "Soruda geçmediği için uygulanmadı: " + ", ".join(silinen)
            sonuc = json.dumps(ham, ensure_ascii=False, default=str)
            kullanilan.append((c.name, parametreler))
            kaynaklar += [sonuc, json.dumps(parametreler, ensure_ascii=False)]   # modelin gönderdiği tarihler de geçerli
            if c.name == "sartname_sor" and isinstance(ham, dict):
                alintilar = ham.get("kaynaklar", [])                # son şartname cevabının doğrulanmış alıntıları
            tablo = tablo_yap(c.name, ham)
            if tablo:                                               # boş tablo gösterilmez
                tablolar[c.name] = tablo                            # aynı araç tekrar çağrılırsa son (düzeltilmiş) sonuç kalır
            girdi.append({"type": "function_call_output", "call_id": c.call_id, "output": sonuc})
    else:                                                           # MAKS_TUR doldu, hâlâ araç istiyor
        return {"cevap": "Bu soruyu güvenilir şekilde cevaplayamadım. Soruyu daraltarak tekrar dener misiniz?",
                "araclar": kullanilan, "tablolar": {}, "kaynaklar": [], "dogrulanmayan": []}

    cevap = yanit.output_text
    supheli = dogrulanmayan_sayilar(cevap, kaynaklar)
    if supheli:                                                     # bir kez düzelttir; düzeltme notu kullanıcıya görünmez
        girdi += yanit.output
        girdi.append({"role": "developer", "content":
                      f"İç kontrol: taslak cevaptaki şu sayılar araç çıktılarında yok: {', '.join(supheli)}. "
                      "Cevabı baştan, sadece araç çıktılarındaki sayılarla yaz. Araç çıktısında karşılığı yoksa o sayıyı "
                      "hiç yazma. Bu kontrolden, önceki taslaktan ya da düzeltmeden kullanıcıya hiç bahsetme."})
        yanit = client.responses.create(model=model or os.getenv("MODEL"), instructions=talimat, input=girdi,
                                        tools=araclar_, tool_choice="none")   # yeni araç çağrısı yaptırma
        cevap = yanit.output_text
        supheli = dogrulanmayan_sayilar(cevap, kaynaklar)
    if supheli:                                                     # hâlâ varsa kullanıcıya açıkça söyle
        cevap += f"\n\n> ⚠️ Şu sayılar veriden doğrulanamadı, dikkate almayın: {', '.join(supheli)}"
    cevap = teklifi_sil(cevap)                                      # "İstersen ... yapayım mı?" kapanışını at
    return {"cevap": cevap, "araclar": kullanilan, "tablolar": tablolar, "kaynaklar": alintilar, "dogrulanmayan": supheli}


def gecmis_notu(sonuc):
    """Sohbet geçmişine eklenecek kısa not: model bir sonraki soruda neyin gösterildiğini bilsin ("çıkar" gibi takipler)."""
    not_ = "".join(f"\n[{ad} tablosu gösterildi: {len(t)} satır]" for ad, t in sonuc.get("tablolar", {}).items())
    if sonuc.get("kaynaklar"):
        not_ += "\n[Şartname kaynakları gösterildi: " + "; ".join(k["etiket"] for k in sonuc["kaynaklar"]) + "]"
    return not_


# ---------- terminalden deneme ----------

if __name__ == "__main__":
    import sys
    belge = None
    if len(sys.argv) > 1:                                           # python ajan.py sartname.pdf -> belgeyle dene
        import rag
        print("Belge okunuyor, ihale kartı çıkarılıyor...")
        belge = rag.belge_hazirla(sys.argv[1], os.path.basename(sys.argv[1]))
        if belge is None:
            sys.exit("Belgede okunabilir metin yok (taranmış PDF olabilir).")
        for k in rag.ihale_karti(belge).values():
            print(f"  {k['ad']}: {k['deger'] or '— belgede bulunamadı'}" + (f"  [{k['etiket']}]" if k["etiket"] else ""))
    gecmis = []
    print("İhale asistanı. Çıkmak için boş satır.")
    while True:
        soru = input("\nSoru: ").strip()
        if not soru:
            break
        sonuc = sor(soru, gecmis, belge)
        for ad, p in sonuc["araclar"]:
            print(f"  [araç] {ad} {json.dumps(p, ensure_ascii=False)}")
        print("\n" + sonuc["cevap"])
        for ad, satirlar in sonuc["tablolar"].items():             # tabloyu program basar, model değil
            print(f"\n--- {ad}: {len(satirlar)} satır ---")
            for satir in satirlar:
                print(" | ".join(str(v)[:45] for v in satir.values()))
        for k in sonuc["kaynaklar"]:                                # şartname alıntıları: belgeden birebir
            print(f"\n  📄 {k['etiket']}\n     «{k['alinti']}»")
        gecmis += [{"role": "user", "content": soru}, {"role": "assistant", "content": sonuc["cevap"] + gecmis_notu(sonuc)}]