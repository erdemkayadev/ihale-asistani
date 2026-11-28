import hashlib                                      # yüklenen dosyaya benzersiz bir kimlik vermek için
import io                                           # baytları dosya gibi okumak için
import json                                         # kullanılan araçları göstermek için
import os                                           # şifreyi okumak ve geçici dosyayı silmek için
import tempfile                                     # yüklenen belgeyi geçici olarak diske yazmak için
import streamlit as st                              # web arayüzü
from pypdf import PdfReader                         # sayfa sayısını kontrol etmek için
import rag                                          # şartname okuma, ihale kartı, alıntılı cevap
import ajan                                         # soruyu cevaplayan ajan (veritabanı + şartname araçları)

MAKS_MB = 10                                        # en fazla 10 MB dosya (config.toml'daki maxUploadSize ile aynı)
MAKS_SAYFA = 150                                    # en fazla 150 sayfa
MAKS_SORU = 30                                      # bir oturumda en fazla 30 soru
GECMIS_MESAJ = 6                                    # modele gönderilecek son mesaj sayısı (maliyeti sınırlar)

st.set_page_config(page_title="İhale Asistanı", page_icon="📄")
st.title("İhale Asistanı", anchor=False)                         # anchor=False: başlığın yanındaki bağlantı simgesi çıkmasın
st.caption("Kamu ihale bültenlerinden emsal, rekabet ve açık ihale bilgisi; yüklediğiniz şartnameden madde kaynaklı cevap. "
           "Cevaplar karar desteğidir, EKAP'tan doğrulayın. Yüklenen belgeler OpenAI'a gönderilir; gizli belge yüklemeyin.")

# ---------- şifre: doğru girilince kutu kaybolur ----------
SIFRE = os.getenv("APP_SIFRE")                                   # şifre .env'de ya da sunucu ayarlarında
if SIFRE and not st.session_state.get("giris"):                  # şifre tanımlı ve bu oturumda henüz giriş yapılmadıysa
    girilen = st.text_input("Şifre", type="password")
    if girilen != SIFRE:
        if girilen:
            st.error("Şifre yanlış.")
        else:
            st.info("Uygulamayı denemek için şifreyi girin.")
        st.stop()                                                # sayfanın geri kalanını çalıştırma
    st.session_state.giris = True                                # bu oturumda bir daha sorma
    st.rerun()                                                   # şifre kutusu ekrandan kalksın

st.session_state.setdefault("mesajlar", [])                      # sohbet geçmişi: [{"rol", "metin", "araclar", "tablolar", "kaynaklar"}]
st.session_state.setdefault("soru_sayisi", 0)                    # bu oturumda sorulan soru sayısı
st.session_state.setdefault("belge", None)                       # yüklenen şartname: metin, maddeler, ihale kartı

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
                with st.spinner("Belge okunuyor, ihale kartı çıkarılıyor... (yarım dakika sürebilir)"):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=uzanti) as f:
                        f.write(icerik)                          # dosyayı geçici olarak diske yaz
                    try:
                        belge = rag.belge_hazirla(f.name, yuklenen.name)   # maddelere ayır
                    except Exception:
                        belge = None
                    finally:
                        os.remove(f.name)                        # geçici dosyayı her durumda sil
                    if belge is not None:
                        try:
                            rag.ihale_karti(belge)               # künye ve kritik şartlar, alıntıları doğrulanmış
                        except Exception:
                            belge["kart"] = None                 # kart çıkmazsa da sorular çalışsın
                if belge is None:                                # bozuk ya da taranmış (metinsiz) belge
                    st.error("Belgede okunabilir metin bulunamadı. Taranmış (resim) PDF olabilir.")
                else:
                    st.session_state.belge = belge
                    st.session_state.kimlik = kimlik
    if not yuklenen and st.session_state.belge is not None:     # kullanıcı dosyayı kaldırdıysa belgeyi de unut
        st.session_state.belge, st.session_state.kimlik = None, None
    belge = st.session_state.belge
    if belge is not None:
        st.success(f"Yüklü: {belge['ad']}")
        if belge.get("kart"):
            st.subheader("İhale kartı")
            eksik = []
            for k in belge["kart"].values():
                if k["durum"] == "dogrulandi":
                    st.markdown(f"**{k['ad']}:** {k['deger']}")
                    st.caption(k["etiket"])                      # bilginin geldiği madde: kullanıcı kontrol edebilsin
                else:
                    eksik.append(k["ad"])
            if eksik:
                st.caption("Belgede bulunamadı: " + ", ".join(eksik))
        if belge.get("istenen"):                                 # katılım için istenen belgeler: kodla çıkarıldı, modelsiz
            with st.expander(f"İstenen belgeler ({len(belge['istenen']['maddeler'])})"):
                st.caption(belge["istenen"]["etiket"])
                for x in belge["istenen"]["maddeler"]:
                    st.markdown(f"**{x['bent']}** {x['kisa']}")
        if not belge.get("kart"):
            st.caption("İhale kartı çıkarılamadı; sorularınızı yine de sorabilirsiniz.")

# ---------- sohbet ----------
GELISTIRICI = os.getenv("GELISTIRICI") == "1"                    # .env'de GELISTIRICI=1 ise araç ayrıntıları görünür
ORNEKLER = [                                                     # karşılama ekranındaki hazır sorular (kısa: düğmeye sığsın)
    "Ankara'da temizlik ihaleleri kaça gidiyor?",
    "Perde ihalelerini en çok kim kazanıyor?",
    "Teklif tarihi geçmemiş yemek ihaleleri",
    "Pazarlıkla mal alımına kaç teklif geliyor?",
]


def mesaji_goster(m):
    """Bir sohbet mesajını; varsa tablosu, şartname alıntıları ve (geliştirici modunda) araç ayrıntısıyla basar."""
    with st.chat_message(m["rol"]):
        st.markdown(m["metin"])
        for ad, satirlar in m.get("tablolar", {}).items():       # listeyi program basar, model değil
            st.dataframe(satirlar, hide_index=True)
        for k in m.get("kaynaklar", []):                         # şartname alıntıları: belgeden birebir, kod doğruladı
            st.markdown(f"📄 **{k['etiket']}**\n> {k['alinti']}")
        if GELISTIRICI and m.get("araclar"):
            with st.expander("Kullanılan araçlar"):
                st.code("\n".join(f"{ad} {json.dumps(p, ensure_ascii=False)}" for ad, p in m["araclar"]))


if not st.session_state.mesajlar:                                # ilk açılış: ne sorulabileceğini göster
    st.markdown("**Neler sorabilirsiniz?** Geçmiş ihalelerin hangi oranlara gittiğini, kimin kazandığını, "
                "açık ihaleleri; yüklediğiniz şartnamenin maddelerini.")
    sutunlar = st.columns(2)
    for i, ornek in enumerate(ORNEKLER):
        if sutunlar[i % 2].button(ornek):
            st.session_state.bekleyen = ornek                    # tıklanan soruyu bir sonraki adımda gönder
            st.rerun()

for m in st.session_state.mesajlar:                              # önceki mesajları ekrana bas
    mesaji_goster(m)

soru = st.chat_input("İhalelerle ilgili sorunuzu yazın") or st.session_state.pop("bekleyen", None)
if soru:
    if st.session_state.soru_sayisi >= MAKS_SORU:                # oturum başına soru sınırı
        st.warning(f"Bu oturumda en fazla {MAKS_SORU} soru sorulabilir.")
        st.stop()
    st.session_state.soru_sayisi += 1
    soru = soru[:500]                                            # çok uzun soruları kes
    gecmis = [{"role": m["rol"], "content": m["metin"] + m.get("not", "")}       # not: gösterilen tablo/alıntı özeti
              for m in st.session_state.mesajlar[-GECMIS_MESAJ:]]
    st.session_state.mesajlar.append({"rol": "user", "metin": soru})
    mesaji_goster(st.session_state.mesajlar[-1])
    with st.spinner("Veriye bakılıyor..."):
        try:
            sonuc = ajan.sor(soru, gecmis, st.session_state.belge)
        except Exception:                                        # API hatası, limit dolması vb.
            sonuc = {"cevap": "Şu an cevap üretilemiyor. Lütfen biraz sonra tekrar deneyin.", "araclar": [],
                     "tablolar": {}, "kaynaklar": []}
    st.session_state.mesajlar.append({"rol": "assistant", "metin": sonuc["cevap"], "araclar": sonuc["araclar"],
                                      "tablolar": sonuc["tablolar"], "kaynaklar": sonuc["kaynaklar"],
                                      "not": ajan.gecmis_notu(sonuc)})
    st.rerun()                                                   # karşılama ekranı kalksın, sohbet düzgün çizilsin