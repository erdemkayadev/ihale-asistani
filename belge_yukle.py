import os                                                           # .env'deki değerlere erişmek için
import re                                                           # "Madde 19-" gibi başlıkları bulmak için
import shutil                                                       # eski veritabanı klasörünü silmek için
from bisect import bisect_right                                     # bir konumun hangi sayfaya düştüğünü bulmak için
from dotenv import load_dotenv                                      # .env dosyasını okuyacak araç
from langchain_community.document_loaders import PyPDFLoader        # PDF'i sayfa sayfa okur
from langchain_core.documents import Document                       # tek bir parça: metin + etiketler (metadata)
from langchain_text_splitters import RecursiveCharacterTextSplitter # uzun maddeleri bölmek için
from langchain_openai import OpenAIEmbeddings                       # metni sayıya çevirir
from langchain_chroma import Chroma                                 # vektör veritabanı

load_dotenv()

# 1) PDF'i oku, sayfaları uç uca ekle, her sayfanın metinde nerede başladığını not et
sayfalar = PyPDFLoader("belgeler/4734.pdf").load()
tam_metin = ""
sayfa_baslangiclari = []                                            # örnek: [0, 2450, 5100, ...]
for sayfa in sayfalar:
    sayfa_baslangiclari.append(len(tam_metin))                      # bu sayfa tam metnin kaçıncı karakterinde başlıyor
    tam_metin += sayfa.page_content + "\n"
print(f"{len(sayfalar)} sayfa okundu")

# 2) Kanunun sonundaki "işlenemeyen hükümler" bölümünü at: orada BAŞKA kanunların maddeleri var
kesme = tam_metin.find("İŞLENEMEYEN HÜKÜMLER")
if kesme != -1:
    tam_metin = tam_metin[:kesme]

# 3) Madde başlıklarını bul: satır başında "Madde 19-" ya da "GEÇİCİ MADDE 4 –" gibi
baslik = re.compile(r"^\s*((?:Ek |Geçici )?Madde\s+(\d+))\s*[-–—]", re.IGNORECASE | re.MULTILINE)
eslesmeler = list(baslik.finditer(tam_metin))
print(f"{len(eslesmeler)} madde başlığı bulundu")


def madde_konusu(konum):
    """Madde satırının hemen üstündeki kısa satırı (örn. 'Doğrudan temin') konu başlığı olarak döndürür."""
    onceki_satirlar = tam_metin[:konum].rstrip().split("\n")        # maddeden önceki metni satırlara böl
    for satir in reversed(onceki_satirlar[-3:]):                    # en yakın 3 satıra sondan başa bak
        satir = re.sub(r"\s*\d+\s*$", "", satir.strip())            # sondaki dipnot/sayfa numarasını sil
        if satir:                                                   # boş olmayan ilk satır
            return satir if len(satir) <= 100 and not satir.endswith(".") else ""   # kısa ve cümle değilse başlıktır
    return ""


bolucu = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150)   # çok uzun maddeler için
parcalar = []
for i, e in enumerate(eslesmeler):
    bas = e.start()                                                 # bu maddenin başladığı yer
    son = eslesmeler[i + 1].start() if i + 1 < len(eslesmeler) else len(tam_metin)   # bir sonraki maddeye kadar
    madde_metni = tam_metin[bas:son].strip()
    on_ek = e.group(1).lower()                                      # başlığın küçük harfli hali
    tur = "Ek " if on_ek.startswith("ek") else "Geçici " if on_ek.startswith("ge") else ""   # ek/geçici madde mi?
    madde_adi = f"{tur}Madde {e.group(2)}"                          # "Madde 19", "Geçici Madde 4"
    konu = madde_konusu(bas)                                        # "Doğrudan temin" gibi konu başlığı
    etiket = f"{madde_adi} - {konu}" if konu else madde_adi         # "Madde 22 - Doğrudan temin"
    sayfa_no = bisect_right(sayfa_baslangiclari, bas)               # başlangıç konumundan sayfa numarasını bul (1'den başlar)
    for alt in bolucu.split_text(madde_metni):                      # kısa maddeler tek parça kalır, uzunlar bölünür
        parcalar.append(Document(
            page_content=f"[{etiket}] {alt}",                       # her parçanın başına madde ve konusunu yaz
            metadata={"madde": madde_adi, "konu": konu, "sayfa": sayfa_no},   # etiketler: cevapta kaynak göstermek için
        ))
print(f"{len(parcalar)} parça oluşturuldu")

# 4) Eski veritabanını sil (yoksa parçalar üst üste eklenir), yenisini kur
shutil.rmtree("vektor_db", ignore_errors=True)
Chroma.from_documents(parcalar, OpenAIEmbeddings(model=os.getenv("EMBED_MODEL")), persist_directory="vektor_db")
print("Veritabanı hazır: vektor_db/")