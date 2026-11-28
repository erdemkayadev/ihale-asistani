"""Şartname okuma ve soru cevaplama.
Yeni yapı: belge bir kez okunur (madde madde), yükleme anında "ihale kartı" çıkarılır, sorular belgenin TAMAMI
(çok büyükse en ilgili maddeler) üzerinden cevaplanır. Her cevap madde etiketi + birebir alıntı vermek zorunda;
alıntı belgede geçmiyorsa kod reddeder."""
import json                                                         # modelin JSON cevabını okumak için
import math                                                         # anahtar kelime puanında nadir kelimeye ağırlık için
import os                                                           # .env'deki değerlere erişmek için
import re                                                           # "Madde 19-" gibi başlıkları bulmak için
from bisect import bisect_right                                     # bir konumun hangi sayfaya düştüğünü bulmak için
from collections import Counter                                     # her sayfada tekrar eden satırları saymak için
from dotenv import load_dotenv                                      # .env dosyasını okuyacak araç
from openai import OpenAI                                           # cevabı yazacak model

load_dotenv()
client = OpenAI()
# "Madde 24 -", "Madde 9.", "MADDE 3)", "Ek Madde 1-" hepsini yakalar; "Madde 33.3" gibi alt maddeleri yakalamaz
BASLIK = re.compile(r"^[ \t]*(?:(Ek|Geçici)[ \t]+)?Madde[ \t]*(\d+)[ \t]*[-–—.):](?!\d)[ \t]*([^\n]*)", re.IGNORECASE | re.MULTILINE)
TAM_SINIR = 250_000            # belge metni bundan kısaysa her soruda belgenin TAMAMI modele gider (kaçırma riski yok)
SECILEN_BOLUM = 12             # büyük belgede soruya en ilgili kaç madde gönderilir
EN_KISA_ALINTI = 15            # alıntının (ve '...' ile bölünmüş her parçasının) en az uzunluğu
GECIS_SAYISI = 8               # sorudaki nadir kelimelerin belgede geçtiği yerlerden en fazla kaçı modele ayrıca gösterilir
SARTNAME_MODEL = os.getenv("SARTNAME_MODEL") or os.getenv("MODEL")   # şartname okuma için daha güçlü model (yoksa MODEL)


def _embedding():
    from langchain_openai import OpenAIEmbeddings                   # sadece büyük belgede gerekir: geç yükle
    return OpenAIEmbeddings(model=os.getenv("EMBED_MODEL"))         # embedding modelini .env'den al


# ---------- 1) Belgeyi oku ----------

def sayfalari_oku(yol):
    """PDF'i sayfa sayfa, Word'ü tek parça olarak okur. Sayfa metinlerinin listesini döndürür."""
    if str(yol).lower().endswith(".docx"):
        import docx                                                 # Word okuyucu (python-docx)
        belge = docx.Document(yol)
        satirlar = [p.text for p in belge.paragraphs]               # normal paragraflar
        for tablo in belge.tables:                                  # tablolardaki yazılar da kaybolmasın
            for satir in tablo.rows:
                satirlar.append(" | ".join(h.text.strip() for h in satir.cells))
        return ["\n".join(satirlar)]                                # Word'de sayfa yok: tek "sayfa"
    from pypdf import PdfReader
    return [(s.extract_text() or "") for s in PdfReader(yol).pages]


def _ust_alt_bilgiyi_sil(sayfalar):
    """Sayfaların yarısından fazlasında tekrar eden satırları (kurum adı, 'Sayfa 3/20' vb.) siler."""
    if len(sayfalar) < 4:
        return sayfalar
    kalip = lambda s: re.sub(r"\d+", "#", s.strip())                # sayıları # yap: "Sayfa 3" = "Sayfa 7"
    sayac = Counter()
    for s in sayfalar:
        sayac.update({kalip(x) for x in s.split("\n") if x.strip()})
    tekrar = {k for k, n in sayac.items() if n >= len(sayfalar) * 0.5 and len(k) > 3}
    return ["\n".join(x for x in s.split("\n") if kalip(x) not in tekrar) for s in sayfalar]


# ---------- 2) Madde başlıklarını bul ----------

def _ust_satir_basligi(metin, e):
    """Kanun tipi: başlık madde satırının ÜSTÜNDE ("Doğrudan temin" / "Madde 22- ..."). Döner: (başlık, konum)."""
    onceki = metin[:e.start()].rstrip()
    satir_basi = onceki.rfind("\n") + 1                              # üstteki dolu satırın başladığı yer
    satir = re.sub(r"\s*\d+\s*$", "", onceki[satir_basi:].strip())   # sondaki sayfa/dipnot numarasını sil
    kisa = satir and len(satir) <= 100 and not satir.endswith((".", ":", ";", ","))
    bolum_basligi = satir.upper() == satir or re.match(r"^[IVX]+\s*[-.]|^\d|^[a-zçğıöşü]{1,2}\)", satir)   # "II-", "3.1", "g)" başlık değildir
    return (satir, satir_basi) if kisa and not bolum_basligi else ("", None)


def _ayni_satir_basligi(e):
    """Şartname tipi: başlık madde satırının DEVAMINDA ("Madde 24 - Tekliflerin geçerlilik süresi")."""
    s = e.group(3).strip(" :")
    return s if s and len(s) <= 90 and not s.endswith(".") and "," not in s else ""


def _bolum_adi(metin, konum):
    """Numaranın 1'e döndüğü yerin üstündeki BÜYÜK HARFLİ bölüm başlığını bulur (örn. 'SÖZLEŞME VE ÖZEL KOŞULLAR')."""
    adaylar = [s.strip() for s in metin[:konum].split("\n")[-120:]
               if len(s.strip()) >= 6 and s.strip().upper() == s.strip() and re.search(r"[A-ZÇĞİÖŞÜ]{4}", s)]
    for s in adaylar:
        if re.search(r"SÖZLEŞME|TALİMAT|ŞARTNAME|BÖLÜM", s):         # bilinen bölüm adları öncelikli
            return s[:50]
    return adaylar[-1][:50] if adaylar else ""


def maddeleri_bul(metin):
    """Metindeki maddeleri bulur; içindekiler listesini atar, numarası 1'e dönen bölümleri adlandırır."""
    es = list(BASLIK.finditer(metin))
    ust = [_ust_satir_basligi(metin, e) for e in es]                # her madde için iki aday başlık
    ayni = [_ayni_satir_basligi(e) for e in es]
    kanun_tipi = sum(1 for b, _ in ust if b) > sum(1 for b in ayni if b)   # belge hangi düzende yazılmış? çoğunluk kazanır
    basliklar = []
    for u, a in zip(ust, ayni):
        if kanun_tipi:
            basliklar.append(u if u[0] else (a, None))              # önce üst satır, yoksa aynı satır
        else:
            basliklar.append((a, None) if a else u)                 # önce aynı satır, yoksa üst satır
    maddeler = []
    for i, e in enumerate(es):
        if i + 1 < len(es):                                         # madde, bir sonrakinin başlığına kadar sürer
            son = basliklar[i + 1][1] if basliklar[i + 1][1] is not None else es[i + 1].start()
        else:
            son = len(metin)
        tur = (e.group(1) or "").capitalize()                       # "Ek" / "Geçici" / ""
        maddeler.append({"no": int(e.group(2)), "tur": tur, "bas": e.start(), "baslik": basliklar[i][0],
                         "govde": metin[e.start():son].strip(),      # "Madde 19- ..." satırı da dahil
                         "icerik": len(metin[e.end():son].strip())}) # başlık satırı hariç uzunluk

    diziler, onceki = [[]], 0                                       # numara 1-2'ye dönünce yeni dizi başlar
    for m in maddeler:
        if not m["tur"] and m["no"] <= 2 and onceki >= 5:
            diziler.append([])
        if not m["tur"]:
            onceki = m["no"]
        diziler[-1].append(m)

    def ortanca_uzunluk(d):
        u = sorted(m["icerik"] for m in d)
        return u[len(u) // 2]
    diziler = [d for d in diziler if d and ortanca_uzunluk(d) >= 40]   # içindekiler: gövdeler boş -> at

    for k, d in enumerate(diziler):                                 # ilk dizi bölümsüz, sonrakiler adlandırılır
        ad = "" if k == 0 else (_bolum_adi(metin, d[0]["bas"]) or f"Bölüm {k + 1}")
        for m in d:
            m["bolum"] = ad
    return [m for d in diziler for m in d]


def _metin_ve_bolumler(yol):
    """Belgeyi okur. Döner: (tam metin, bölümler). Bölüm = {"etiket", "metin", "sayfa"}: madde varsa madde madde,
    yoksa sayfa sayfa."""
    sayfalar = _ust_alt_bilgiyi_sil(sayfalari_oku(yol))
    tam_metin, sayfa_baslangiclari = "", []
    for s in sayfalar:
        sayfa_baslangiclari.append(len(tam_metin))                  # her sayfanın metindeki başlangıç konumu
        tam_metin += s + "\n"
    kesme = tam_metin.find("İŞLENEMEYEN HÜKÜMLER")                  # kanun PDF'lerinin sonundaki başka kanun maddeleri
    if kesme != -1:
        tam_metin = tam_metin[:kesme]
    sayfali = len(sayfalar) > 1                                     # Word'de sayfa numarası yok

    maddeler = maddeleri_bul(tam_metin)
    if len(maddeler) < 3:                                           # madde yapısı yok: sayfa sayfa
        return tam_metin, [{"etiket": f"Sayfa {no}" if sayfali else "Belge", "metin": s.strip(), "sayfa": no if sayfali else 0}
                           for no, s in enumerate(sayfalar, start=1) if s.strip()]
    bolumler = []
    for m in maddeler:
        madde = f"{m['tur'] + ' ' if m['tur'] else ''}Madde {m['no']}"
        if m["bolum"]:
            madde = f"{m['bolum']} {madde}"                         # örn. "SÖZLEŞME VE ÖZEL KOŞULLAR Madde 26"
        etiket = f"{madde} - {m['baslik']}" if m["baslik"] else madde
        sayfa = bisect_right(sayfa_baslangiclari, m["bas"]) if sayfali else 0
        bolumler.append({"etiket": etiket, "metin": m["govde"], "sayfa": sayfa})
    ilk = maddeler[0]["bas"]                                        # ilk maddeden önceki kapak / başlık metni kaybolmasın
    if tam_metin[:ilk].strip():
        bolumler.insert(0, {"etiket": "Giriş", "metin": tam_metin[:ilk].strip(), "sayfa": 1 if sayfali else 0})
    return tam_metin, bolumler


# ---------- 3) Alıntı doğrulama ----------

def _kucuk(metin):
    """Türkçe küçük harf: 'İ' -> 'i', 'I' -> 'ı' (Python'un lower()'ı bunları yanlış çevirir)."""
    return (metin or "").replace("I", "ı").replace("İ", "i").lower()


def _duz(metin):
    """Karşılaştırma biçimi: küçük harf, harf/rakam dışı her şey tek boşluk. PDF'teki satır sonları, tireler,
    fazla boşluklar alıntı kontrolünü bozmasın."""
    metin = re.sub(r"(?<=\d)(?=[^\d\W])|(?<=[^\d\W])(?=\d)", " ", _kucuk(metin).replace("%", " % "))   # "1yıl" -> "1 yıl"
    return " " + re.sub(r"[^0-9a-zçğıöşüâîû%]+", " ", metin).strip() + " "


def alinti_belgede_mi(alinti, belge):
    """Alıntı belgede birebir geçiyor mu? '...' ile bölünmüş alıntıların her parçası ayrı ayrı aranır."""
    parcalar = [_duz(p).strip() for p in re.split(r"\.\.\.|…", alinti or "")]
    parcalar = [p for p in parcalar if p]
    if not parcalar or min(len(p) for p in parcalar) < EN_KISA_ALINTI:
        return False                                                # "7.1. ..." gibi içi boş parçalar hiçbir şeyi kanıtlamaz
    return all(f" {p} " in belge["duz"] for p in parcalar)


def gercek_etiket(alinti, belge):
    """Alıntının gerçekten geçtiği bölümün etiketi (sayfa ile). Model etiketi yanlış yazsa da doğrusu gösterilir."""
    parca = max((_duz(p).strip() for p in re.split(r"\.\.\.|…", alinti or "")), key=len, default="")
    for b in belge["bolumler"]:
        if parca and f" {parca} " in _duz(b["metin"]):
            return b["etiket"] + (f", Sayfa {b['sayfa']}" if b["sayfa"] else "")
    return None                                                     # bölüm sınırına denk gelen alıntı: bulunamadı


# ---------- 3b) İstenen belgeler listesi (kodla, modelsiz) ----------

YETERLIK = re.compile(r"katılabilmek için gereken|yeterlik kriter|yeterlik şart|istenen belge")
BENT = re.compile(r"^([a-zçğıöşü]{1,2})\s*\)\s*(.+)")             # "a) Vergi borcu yoktur belgesi", "ğ) ..."
ALT_MADDE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})+)\.?(?=\s|[^\W\d_])\s*(.+)")   # "7.1.İsteklilerin", "7.5.3. İş..."; "11.06.2011" değil


def istenen_belgeler(bolumler):
    """İhaleye katılım için istenen belgeler maddesini (genelde Madde 7) bulur, bentlerini ve alt maddelerini tek tek
    listeler. Model uzun metinde aramak yerine bu düzenli listeye bakar; "listede var mı" sorusunu kod cevaplar.
    Döner: {"etiket", "maddeler": [{"bent", "metin"}]} ya da None."""
    b = next((b for b in bolumler if YETERLIK.search(_kucuk(b["etiket"]))), None)
    if b is None:
        return None
    maddeler, simdiki, ust, ilk_ust = [], None, None, None
    for satir in b["metin"].split("\n")[1:]:                       # ilk satır madde başlığı
        s_ = satir.strip().lstrip("➢•-– ").strip()
        if not s_ or s_.isdigit():                                  # boş satır ya da sayfa numarası
            continue
        alt, bent = ALT_MADDE.match(s_), BENT.match(s_)
        if alt:                                                     # "7.6.1 ..." numaralı alt madde
            ust = alt.group(1)
            ilk_ust = ilk_ust or ust
            simdiki = {"bent": ust, "metin": alt.group(2)}
            maddeler.append(simdiki)
        elif bent:                                                  # "a) ..." bent: hangi alt maddenin altında olduğu da yazılır
            onek = f"{ust} " if ust and ust != ilk_ust else ""
            simdiki = {"bent": f"{onek}{bent.group(1)})", "metin": bent.group(2)}
            maddeler.append(simdiki)
        elif simdiki:
            simdiki["metin"] += " " + s_                            # önceki bendin devam satırı
    for x in maddeler:
        x["metin"] = re.sub(r"\s+", " ", x["metin"]).strip()          # tam metin: "listede var mı" kontrolü bununla
        x["kisa"] = x["metin"][:300] + ("..." if len(x["metin"]) > 300 else "")   # ekranda ve modelde gösterilen
    if not maddeler:
        return None
    return {"etiket": b["etiket"] + (f", Sayfa {b['sayfa']}" if b["sayfa"] else ""), "maddeler": maddeler}


IS_DENEYIM_BELGESI = ["deneyim", "iş bitirme", "iş durum", "iş denetleme", "iş yönetme"]   # KİK'teki iş deneyim belgesi türleri
KURAL_BASI = ["tüzel kişi tarafından", "devredilen", "kanun kapsamındaki", "yurt dışında", "ilk ilan tarihinden",
              "devam eden işlerde", "iş ortaklığı", "ortak girişim"]   # iş deneyiminin NASIL belgeleneceğine dair kurallar
OLUMSUZ = ["istenmeyecek", "istenmemekte", "istenmez", "aranmayacak", "aranmamakta", "talep edilmeyecek"]
DIGER_IZI = ["aktif olarak", "referans", "tecrübe"]                  # iş deneyim belgesi dışındaki deneyim şartı izleri
MALI = ["banka", "bilanço", "ciro", "kredi", "mevduat", "mali müşavir", "ekonomik ve mali", "likidite", "öz kaynak"]
# ^ bu kelimeler geçen bent mali yeterliktir ("banka referans mektubu" gibi): deneyim şartı sayılmaz
SOMUT = ["oranından", "oranında", "tutarında", "tutarlı", "asgari", "az olmamak"]   # iş deneyimi hükmünün somut olduğunu gösterir
CUMLE = re.compile(r"(?<=\.)\s+(?=[A-ZÇĞİÖŞÜ(\d-])")             # ". " + büyük harf / "(2)" ile yeni cümle; "25.000.000" bölünmez


def _iceriyor(metin, ifadeler):
    """Metinde ifadelerden biri kelime başından itibaren geçiyor mu? ('iş bitirme' -> 'iş bitirme belgesi' evet)."""
    d = _duz(metin)
    return any(f" {_duz(k).strip()}" in d for k in ifadeler)


def _basliyor(metin, ifadeler):
    """Metin ifadelerden biriyle mi başlıyor? Ek almış hali de sayılır ('İş ortaklığının...' -> 'iş ortaklığı')."""
    d = _duz(metin)
    return any(d.startswith(f" {_duz(k).strip()}") for k in ifadeler)


def _somut_mu(metin):
    """Oran ya da tutar veriyor mu ('%25', 'oranından az olmamak üzere', 'asgari 25.000.000 TL')?"""
    return "%" in metin or _iceriyor(metin, SOMUT)


def _bent_turu(x):
    """İş deneyim bendinin türü, cümle cümle bakılarak: "somut" (oran/tutar veriyor), "olumsuz" (istenmiyor diyor), "genel".
    Aynı bentte hem oran hem 'istenmez' varsa (ör. '...iş bitirme belgesi sunacaktır. Aslı istenmez.'), oranı veren
    cümle kazanır: olumsuzluk iş deneyimi hakkında olmayabilir."""
    cumleler = [c for c in CUMLE.split(x["metin"]) if _iceriyor(c, IS_DENEYIM_BELGESI)]
    if any(_somut_mu(c) for c in cumleler):
        return "somut"
    if any(_iceriyor(c, OLUMSUZ) for c in cumleler):
        return "olumsuz"
    return "somut" if _somut_mu(x["metin"]) and not _iceriyor(x["metin"], OLUMSUZ) else "genel"


def _alt_bentler(istenen, x):
    """Bir alt maddenin altındaki bentler: '7.5.1' için '7.5.1 a)', '7.5.1 b)'..."""
    return [y for y in istenen["maddeler"] if y["bent"].startswith(x["bent"] + " ")]


def is_deneyim_bentleri(istenen):
    """İş deneyim belgesinin istenip istenmediğini söyleyen bent/alt maddeler (7.6.1 gibi numaralılar dahil).
    'Tüzel kişi tarafından...', 'İş ortaklığının...', 'Devredilen işlerde...' gibi belgeleme kuralları sayılmaz."""
    return [x for x in (istenen or {}).get("maddeler", [])
            if _iceriyor(x["metin"], IS_DENEYIM_BELGESI) and not _basliyor(x["metin"], KURAL_BASI)]


BEDEL = ["teklif edilen bedel", "teklif ettiği bedel", "teklif ettikleri bedel", "yaklaşık maliyet"]
CUMLE_KURALI = KURAL_BASI + ["konsorsiyum", "ancak"]                # "Konsorsiyumda ise...", "Ancak ... aranmaz" asıl hüküm değil


def _ust_etiket(bent):
    """'7.5.1 d)' -> '7.5.1' (bentten sonra gelen cümle aslında üst maddenin hükmüdür); 'g)' -> 'g)'."""
    return bent.rsplit(" ", 1)[0] if " " in bent else bent


def _oran_cumleleri(istenen):
    """KİK şablonunda asıl oran cümlesi ('İstekli tarafından teklif edilen bedelin % 25'den az olmamak üzere... iş
    deneyimini gösteren belgelerin sunulması gerekir.') listenin son bendinin ('d) Devredilen işlerde...') arkasına
    yapışık gelir. Bu cümleleri bentlerin içinden ayıklar. Döner: [{"bent", "metin", "kisa"}]."""
    bulunan = []
    for x in istenen["maddeler"]:
        for c in CUMLE.split(x["metin"]):
            if (_iceriyor(c, IS_DENEYIM_BELGESI) and _iceriyor(c, BEDEL) and _somut_mu(c)
                    and not _basliyor(c, CUMLE_KURALI) and all(c != y["metin"] for y in bulunan)):
                bulunan.append({"bent": _ust_etiket(x["bent"]), "metin": c, "kisa": c[:300] + ("..." if len(c) > 300 else "")})
    return bulunan


def _bos_mesleki_yeterlik(istenen):
    """'7.3 Mesleki ve teknik yeterliğe ilişkin belgeler:' + alt maddelerinin hepsi 'Bu madde boş bırakılmıştır.'
    Bu, iş deneyimi istenmediğinin dayanağıdır."""
    for x in istenen["maddeler"]:
        if not _iceriyor(x["metin"], ["mesleki ve teknik"]):
            continue
        alt = [y for y in istenen["maddeler"] if y["bent"].startswith(x["bent"] + ".")]
        if alt and all(_iceriyor(y["metin"], ["boş bırakılmış"]) for y in alt):
            m = f"{x['metin']} Alt maddeleri ({alt[0]['bent']}–{alt[-1]['bent']}): Bu madde boş bırakılmıştır."
            return [{"bent": x["bent"], "metin": m, "kisa": m}]
    return []


def is_deneyim_durumu(istenen):
    """Kodun tespiti. Döner: ("isteniyor" | "istenmiyor" | None, dayanak bentler).
    Öncelik: oran/tutar veren hüküm > 'istenmemektedir' diyen hüküm > genel bent ('İş deneyimini gösteren belgeler').
    Genel bendin altındaki oran veren alt bent ve listenin arkasına yapışık oran cümlesi de somut hüküm sayılır."""
    if not istenen:
        return None, []
    bentler = is_deneyim_bentleri(istenen)
    tur = {id(x): _bent_turu(x) for x in bentler}
    somut = [x for x in bentler if tur[id(x)] == "somut"]
    for x in bentler:                                               # "7.5.1 İş deneyimini gösteren belgeler:" + "a) ...%25..."
        if tur[id(x)] == "genel":
            somut += [y for y in _alt_bentler(istenen, x) if y not in somut and _somut_mu(y["metin"])
                      and not _basliyor(y["metin"], KURAL_BASI)]
    somut += [c for c in _oran_cumleleri(istenen) if not any(c["metin"] in x["metin"] for x in somut)]
    olumsuz = [x for x in bentler if tur[id(x)] == "olumsuz"]
    genel = [x for x in bentler if tur[id(x)] == "genel"]
    if somut:
        return "isteniyor", somut
    if olumsuz:                                                     # "istenmemektedir" diyen hüküm, genel bentten önce gelir
        return "istenmiyor", olumsuz
    if genel:
        return "isteniyor", genel
    return "istenmiyor", _bos_mesleki_yeterlik(istenen)             # listede hiç geçmiyor (varsa boş bırakılan madde dayanaktır)


def deneyim_sartlari(istenen):
    """İş deneyim belgesi DIŞINDAKİ deneyim şartları (ör. 'aktif olarak en az 1 üniversitede hizmet vermesi').
    Mali yeterlik bentleri (banka referans mektubu, bilanço oranları) sayılmaz."""
    if not istenen:
        return []
    is_b = is_deneyim_bentleri(istenen) + is_deneyim_durumu(istenen)[1]
    return [x for x in istenen["maddeler"] if x not in is_b and not _basliyor(x["metin"], KURAL_BASI)
            and _iceriyor(x["metin"], DIGER_IZI) and not _iceriyor(x["metin"], MALI)]


def liste_metni(istenen):
    """İstenen belgeler listesi, modele verilecek düz metin olarak."""
    return "\n".join(f"{x['bent']} {x['kisa']}" for x in istenen["maddeler"]) if istenen else ""


# ---------- 4) Belgeyi hazırla ----------

def _kokler(metin):
    """Kelime kökleri (ilk 5 harf): 'deneyim', 'deneyimi', 'deneyimine' aynı kök sayılsın."""
    return {k[:5] for k in _duz(metin).split() if len(k) >= 3}


def belge_hazirla(yol, ad="belge"):
    """Belgeyi okur ve sorulara hazır hale getirir. Döner: belge sözlüğü. Metinsiz (taranmış) belgede None."""
    tam_metin, bolumler = _metin_ve_bolumler(yol)
    if len(tam_metin.strip()) < 200:                                # taranmış PDF: metin yok
        return None
    for b in bolumler:
        b["kokler"] = _kokler(b["metin"])                           # anahtar kelime araması için
    belge = {"ad": ad, "metin": tam_metin, "duz": _duz(tam_metin), "bolumler": bolumler,
             "tam": len(tam_metin) <= TAM_SINIR, "db": None, "kart": None,
             "istenen": istenen_belgeler(bolumler)}
    if not belge["tam"]:                                            # büyük belge: anlam araması için vektör veritabanı da kur
        from langchain_core.documents import Document
        from langchain_chroma import Chroma
        belgeler = [Document(page_content=b["metin"][:6000], metadata={"sira": i}) for i, b in enumerate(bolumler)]
        belge["db"] = Chroma.from_documents(belgeler, _embedding(), collection_name=f"b{abs(hash(ad)) % 10**8}")
    return belge


def _bolum_metni(b):
    """Modele giden bölüm: başta etiket (sayfa varsa sayfa ile), sonra gövde."""
    return f"[{b['etiket']}{', Sayfa ' + str(b['sayfa']) if b['sayfa'] else ''}]\n{b['metin']}"


def _anahtar_kelimeyle_bul(belge, soru, k):
    """Soru köklerinin geçtiği bölümler; nadir kökler daha çok puan alır ('ihale' her yerde geçer, 'deney' nadirdir)."""
    soru_kok = _kokler(soru)
    n = len(belge["bolumler"])
    df = Counter(kok for b in belge["bolumler"] for kok in b["kokler"] & soru_kok)
    puanlar = [(sum(math.log(1 + n / df[kok]) for kok in b["kokler"] & soru_kok), i) for i, b in enumerate(belge["bolumler"])]
    return [i for p, i in sorted(puanlar, reverse=True)[:k] if p > 0]


def _baglam(belge, soru):
    """Modele verilecek metin. Küçük belgede tamamı; büyükte künye (ilk 3 bölüm) + anahtar kelime + anlam araması."""
    if belge["tam"]:
        return "\n\n".join(_bolum_metni(b) for b in belge["bolumler"]), "belgenin tamamı"
    secilen = list(range(min(3, len(belge["bolumler"]))))           # künye: idare, iş, ihale bilgileri
    secilen += _anahtar_kelimeyle_bul(belge, soru, SECILEN_BOLUM // 2)
    secilen += [d.metadata["sira"] for d in belge["db"].similarity_search(soru, k=SECILEN_BOLUM // 2)]
    secilen = sorted(set(secilen))
    return "\n\n".join(_bolum_metni(belge["bolumler"][i]) for i in secilen), [belge["bolumler"][i]["etiket"] for i in secilen]


SORU_KELIMELERI = {"kadar", "nedir", "neler", "hangi", "nasıl", "mıdır", "midir", "isten", "gerek", "olmal", "belge",
                   "ihale", "lütfe", "söyle", "acaba", "madde", "başlı", "hüküm", "numar", "belir", "ilgil", "bilgi",
                   "şart", "şartı", "şartl", "koşul", "krite", "tanım", "kapsa", "metin", "varsa", "yoksa", "dair",
                   "olara", "ayrıc", "oran", "oranı", "tutar", "buna", "diyor", "yazın", "tümün", "durum"}
# ^ soru kalıbı kökleri (kelimenin ilk 5 harfi): içerik değil, eşleşmede sayılmaz
DENEYIM_KOKLERI = {"deney", "benze", "refer", "tecrü"}               # bu kökler soruda geçiyorsa soru deneyim sorusudur


def _nadir_kokler(belge, soru):
    """Sorudaki, belgenin her yerinde geçmeyen kelime kökleri ('deney', 'temin'); 'ihale', 'kadar' gibi yaygınlar atılır."""
    n = len(belge["bolumler"])
    return [k for k in _kokler(soru) if len(k) >= 4 and k not in SORU_KELIMELERI
            and sum(k in b["kokler"] for b in belge["bolumler"]) <= max(2, n // 4)]


def _gecis_yerleri(belge, soru):
    """Sorudaki nadir kelimelerin ('deneyim', 'teminat') belgede geçtiği her yer, ±250 harf bağlamla.
    Model 'iş deneyim belgesi' ifadesinin hangi maddelerde, hangi bağlamda geçtiğini topluca görsün."""
    kokler = _nadir_kokler(belge, soru)
    kucuk = _kucuk(belge["metin"])                                  # konumlar orijinal metinle aynı kalır
    yerler = []
    for kok in kokler:
        for m in re.finditer(r"(?<![0-9a-zçğıöşü])" + re.escape(kok), kucuk):
            yerler.append(belge["metin"][max(0, m.start() - 250):m.end() + 250].replace("\n", " "))
            if len(yerler) >= GECIS_SAYISI:
                return yerler
    return yerler


BELGE_SORUSU = re.compile(r"iste|belge|gerek|şart|yeterlik|sunul")    # "isteniyor mu", "hangi belgeler", "şartı ne"
LISTE_SORUSU = re.compile(r"(hangi|tüm|bütün|istenen|gereken|gerekli).{0,30}(belge|evrak)|(belge|evrak)\w*.{0,40}(neler|listele|liste)")


def _liste_kontrolu(belge, soru):
    """'Bir belge isteniyor mu' sorularında kodun tespiti: sorudaki nadir kelimeler istenen belgeler listesinin hangi
    bentlerinde geçiyor? Model bu tespitle çelişemez."""
    istenen = belge.get("istenen")
    if not istenen or not BELGE_SORUSU.search(_kucuk(soru)):
        return ""
    kokler = _nadir_kokler(belge, soru)
    if not kokler:
        return ""
    parcalar = []
    for kok in kokler:                                              # her kelime için ayrı tespit: hangisi listede var, hangisi yok
        gecen = [x["bent"] for x in istenen["maddeler"] if kok in _kokler(x["metin"])]
        if kok == "deney":                                          # iş deneyimi: kodun tespiti kesin bilgi olarak verilir
            durum, bentler = is_deneyim_durumu(istenen)
            parcalar.append("İŞ DENEYİM BELGESİ: " + {"isteniyor": "İSTENİYOR", "istenmiyor": "İSTENMİYOR"}[durum] + (" (" + "; ".join(f"{x['bent']} {x['kisa']}" for x in bentler) + ")" if bentler else " (istenen belgeler arasında hiç geçmiyor)"))
            continue
        parcalar.append(f"'{kok}...' {'listede şu bentlerde geçiyor: ' + ', '.join(gecen) if gecen else 'listenin HİÇBİR bendinde geçmiyor'}")
    tespit = "; ".join(parcalar) + ". Bir kelimenin hiçbir bentte geçmemesi, o belge/şartın katılım için istenmediği demektir."
    benzer = deneyim_sartlari(istenen)
    if benzer and _kokler(soru) & DENEYIM_KOKLERI:                  # deneyim sorusu: deneyim türü şartlar mutlaka söylenir
        tespit += (" Listede şu deneyim/kapasite türü şart(lar) VAR; cevapta bunları mutlaka belirt: "
                   + "; ".join(f"{x['bent']} {x['kisa']}" for x in benzer))
    return f"\n\nKOD KONTROLÜ ({istenen['etiket']} listesine göre): {tespit}"


def _json_iste(talimat, girdi, sema, ad):
    """Modelden şemaya uyan JSON ister. Belge metni girdinin başında: aynı belgeye sorulan sorularda OpenAI
    önbelleği devreye girer, maliyet düşer."""
    yanit = client.responses.create(model=SARTNAME_MODEL, instructions=talimat, input=girdi,
                                    text={"format": {"type": "json_schema", "name": ad, "schema": sema, "strict": True}})
    u = getattr(yanit, "usage", None)
    if os.getenv("GELISTIRICI") == "1" and u:                        # maliyeti ölçmek için: kaç token gitti, ne kadarı önbellekten
        onbellek = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        print(f"  [{ad}] {SARTNAME_MODEL}: girdi {u.input_tokens} token (önbellekten {onbellek}), çıktı {u.output_tokens} token")
    return json.loads(yanit.output_text)


# ---------- 5) İhale kartı (yükleme anında bir kez) ----------

KART_ALANLARI = [                                                   # (anahtar, ekranda görünen ad, modele açıklama)
    ("ikn", "İKN", "İhale kayıt numarası"),
    ("is_adi", "İşin adı", "İhale konusu işin adı"),
    ("idare", "İdare", "İhaleyi yapan idarenin adı"),
    ("ihale_tarihi", "İhale tarihi", "İhale (son teklif verme) tarihi ve saati"),
    ("is_suresi", "İşin süresi", "İşin süresi ya da başlama-bitiş tarihleri"),
    ("gecici_teminat", "Geçici teminat", "Geçici teminat tutarı ya da oranı"),
    ("is_deneyimi", "İş deneyim belgesi", "İhaleye katılım için istenen belgeler listesinde iş deneyim belgesi var mı; "
                                          "varsa yaklaşık maliyetin/teklif bedelinin yüzde kaçı. Listede yoksa deger "
                                          "'İstenmiyor', alinti olarak listenin başlangıç cümlesi"),
    ("benzer_is", "Benzer iş", "Benzer iş olarak kabul edilen işler"),
    ("deneyim_sarti", "Diğer deneyim şartı", "İş deneyim belgesi dışındaki deneyim/referans şartları "
                                            "(ör. belli bir yerde aktif hizmet verme, referans mektubu, personel deneyimi)"),
    ("teklif_gecerlilik", "Teklif geçerliliği", "Tekliflerin geçerlilik süresi"),
    ("kismi_teklif", "Kısmi teklif", "Kısmi teklif verilebilir mi"),
    ("alt_yuklenici", "Alt yüklenici", "Alt yüklenici çalıştırılabilir mi"),
    ("fiyat_disi_unsur", "Fiyat dışı unsur", "Ekonomik açıdan en avantajlı teklif fiyat dışında bir unsurla mı belirleniyor"),
    ("asiri_dusuk", "Aşırı düşük teklif", "Sınır değer / aşırı düşük teklif uygulaması"),
    ("odeme", "Ödeme", "Ödeme yeri ve şartları (hakediş, avans)"),
]
KART_KELIMELERI = {                                                 # alıntıda bunlardan biri geçmeli; yoksa alan yanlış eşleşmiştir
    "ikn": ["kayıt numara", "ikn"],
    "is_suresi": ["süre", "takvim gün", "başla", "bitiş"],
    "gecici_teminat": ["teminat"],
    "is_deneyimi": ["deneyim"],
    "benzer_is": ["benzer"],
    "teklif_gecerlilik": ["geçerlilik"],
    "kismi_teklif": ["kısmi"],
    "alt_yuklenici": ["alt yüklenici"],
    "fiyat_disi_unsur": ["avantajlı", "fiyat dışı"],
    "asiri_dusuk": ["aşırı düşük", "sınır değer"],
    "odeme": ["ödeme", "hakediş", "avans"],
    "deneyim_sarti": ["aktif olarak", "referans", "deneyim", "tecrübe"],
}


def _alan_uyuyor_mu(anahtar, deger, alinti, belge):
    """Alıntı bu alanla ilgili mi? 'İş deneyimi' alanına bilanço satırı konmasın.
    Anahtar kelime alıntıda ya da alıntının geçtiği maddenin başlığında ("Madde 33 - Aşırı düşük teklifler") aranır."""
    kelimeler = KART_KELIMELERI.get(anahtar)
    if not kelimeler:
        return True
    if anahtar == "is_deneyimi" and _kucuk(deger or "").startswith("istenm"):
        return True                                                 # "İstenmiyor": alıntı listenin başı olabilir
    metin = _duz(alinti) + _duz(gercek_etiket(alinti, belge) or "")
    return any(f" {_duz(k).strip()}" in metin for k in kelimeler)


_KART_ALAN_SEMASI = {"type": "object", "additionalProperties": False, "required": ["deger", "etiket", "alinti"],
                     "properties": {"deger": {"type": ["string", "null"]}, "etiket": {"type": ["string", "null"]},
                                    "alinti": {"type": ["string", "null"]}}}
KART_SEMASI = {"type": "object", "additionalProperties": False, "required": [a for a, _, _ in KART_ALANLARI],
               "properties": {a: _KART_ALAN_SEMASI for a, _, _ in KART_ALANLARI}}
KART_TALIMAT = """Sana bir kamu ihale dokümanı veriliyor. Her alan için belgeden bilgi çıkar.
- deger: kısa ve net cevap (örn. "Teklif bedelinin %3'ünden az olmamak üzere", "İstenmiyor", "120 takvim günü").
  Sayıları ve yazıyla yazılmış sayıları belgedeki gibi al; yazım hatası olsa da düzeltme, yorumlama.
  Belgede alan boş bırakılmışsa ("------", "....") üçünü de null yap.
- etiket: bilginin bulunduğu bölümün köşeli parantez içindeki etiketi, AYNEN (örn. "Madde 7 - Teklif verme şartları").
- alinti: belgeden BİREBİR kopyalanmış, kelimesi kelimesine aynı, en fazla 40 kelimelik cümle ya da cümle parçası.
Bir belgenin standart formlar arasında sayılması, istendiği anlamına gelmez; istenen belgeler listesine bak.
Belgede bu bilgi yoksa üçünü de null yap. Tahmin etme, belgeye dayanmayan bilgi yazma."""


def ihale_karti(belge):
    """Kartı çıkarır ve her alanın alıntısını belgede doğrular. Döner: {anahtar: {"ad", "deger", "etiket", "alinti", "durum"}}.
    durum: "dogrulandi" / "bulunamadi" / "dogrulanamadi"."""
    metin = "\n\n".join(_bolum_metni(b) for b in belge["bolumler"])[:TAM_SINIR]   # büyük belgede baştaki kısım (künye orada)
    ham = _json_iste(KART_TALIMAT, f"BELGE:\n{metin}", KART_SEMASI, "ihale_karti")
    kart = {}
    for anahtar, ad, _ in KART_ALANLARI:
        a = ham.get(anahtar) or {}
        if not a.get("deger") or not _duz(a["deger"]).strip():      # boş ya da "------" gibi harfsiz değer
            durum = "bulunamadi"
        elif alinti_belgede_mi(a.get("alinti"), belge) and _alan_uyuyor_mu(anahtar, a.get("deger"), a.get("alinti"), belge):
            durum = "dogrulandi"
        else:
            durum = "dogrulanamadi"                                 # model alıntı uydurdu ya da değiştirdi: gösterme
        kart[anahtar] = {"ad": ad, "deger": a.get("deger") if durum == "dogrulandi" else None,
                         "etiket": (gercek_etiket(a.get("alinti"), belge) or a.get("etiket")) if durum == "dogrulandi" else None,
                         "alinti": a.get("alinti") if durum == "dogrulandi" else None, "durum": durum}
    istenen = belge.get("istenen")
    durum, is_bentleri = is_deneyim_durumu(istenen)
    if durum:                                                       # kodun tespiti modelinkinden önce gelir
        ayrinti = "; ".join(f"{x['bent']} {x['kisa']}" for x in is_bentleri)
        deger = (f"İsteniyor: {ayrinti}" if durum == "isteniyor" else
                 f"İstenmiyor: {ayrinti}" if ayrinti else "İstenmiyor (katılım için istenen belgeler arasında yok)")
        kart["is_deneyimi"] = {"ad": "İş deneyim belgesi", "deger": deger, "etiket": istenen["etiket"],
                               "alinti": None, "durum": "dogrulandi"}
    ds = kart.get("deneyim_sarti", {})
    if ds.get("durum") == "dogrulandi" and ds.get("alinti") and any(
            _duz(ds["alinti"]).strip() in _duz(x["metin"]) for x in is_deneyim_bentleri(istenen) + is_deneyim_durumu(istenen)[1]):
        kart["deneyim_sarti"] = {"ad": ds["ad"], "deger": None, "etiket": None, "alinti": None, "durum": "bulunamadi"}   # tekrar
    benzer = deneyim_sartlari(istenen)                              # iş deneyim belgesi dışındaki şartlar
    if benzer:                                                      # kodun bulduğu deneyim şartı modelinkinden önce gelir
        kart["deneyim_sarti"] = {"ad": "Diğer deneyim şartı", "deger": "; ".join(f"{x['bent']} {x['kisa']}" for x in benzer),
                                 "etiket": istenen["etiket"], "alinti": None, "durum": "dogrulandi"}
    if istenen and os.getenv("GELISTIRICI") == "1":
        print(f"  [istenen belgeler] {istenen['etiket']}: " + ", ".join(x["bent"] for x in istenen["maddeler"]))
    belge["kart"] = kart
    return kart


def kart_metni(kart):
    """Kartın doğrulanmış alanları, ajana verilecek kısa metin olarak."""
    return "\n".join(f"- {k['ad']}: {k['deger']} [{k['etiket']}]" for k in (kart or {}).values() if k["durum"] == "dogrulandi")


# ---------- 6) Soru cevaplama ----------

CEVAP_SEMASI = {"type": "object", "additionalProperties": False, "required": ["bulundu", "cevap", "kaynaklar"],
                "properties": {
                    "bulundu": {"type": "boolean"},
                    "cevap": {"type": "string"},
                    "kaynaklar": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False, "required": ["etiket", "alinti"],
                        "properties": {"etiket": {"type": "string"}, "alinti": {"type": "string"}}}}}}
CEVAP_TALIMAT = """Sen bir kamu ihale dokümanı uzmanısın. Soruyu SADECE verilen belge metnine dayanarak cevapla.
- bulundu: belgede soruyu cevaplayan bir hüküm varsa true, yoksa false.
- cevap: kısa, net Türkçe cevap. "İstenmemektedir", "uygulanmayacaktır" gibi olumsuz hükümler de cevaptır (bulundu=true).
- kaynaklar: cevabın dayandığı her bölüm için etiket (köşeli parantez içindeki etiket, AYNEN) ve alinti (belgeden BİREBİR,
  kelimesi kelimesine aynı, en fazla 40 kelime). Alıntıyı değiştirme, özetleme, düzeltme.
- Her alıntı, cevaptaki iddiayı TEK BAŞINA kanıtlamalı. Bir listenin giriş cümlesini değil, ilgili bendin (a), b), j) gibi)
  tam cümlesini alıntıla. "..." ile parça atlama; gerekiyorsa iki ayrı kaynak ver.
- "Bir belge isteniyor mu" sorularında ihaleye katılım için istenen belgeler listesinin (genelde Madde 7) TAMAMINI tek tek
  incele. Belgenin standart formlar arasında sayılması ya da başka bir maddede (eşitlik halinde değerlendirme gibi) geçmesi,
  istendiği anlamına GELMEZ. Listede yoksa "istenmemektedir / listede yer almıyor" de ve listedeki en yakın şartı
  (referans, aktif hizmet, kapasite, sertifika gibi) ayrıca belirt.
- İddian alıntılardan doğrudan çıkmıyorsa onu yazma. Belgede olmayan bir şeye sayı uydurma ("%0", "sıfır" gibi);
  "istenmiyor" de.
- "KOD KONTROLÜ" verildiyse onunla çelişme; orada sayılan şartları cevabında belirt.
- "DOĞRULANMIŞ İHALE KARTI" verildiyse onunla çelişme; kartta sorulan konuyla ilgili bilgi varsa cevaba kat.
- Kullanıcıya belgenin içeriğini sorma ("belgede geçiyor mu?" gibi); belgeyi sen incelersin.
- bulundu=false ise cevap'ta en yakın ilgili hükmü (varsa) söyle; kaynaklar'a o hükmü koy.
Tahmin etme; belgede olmayan bilgiyi yazma."""


def sartname_cevapla(belge, soru):
    """Soruyu belgeye sorar, alıntıları doğrular. Döner: {"bulundu", "cevap", "kaynaklar", "okunan", "dogrulanamayan"}."""
    baglam, okunan = _baglam(belge, soru)
    gecisler = _gecis_yerleri(belge, soru)
    ek = ("\n\nSORUDAKİ KELİMELERİN BELGEDE GEÇTİĞİ YERLER (dikkat: bir kelimenin geçmesi, o şeyin istendiği anlamına "
          "gelmez; bağlama bak):\n" + "\n".join(f"- ...{g}..." for g in gecisler)) if gecisler else ""
    kart = kart_metni(belge.get("kart"))
    kart = f"\n\nDOĞRULANMIŞ İHALE KARTI (belgeden alıntıyla teyit edildi):\n{kart}" if kart else ""
    istenen = belge.get("istenen")
    liste = (f"\n\nİHALEYE KATILIM İÇİN İSTENEN BELGELER VE KOŞULLAR ({istenen['etiket']}, kodla çıkarıldı):\n"
             + liste_metni(istenen)) if istenen else ""
    girdi = f"BELGE:\n{baglam}{kart}{liste}{ek}{_liste_kontrolu(belge, soru)}\n\nSORU: {soru}"   # belge önde: önbellek için
    sonuc = _json_iste(CEVAP_TALIMAT, girdi, CEVAP_SEMASI, "sartname_cevabi")
    gecerli = [k for k in sonuc["kaynaklar"] if alinti_belgede_mi(k["alinti"], belge)]
    gecersiz = [k for k in sonuc["kaynaklar"] if k not in gecerli]
    if gecersiz:                                                    # bir kez düzelttir: alıntıyı belgeden aynen kopyalasın
        girdi += ("\n\nÖnceki cevabındaki şu alıntılar belgede birebir geçmiyor: "
                  + " | ".join(k["alinti"] for k in gecersiz)
                  + "\nAynı soruyu tekrar cevapla; alıntıları belgeden harfi harfine kopyala.")
        sonuc = _json_iste(CEVAP_TALIMAT, girdi, CEVAP_SEMASI, "sartname_cevabi")
        gecerli = [k for k in sonuc["kaynaklar"] if alinti_belgede_mi(k["alinti"], belge)]
        gecersiz = [k for k in sonuc["kaynaklar"] if k not in gecerli]
    for k in gecerli:                                               # etiketi model değil kod koyar: alıntının geçtiği madde
        k["etiket"] = gercek_etiket(k["alinti"], belge) or k["etiket"]
    if istenen and _kokler(soru) & DENEYIM_KOKLERI:                 # deneyim sorusu: ilgili bentleri kod ekler
        _, is_bentleri = is_deneyim_durumu(istenen)
        diger = deneyim_sartlari(istenen)
        for x in is_bentleri + diger:
            if any(_duz(x["metin"][:60]).strip() in _duz(k["alinti"]) for k in gecerli):
                continue                                            # model zaten alıntılamış
            gecerli.append({"etiket": istenen["etiket"], "alinti": f"{x['bent']} {x['metin'][:600]}"})
            if x in diger and _duz(x["metin"][:40]).strip() not in _duz(sonuc["cevap"]):
                sonuc["cevap"] += f" Ayrıca listede deneyim türü bir şart var: {x['bent']} {x['kisa']}"
            sonuc["bulundu"] = True
    if sonuc["bulundu"] and not gecerli:                            # kaynağı doğrulanamayan "buldum" cevabı kabul edilmez
        sonuc["bulundu"] = False
        sonuc["cevap"] = "Bu soruya belgeden doğrulanabilir bir alıntıyla cevap bulunamadı."
    sonuc_ = {"bulundu": sonuc["bulundu"], "cevap": sonuc["cevap"], "kaynaklar": gecerli, "okunan": okunan,
              "dogrulanamayan_alinti_sayisi": len(gecersiz)}
    if istenen and LISTE_SORUSU.search(_kucuk(soru)):              # "hangi belgeler": listeyi kod verir, ekranda tablo olur
        sonuc_["istenen_belgeler"] = [{"bent": x["bent"], "kisa": x["kisa"]} for x in istenen["maddeler"]]
        sonuc_["liste_notu"] = f"İstenen belgelerin tam listesi ({istenen['etiket']}) kullanıcıya tablo olarak gösteriliyor."
        madde7 = istenen["etiket"].split(",")[0]                    # tabloda zaten var: aynı maddenin alıntıları gizlenir
        sonuc_["kaynaklar"] = [k for k in gecerli if not k["etiket"].startswith(madde7)]
    return sonuc_


# ---------- eski arayüz (belge_yukle.py gibi eski dosyalar bozulmasın) ----------

def belge_parcala(yol):
    """Eski yöntem: bölümleri vektör veritabanı parçalarına çevirir."""
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    bolucu = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150)
    _, bolumler = _metin_ve_bolumler(yol)
    return [Document(page_content=f"[{b['etiket']}] {alt}", metadata={"madde": b["etiket"], "konu": "", "sayfa": b["sayfa"]})
            for b in bolumler for alt in bolucu.split_text(b["metin"])]


pdf_parcala = belge_parcala                                         # daha eski ad


def veritabani_kur(parcalar, klasor=None, ad="belge"):
    """Parçaları vektör veritabanına koyar (eski yöntem)."""
    from langchain_chroma import Chroma
    return Chroma.from_documents(parcalar, _embedding(), persist_directory=klasor, collection_name=ad)


def veritabani_ac(klasor, ad="belge"):
    """Diske kaydedilmiş veritabanını açar (eski yöntem)."""
    from langchain_chroma import Chroma
    return Chroma(persist_directory=klasor, embedding_function=_embedding(), collection_name=ad)