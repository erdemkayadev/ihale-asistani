import hashlib                                      # yüklenen dosyaya benzersiz bir kimlik vermek için
import tempfile                                     # yüklenen PDF'i geçici olarak diske yazmak için
import streamlit as st                              # web arayüzü
from rag import pdf_parcala, veritabani_kur, cevapla

st.set_page_config(page_title="İhale Asistanı", page_icon="📄")
st.title("İhale Asistanı")
st.caption("İhale belgenizi (PDF) yükleyin, sorularınızı sorun. Cevaplar madde ve sayfa kaynağıyla gelir.")

yuklenen = st.file_uploader("PDF yükle", type="pdf")             # dosya yükleme kutusu

if yuklenen:
    icerik = yuklenen.getvalue()                                 # dosyanın baytları
    kimlik = hashlib.md5(icerik).hexdigest()[:12]                # aynı dosya mı, farklı mı anlamak için

    if st.session_state.get("kimlik") != kimlik:                 # yeni bir dosya yüklendiyse
        with st.spinner("Belge okunuyor ve hazırlanıyor..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
                f.write(icerik)                                  # PDF'i geçici dosyaya yaz
            parcalar = pdf_parcala(f.name)                       # parçala
            st.session_state.db = veritabani_kur(parcalar, ad=f"b{kimlik}")   # hafızada veritabanı kur
            st.session_state.kimlik = kimlik
            st.session_state.mesajlar = []                       # yeni belge, sohbeti sıfırla
        st.success(f"Hazır: {len(parcalar)} parça.")

    for m in st.session_state.mesajlar:                          # önceki mesajları ekrana bas
        with st.chat_message(m["rol"]):
            st.markdown(m["metin"])

    soru = st.chat_input("Belgeyle ilgili sorunuzu yazın")       # alttaki soru kutusu
    if soru:
        st.session_state.mesajlar.append({"rol": "user", "metin": soru})
        with st.chat_message("user"):
            st.markdown(soru)
        with st.chat_message("assistant"):
            with st.spinner("Belgede aranıyor..."):
                cevap = cevapla(st.session_state.db, soru)
            st.markdown(cevap)
        st.session_state.mesajlar.append({"rol": "assistant", "metin": cevap})
else:
    st.info("Başlamak için bir PDF yükleyin.")