# İhale Asistanı

Kamu ihale ilanlarından önemli bilgileri yapay zekâ ile otomatik çıkaran ve sonucu ölçen bir araç.

## Ne yapar?

İhale ilanı metnini okur ve şu 5 alanı yapılandırılmış JSON olarak çıkarır:

| Alan | Örnek |
| --- | --- |
| İKN (İhale Kayıt Numarası) | 2026/1425153 |
| İdare | AVCILAR BELEDİYE BAŞKANLIĞI AFET İŞLERİ MÜDÜRLÜĞÜ |
| Konu | MUHTELİF ÇADIR ALIMI İŞİ |
| İhale tarihi | 20.08.2026 10:00 |
| Usul | açık ihale |

## Sonuçlar

10 gerçek kamu ihale ilanında (mal, hizmet ve yapım işi) **50 alanın 50'si doğru (%100)**.

- Doğruluk, elle hazırlanmış cevap anahtarıyla (`dogru_cevaplar.json`) otomatik karşılaştırılarak ölçüldü.
- İlk sürüm %96 doğruluktaydı. Hatalar incelendi ve talimat (prompt) iki adımda iyileştirildi: alanların ilanın hangi satırından alınacağı belirtildi ve "usul" alanı sabit bir değer listesiyle sınırlandı.
- Not: Talimat bu 10 ilan üzerinde ayarlandı. Hiç görülmemiş ilanlarla ayrı bir test planlanıyor.

## Nasıl çalışır?

1. `cikar.py`: `ornekler/` klasöründeki her ilanı OpenAI modeline gönderir ve dönen JSON'u `ciktilar/` klasörüne kaydeder.
2. `degerlendir.py`: Çıktıları cevap anahtarıyla karşılaştırır, yanlışları listeler ve doğruluk oranını hesaplar. Türkçe büyük/küçük harf farklarını (İ/I) normalize eder.

## Teknolojiler

- Python
- OpenAI API (`gpt-5.4-nano`; model adı `.env` dosyasından değiştirilebilir)
- python-dotenv

## Kurulum

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash)
pip install -r requirements.txt
cp .env.example .env               # sonra .env içine kendi API anahtarını yaz
```

## Kullanım

```bash
python cikar.py          # ilanları işle
python degerlendir.py    # sonucu ölç
```

## Yol haritası

- [x] İlandan bilgi çıkarma
- [x] Otomatik doğruluk ölçümü
- [ ] Görülmemiş ilanlarla test
- [ ] Uzun şartnamelere kaynak göstererek soru-cevap (RAG)
- [ ] Web arayüzü (Streamlit)