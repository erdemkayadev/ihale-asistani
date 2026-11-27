import json                        # JSON metnini Python sözlüğüne çevirmek için
import os                          # .env'den okunan değerlere erişmek için
from pathlib import Path           # dosya ve klasörlerle kolay çalışmak için
from dotenv import load_dotenv     # .env dosyasını okuyacak araç
from openai import OpenAI          # OpenAI kütüphanesi

load_dotenv()                      # .env içeriğini yükle
client = OpenAI()                  # bağlantıyı kur (parantezli!)
MODEL = os.getenv("MODEL")         # model adını .env'den al

# Modele ne yapacağını anlatan talimat (prompt)
TALIMAT = """Sen bir kamu ihale uzmanısın. Sana verilen ihale ilanından şu alanları çıkar.
SADECE geçerli bir JSON döndür, başka hiçbir şey yazma:
{
  "ikn": "İhale Kayıt Numarası, örnek: 2026/123456",
    "idare": "İlandaki '1.1. Adı' satırında yazan ad, hiçbir şey eklemeden aynen",
  "konu": "İhale konusu işin adı",
  "ihale_tarihi": "GG.AA.YYYY SS:DD biçiminde",
  "usul": "İhale usulü, örnek: açık ihale"
}
Bir bilgi ilanda yoksa değerine null yaz. Tahmin etme."""


def ilandan_bilgi_cikar(ilan_metni):
    """İlan metnini modele gönderir, dönen JSON'u sözlük olarak verir."""
    cevap = client.responses.create(
        model=MODEL,
        instructions=TALIMAT,      # modelin rolü ve kuralları
        input=ilan_metni,          # işlenecek metin: ilanın kendisi
    )
    metin = cevap.output_text.strip()                       # cevabın metni, baş/son boşluklar silinmiş
    metin = metin.removeprefix("```json").removeprefix("```").removesuffix("```").strip()  # model ```json ile sararsa temizle
    return json.loads(metin)                                # JSON metnini sözlüğe çevir


if __name__ == "__main__":
    Path("ciktilar").mkdir(exist_ok=True)                   # sonuçların yazılacağı klasör (varsa dokunma)
    for dosya in sorted(Path("ornekler").glob("*.txt")):    # ornekler içindeki her .txt dosyası için
        ilan = dosya.read_text(encoding="utf-8")            # dosyayı oku (Türkçe karakterler için utf-8)
        bilgi = ilandan_bilgi_cikar(ilan)                   # modelden bilgileri al
        guzel_json = json.dumps(bilgi, ensure_ascii=False, indent=2)  # sözlüğü okunaklı JSON metnine çevir
        print(f"--- {dosya.name}")                          # hangi ilan olduğunu yaz
        print(guzel_json)                                   # sonucu ekrana yaz
        (Path("ciktilar") / f"{dosya.stem}.json").write_text(guzel_json, encoding="utf-8")  # ciktilar/ilan1.json olarak kaydet