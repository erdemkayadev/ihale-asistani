import hashlib                                      # yüklenen dosyaya benzersiz bir kimlik vermek için
import io                                           # baytları dosya gibi okumak için
import json                                         # kullanılan araçları göstermek için
import os                                           # şifreyi okumak ve geçici dosyayı silmek için
import tempfile                                     # yüklenen belgeyi geçici olarak diske yazmak için
import streamlit as st                              # web arayüzü
from pypdf import PdfReader                         # sayfa sayısını kontrol etmek için
from rag import belge_parcala, veritabani_kur       # şartname okuma ve vektör veritabanı
import ajan                                         # soruyu cevaplayan ajan (veritabanı + şartname araçları)

MAKS_MB = 10                                        # en fazla 10 MB dosya
MAKS_SAYFA = 150                                    # en fazla 150 sayfa
MAKS_SORU = 30                                      # bir oturumda en fazla 30 soru
GECMIS_MESAJ = 6                                    # modele gönderilecek son mesaj sayısı (maliyeti sınırlar)

st.set_page_config(page_title="İhale Asistanı", page_icon="📄")
st.title("İhale Asistanı")
st.caption("Kamu ihale bültenlerinden emsal, rekabet ve açık ihale bilgisi; yüklediğiniz şartnameden madde kaynaklı cevap. "
           "Cevaplar karar desteğidir, EKAP'tan doğrulayın. Yüklenen belgeler OpenAI'a gönderilir; gizli belge yüklemeyin.")

SIFRE = os.getenv("APP_SIFRE")                                   # şifre .env'de ya da sunucu ayarlarında
if SIFRE and st.text_input("Şifre", type="password") != SIFRE:   # şifre tanımlıysa ve yanlış girildiyse
    st.info("Uygulamayı denemek için şifreyi girin.")
    st.stop()                                                    # sayfanın geri kalanını çalıştırma

st.session_state.setdefault("mesajlar", [])                      # sohbet geçmişi: [{"rol", "metin", "araclar"}]
st.session_state.setdefault("soru_sayisi", 0)                    # bu oturumda sorulan soru sayısı
st.session_state.setdefault("db", None)                          # yüklenen şartnamenin vektör veritabanı

# ---------- yan menü: isteğe bağlı şartname ----------
with st.sidebar:
    st.subheader("Şartname (isteğe bağlı)")
    yuklenen = st.file_uploader(f"PDF ya da Word (en fazla {MAKS_MB} MB, {MAKS_SAYFA} sayfa)", type=["pdf", "docx"])
    if yuklenen:
        icerik = yuklenen.getvalue()                             # dosyanın baytları
        kimlik = hashlib.md5(icerik).hexdigest()[:12]            # aynı dosya mı, farklı mı anlamak için
        if len(icerik) > MAKS_MB * 1024 * 1024:                  # boyut kontrolü
            st.error(f"Dosya {MAKS_MB} MB'tan büyük.")
        elif st.session_state.get("kimlik") != kimlik:           # yeni bir dosya yüklendiyse
            uzanti = ".docx" if yuklenen.name.lower().endswith(".docx") else ".pdf"
            hata = None
            if uzanti == ".pdf":
                try:
                    sayfa = len(PdfReader(io.BytesIO(icerik)).pages)   # sayfa sayısını oku
                    if sayfa > MAKS_SAYFA:
                        hata = f"Belge {sayfa} sayfa; en fazla {MAKS_SAYFA} sayfa yüklenebilir."
                except Exception:                                # bozuk ya da şifreli PDF
                    hata = "PDF okunamadı. Dosya bozuk ya da şifreli olabilir."
            if hata:
                st.error(hata)
            else:
                with st.spinner("Belge okunuyor..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=uzanti) as f:
                        f.write(icerik)                          # dosyayı geçici olarak diske yaz
                    try:
                        parcalar = belge_parcala(f.name)         # maddelere göre parçala
                    except Exception:
                        parcalar = None
                    finally:
                        os.remove(f.name)                        # geçici dosyayı her durumda sil
                if not parcalar:                                 # bozuk ya da taranmış (metinsiz) belge
                    st.error("Belgede okunabilir metin bulunamadı.")
                else:
                    st.session_state.db = veritabani_kur(parcalar, ad=f"b{kimlik}")   # hafızada veritabanı kur
                    st.session_state.kimlik = kimlik
                    st.session_state.belge_adi = yuklenen.name
    if st.session_state.db is not None:
        st.success(f"Yüklü: {st.session_state.get('belge_adi', 'belge')}")

# ---------- sohbet ----------
for m in st.session_state.mesajlar:                              # önceki mesajları ekrana bas
    with st.chat_message(m["rol"]):
        st.markdown(m["metin"])
        for ad, satirlar in m.get("tablolar", {}).items():         # listeyi program basar, model değil
            st.dataframe(satirlar, hide_index=True)
        if m.get("araclar"):
            with st.expander("Kullanılan araçlar"):
                st.code("\n".join(f"{ad} {json.dumps(p, ensure_ascii=False)}" for ad, p in m["araclar"]))

soru = st.chat_input("Örn: Ankara'da temizlik hizmeti ihaleleri yaklaşık maliyetin yüzde kaçına gidiyor?")
if soru:
    if st.session_state.soru_sayisi >= MAKS_SORU:                # oturum başına soru sınırı
        st.warning(f"Bu oturumda en fazla {MAKS_SORU} soru sorulabilir.")
        st.stop()
    st.session_state.soru_sayisi += 1
    soru = soru[:500]                                            # çok uzun soruları kes
    gecmis = [{"role": m["rol"], "content": m["metin"] + "".join(f"\n[{ad} tablosu gösterildi: {len(t)} satır]"
                                                                  for ad, t in m.get("tablolar", {}).items())}
              for m in st.session_state.mesajlar[-GECMIS_MESAJ:]]
    st.session_state.mesajlar.append({"rol": "user", "metin": soru})
    with st.chat_message("user"):
        st.markdown(soru)
    with st.chat_message("assistant"):
        with st.spinner("Veriye bakılıyor..."):
            try:
                sonuc = ajan.sor(soru, gecmis, st.session_state.db)
            except Exception:                                    # API hatası, limit dolması vb.
                sonuc = {"cevap": "Şu an cevap üretilemiyor. Lütfen daha sonra tekrar deneyin.", "araclar": [], "tablolar": {}}
        st.markdown(sonuc["cevap"])
        for ad, satirlar in sonuc["tablolar"].items():               # İKN, kazanan, bedel: veritabanından birebir
            st.dataframe(satirlar, hide_index=True)
        if sonuc["araclar"]:
            with st.expander("Kullanılan araçlar"):
                st.code("\n".join(f"{ad} {json.dumps(p, ensure_ascii=False)}" for ad, p in sonuc["araclar"]))
    st.session_state.mesajlar.append({"rol": "assistant", "metin": sonuc["cevap"], "araclar": sonuc["araclar"],
                                      "tablolar": sonuc["tablolar"]})