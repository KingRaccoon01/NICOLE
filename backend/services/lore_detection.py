"""
Nicole — Lore/Flag tespit servisi
Ollama üzerinden yerel llama3 modelini kullanır: tamamen ücretsiz, internet gerektirmez.
Önce kur: https://ollama.com  ardından terminalde: `ollama pull llama3`

Görev: Whisper'ın yazıya döktüğü paragrafta yazarın kontrol etmesi gereken kelimeleri bulmak:
  1) Yanlış duyulmuş kelimeler -> öneri: bağlama göre yazarın büyük ihtimalle söylediği kelime
     (örn. "falız" -> "filiz").
  2) Lore Sözlüğü'nde olmayan yeni kurgusal adlar -> öneri: sözlükteki bir terimin yanlış
     duyulmuş hali ise o terimin doğru yazımı, değilse terimin kendisi (yazar onaylar).
"""
from __future__ import annotations
import difflib
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


def align_to_text(term: str, text: str) -> str:
    """
    Model terimi bazen metindekinden farklı yazar ("falızların" yerine "faliz") ve o zaman metinde
    bulunamayıp flag hiç oluşmaz. Metinde aynen yoksa, metindeki en benzer kelimeye eşlenir.
    """
    if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE):
        return term
    t = tr_lower(term)

    def score(word: str) -> float:
        w = tr_lower(word)
        # ikinci ölçü kelimenin kökünü (ilk len(t) harf) karşılaştırır: "faliz" ~ "falız|ların"
        return max(difflib.SequenceMatcher(None, t, w).ratio(), difflib.SequenceMatcher(None, t, w[: len(t)]).ratio())

    words = re.findall(r"\w+(?:['’]\w+)?", text)
    best = max(words, key=score, default=None)
    return best if best is not None and score(best) >= 0.7 else term


def is_common_term(term: str) -> bool:
    """Terimi oluşturan TÜM kelimeler yaygın Türkçe kelimeyse True (örn. 'selam', 'nasılsınız')."""
    words = [w for w in re.split(r"[\s\-]+", term) if w]
    return bool(words) and all(_word_is_common(w) for w in words)


def filter_flags(flags: list, known_terms: list[str], uncertain_words: list[str] | None = None) -> list[dict]:
    """
    Modelin döndürdüğü adayları süzer: boş/çok kısa olanlar, zaten Lore Sözlüğü'nde olanlar,
    tekrarlar ve yaygın Türkçe kelimeler elenir. Dönüş: [{"term","suggestion"}, ...]

    İSTİSNA: Whisper'ın emin olmadığı (uncertain_words) yaygın kelimeler elenmez — yanlış
    duyulan kelime çoğu zaman gerçek bir kelimedir ("filiz" yerine "faiz" gibi).
    """
    known_lower = {tr_lower(t) for t in known_terms}
    uncertain_lower = {tr_lower(w) for w in (uncertain_words or [])}
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
        suggestion = str(f.get("suggestion") or "").strip()
        if tr_lower(suggestion) == key:
            suggestion = ""  # öneri terimin aynısıysa "öneriyi kabul et" anlamsız; "orijinali kabul et" yeter
        # Yaygın kelime: sadece Whisper emin değilse VE model gerçekten farklı bir kelime öneriyorsa kalır
        # (yoksa model sıradan "mektup" gibi kelimeleri öneri vermeden işaretleyebiliyor).
        if is_common_term(term) and not (key in uncertain_lower and suggestion):
            continue
        seen.add(key)
        result.append({"term": term, "suggestion": suggestion})
    return result


_SYSTEM_PROMPT = """\
Sen bir roman yazarının yardımcısısın. Yazar romanını SESLE dikte ediyor; konuşma tanıma (Whisper)
bazı kelimeleri yanlış duyabiliyor. Görevin paragrafta yazarın kontrol etmesi gereken kelimeleri bulmak.

İKİ TÜR KELİME ARA:
1) YANLIŞ DUYULMUŞ KELİME: cümlede anlamsız duran, bağlama uymayan ya da Türkçede olmayan kelime.
   "suggestion" = cümlenin anlamına göre yazarın büyük ihtimalle söylediği kelime (aynı ekleriyle).
   Öneri HER ZAMAN TÜRKÇE olmalı — asla İngilizce çeviri yazma.
2) YENİ KURGUSAL AD: karakter, yer, teknik, büyü, eşya adı gibi uydurma özel adlar.
   LORE SÖZLÜĞÜ'ndeki bir terime benziyorsa (yanlış duyulmuş hali) "suggestion" = o terimin doğru
   yazımı. Benzemiyorsa "suggestion" = terimin kendisi.

KURALLAR:
- "term" metinde GEÇTİĞİ HALİYLE, harfi harfine aynı olmalı (tek kelime ya da kısa ad).
- Bağlama uyan normal Türkçe kelimeleri, selamlaşmaları, yaygın kişi/yer adlarını (Ahmet, İstanbul) İŞARETLEME.
- Lore Sözlüğü'nde AYNEN yazılı terimleri İŞARETLEME.
- "Whisper'ın emin olmadığı kelimeler" verilirse onlara özellikle dikkat et — ama bağlama uyuyorsa işaretleme.
- Emin değilsen işaretleme. Çoğu paragrafta hiçbir şey yoktur; doğru cevap çoğu zaman boş listedir.

SADECE JSON döndür: {"flags": [{"term": "...", "suggestion": "..."}]}

Örnekler:
1) Paragraf: "Bahçenin köşesinde küçük bir falız yeşermişti." -> {"flags": [{"term": "falız", "suggestion": "filiz"}]}
2) Lore: Rasengan | Paragraf: "Naruto ra sengan ile saldırdı." -> {"flags": [{"term": "ra sengan", "suggestion": "Rasengan"}]}
3) Lore: (boş) | Paragraf: "Kael, Vorthan kılıcını kınından çekti." -> {"flags": [{"term": "Vorthan", "suggestion": "Vorthan"}]}
4) Paragraf: "Akşam yemeğinden sonra parka yürüdük." -> {"flags": []}
5) Paragraf: "Kapıyı açtığında içerisi zifiri karanlıktı ama bir mum yandı." -> {"flags": []}
"""

_MAX_LORE_IN_PROMPT = 200  # küçük modelin bağlamını boğmasın


def _build_user_prompt(paragraph_text: str, known_terms: list[str], uncertain_words: list[str], context: str | None) -> str:
    parts = []
    if known_terms:
        parts.append("LORE SÖZLÜĞÜ (doğru yazımlar): " + ", ".join(known_terms[:_MAX_LORE_IN_PROMPT]))
    if context:
        parts.append(f"Önceki metin (sadece bağlam, buradan işaretleme yapma):\n{context[-1500:]}")
    if uncertain_words:
        parts.append("Whisper'ın emin olmadığı kelimeler: " + ", ".join(uncertain_words))
    parts.append(f"Paragraf:\n{paragraph_text}")
    return "\n\n".join(parts)


async def detect_flags(
    paragraph_text: str,
    known_terms: list[str],
    uncertain_words: list[str] | None = None,
    context: str | None = None,
    engine: str | None = None,
) -> list[dict]:
    """
    Dönüş: [{"term": "falız", "suggestion": "filiz"}, ...] — Ollama'ya ulaşılamazsa None.
    uncertain_words: Whisper'ın düşük eminlikle yazdığı kelimeler (stt_service.uncertain_words).
    context: aynı bölümün önceki metni — "en mantıklı kelime" ancak bağlamla bulunur.
    engine: "ollama" | "gemini" (arayüzdeki ayar); None ise .env'deki FLAG_ENGINE.

    Lore terimleri modele "doğru yazımlar" olarak verilir (yanlış duyulmuş bir terime doğru
    yazımı önerebilsin diye). "Zaten sözlükte mi" elemesi yine Python tarafında, filter_flags
    içinde yapılır — küçük modeller bu karşılaştırmada tutarsız kalabiliyor.
    """
    user_prompt = _build_user_prompt(paragraph_text, known_terms, uncertain_words or [], context)

    if (engine or config.FLAG_ENGINE) == "gemini" and config.GEMINI_API_KEY:
        from . import gemini_service  # döngüsel import olmasın diye burada
        try:
            raw = await gemini_service.generate_json(_SYSTEM_PROMPT, user_prompt, config.FLAG_GEMINI_MODEL)
            logger.info("Flag taraması Gemini (%s) ile yapıldı.", config.FLAG_GEMINI_MODEL)
            return _parse_and_filter(raw, paragraph_text, known_terms, uncertain_words)
        except Exception as exc:
            logger.warning("Gemini flag taraması başarısız (%s: %s) — Ollama ile taranıyor.", type(exc).__name__, exc)

    payload = {
        "model": config.OLLAMA_MODEL,
        "system": _SYSTEM_PROMPT,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,  # daha tutarlı/deterministik yanıt için düşük sıcaklık
            "num_predict": 400,  # çıktıyı JSON'a yetecek kadar kısa tut — model gereksiz
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
        return None

    return _parse_and_filter(data.get("response", "{}"), paragraph_text, known_terms, uncertain_words)


def _parse_and_filter(raw: str, paragraph_text: str, known_terms: list[str], uncertain_words: list[str] | None) -> list[dict]:
    """Modelin JSON cevabını ayrıştırır, terimleri metne hizalar ve süzer (Ollama ve Gemini ortak)."""

    # TEŞHİS AMAÇLI: gönderilen bilinen terimler ve Ollama'nın ham yanıtını
    # terminale yazdır — "neden flag olmadı" sorusunu VSCode terminalinden
    # kendi başına cevaplayabilmen için (özellikle "rasengan zaten known_terms
    # listesinde mi" ya da "model boş flags mi döndürdü" ayrımını görürsün).
    logger.info(
        "Flag taraması — ham yanıt: %s | emin olunmayan kelimeler: %s | bilinen terim sayısı: %d",
        raw[:300],
        uncertain_words or "(yok)",
        len(known_terms),
    )

    try:
        parsed = json.loads(raw)
        flags = parsed.get("flags", []) if isinstance(parsed, dict) else []
        if not isinstance(flags, list):
            flags = []
    except json.JSONDecodeError:
        logger.warning("Model JSON döndürmedi, ham çıktı (ilk 500 karakter): %s", raw[:500])
        return []

    for f in flags:
        if isinstance(f, dict) and f.get("term"):
            f["term"] = align_to_text(str(f["term"]).strip(), paragraph_text)
    # Aday listesini süz: Lore Sözlüğü'ndekiler, tekrarlar ve yaygın Türkçe kelimeler elenir.
    result = filter_flags(flags, known_terms, uncertain_words)
    if len(result) != len(flags):
        logger.info("Flag filtresi: %d aday -> %d flag (yaygın kelime / bilinen terim / tekrar elendi)",
                    len(flags), len(result))
    return result


def find_term_positions(paragraph_text: str, term: str) -> list[tuple[int, int]]:
    """Paragraf içinde bir terimin tüm geçtiği (start, end) pozisyonlarını bulur — sadece tam kelime
    olarak ("ali" terimi "kalibre" içinde eşleşmez). Ek almış hali ("Gaara'nın") eşleşir."""
    positions = []
    for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", paragraph_text, flags=re.IGNORECASE):
        positions.append((m.start(), m.end()))
    return positions