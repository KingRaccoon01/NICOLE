# NICOLE

Romanını **sesle dikte edebileceğin** bir yazma yardımcısı. Konuştuğun metni yazıya döker,
yanlış duyulan kelimeleri ve yeni kurgusal adları işaretleyip düzeltme önerir, her romanın
kendi terim sözlüğünü (Lore Sözlüğü) tutar ve hikâyeni tartışabileceğin bir AI editör sunar.

Kendi bilgisayarında çalışır, tarayıcıdan kullanılır. Ücretsiz araçlarla çalışır.

Detaylı proje planı: `nicole_proje_plani.md`

---

## 1. Gereksinimler

- Python 3.10+
- [Ollama](https://ollama.com) — yerel, ücretsiz dil modeli (flag taraması için)
- Ücretsiz bir Google Gemini API anahtarı (Nicole Editör sohbeti için) — https://aistudio.google.com/apikey
- (İsteğe bağlı) NVIDIA GPU — Whisper'ı hızlandırır. Yoksa CPU'da çalışır, sadece daha yavaştır.

## 2. Kurulum

```bash
# 1) Sanal ortam
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate

# 2) Bağımlılıklar
pip install -r requirements.txt

# 3) Ollama modelini indir (bir kereye mahsus, ~2 GB)
ollama pull llama3.2:3b

# 4) Ayarlar
copy .env.example .env           # macOS/Linux: cp .env.example .env
# .env dosyasını aç ve GEMINI_API_KEY satırına anahtarını yapıştır.
```

`.env` dosyası **sadece senin bilgisayarında** kalmalı; içinde API anahtarın var
(`.gitignore` onu zaten GitHub'a göndermez).

## 3. Çalıştırma

```bash
# Ollama arka planda çalışmıyorsa:
ollama serve

# Nicole
uvicorn backend.main:app --reload
```

Tarayıcıda `http://localhost:8000` adresine git. **Romanlar** ekranından yeni bir roman
oluştur, içine girince dikte/yazma ekranı açılır.

- Whisper modeli ilk kullanımda bir kez indirilir (`WHISPER_MODEL_SIZE`'a göre ~150 MB – 3 GB),
  sonra internetsiz çalışır.
- Bir şey çalışmıyorsa sağ üstteki **🛠 Debug** paneli Whisper/Ollama/Gemini bağlantılarını tek tek test eder.

## 4. Kullanım

- **Dikte:** Mikrofonu seç, kaydı başlat, konuş. Durdurduğunda metin yazılır ve taranır.
  Whisper'ın emin olmadığı kelimelerin altı noktalı çizilir.
- **Bölümler:** Dikte panelinin üstündeki listeden bölüm değiştirebilir, yeni bölüm açabilir
  veya bölümün adını değiştirebilirsin.
- **⬇ DOCX:** Romanın tamamını tek bir Word dosyası olarak indirir (birden fazla bölüm varsa bölüm başlıklarıyla).
- **Nicole Editör:** Hikâyen hakkında konuşabileceğin AI editör. "Metni doğrudan düzenleme izni"
  açıkken metinde değişiklik yapabilir; her değişiklik **Geri al** ile geri alınabilir.

## 5. Flag Sistemi — Nasıl Çalışır

1. Sesle dikte edilen her parça Whisper ile yazıya dökülür; Whisper'ın **emin olmadığı kelimeler**
   de not edilir (eşik: `WHISPER_UNCERTAIN_PROB`, varsayılan 0.5). Klavyeyle yazılan metin taranmaz.
2. Metin; önceki paragraflar (bağlam), romanın Lore Sözlüğü ve emin olunmayan kelimelerle birlikte
   dil modeline gönderilir. Model iki şey arar:
   - **Yanlış duyulmuş kelimeler** → bağlama göre en mantıklı kelimeyi önerir (örn. "falız" → "filiz").
   - **Sözlükte olmayan kurgusal adlar** → sözlükteki bir terimin yanlış duyulmuş haliyse doğru yazımını önerir.
3. Tespit edilen kelime ekranda **kırmızı 🚩 rozet** olarak gösterilir.
4. Rozete tıklanınca 4 seçenek çıkar:

   | Buton | Sonuç | Lore'a eklenir mi |
   |---|---|---|
   | 🔵 Orijinali Kabul Et | Kelime aynen kalır | Evet, otomatik |
   | 🟢 Öneriyi Kabul Et | Öneriyle değişir | Evet, otomatik |
   | 🟡 Manuel Düzelt | Senin yazdığınla değişir | İsteğe bağlı |
   | ✕ İptal | Hiçbir şey değişmez | Hayır — kırmızı flag kalır |

5. Her romanın **kendi Lore Sözlüğü** vardır. Sözlükteki terimler Whisper'a da ipucu olarak verilir,
   böylece bir sonraki dikte de doğru yazılma ihtimali artar.

**Hangi model taransın?** `.env` içinde `FLAG_ENGINE`:
- `ollama` (varsayılan) — yerel, internetsiz, kota yok.
- `gemini` — Türkçeyi ve bağlamı daha iyi anlar. Editör sohbetinin kotasını harcamamak için ayrı bir
  model kullanır (`FLAG_GEMINI_MODEL`). Kota biter ya da internet giderse o paragraf otomatik olarak
  Ollama ile taranır. Ollama'ya da ulaşılamazsa ekranda "flag taraması yapılmadı" uyarısı çıkar.

## 6. Gizlilik

- Whisper ve Ollama tamamen senin bilgisayarında çalışır.
- **Nicole Editör** ve `FLAG_ENGINE=gemini` seçiliyse metnin Google'a (Gemini API) gönderilir.
  Editörde "hikâye bağlamı" kutusu açıkken romanın tamamı gider; kapatabilirsin.
  Ücretsiz Gemini katmanında Google gönderilen içeriği ürünlerini geliştirmek için kullanabilir.

## 7. Testler

```bash
pip install pytest
python -m pytest tests
```

Testler ayrı, geçici bir veritabanı kullanır; romanlarına dokunmaz ve Whisper/Ollama/Gemini'ye istek atmaz.

## 8. Klasör Yapısı

```
nicole/
├── backend/
│   ├── main.py              # FastAPI giriş noktası
│   ├── config.py            # .env okuma
│   ├── database.py          # SQLAlchemy modelleri + açılışta veritabanı güncellemeleri
│   ├── schemas.py           # API giriş/çıkış şemaları
│   ├── routers/
│   │   ├── projects.py      # Romanlar
│   │   ├── chapters.py      # Bölümler + paragraflar
│   │   ├── flags.py         # Flag çözme (4 aksiyon)
│   │   ├── lore.py          # Lore Sözlüğü (roman başına)
│   │   ├── chat.py          # Nicole Editör (Gemini) + düzenleme/geri alma
│   │   ├── export.py        # Word (.docx) dışa aktarım
│   │   ├── transcribe.py    # Sohbet için sesle mesaj yazma
│   │   ├── dictation_ws.py  # Canlı dikte WebSocket'i
│   │   └── debug/           # Debug paneli testleri
│   └── services/
│       ├── stt_service.py       # faster-whisper (+ kelime eminliği)
│       ├── lore_detection.py    # Flag taraması (Ollama / Gemini)
│       ├── gemini_service.py    # Gemini API
│       ├── http_client.py       # Ağ sorunlarına dayanıklı HTTP istemcisi
│       ├── lore_recovery.py     # Kaybolan Lore terimlerini kurtarma
│       └── docx_export.py       # Word çıktısı
├── frontend/                # index.html (yazma ekranı), projects.html (romanlar), debug.html
└── tests/                   # pytest testleri
```
