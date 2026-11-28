"""İhale asistanı ajanı. Model soruya göre araç seçer (veritabanı, şartname), sonucu okur ve cevap yazar.
Halüsinasyon önlemleri: model SQL yazmaz, hesap yapmaz; cevaptaki her sayı araç çıktılarında aranır."""
import json                                                         # araç parametreleri ve çıktıları JSON olarak gider gelir
import os                                                           # model adını .env'den almak için
import re                                                           # cevaptaki sayıları bulmak için
from datetime import date                                           # talimata bugünün tarihini yazmak için
from dotenv import load_dotenv                                      # .env dosyasını okumak için
from openai import OpenAI                                           # model
import araclar                                                      # veritabanı araçları

load_dotenv()
client = OpenAI()
MAKS_TUR = 6                                                        # bir soruda en fazla kaç kez araç çağrılabilir

TALIMAT = """Sen Türkiye kamu ihaleleri konusunda bir asistansın. Kullanıcılar EKAP'tan iş kovalayan firmalar.

KURALLAR:
1. Rakam, oran, sayı, firma, idare ya da ihale bilgisi gereken HER soruda araç kullan. Kendi bilginden rakam verme.
2. Sayıları araç çıktısında yazdığı gibi aktar. Yuvarlama, toplama, ortalama alma gibi hiçbir hesap yapma.
3. İstatistik cevabında şunları mutlaka yaz: dönem, kaç ihale ve sözleşmeye dayandığı, uygulanan filtreler,
   araç çıktısındaki uyarılar, doğrulama için 2-3 örnek İKN.
4. Araç "veri_yok" ya da "hata" döndürürse bunu açıkça söyle; tahmin yürütme. Hiçbir araç soruyu kapsamıyorsa
   (örn. Türkiye geneli yıllık toplamlar) "bu bilgi veritabanımda yok" de ve hiç sayı yazma; KİK'in yıllık
   istatistiklerine bakmasını öner.
5. Konu araması kelime başına göre çalışır ("stor" -> "storage" da eşleşir). Sonuçlardaki "eslesen_kelime"ye bak;
   alakasız eşleşme varsa "haric" parametresiyle çıkarıp aracı tekrar çağır.
6. Alan bilgisi: perde, stor, tül, jaluzi ÜRÜNÜ soruluyorsa tür "Mal"dır (yapım işlerinde "perde" betonarme duvar demektir).
   Yemek, temizlik, güvenlik, taşıma işleri "Hizmet"tir. Bina, yol, altyapı işleri "Yapım"dır.
7. Kullanıcının yüklediği şartname/ihale dokümanıyla ilgili sorularda "sartname_sor" aracını kullan. Belge yüklenmemişse
   kullanıcıdan yüklemesini iste.
8. Hukuki tavsiye verme. Cevapların karar desteğidir; önemli kararlardan önce EKAP'tan ve ihale dokümanından doğrulanmalıdır.
9. Tutarları kısaltmadan, araçtaki gibi yaz ("1,2 milyon" değil "1.200.000 TL").
10. Sorudaki iş konusunu (temizlik, perde, yemek, asfalt...) MUTLAKA "konu" parametresine yaz. Kullanıcının söylemediği
    filtreyi (usul, il, tarih) ekleme.
11. Soru yeterince netse kullanıcıya onay sorma; aracı çağır. Sonuç beklenmedikse parametreyi düzeltip aracı kendin
    tekrar çağır.
12. "sonuc_listesi" ve "acik_ihaleler" sonuçları kullanıcıya program tarafından ayrıca TABLO olarak gösterilir.
    Tabloyu ya da satırları tekrar yazma. Sadece 2-4 cümle özet yaz: toplam eşleşen sayısı, filtreler, uyarılar,
    dikkat çeken 1-2 kayıt (İKN ile).
13. Önceki mesajlardaki rakamları tekrar kullanacaksan aracı yeniden çağır.
14. Kısa, net, Türkçe yaz.
Bugünün tarihi: {bugun}."""

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
    {"type": "function", "name": "acik_ihaleler",
     "description": "Teklif tarihi henüz geçmemiş ihale ilanları, en yakın tarihten başlayarak. En fazla 20 satır.",
     "parameters": {"type": "object", "properties": _ORTAK}},
    {"type": "function", "name": "sartname_sor",
     "description": "Kullanıcının yüklediği ihale dokümanında (idari/teknik şartname) arama yapar, madde kaynaklı cevap döndürür.",
     "parameters": {"type": "object", "properties": {"soru": {"type": "string", "description": "Belgeye sorulacak soru"}},
                    "required": ["soru"]}},
]
for _t in ARAC_TANIMLARI:                                            # parametreler isteğe bağlı: katı şema doğrulamasını kapat
    _t["strict"] = False


def _araci_calistir(ad, parametreler, sartname_db):
    """Modelin istediği aracı çalıştırır. Döner: JSON'a çevrilebilir sonuç."""
    try:
        if ad == "kapsam":
            return {k: v for k, v in araclar.kapsam().items() if k in ("ozet", "son_bulten")}
        if ad == "sartname_sor":
            if sartname_db is None:
                return {"hata": "Kullanıcı henüz bir ihale dokümanı yüklemedi."}
            from rag import cevapla                                 # şartname aracı sadece gerekince yüklensin
            return {"cevap": cevapla(sartname_db, parametreler.get("soru", ""))}
        if ad in ("sonuc_istatistigi", "sonuc_listesi", "acik_ihaleler"):
            return getattr(araclar, ad)(**parametreler)
        return {"hata": f"Bilinmeyen araç: {ad}"}
    except TypeError as hata:                                       # model tanımsız bir parametre gönderdiyse
        return {"hata": f"Geçersiz parametre: {hata}"}


# ---------- listeler: tabloyu model değil program basar ----------

LISTE_ARACLARI = {                                                  # araç adı -> (sonuçtaki liste anahtarı, sütunlar)
    "sonuc_listesi": ("sonuclar", [("ikn", "İKN"), ("sonuc_ilani", "Sonuç ilanı"), ("idare", "İdare"), ("konu", "Konu"),
                                   ("usul", "Usul"), ("yuklenici", "Kazanan"), ("sozlesme_bedeli", "Bedel"),
                                   ("teklif_sayisi", "Teklif"), ("oran_yuzde", "Oran %")]),
    "acik_ihaleler": ("ilanlar", [("ikn", "İKN"), ("ihale_tarihi", "Teklif tarihi"), ("il", "İl"), ("idare", "İdare"),
                                  ("konu", "Konu"), ("usul", "Usul"), ("uyari", "Uyarı")]),
}


def _bicim(anahtar, deger, satir):
    """Hücre metni: bedel Türkçe binlik ayraçla ve para birimiyle, boşlar '-'."""
    if deger is None or deger == "":
        return "-"
    if anahtar == "sozlesme_bedeli":
        metin = f"{deger:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")   # 1234567.5 -> 1.234.567,50
        return f"{metin} {satir.get('para_birimi') or ''}".strip()
    if anahtar == "oran_yuzde":
        return str(deger).replace(".", ",")
    return str(deger)


def tablo_yap(ad, sonuc):
    """Liste aracının çıktısından tablo satırları: [{"İKN": ..., "Kazanan": ...}]. Liste aracı değilse None."""
    if ad not in LISTE_ARACLARI or not isinstance(sonuc, dict):
        return None
    anahtar, sutunlar = LISTE_ARACLARI[ad]
    return [{baslik: _bicim(k, satir.get(k), satir) for k, baslik in sutunlar} for satir in sonuc.get(anahtar, [])]


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

def sor(soru, gecmis=None, sartname_db=None, model=None):
    """Soruyu cevaplar. gecmis: [{"role": "user"/"assistant", "content": "..."}].
    Döner: {"cevap": metin, "araclar": [(ad, parametreler)], "tablolar": {araç adı: satırlar}, "dogrulanmayan": [sayılar]}."""
    girdi = list(gecmis or []) + [{"role": "user", "content": soru}]
    bugun = date.today()
    talimat = TALIMAT.format(bugun=bugun.isoformat())
    yillar = "yıllar: " + " ".join(str(y) for y in range(2015, bugun.year + 2))   # "geçen yıl" -> 2025 yazabilsin
    kaynaklar, kullanilan, tablolar = [soru, talimat, yillar], [], {}             # rakam kontrolünün izin verdiği metinler
    for _ in range(MAKS_TUR):
        yanit = client.responses.create(model=model or os.getenv("MODEL"), instructions=talimat,
                                        input=girdi, tools=ARAC_TANIMLARI)
        cagrilar = [o for o in yanit.output if o.type == "function_call"]
        if not cagrilar:                                            # araç istemedi: cevap hazır
            break
        girdi += yanit.output                                       # modelin araç isteklerini geçmişe ekle
        for c in cagrilar:
            parametreler = json.loads(c.arguments or "{}")
            parametreler = {k: v for k, v in parametreler.items() if v not in (None, "")}   # boş parametreleri at
            ham = _araci_calistir(c.name, parametreler, sartname_db)
            sonuc = json.dumps(ham, ensure_ascii=False, default=str)
            kullanilan.append((c.name, parametreler))
            kaynaklar += [sonuc, json.dumps(parametreler, ensure_ascii=False)]   # modelin gönderdiği tarihler de geçerli
            tablo = tablo_yap(c.name, ham)
            if tablo is not None:
                tablolar[c.name] = tablo                            # aynı araç tekrar çağrılırsa son (düzeltilmiş) sonuç kalır
            girdi.append({"type": "function_call_output", "call_id": c.call_id, "output": sonuc})
    else:                                                           # MAKS_TUR doldu, hâlâ araç istiyor
        return {"cevap": "Bu soruyu güvenilir şekilde cevaplayamadım. Soruyu daraltarak tekrar dener misiniz?",
                "araclar": kullanilan, "tablolar": {}, "dogrulanmayan": []}

    cevap = yanit.output_text
    supheli = dogrulanmayan_sayilar(cevap, kaynaklar)
    if supheli:                                                     # bir kez düzelttir; düzeltme notu kullanıcıya görünmez
        girdi += yanit.output
        girdi.append({"role": "developer", "content":
                      f"İç kontrol: taslak cevaptaki şu sayılar araç çıktılarında yok: {', '.join(supheli)}. "
                      "Cevabı baştan, sadece araç çıktılarındaki sayılarla yaz. Araç çıktısında karşılığı yoksa o sayıyı "
                      "hiç yazma. Bu kontrolden, önceki taslaktan ya da düzeltmeden kullanıcıya hiç bahsetme."})
        yanit = client.responses.create(model=model or os.getenv("MODEL"), instructions=talimat, input=girdi,
                                        tools=ARAC_TANIMLARI, tool_choice="none")   # yeni araç çağrısı yaptırma
        cevap = yanit.output_text
        supheli = dogrulanmayan_sayilar(cevap, kaynaklar)
    if supheli:                                                     # hâlâ varsa kullanıcıya açıkça söyle
        cevap += f"\n\n> ⚠️ Şu sayılar veriden doğrulanamadı, dikkate almayın: {', '.join(supheli)}"
    return {"cevap": cevap, "araclar": kullanilan, "tablolar": tablolar, "dogrulanmayan": supheli}


# ---------- terminalden deneme ----------

if __name__ == "__main__":
    gecmis = []
    print("İhale asistanı. Çıkmak için boş satır.")
    while True:
        soru = input("\nSoru: ").strip()
        if not soru:
            break
        sonuc = sor(soru, gecmis)
        for ad, p in sonuc["araclar"]:
            print(f"  [araç] {ad} {json.dumps(p, ensure_ascii=False)}")
        print("\n" + sonuc["cevap"])
        for ad, satirlar in sonuc["tablolar"].items():             # tabloyu program basar, model değil
            print(f"\n--- {ad}: {len(satirlar)} satır ---")
            for satir in satirlar:
                print(" | ".join(str(v)[:45] for v in satir.values()))
        not_ = "".join(f"\n[{ad} tablosu gösterildi: {len(t)} satır]" for ad, t in sonuc["tablolar"].items())
        gecmis += [{"role": "user", "content": soru}, {"role": "assistant", "content": sonuc["cevap"] + not_}]