"""Veritabanı katmanı: projedeki TÜM SQL bu dosyada.
İleride SQLite'tan PostgreSQL'e geçerken sadece bu dosya değişecek."""
import sqlite3                                                      # şimdilik SQLite: tek dosya, kurulum yok

DB_YOLU = "ihaleler.db"

# Tablolar ve veri tipleri. TEXT tarihler ISO biçiminde: "2026-11-27" / "2026-12-22 10:00" (sıralanabilir).
SEMA = """
CREATE TABLE IF NOT EXISTS ilanlar (
    bulten_tarihi   TEXT    NOT NULL,   -- bültenin yayın günü
    tur             TEXT    NOT NULL,   -- Mal / Hizmet / Yapım / Danışmanlık
    ilan_turu       TEXT    NOT NULL,   -- İHALE İLANLARI, İHALE İPTAL İLANLARI ...
    sira            INTEGER NOT NULL,   -- bülten içindeki sıra no
    ikn             TEXT    NOT NULL,   -- ihale kayıt numarası
    idare           TEXT,
    il              TEXT,
    konu            TEXT,
    ihale_tarihi    TEXT,               -- "2026-12-22 10:00"
    usul            TEXT,
    gecici_teminat  INTEGER,            -- yüzde
    gecerlilik_gun  INTEGER,
    PRIMARY KEY (bulten_tarihi, tur, ilan_turu, sira)
);
CREATE TABLE IF NOT EXISTS sonuclar (
    bulten_tarihi    TEXT    NOT NULL,
    tur              TEXT    NOT NULL,
    ilan_turu        TEXT    NOT NULL,
    sira             INTEGER NOT NULL,
    ikn              TEXT    NOT NULL,
    idare            TEXT,
    konu             TEXT,
    ihale_tarihi     TEXT,              -- "2026-10-14"
    usul             TEXT,
    yaklasik_maliyet REAL,
    kisimli          INTEGER,           -- 1: kısımlı ihale, bedel/yaklaşık oranı anlamsız
    dokuman_indiren  INTEGER,
    teklif_sayisi    INTEGER,
    gecerli_teklif   INTEGER,
    sozlesme_bedeli  REAL,
    para_birimi      TEXT,
    sozlesme_tarihi  TEXT,
    yuklenici        TEXT,
    kisim_yaklasik   REAL,              -- "Sözleşmeye Esas Kısımlarının Yaklaşık Maliyeti" (kısımlı ihalede)
    PRIMARY KEY (bulten_tarihi, tur, ilan_turu, sira)
);
CREATE TABLE IF NOT EXISTS islenen_dosyalar (
    dosya          TEXT PRIMARY KEY,
    bulten_tarihi  TEXT,
    tur            TEXT,
    sonuc          INTEGER,
    kayit          INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ilan_il     ON ilanlar (il);              -- "Ankara'daki ihaleler" hızlı gelsin
CREATE INDEX IF NOT EXISTS ix_ilan_ikn    ON ilanlar (ikn);
CREATE INDEX IF NOT EXISTS ix_sonuc_ikn   ON sonuclar (ikn);
CREATE INDEX IF NOT EXISTS ix_sonuc_firma ON sonuclar (yuklenici);      -- "şu firma neler kazandı" hızlı gelsin
"""
# Sonradan eklenen sütunlar. Eski veritabanında yoksa baglan() otomatik ekler (veri silinmez).
# Yeni sütun eklerken hem SEMA'ya hem buraya yaz.
EK_SUTUNLAR = {
    "sonuclar": {"kisim_yaklasik": "REAL"},
}
ANAHTAR = ["bulten_tarihi", "tur", "ilan_turu", "sira"]             # bir kaydı benzersiz yapan alanlar
ALANLAR = {
    "ilanlar": ANAHTAR + ["ikn", "idare", "il", "konu", "ihale_tarihi", "usul", "gecici_teminat", "gecerlilik_gun"],
    "sonuclar": ANAHTAR + ["ikn", "idare", "konu", "ihale_tarihi", "usul", "yaklasik_maliyet", "kisimli",
                           "dokuman_indiren", "teklif_sayisi", "gecerli_teklif", "sozlesme_bedeli", "para_birimi",
                           "sozlesme_tarihi", "yuklenici", "kisim_yaklasik"],
}


def _sutunlari_tamamla(db):
    """Eski veritabanında eksik olan sütunları ekler (küçük bir 'migration')."""
    for tablo, sutunlar in EK_SUTUNLAR.items():
        var = {s[1] for s in db.execute(f"PRAGMA table_info({tablo})")}   # tablodaki mevcut sütun adları
        for ad, tip in sutunlar.items():
            if ad not in var:
                db.execute(f"ALTER TABLE {tablo} ADD COLUMN {ad} {tip}")  # eski kayıtlarda bu sütun boş (NULL) kalır


def baglan():
    """Veritabanına bağlanır; tablolar yoksa kurar, eksik sütunları ekler."""
    db = sqlite3.connect(DB_YOLU)
    db.executescript(SEMA)
    _sutunlari_tamamla(db)
    return db


def kayitlari_yaz(tablo, kayitlar):
    """Kayıtları yazar. Aynı kayıt zaten varsa günceller (çoğalmaz).
    ON CONFLICT ... DO UPDATE hem SQLite'ta hem PostgreSQL'de aynı çalışır."""
    alanlar = ALANLAR[tablo]
    guncelle = ", ".join(f"{a} = excluded.{a}" for a in alanlar if a not in ANAHTAR)
    sql = (f"INSERT INTO {tablo} ({', '.join(alanlar)}) VALUES ({', '.join('?' * len(alanlar))}) "
           f"ON CONFLICT ({', '.join(ANAHTAR)}) DO UPDATE SET {guncelle}")
    with baglan() as db:                                            # "with": hata olursa yarım kayıt bırakmaz
        db.executemany(sql, [[k.get(a) for a in alanlar] for k in kayitlar])


def islenmis_mi(dosya):
    """Bu PDF daha önce okundu mu?"""
    with baglan() as db:
        return db.execute("SELECT 1 FROM islenen_dosyalar WHERE dosya = ?", (dosya,)).fetchone() is not None


def islendi_isaretle(dosya, bulten_tarihi, tur, sonuc, kayit):
    """PDF'in okunduğunu kaydeder; bir sonraki çalıştırmada atlanır."""
    with baglan() as db:
        db.execute("INSERT INTO islenen_dosyalar VALUES (?, ?, ?, ?, ?) ON CONFLICT (dosya) DO UPDATE SET "
                   "kayit = excluded.kayit", (dosya, bulten_tarihi, tur, int(sonuc), kayit))


def sorgu(sql, parametreler=()):
    """Okuma sorgusu çalıştırır, satırları liste olarak döndürür (özet ve ileride agent'ın tablo aracı için)."""
    with baglan() as db:
        return db.execute(sql, parametreler).fetchall()