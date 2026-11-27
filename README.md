# İhale Asistanı

Kamu ihale belgelerini yapay zekâ ile okuyan araç: belgeye kaynak göstererek soru cevaplar, ihale ilanlarından önemli bilgileri otomatik çıkarır.

**Canlı demo:** https://ihale-asistani.streamlit.app (şifre için iletişime geçin)

## Ne yapar?

**1. Belgeye soru-cevap (RAG)**
PDF yüklersiniz, Türkçe soru sorarsınız. Cevap, belgenin ilgili maddesine ve sayfasına dayanarak verilir: `[Madde 33, Sayfa 26]`. Belgede olmayan bir soruya cevap uydurmaz, "Belgede bu bilgiyi bulamadım" der.

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

Her iki modül de elle hazırlanmış cevap anahtarlarıyla otomatik olarak ölçülür.

### Soru-cevap (RAG)

Kamu İhale Kanunu (4734, 83 sayfa) üzerinde 20 soruluk test seti: 17'si belgede cevabı olan soru, 3'ü belgede olmayan kontrol sorusu.

| Sürüm | Değişiklik | Doğruluk |
| --- | --- | --- |
| 1 | Sabit 1000 karakterlik parçalama | %45 |
| 2 | Kanun yapısına göre madde madde parçalama | %80 |
| 3 | Madde konu başlığını parçaya ekleme, kapsam dışı bölümü ayıklama | **%90** |

Bu sürümlerin yanında yazım hatalı sorular, aramadan önce modelle düzeltiliyor (query rewriting). Örneğin "gecici teminat oranı nedr" sorusu önce bulunamıyordu, bu adımdan sonra doğru maddeyle cevaplanıyor.

### İlandan bilgi çıkarma

10 gerçek kamu ihale ilanında (mal, hizmet, yapım işi) **50 alanın 50'si doğru (%100)**. İlk sürüm %96'daydı. Hatalar incelendi ve talimat iki adımda iyileştirildi: alanların ilanın hangi satırından alınacağı belirtildi, "usul" alanı da sabit bir değer listesiyle sınırlandı.

## Nasıl çalışır?

```
PDF ──► Parçalama ──► Embedding ──► Chroma (vektör veritabanı)
                                          │
Soru ──► Yazım düzeltme ──► Benzer parça araması (8 parça)
                                          │
                                          ▼
                     Model: sadece bu parçalara dayanarak cevap + kaynak
```

- **Parçalama:** Belgede "Madde 19-" gibi başlıklar varsa her madde ayrı parça olur; parçanın başına madde adı ve konusu eklenir. Madde yapısı yoksa belge sabit boyutlu parçalara bölünür.
- **Kaynak gösterme:** Her parça madde ve sayfa numarasıyla etiketlenir. Model cevabın sonuna bu etiketi yazar.

## Proje yapısı

| Dosya | Görevi |
| --- | --- |
| `app.py` | Web arayüzü (Streamlit) |
| `rag.py` | Parçalama, veritabanı ve cevaplama: sistemin çekirdeği |
| `belge_yukle.py` | Örnek belgeyi (`belgeler/4734.pdf`) veritabanına yükler |
| `soru_sor.py` | Terminalden soru sorma |
| `rag_degerlendir.py` | Soru-cevap doğruluğunu ölçer (`rag_test_seti.json`) |
| `cikar.py` | İlanlardan bilgi çıkarır (`ornekler/` → `ciktilar/`) |
| `degerlendir.py` | Çıkarma doğruluğunu ölçer (`dogru_cevaplar.json`) |
| `test_baglanti.py` | OpenAI bağlantı testi |

## Teknolojiler

- Python 3.13
- OpenAI API: `gpt-5.4-nano` (cevap), `text-embedding-3-small` (embedding). Model adları `.env` dosyasından değiştirilebilir.
- LangChain, Chroma
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
streamlit run app.py            # web arayüzü

python belge_yukle.py           # örnek belgeyi veritabanına yükle
python soru_sor.py              # terminalden soru sor
python rag_degerlendir.py       # soru-cevap doğruluğunu ölç

python cikar.py                 # ilanlardan bilgi çıkar
python degerlendir.py           # çıkarma doğruluğunu ölç
```

## Güvenlik

- API anahtarı ve şifre sadece `.env` dosyasında (yerelde) ve Streamlit Secrets'ta (sunucuda) tutulur; GitHub'a gönderilmez.
- Uygulama şifre korumalıdır.
- Kötüye kullanıma karşı sınırlar: en fazla 10 MB ve 150 sayfalık PDF, oturum başına en fazla 30 soru.
- OpenAI tarafında aylık harcama sınırı ve izin verilen modeller listesi tanımlıdır.

## Bilinen sınırlar

- Testler Kamu İhale Kanunu üzerinde yapıldı; idari ve teknik şartnamelerde henüz ölçülmedi.
- Aynı soru farklı çalıştırmalarda farklı sonuç verebiliyor; ölçümler tek çalıştırmaya dayanıyor.
- Taranmış (resim) PDF'ler ve ekran görüntüleri desteklenmiyor.
- Örnek kanun metni 2024 tarihli; içindeki parasal eşikler güncel değil.

## Yol haritası

- [x] İlandan bilgi çıkarma ve otomatik ölçüm
- [x] Kaynak gösteren soru-cevap (RAG) ve otomatik ölçüm
- [x] Web arayüzü ve canlı yayın
- [ ] Gerçek idari şartnamelerle test
- [ ] Taranmış PDF ve görsel desteği
- [ ] Araç kullanımı: teminat ve son teklif tarihi hesaplama