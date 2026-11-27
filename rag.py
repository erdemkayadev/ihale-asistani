import os                                                           # .env'deki değerlere erişmek için
import re                                                           # "Madde 19-" gibi başlıkları bulmak için
from bisect import bisect_right                                     # bir konumun hangi sayfaya düştüğünü bulmak için
from dotenv import load_dotenv                                      # .env dosyasını okuyacak araç
from openai import OpenAI                                           # cevabı yazacak model
from langchain_community.document_loaders import PyPDFLoader        # PDF'i sayfa sayfa okur
from langchain_core.documents import Document                       # tek bir parça: metin + etiketler
from langchain_text_splitters import RecursiveCharacterTextSplitter # metni parçalara böler
from langchain_openai import OpenAIEmbeddings                       # metni sayıya çevirir
from langchain_chroma import Chroma                                 # vektör veritabanı

load_dotenv()
client = OpenAI()
BASLIK = re.compile(r"^\s*((?:Ek |Geçici )?Madde\s+(\d+))\s*[-–—]", re.IGNORECASE | re.MULTILINE)
BOLUCU = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150)

TALIMAT = """Sen bir kamu ihale belgesi asistanısın. Soruyu SADECE sana verilen belge parçalarına dayanarak cevapla.
Her bilginin sonuna kaynağını parçanın başındaki etiketle [Madde X, Sayfa Y] ya da [Sayfa Y] şeklinde yaz.
Cevap parçalarda yoksa sadece "Belgede bu bilgiyi bulamadım." yaz. Tahmin etme."""


def _embedding():
    return OpenAIEmbeddings(model=os.getenv("EMBED_MODEL"))         # embedding modelini .env'den al


def _konu_bul(metin, konum):
    """Madde satırının hemen üstündeki kısa satırı (örn. 'Doğrudan temin') konu başlığı olarak döndürür."""
    for satir in reversed(metin[:konum].rstrip().split("\n")[-3:]):   # maddeden önceki en yakın 3 satır
        satir = re.sub(r"\s*\d+\s*$", "", satir.strip())            # sondaki dipnot/sayfa numarasını sil
        if satir:
            return satir if len(satir) <= 100 and not satir.endswith(".") else ""
    return ""


def pdf_parcala(pdf_yolu):
    """PDF'i parçalara böler. Madde başlığı varsa madde madde, yoksa sabit boyutta böler."""
    sayfalar = PyPDFLoader(pdf_yolu).load()
    tam_metin, sayfa_baslangiclari = "", []
    for sayfa in sayfalar:
        sayfa_baslangiclari.append(len(tam_metin))                  # her sayfanın metindeki başlangıç konumu
        tam_metin += sayfa.page_content + "\n"

    kesme = tam_metin.find("İŞLENEMEYEN HÜKÜMLER")                  # kanun PDF'lerinin sonundaki başka kanun maddeleri
    if kesme != -1:
        tam_metin = tam_metin[:kesme]

    eslesmeler = list(BASLIK.finditer(tam_metin))
    if len(eslesmeler) < 3:                                         # madde yapısı yoksa: sayfa sayfa, sabit boyutta böl
        parcalar = BOLUCU.split_documents(sayfalar)
        for p in parcalar:
            p.metadata = {"madde": "", "konu": "", "sayfa": p.metadata.get("page", 0) + 1}
            p.page_content = f"[Sayfa {p.metadata['sayfa']}] {p.page_content}"
        return parcalar

    parcalar = []
    for i, e in enumerate(eslesmeler):                              # madde yapısı varsa: madde madde böl
        bas = e.start()
        son = eslesmeler[i + 1].start() if i + 1 < len(eslesmeler) else len(tam_metin)
        on_ek = e.group(1).lower()
        tur = "Ek " if on_ek.startswith("ek") else "Geçici " if on_ek.startswith("ge") else ""
        madde_adi = f"{tur}Madde {e.group(2)}"
        konu = _konu_bul(tam_metin, bas)
        etiket = f"{madde_adi} - {konu}" if konu else madde_adi
        sayfa_no = bisect_right(sayfa_baslangiclari, bas)
        for alt in BOLUCU.split_text(tam_metin[bas:son].strip()):
            parcalar.append(Document(page_content=f"[{etiket}] {alt}",
                                     metadata={"madde": madde_adi, "konu": konu, "sayfa": sayfa_no}))
    return parcalar


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


def cevapla(db, soru):
    """Soruyu düzeltir, ilgili parçaları bulur, kaynaklı cevap döndürür."""
    duzgun_soru = soruyu_duzelt(soru)
    parcalar = db.similarity_search(duzgun_soru, k=8)               # en yakın 8 parça
    baglam = "\n\n".join(                                           # her parçanın başına madde ve sayfa etiketini ekle
    f"[{p.metadata['madde'] + ', ' if p.metadata['madde'] else ''}Sayfa {p.metadata['sayfa']}]\n{p.page_content}"
    for p in parcalar)          # her parçanın başında zaten etiketi var
    cevap = client.responses.create(
        model=os.getenv("MODEL"),
        instructions=TALIMAT,
        input=f"BELGE PARÇALARI:\n{baglam}\n\nSORU: {duzgun_soru}",
    )
    return cevap.output_text