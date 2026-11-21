# NICOLE

Sesle dikte edilen metni yazıya döken, dünya/lore tutarlılığını otomatik
kontrol eden ve hikâyeni tartışabileceğin bir AI editör sohbeti içeren
yazma masaüstü uygulaması. Tamamen ücretsiz araçlarla çalışır.

Detaylı proje planı için: `nicole_proje_plani.md`

---

## 1. Gereksinimler

- Python 3.10+
- (Sesli dikte için) [Ollama](https://ollama.com) — yerel, ücretsiz LLM çalıştırıcı
- (Opsiyonel ama önerilir) NVIDIA GPU — RTX 4060 gibi, Whisper'ı hızlandırır. GPU yoksa CPU'da da çalışır, sadece yavaştır.
- Ücretsiz bir Google Gemini API anahtarı (AI Editör Sohbeti için)

## 2. Kurulum

```bash
# 1) Sanal ortam oluştur
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2) Bağımlılıkları kur
pip install -r requirements.txt

# 3) Ollama'yı kur ve llama3 modelini indir (bir kereye mahsus)
#    https://ollama.com adresinden indir, sonra:
ollama pull llama3

# 4) Ortam değişkenlerini ayarla
cp .env.example .env
# .env dosyasını aç, GEMINI_API_KEY alanına
# https://aistudio.google.com/apikey adresinden aldığın ücretsiz anahtarı yapıştır.
# GPU'n yoksa WHISPER_DEVICE=cpu ve WHISPER_COMPUTE_TYPE=int8 yap.
```

## 3. Çalıştırma

```bash
# Ollama'nın arka planda çalıştığından emin ol (genelde kurulumla otomatik başlar)
ollama serve   # eğer otomatik başlamadıysa

# Nicole sunucusunu başlat
uvicorn backend.main:app --reload
```

Tarayıcıda `http://localhost:8000` adresine git. İlk açılışta seni otomatik
olarak **Romanlar** ekranına yönlendirir — yeni bir proje oluşturup içine girdiğinde
dikte/yazma ekranı açılır.

İlk Whisper kullanımında model dosyası internetten bir kez indirilir
(`WHISPER_MODEL_SIZE` ayarına göre değişen boyutta), sonrasında tamamen
çevrimdışı çalışır.

## Testler

```bash
pip install pytest
python -m pytest tests
```
Testler ayrı, geçici bir veritabanı kullanır; romanlarına dokunmaz. Whisper/Ollama/Gemini'ye istek atmaz.

## 4. Klasör Yapısı

```
nicole/
├── backend/
│   ├── main.py              # FastAPI giriş noktası
│   ├── config.py            # .env okuma
│   ├── database.py          # SQLAlchemy modelleri (Project, Chapter, Paragraph, FlaggedTerm, LoreEntry, ChatMessage)
│   ├── schemas.py           # Pydantic şemaları
│   ├── routers/
│   │   ├── projects.py      # Romanlar CRUD
│   │   ├── chapters.py      # Bölüm + paragraf (klavye modu dahil)
│   │   ├── flags.py         # Müdahale modalı — 4 aksiyon
│   │   ├── lore.py          # Paylaşımlı Lore Sözlüğü CRUD
│   │   ├── chat.py          # Nicole Editör (Gemini)
│   │   ├── export.py        # DOCX export
│   │   └── dictation_ws.py  # Canlı ses akışı WebSocket'i
│   └── services/
│       ├── stt_service.py       # faster-whisper
│       ├── lore_detection.py    # Ollama/llama3 ile flag tespiti
│       ├── gemini_service.py    # Gemini API çağrısı
│       └── docx_export.py       # python-docx ile export
└── frontend/
    ├── index.html            # Dikte + Editör + Lore ekranı
    ├── projects.html         # Romanlar (çoklu proje) ekranı
    ├── css/style.css         # Japon estetiği tema
    └── js/                   # api, dictation, flags, editor_chat, lore, app
```

## 5. Flag Sistemi — Nasıl Çalışır

1. Sesle dikte edilen her parça Whisper ile yazıya dökülür; Whisper'ın **emin olmadığı kelimeler** de not edilir
   (eşik: `WHISPER_UNCERTAIN_PROB`, varsayılan 0.5). Klavyeyle yazılan metin taranmaz.
2. Metin, önceki paragraflar (bağlam), romanın Lore Sözlüğü ve emin olunmayan kelimelerle birlikte Ollama'ya gönderilir. Ollama iki şey arar:
   - **Yanlış duyulmuş kelimeler** → bağlama göre en mantıklı kelimeyi önerir (örn. "falız" → "filiz").
   - **Sözlükte olmayan kurgusal adlar** → sözlükteki bir terimin yanlış duyulmuş haliyse doğru yazımını önerir.
3. Tespit edilen terim ekranda **kırmızı 🚩 rozet** olarak gösterilir (kelime yazılmaz).
4. Rozete tıklanınca **Müdahale modalı** açılır, 4 seçenek sunar:

   | Buton | Sonuç | Lore'a eklenir mi |
   |---|---|---|
   | 🔵 Orijinal Terimi Kabul Et | Kelime aynen kalır | Evet, otomatik |
   | 🟢 Öneriyi Kabul Et | AI önerisiyle değişir | Evet, otomatik |
   | 🟡 Manuel Düzelt | Kullanıcının yazdığıyla değişir | İsteğe bağlı (ayrı onay kutusu) |
   | ✕ İptal | Hiçbir şey değişmez | Hayır — kırmızı flag kalır |

5. Her romanın **kendi Lore Sözlüğü** vardır — bir romanda tanımlanan terim diğerlerini etkilemez.

## 6. Bilinen Sınırlamalar / Sıradaki Adımlar

- Bu sandbox ortamında Ollama ve gerçek bir Gemini anahtarı bulunmadığından, flag
  tespiti ve AI sohbeti **kendi bilgisayarında** ilk elden test edilmelidir —
  ancak API akışı, veritabanı, DOCX export ve flag çözümleme mantığı burada
  gerçek isteklerle uçtan uca test edilmiş ve çalışır durumdadır.
- Whisper modeli ilk çalıştırmada internetten indirilir (~500MB, model boyutuna göre değişir).
- Export sırasında çözülmemiş flag uyarısı gösterilip gösterilmeyeceği açık bırakıldı (plandaki 5. soru) — `export.py` zaten `X-Unresolved-Flags` header'ını döndürüyor, frontend'e uyarı eklemek tek adımlık bir iş.
