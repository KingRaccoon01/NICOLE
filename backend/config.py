"""
Nicole — Yapılandırma
Tüm ayarlar .env dosyasından okunur. .env.example dosyasını .env olarak
kopyalayıp kendi ücretsiz Gemini API anahtarını girmen yeterli.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv kurulu değilse ortam değişkenlerini doğrudan kullan

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Veritabanı ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'nicole.db'}")

# --- Whisper (STT) ---
# Ücretsiz, yerel çalışır. Model boyutu küçüldükçe hız artar, doğruluk azalır.
# Seçenekler: tiny, base, small, medium, large-v3
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
# Varsayılan "cpu" — çoğu bilgisayarda ekstra kurulum gerektirmeden çalışır.
# CUDA Toolkit + cuDNN kurulu bir NVIDIA GPU'n varsa (RTX 4060 gibi) .env'de
# WHISPER_DEVICE=cuda yaparak hızlandırabilirsin. Sadece GPU'nun olması yetmez,
# CUDA Toolkit ayrıca kurulu olmalı — kurulu değilse kod otomatik CPU'ya düşer.
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "tr")
# Açıkken Whisper modeli sunucu açılırken ARKA PLANDA yüklenir; böylece ilk konuşmada
# beklemezsin. Model bir kez yüklenince sunucu kapanana kadar bellekte kalır.
# Bellek/açılış hızı için kapatmak istersen .env'e WHISPER_PRELOAD=false yaz.
WHISPER_PRELOAD = os.getenv("WHISPER_PRELOAD", "true").strip().lower() in ("1", "true", "yes", "evet")
# CPU'da kaç iş parçacığı kullanılsın? 0 = otomatik (işlemci çekirdeklerinin yarısı, en az 4).
# Kaliteyi DEĞİŞTİRMEZ, sadece hızı etkiler. GPU (WHISPER_DEVICE=cuda) kullanırken yok sayılır.
WHISPER_CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "0") or 0)
# Whisper, az duyduğu özel isim/jargon kelimelerini (örn. "Rasengan", "Gaara",
# "Rock Lee") yanlış duyabilir (örn. "Rahatli garayı"). Buraya hikayende geçen
# özel isimleri/terimleri virgülle ayırarak yazarsan, Whisper'a önceden "bu
# kelimeler geçebilir" ipucu verilir ve doğru tanıma ihtimali belirgin artar.
# Nicole ayrıca Lore Sözlüğü'nde onayladığın terimleri de otomatik olarak buna ekler.
WHISPER_VOCABULARY_HINT = os.getenv("WHISPER_VOCABULARY_HINT", "")

# --- Ollama (Lore/Flag tespiti) ---
# Ücretsiz, yerel çalışır. Önce `ollama pull llama3` ile modeli indirmen gerekir.
# NOT: "localhost" yerine kasıtlı olarak "127.0.0.1" kullanılıyor — Windows'ta
# "localhost" bazen önce IPv6 (::1) olarak çözülür, Ollama ise varsayılan olarak
# sadece IPv4 (127.0.0.1) üzerinde dinler; bu uyumsuzluk "bağlantı reddedildi"
# hatasına yol açabiliyordu.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")  # denemelerde 8B llama3 öneriyi İngilizce verebiliyordu

# --- Flag taraması motoru ---
# "ollama" (varsayılan): yerel, internetsiz, kota yok.
# "gemini": Türkçeyi ve bağlamı çok daha iyi anlar. Editör sohbetinin kotasını yememesi için AYRI
# bir model kullanır (Google'da her modelin ücretsiz kotası ayrı sayılır). Gemini hata verirse
# (kota bitti, internet yok) o paragraf otomatik olarak Ollama ile taranır.
FLAG_ENGINE = os.getenv("FLAG_ENGINE", "ollama").strip().lower()
FLAG_GEMINI_MODEL = os.getenv("FLAG_GEMINI_MODEL", "gemini-flash-lite-latest")

# --- Gemini (AI Editör Sohbeti) ---
# Ücretsiz katman: https://aistudio.google.com/apikey adresinden anahtar alınır.
# NOT: Gemini 2.0 Flash (1 Haziran 2026) ve ardından 2.5 Flash de kapatıldı.
# Güncel varsayılan: gemini-3.6-flash (21 Temmuz 2026'da çıktı, ücretsiz katmanda).
# İleride bu da kapanırsa https://ai.google.dev/gemini-api/docs/models adresinden
# güncel model adını kontrol edip .env'deki GEMINI_MODEL'i güncelle — kodun kendisi
# artık Google'ın döndürdüğü hata mesajını (varsa önerdiği yeni model adını dahil)
# doğrudan gösteriyor, bir dahaki sefere log kazmaya gerek kalmayacak.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Ayarlar'da "Hızlı" seçilince Editör bu modelle başlar. Denemede (21 Kasım 2026) 3.6-flash yoğunluktan
# 50-96 sn sürerken / 503 verirken 3.1-flash-lite 2-3 sn'de cevap verdi.
GEMINI_FAST_MODEL = os.getenv("GEMINI_FAST_MODEL", "gemini-3.1-flash-lite")
# Ana model "çok talep var" (HTTP 503) ya da kota (HTTP 429) hatası verirse sırayla
# denenecek YEDEK modeller (virgülle ayır). Her modelin ücretsiz kotası ayrı sayılır
# ve Flash-Lite modellerinin günlük limiti genelde daha yüksektir. "-latest" takma
# adları Google model sürümünü kapatınca bozulmaz. Yedek istemiyorsan boş bırak.
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest,gemini-3.1-flash-lite").split(",")
    if m.strip()
]

# --- Genel ---
APP_NAME = "Nicole"
