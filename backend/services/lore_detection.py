"""
Nicole — Lore/Flag tespit servisi
Ollama üzerinden yerel llama3 modelini kullanır: tamamen ücretsiz, internet gerektirmez.
Önce kur: https://ollama.com  ardından terminalde: `ollama pull llama3`

Görev: Bir paragraf metninde geçen, Lore Sözlüğü'nde KARŞILIĞI OLMAYAN
kurgusal/jargon terimleri bulmak (örn. "chidori", "rasengan", "bankai")
ve her biri için makul bir gerçek-dünya karşılığı önermek (örn. "çakı").
"""
from __future__ import annotations
import json
import logging
import os
import re

import httpx

from .. import config

logger = logging.getLogger("nicole.lore_detection")

# --- "Sıradan Türkçe kelime" filtresi -------------------------------------------------
# Küçük bir yerel model (llama3.2:3b) bazen "selam", "ben", "nasılsınız" gibi gayet
# normal kelimeleri de kurgusal terim sanıp flag'liyordu (özellikle Lore Sözlüğü boşken).
# Modelin çıktısını Python tarafında ayrıca süzüyoruz: yaygın Türkçe kelimeler ASLA flag'lenmez.
#  - `wordfreq` paketi kuruluysa (pip install wordfreq) Türkçe kelime sıklığına bakılır:
#    Zipf sıklığı bu eşiğin üstündeki kelimeler "yaygın" sayılır. Eşiği .env'deki
#    FLAG_COMMON_WORD_ZIPF ile değiştirebilirsin (yükselirse daha az kelime elenir).
#  - Kurulu değilse aşağıdaki küçük yerleşik liste (selamlaşma/zamir/bağlaç) kullanılır.
COMMON_WORD_ZIPF = float(os.getenv("FLAG_COMMON_WORD_ZIPF", "3.5") or 3.5)

try:  # opsiyonel bağımlılık
    from wordfreq import zipf_frequency as _zipf_frequency
except Exception:  # noqa: wordfreq yoksa yerleşik liste yeterli
    _zipf_frequency = None

_BUILTIN_COMMON_WORDS = {
    "selam", "selamlar", "merhaba", "merhabalar", "hey", "hi", "hello", "günaydın", "iyi", "akşamlar",
    "geceler", "arkadaş", "arkadaşlar", "dostlar", "nasıl", "nasılsın", "nasılsınız", "nasılsınızdır",
    "ben", "sen", "o", "biz", "siz", "onlar", "bu", "şu", "ve", "ile", "ama", "fakat", "ya", "da", "de",
    "evet", "hayır", "tamam", "peki", "tabii", "tabi", "belki", "lütfen", "teşekkürler", "sağol",
    "hoşgeldin", "hoşgeldiniz", "hoş", "geldin", "geldiniz", "görüşürüz", "hadi", "haydi", "bir", "iki",
    "üç", "çok", "az", "bugün", "yarın", "dün", "şimdi", "sonra", "önce", "burada", "orada", "neden",
    "niye", "kim", "ne", "nerede", "hangi", "kadar", "gibi", "için", "değil", "var", "yok", "benim",
    "senin", "bizim", "sizin", "onun", "adım", "ismim", "adı", "ismi",
}

_PUNCT = " .,!?;:\"()[]{}«»“”‘’…-–—"


def tr_lower(text: str) -> str:
    """Türkçe'ye uygun küçük harf (I->ı, İ->i). Python'un .lower()'ı İ'yi bozar."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def _zipf(word: str) -> float:
    if _zipf_frequency is None:
        return 0.0
    try:
        return float(_zipf_frequency(word, "tr"))
    except Exception:
        return 0.0


def _word_is_common(word: str) -> bool:
    w = tr_lower(word.strip(_PUNCT))
    if not w:
        return True
    base = re.split(r"['’]", w)[0]  # "Sasuke'nin" -> "sasuke"
    for candidate in {w, base}:
        if not candidate:
            continue
        if candidate in _BUILTIN_COMMON_WORDS or _zipf(candidate) >= COMMON_WORD_ZIPF:
            return True
    return False


def is_common_term(term: str) -> bool:
    """Terimi oluşturan TÜM kelimeler yaygın Türkçe kelimeyse True (örn. 'selam', 'nasılsınız')."""
    words = [w for w in re.split(r"[\s\-]+", term) if w]
    return bool(words) and all(_word_is_common(w) for w in words)


def filter_flags(flags: list, known_terms: list[str]) -> list[dict]:
    """
    Modelin döndürdüğü adayları süzer: boş/çok kısa olanlar, zaten Lore Sözlüğü'nde olanlar,
    tekrarlar ve yaygın Türkçe kelimeler elenir. Dönüş: [{"term","suggestion"}, ...]
    """
    known_lower = {tr_lower(t) for t in known_terms}
    seen: set[str] = set()
    result: list[dict] = []
    for f in flags:
        if not isinstance(f, dict):
            continue
        term = str(f.get("term") or "").strip()
        if len(term) < 3:
            continue
        key = tr_lower(term)
        if key in known_lower or key in seen:
            continue
        if is_common_term(term):
            continue
        seen.add(key)
        result.append({"term": term, "suggestion": str(f.get("suggestion") or "").strip()})
    return result


_SYSTEM_PROMPT = """\
Sen bir kurgu editörü asistanısın. Görevin: paragrafta geçen KURGUSAL / UYDURMA özel
terimleri bulmak — teknik/beceri/güç isimleri, uydurma eşya, yer ve karakter isimleri
(örn. Chidori, Rasengan, Tsukuyomi, Bankai, Kamehameha gibi manga/anime terimleri de dahil).
Bu terimler senin için ne kadar ünlü/tanıdık olursa olsun hepsini bul — "bilinen mi değil mi"
kararını SEN VERME, o karar senden sonra ayrıca kontrol edilecek.

KESİNLİKLE İŞARETLEME (bunlar normal Türkçe konuşmadır):
- Günlük Türkçe kelimeler: zamirler, fiiller, sıfatlar, bağlaçlar (ben, sen, bu, güzel, gitti, nasılsınız ...)
- Selamlaşma ve hitap kalıpları (selam, merhaba, hey, arkadaşlar, günaydın ...)
- Sıradan kişi ve yer isimleri (Ahmet, İstanbul, Naruto, Sasuke gibi)
- Cümlenin kendisini, birden fazla kelimelik sıradan bir ifadeyi
Emin değilsen İŞARETLEME. Çoğu paragrafta hiç terim yoktur; doğru cevap çoğu zaman boş listedir.

Her terime kısa, günlük dilde bir karşılık öner. SADECE JSON döndür, başka hiçbir
metin ekleme: {"flags": [{"term": "...", "suggestion": "..."}]}

Örnekler:
1) Paragraf: "Naruto Rasengan kullandı." -> {"flags": [{"term": "Rasengan", "suggestion": "top"}]}
2) Paragraf: "Sasuke Tsukuyomi'yi aktive etti." -> {"flags": [{"term": "Tsukuyomi", "suggestion": "rüya"}]}
3) Paragraf: "Ahmet İstanbul'a gitti." -> {"flags": []}
4) Paragraf: "Hey selam ben Adal." -> {"flags": []}
5) Paragraf: "Selam arkadaşlar nasılsınız?" -> {"flags": []}
6) Paragraf: "Bugün hava çok güzeldi, eve gidip yemek yaptım." -> {"flags": []}

Hiçbir terim bulamazsan: {"flags": []}
"""


async def detect_flags(paragraph_text: str, known_terms: list[str]) -> list[dict]:
    """
    Dönüş: [{"term": "chidori", "suggestion": "çakı"}, ...]
    Ollama'ya erişilemezse (kurulu değilse) boş liste döner ve hata loglanır —
    uygulamanın geri kalanı çalışmaya devam eder.

    NOT: Modele artık "bilinen terimler" listesi VERİLMİYOR — küçük modellerin
    (örn. llama3.2:3b) "hem terimi tanı hem bilinen listeyle karşılaştır" gibi
    çift görevi bir arada yapmakta tutarsız kaldığı gözlemlendi (bazı ünlü
    terimleri gereksiz yere flag'liyor, bazı daha az bilinenleri hiç bulamıyordu).
    Model artık SADECE "bu paragrafta hangi kurgusal terimler var" sorusuna
    odaklanıyor; "zaten biliniyor mu" filtrelemesi aşağıda Python tarafında
    (filter_flags içinde, known_terms ile karşılaştırarak) güvenilir şekilde yapılıyor.
    """
    user_prompt = f"Paragraf:\n{paragraph_text}"

    payload = {
        "model": config.OLLAMA_MODEL,
        "system": _SYSTEM_PROMPT,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,  # daha tutarlı/deterministik yanıt için düşük sıcaklık
            "num_predict": 200,  # çıktıyı JSON'a yetecek kadar kısa tut — model gereksiz
                                  # uzun/"düşünen" çıktı üretip vakit kaybetmesin
        },
        # Modeli bellekte daha uzun tut — Ollama'nın varsayılanı 5 dakikadır, bu
        # sürenin sonunda model bellekten atılır ve bir sonraki istekte yeniden
        # yüklenmesi gerekir (birkaç saniye sürebilir). Aralıklı kullanımda bu
        # "soğuk başlangıç" gecikmesi zaman aşımına neden olabiliyordu.
        "keep_alive": "30m",
    }

    try:
        # Zaman aşımı 120sn — CPU'da 8B parametreli bir modelin yanıt süresi,
        # özellikle GPU kullanılmıyorsa, uzun olabiliyor. Kalıcı olarak yavaşsa
        # (her seferinde 90sn+ sürüyorsa) asıl çözüm daha küçük bir model
        # kullanmaktır — bkz. .env.example'daki OLLAMA_MODEL notu.
        #
        # limits: keep-alive bağlantı havuzunu kapatıyoruz. Ollama'nın
        # OLLAMA_NUM_PARALLEL=1 (aynı anda tek istek) olması nedeniyle, önceki
        # bir isteğin bıraktığı "yarım kalmış"/bozuk bir bağlantı havuzda
        # kalıp sonraki isteği ConnectError ile başarısız kılabiliyordu — her
        # istekte kesinlikle taze bir TCP bağlantısı açılmasını zorluyoruz.
        limits = httpx.Limits(max_keepalive_connections=0, max_connections=5)
        async with httpx.AsyncClient(timeout=120.0, limits=limits) as client:
            resp = await client.post(f"{config.OLLAMA_BASE_URL}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # Ollama kapalı/kurulu değilse sessizce devam et
        # str(exc) bazı exception tiplerinde (özellikle timeout) boş dönebilir —
        # bu yüzden exception TİPİNİ de her zaman logluyoruz, "ulaşılamadı: "
        # (boş) gibi bilgisiz bir mesajla baş başa kalmayasın.
        logger.warning(
            "Ollama'ya ulaşılamadı, flag tespiti atlanıyor (%s): %s [url=%s]",
            type(exc).__name__, exc or "(mesaj yok)", f"{config.OLLAMA_BASE_URL}/api/generate",
        )
        return []

    raw = data.get("response", "{}")

    # TEŞHİS AMAÇLI: gönderilen bilinen terimler ve Ollama'nın ham yanıtını
    # terminale yazdır — "neden flag olmadı" sorusunu VSCode terminalinden
    # kendi başına cevaplayabilmen için (özellikle "rasengan zaten known_terms
    # listesinde mi" ya da "model boş flags mi döndürdü" ayrımını görürsün).
    logger.info(
        "Ollama flag taraması — ham yanıt: %s | post-filtre için bilinen terimler: %s",
        raw[:300],
        known_terms if known_terms else "(boş)",
    )

    try:
        parsed = json.loads(raw)
        flags = parsed.get("flags", []) if isinstance(parsed, dict) else []
        if not isinstance(flags, list):
            flags = []
    except json.JSONDecodeError:
        logger.warning("Ollama JSON döndürmedi, ham çıktı (ilk 500 karakter): %s", raw[:500])
        return []

    # Aday listesini süz: Lore Sözlüğü'ndekiler, tekrarlar ve yaygın Türkçe kelimeler elenir.
    result = filter_flags(flags, known_terms)
    if len(result) != len(flags):
        logger.info("Flag filtresi: %d aday -> %d flag (yaygın kelime / bilinen terim / tekrar elendi)",
                    len(flags), len(result))
    return result


def find_term_positions(paragraph_text: str, term: str) -> list[tuple[int, int]]:
    """Paragraf içinde bir terimin tüm geçtiği (start, end) pozisyonlarını bulur."""
    positions = []
    for m in re.finditer(re.escape(term), paragraph_text, flags=re.IGNORECASE):
        positions.append((m.start(), m.end()))
    return positions