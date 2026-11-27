import os                                                           # .env'deki değerlere erişmek için
import re                                                           # "Madde 19-" gibi başlıkları bulmak için
from bisect import bisect_right                                     # bir konumun hangi sayfaya düştüğünü bulmak için
from collections import Counter                                     # her sayfada tekrar eden satırları saymak için
from dotenv import load_dotenv                                      # .env dosyasını okuyacak araç
from openai import OpenAI                                           # cevabı yazacak model
from langchain_core.documents import Document                       # tek bir parça: metin + etiketler
from langchain_text_splitters import RecursiveCharacterTextSplitter # uzun metni parçalara böler
from langchain_openai import OpenAIEmbeddings                       # metni sayıya çevirir
from langchain_chroma import Chroma                                 # vektör veritabanı

load_dotenv()
client = OpenAI()
# "Madde 24 -", "Madde 9.", "MADDE 3)", "Ek Madde 1-" hepsini yakalar; "Madde 33.3" gibi alt maddeleri yakalamaz
BASLIK = re.compile(r"^[ \t]*(?:(Ek|Geçici)[ \t]+)?Madde[ \t]*(\d+)[ \t]*[-–—.):](?!\d)[ \t]*([^\n]*)", re.IGNORECASE | re.MULTILINE)
BOLUCU = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150)

TALIMAT = """Sen bir kamu ihale belgesi asistanısın. Soruyu SADECE sana verilen belge parçalarına dayanarak cevapla.
Her bilginin sonuna, o bilgiyi aldığın parçanın en başındaki köşeli parantezli etiketi AYNEN kopyala (örn. [Madde 24, Sayfa 8] ya da [Madde 3]).
Etikette sayfa numarası yoksa sayfa numarası yazma, uydurma.
Cevap parçalarda yoksa sadece "Belgede bu bilgiyi bulamadım." yaz. Tahmin etme."""

def _embedding():
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


# ---------- 3) Parçala ----------

def belge_parcala(yol):
    """PDF ya da Word belgesini parçalara böler. Madde yapısı varsa madde madde, yoksa sayfa sayfa."""
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
    parcalar = []
    if len(maddeler) < 3:                                           # madde yapısı yok: sayfa sayfa, sabit boyutta böl
        for no, s in enumerate(sayfalar, start=1):
            for alt in BOLUCU.split_text(s):
                etiket = f"Sayfa {no}" if sayfali else "Belge"
                parcalar.append(Document(page_content=f"[{etiket}] {alt}",
                                         metadata={"madde": "", "konu": "", "sayfa": no if sayfali else 0}))
        return parcalar

    for m in maddeler:
        madde = f"{m['tur'] + ' ' if m['tur'] else ''}Madde {m['no']}"
        if m["bolum"]:
            madde = f"{m['bolum']} {madde}"                         # örn. "SÖZLEŞME VE ÖZEL KOŞULLAR Madde 26"
        etiket = f"{madde} - {m['baslik']}" if m["baslik"] else madde
        sayfa = bisect_right(sayfa_baslangiclari, m["bas"]) if sayfali else 0
        for alt in BOLUCU.split_text(m["govde"]):
            parcalar.append(Document(page_content=f"[{etiket}] {alt}",
                                     metadata={"madde": madde, "konu": m["baslik"], "sayfa": sayfa}))
    return parcalar


pdf_parcala = belge_parcala                                         # eski ad da çalışsın (belge_yukle.py, app.py)


# ---------- 4) Veritabanı ve cevap ----------

def veritabani_kur(parcalar, klasor=None, ad="belge"):
    """Parçaları sayıya çevirip veritabanına koyar. klasor verilirse diske kaydeder, verilmezse sadece hafızada tutar."""
    return Chroma.from_documents(parcalar, _embedding(), persist_directory=klasor, collection_name=ad)


def veritabani_ac(klasor, ad="belge"):
    """Diske kaydedilmiş veritabanını açar."""
    return Chroma(persist_directory=klasor, embedding_function=_embedding(), collection_name=ad)


def soruyu_duzelt(soru):
    """Aramadan önce yazım hatalarını ve eksik Türkçe karakterleri düzeltir."""
    cevap = client.responses.create(
        model=os.getenv("MODEL"),
        instructions="Kullanıcının sorusundaki yazım hatalarını ve eksik Türkçe karakterleri düzelt. "
                     "Anlamı değiştirme, soruya cevap verme. Sadece düzeltilmiş soruyu yaz.",
        input=soru,
    )
    return cevap.output_text.strip()


def _kaynak(p):
    """Parçanın kaynak etiketi: [Madde 24, Sayfa 7] / [Madde 24] / [Sayfa 7]."""
    parcalar = [x for x in (p.metadata["madde"], f"Sayfa {p.metadata['sayfa']}" if p.metadata["sayfa"] else "") if x]
    return f"[{', '.join(parcalar) or 'Belge'}]"


def _kunye(db):
    """Şartnamenin ilk 3 maddesi (idare, iş, ihale bilgileri) ihalenin kimlik kartıdır: tarih, usul, İKN burada.
    Bu parçaları veritabanından doğrudan çeker. Kanunda bu maddeler yok, boş döner."""
    sonuc = db.get(where={"madde": {"$in": ["Madde 1", "Madde 2", "Madde 3"]}})
    return [Document(page_content=d, metadata=m) for d, m in zip(sonuc["documents"], sonuc["metadatas"])
            if "ilişkin" in m["konu"]]                              # "İhaleye ilişkin bilgiler" gibi başlıklar


def cevapla(db, soru):
    """Soruyu düzeltir, ilgili parçaları bulur, kaynaklı cevap döndürür."""
    duzgun_soru = soruyu_duzelt(soru)
    kunye = _kunye(db)                                              # her soruda künye de bağlamda olsun
    bulunan = db.similarity_search(duzgun_soru, k=8)                # en yakın 8 parça
    parcalar = kunye + [p for p in bulunan if p.page_content not in {k.page_content for k in kunye}]   # tekrar etmesin
    baglam = "\n\n".join(f"{_kaynak(p)}\n{p.page_content}" for p in parcalar)
    cevap = client.responses.create(
        model=os.getenv("MODEL"),
        instructions=TALIMAT,
        input=f"BELGE PARÇALARI:\n{baglam}\n\nSORU: {duzgun_soru}",
    )
    return cevap.output_text