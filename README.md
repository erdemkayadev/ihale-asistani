# İhale Asistanı

Kamu ihale belgelerini yapay zekâ ile okuyan araç: belgeye kaynak göstererek soru cevaplar, ihale ilanlarından önemli bilgileri otomatik çıkarır.

**Canlı demo:** https://ihale-asistani.streamlit.app (şifre için iletişime geçin)

## Ne yapar?

**1. Belgeye soru-cevap (RAG)**
PDF ya da Word belgesi yüklersiniz, Türkçe soru sorarsınız. Cevap, belgenin ilgili maddesine ve sayfasına dayanarak verilir: `[Madde 24, Sayfa 8]`. Belgede olmayan bir soruya cevap uydurmaz, "Belgede bu bilgiyi bulamadım" der.

**2. İlandan bilgi çıkarma**
İhale ilanı metninden 5 alanı yapılandırılmış JSON olarak çıkarır:

| Alan | Örnek |
| --- | --- |
| İKN | 2026/1425153 |
| İdare | AVCILAR BELEDİYE BAŞKANLIĞI AFET İŞLERİ MÜDÜRLÜĞÜ |
| Konu | MUHTELİF ÇADIR ALIMI İŞİ |
| İhale tarihi | 20.08.2026 10:00 |
| Usul | açık ihale |

## Sonuçlar

Tüm modüller elle hazırlanmış cevap anahtarlarıyla otomatik olarak ölçülür.

### Soru-cevap: 10 gerçek idari şartname

Farklı kurumlardan 10 gerçek idari şartname üzerinde 64 soruluk test seti: 54 soru belgede cevabı olan sorular (ihale tarihi ve usulü, teklif geçerlilik süresi, geçici ve kesin teminat, kısmi teklif, fiyat farkı), 10 soru ise belgede olmayan kontrol sorusu. Bir cevap ancak **doğru maddeyi kaynak gösterir ve doğru bilgiyi verirse** doğru sayılır.

| Sürüm | Değişiklik | Doğruluk |
| --- | --- | --- |
| 1 | Genel parçalayıcı: Word desteği, içindekiler ve sayfa üst/alt bilgisi temizliği, çok bölümlü belgeler | 58/64 (%90) |
| 2 | Başlık düzenini belgeye göre seçme; ihale künyesini (Madde 1–3) her soruda bağlama ekleme | 62/64 (%96)* |
| 3 | Modelin kaynak etiketini aynen kopyalaması (Word'de sayfa numarası uydurmasını önler) | **63/64 (%98)** |

Son sürümde 64 cevabın 64'ünde kaynak madde doğru.

\* 2. ölçümle birlikte test setinde iki düzeltme yapıldı: Şablon bir belgede kısmi teklif maddesi iki seçenek içerdiği için o sorunun beklenen cevabı iki seçeneği de kapsayacak şekilde değiştirildi. Puanlamada ise "bulunamadı" gibi eş anlamlı ifadeler ve Türkçe ünsüz yumuşaması (hesaplanmayacak → hesaplanmayacağı) için kök arama eklendi.

Test belgeleri birbirinden farklı: mal, hizmet ve yapım işleri; bir Word dosyası; boşlukları doldurulmamış bir şablon; içindekiler sayfası olan bir belge; numaraları her bölümde yeniden başlayan 45 sayfalık bir ihale dosyası.

### Soru-cevap: Kamu İhale Kanunu

Kamu İhale Kanunu (4734) üzerinde 20 soruluk test seti: 17 soru belgede cevabı olan sorular, 3 soru belgede olmayan kontrol sorusu.

| Sürüm | Değişiklik | Doğruluk |
| --- | --- | --- |
| 1 | Sabit 1000 karakterlik parçalama | %45 |
| 2 | Kanun yapısına göre madde madde parçalama | %80 |
| 3 | Madde konu başlığını parçaya ekleme, kapsam dışı bölümü ayıklama | %90 |
| 4 | Kanun ve şartnameyi birlikte tanıyan genel parçalayıcı | **%90** (çalıştırmalar arasında %85–95) |

Yazım hatalı sorular aramadan önce modelle düzeltiliyor (query rewriting). Örneğin "gecici teminat oranı nedr" sorusu önce bulunamıyordu, bu adımdan sonra doğru maddeyle cevaplanıyor.

### İlandan bilgi çıkarma

10 gerçek kamu ihale ilanında (mal, hizmet, yapım işi) **50 alanın 50'si doğru (%100)**. İlk sürüm %96'daydı. Hatalar incelendi ve talimat iki adımda iyileştirildi: alanların ilanın hangi satırından alınacağı belirtildi, "usul" alanı da sabit bir değer listesiyle sınırlandı.

## Nasıl çalışır?

```
PDF / Word ──► Parçalama ──► Embedding ──► Chroma (vektör veritabanı)
                                                │
Soru ──► Yazım düzeltme ──► Benzer parça araması (8 parça) + ihale künyesi (Madde 1–3)
                                                │
                                                ▼
                         Model: sadece bu parçalara dayanarak cevap + kaynak
```

- **Parçalama:** "Madde 24 -", "Madde 9." gibi başlıklar bulunur ve her madde ayrı parça olur. Kanunda madde başlığı üst satırda, şartnamede aynı satırda yazılır; kod belgenin hangi düzende yazıldığını çoğunluğa bakarak anlar. İçindekiler sayfası, her sayfada tekrar eden üst/alt bilgi ve alt maddeler ("Madde 33.3") ayıklanır. Numaraları yeniden başlayan bölümler adlandırılır (örn. `SÖZLEŞME VE ÖZEL KOŞULLAR Madde 29`). Madde yapısı yoksa belge sabit boyutlu parçalara bölünür.
- **İhale künyesi:** Tip idari şartnamelerde ilk 3 madde (idare, iş ve ihale bilgileri) ihale tarihi, usul ve İKN gibi temel bilgileri içerir. Bu maddeler her soruda bağlama eklenir. Kanunda bu maddeler olmadığı için bir şey eklenmez.
- **Kaynak gösterme:** Her parça madde ve sayfa numarasıyla etiketlenir. Model bu etiketi cevabın sonuna aynen yazar.

## Proje yapısı

| Dosya | Görevi |
| --- | --- |
| `app.py` | Web arayüzü (Streamlit) |
| `rag.py` | Okuma, parçalama, veritabanı ve cevaplama: sistemin çekirdeği |
| `belge_yukle.py` | Kanun metnini (`belgeler/4734.pdf`) veritabanına yükler |
| `soru_sor.py` | Terminalden soru sorma |
| `rag_degerlendir.py` | Kanun testini çalıştırır (`rag_test_seti.json`) |
| `sartname_degerlendir.py` | 10 şartname testini çalıştırır (`sartname_test_seti.json`, belgeler `belgeler/sartnameler/` içinde) |
| `cikar.py` | İlanlardan bilgi çıkarır (`ornekler/` → `ciktilar/`) |
| `degerlendir.py` | Çıkarma doğruluğunu ölçer (`dogru_cevaplar.json`) |
| `test_baglanti.py` | OpenAI bağlantı testi |

## Teknolojiler

- Python 3.13
- OpenAI API: `gpt-5.4-nano` (cevap), `text-embedding-3-small` (embedding). Model adları `.env` dosyasından değiştirilebilir.
- LangChain, Chroma, pypdf, python-docx
- Streamlit (arayüz ve yayın)

## Kurulum

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash)
pip install -r requirements.txt
cp .env.example .env               # .env içine kendi değerlerini yaz
```

`.env` içinde 4 değer gerekir: `OPENAI_API_KEY`, `MODEL`, `EMBED_MODEL`, `APP_SIFRE`.

## Kullanım

```bash
streamlit run app.py              # web arayüzü

python belge_yukle.py             # kanun metnini veritabanına yükle
python soru_sor.py                # terminalden soru sor
python rag_degerlendir.py         # kanun testi
python sartname_degerlendir.py    # 10 şartname testi

python cikar.py                   # ilanlardan bilgi çıkar
python degerlendir.py             # çıkarma doğruluğunu ölç
```

## Güvenlik

- API anahtarı ve şifre sadece `.env` dosyasında (yerelde) ve Streamlit Secrets'ta (sunucuda) tutulur; GitHub'a gönderilmez.
- Uygulama şifre korumalıdır.
- Kötüye kullanıma karşı sınırlar: en fazla 10 MB'lık dosya, PDF'te en fazla 150 sayfa, oturum başına en fazla 30 soru.
- OpenAI tarafında aylık harcama sınırı ve izin verilen modeller listesi tanımlıdır.

## Bilinen sınırlar

- **Şablon belgelerdeki koşullu hükümler:** "Ayrı ihale edilirse şu, birlikte ihale edilirse bu" gibi iki seçenekli maddelerde model seçeneklerden yalnızca birini söyleyebiliyor.
- **Tanım maddesi:** Bazı sorularda model cevabı doğru veriyor ama asıl madde yerine tanımlar maddesini (Madde 4) kaynak gösteriyor.
- Aynı soru farklı çalıştırmalarda farklı sonuç verebiliyor; tablolardaki sayılar tek çalıştırmaya dayanıyor.
- Word dosyalarında sayfa numarası yok; kaynak sadece madde olarak gösterilir.
- Taranmış (resim) PDF'ler ve ekran görüntüleri desteklenmiyor.
- Örnek kanun metni 2024 tarihli; içindeki parasal eşikler güncel değil.

## Yol haritası

- [x] İlandan bilgi çıkarma ve otomatik ölçüm
- [x] Kaynak gösteren soru-cevap (RAG) ve otomatik ölçüm
- [x] Web arayüzü ve canlı yayın
- [x] 10 gerçek idari şartnameyle test, Word desteği
- [ ] Mevzuat ve Kamu İhale Kurulu kararları katmanı
- [ ] Araç kullanan asistan (agent): şartname + mevzuat + hesaplama araçları
- [ ] Taranmış PDF ve görsel desteği