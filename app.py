import hashlib                                      # yüklenen dosyaya benzersiz bir kimlik vermek için
import io                                           # baytları dosya gibi okumak için
import os                                           # şifreyi okumak ve geçici dosyayı silmek için
import tempfile                                     # yüklenen PDF'i geçici olarak diske yazmak için
import streamlit as st                              # web arayüzü
from pypdf import PdfReader                         # sayfa sayısını kontrol etmek için
from rag import pdf_parcala, veritabani_kur, cevapla

MAKS_MB = 10                                        # en fazla 10 MB PDF
MAKS_SAYFA = 150                                    # en fazla 150 sayfa
MAKS_SORU = 30                                      # bir oturumda en fazla 30 soru

st.set_page_config(page_title="İhale Asistanı", page_icon="📄")
st.title("İhale Asistanı")
st.caption("İhale belgenizi (PDF) yükleyin, sorularınızı sorun. Cevaplar madde ve sayfa kaynağıyla gelir. "
           "Yüklenen belgeler cevap üretmek için OpenAI'a gönderilir; gizli belge yüklemeyin.")

SIFRE = os.getenv("APP_SIFRE")                                   # şifre .env'de ya da sunucu ayarlarında
if SIFRE and st.text_input("Şifre", type="password") != SIFRE:   # şifre tanımlıysa ve yanlış girildiyse
    st.info("Uygulamayı denemek için şifreyi girin.")
    st.stop()                                                    # sayfanın geri kalanını çalıştırma

yuklenen = st.file_uploader(f"PDF yükle (en fazla {MAKS_MB} MB, {MAKS_SAYFA} sayfa)", type="pdf")

if yuklenen:
    icerik = yuklenen.getvalue()                                 # dosyanın baytları
    if len(icerik) > MAKS_MB * 1024 * 1024:                      # boyut kontrolü
        st.error(f"Dosya {MAKS_MB} MB'tan büyük.")
        st.stop()
    kimlik = hashlib.md5(icerik).hexdigest()[:12]                # aynı dosya mı, farklı mı anlamak için

    if st.session_state.get("kimlik") != kimlik:                 # yeni bir dosya yüklendiyse
        try:
            sayfa_sayisi = len(PdfReader(io.BytesIO(icerik)).pages)   # sayfa sayısını oku
        except Exception:                                        # bozuk ya da şifreli PDF
            st.error("PDF okunamadı. Dosya bozuk ya da şifreli olabilir.")
            st.stop()
        if sayfa_sayisi > MAKS_SAYFA:
            st.error(f"Belge {sayfa_sayisi} sayfa; en fazla {MAKS_SAYFA} sayfa yüklenebilir.")
            st.stop()

        with st.spinner("Belge okunuyor ve hazırlanıyor..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
                f.write(icerik)                                  # PDF'i geçici dosyaya yaz
            try:
                parcalar = pdf_parcala(f.name)                   # parçala
            finally:
                os.remove(f.name)                                # geçici dosyayı her durumda sil
            if not parcalar:                                     # resim/taranmış PDF: içinde metin yok
                st.error("Belgede okunabilir metin bulunamadı (taranmış PDF olabilir).")
                st.stop()
            st.session_state.db = veritabani_kur(parcalar, ad=f"b{kimlik}")   # hafızada veritabanı kur
            st.session_state.kimlik = kimlik
            st.session_state.mesajlar = []                       # yeni belge, sohbeti sıfırla
        st.success(f"Hazır: {sayfa_sayisi} sayfa, {len(parcalar)} parça.")

    st.session_state.setdefault("soru_sayisi", 0)                # bu oturumda sorulan soru sayısı

    for m in st.session_state.mesajlar:                          # önceki mesajları ekrana bas
        with st.chat_message(m["rol"]):
            st.markdown(m["metin"])

    soru = st.chat_input("Belgeyle ilgili sorunuzu yazın")       # alttaki soru kutusu
    if soru:
        if st.session_state.soru_sayisi >= MAKS_SORU:            # oturum başına soru sınırı
            st.warning(f"Bu oturumda en fazla {MAKS_SORU} soru sorulabilir.")
            st.stop()
        st.session_state.soru_sayisi += 1
        soru = soru[:500]                                        # çok uzun soruları kes
        st.session_state.mesajlar.append({"rol": "user", "metin": soru})
        with st.chat_message("user"):
            st.markdown(soru)
        with st.chat_message("assistant"):
            with st.spinner("Belgede aranıyor..."):
                try:
                    cevap = cevapla(st.session_state.db, soru)
                except Exception:                                # API hatası, limit dolması vb.
                    cevap = "Şu an cevap üretilemiyor. Lütfen daha sonra tekrar deneyin."
            st.markdown(cevap)
        st.session_state.mesajlar.append({"rol": "assistant", "metin": cevap})
else:
    st.info("Başlamak için bir PDF yükleyin.")