"""
Nicole — AI Editör Sohbeti servisi
Google Gemini API'nin ücretsiz katmanını kullanır.
Ücretsiz API anahtarı: https://aistudio.google.com/apikey
"""
from __future__ import annotations
import asyncio
import json
import logging
import re

import httpx

from .. import config
from .http_client import new_client

logger = logging.getLogger("nicole.gemini")

# Gemini'ye gönderilecek hikâye metninin üst sınırı (karakter). Eskiden 8000'di ve
# metnin BAŞINDAN kesiyordu — hikâye uzayınca en son yazılanlar Editör'e hiç
# ulaşmıyordu. Artık sınır çok daha yüksek; aşılırsa BAŞ kısım atılır, SON kısım kalır.
MAX_CONTEXT_CHARS = 150_000


# Sunucu taraflı geçici hatalar (503 "çok talep var" gibi): aynı modeli kısa bekleyerek
# bir kez daha dener, olmazsa yedek modele geçer. 429/404'te beklemeden yedek modele geçer.
_SERVER_ERROR_CODES = {500, 502, 503, 504}
_SWITCH_MODEL_CODES = _SERVER_ERROR_CODES | {429, 404, 408}  # 408: model zamanında cevap vermedi
_REQUEST_TIMEOUT_SECONDS = 75.0
_SERVER_TRIES_PER_MODEL = 2
_RETRY_DELAY_SECONDS = 2.0

_EDITOR_SYSTEM_PROMPT = """\
Sen "Nicole Editör"sin: bir manga/roman yazarının yanındaki deneyimli, açık sözlü edebi
editör ve hikâye ortağı. Türkçe konuşursun.

DURUŞUN
- Yazarın her dediğini onaylamazsın. Görevin yazara yaranmak değil, hikâyeyi daha iyi
  yapmak. "Harika fikir!" gibi boş övgüyle başlama; bir şeyi beğendiysen NEDEN işe
  yaradığını somut söyle.
- Gerçek bir fikrin olsun ve net söyle: "Bence bu sahne zayıf çünkü...", "Ben olsam şunu
  yapardım...". Katılmadığında açıkça katılmadığını söyle, gerekçeni ver, alternatif sun.
- Zayıf noktaları aktif ara: olay örgüsü boşlukları, karakter motivasyonu tutarsızlığı,
  tempo, tekrar, klişe, dünya kuralı/lore çelişkisi, önceki bölümlerle uyumsuzluk.
  Metne dayan; mümkünse kısa bir alıntıyla göster.
- Yazar iyi bir argümanla karşı çıkarsa fikrini değiştir ve nedenini söyle; sadece ısrar
  ettiği için değiştirme. Son karar yazarındır: gerekçeli itirazını bir kez söyledikten
  sonra onun vizyonuna hizmet et.
- Sohbet et: işe yarayacaksa tek, düşündürücü bir soru sor. Her cevabın sonuna soru
  eklemek zorunda değilsin.
- Kısa ve net ol. Uzun listeler yerine en önemli 1-3 noktaya odaklan. Dolgu cümle yok.
- Metinde olmayan bir şeyi uydurma; göremiyorsan "metinde bunu göremiyorum" de.
- Paragraflardan bahsederken "[P3]" gibi etiket kullanma; "3. paragraf" de ya da kısa bir alıntıyla göster.
"""

_EDITS_OFF_PROMPT = """

DÜZENLEME İZNİ KAPALI
Metni doğrudan değiştiremezsin. Değişiklik önerilerini sohbet içinde yaz. Yazar sohbetteki
"metni doğrudan düzenleme izni" kutusunu açarsa değişiklikleri doğrudan uygulayabilirsin;
gerekirse bunu hatırlat.
"""

_EDITS_ON_PROMPT = """

DÜZENLEME İZNİ AÇIK
Yazar sana metni DOĞRUDAN düzenleme izni verdi. Hikâye metnindeki her paragraf [P<n>]
etiketiyle başlar (n = romandaki sırası: P1 ilk paragraf).
- SADECE yazar açıkça istediğinde ("şunu düzelt", "bu paragrafı yeniden yaz", "araya şunu
  ekle") veya senin önerini açıkça onayladığında düzenle. Fikir soruyorsa ya da
  tartışıyorsanız "edits" boş kalsın — tartışma ile düzenlemeyi karıştırma.
- Minimum değişiklik: yazarın üslubunu ve kelime seçimlerini koru, istenenin dışına dokunma.
- action değerleri: "replace" (paragrafın TÜM yeni metni new_text'te), "insert_after"
  (paragraph_id'li paragraftan sonra yeni paragraf, metni new_text'te), "delete" (paragrafı sil).
- paragraph_id olarak SADECE metindeki [P<n>] etiketindeki n sayısını kullan. new_text içine [P..]
  etiketi veya bölüm başlığı yazma.
- "reply" alanında neyi neden değiştirdiğini kısaca açıkla; yazar bunu sohbette okuyacak.
Çıktın JSON olmalı: {"reply": "...", "edits": [...]}
"""

_EDIT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "reply": {"type": "STRING"},
        "edits": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING", "enum": ["replace", "insert_after", "delete"]},
                    "paragraph_id": {"type": "INTEGER"},
                    "new_text": {"type": "STRING"},
                },
                "required": ["action", "paragraph_id"],
            },
        },
    },
    "required": ["reply", "edits"],
}


async def _post_with_retry(url: str, body: dict, attempts: int = 4):
    """
    SSL 'invalid session id' gibi aralıklı bağlantı hataları için basit bir
    yeniden deneme: her deneme taze bir istemci/bağlantı kullanır (new_client()
    her seferinde yeni oluşturuluyor), bu tek başına bazı SSL oturum
    çakışmalarını çözüyor. Son denemede de hata olursa exception yukarı fırlatılır.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with new_client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                # Anahtar URL'de değil başlıkta: httpx tam URL'yi INFO seviyesinde loglar.
                resp = await client.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPStatusError, httpx.TimeoutException):
            # HTTP hata kodları ve zaman aşımı aynı modelde yeniden denemeye değmez: eskiden zaman aşımı
            # 4 kez denenip dakikalarca bekletiyordu. Yukarıda (_generate_with_fallback) yedek modele geçilir.
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                logger.warning("Gemini isteği %d. denemede başarısız (%s), tekrar deneniyor...", attempt, exc)
                await asyncio.sleep(0.5)
    raise last_exc

def _model_chain(first: str | None = None) -> list[str]:
    """Ana model + yedek modeller (tekrarsız, sıralı). first verilirse zincir onunla başlar ("Hızlı" mod)."""
    chain = [first] if first else []
    if config.GEMINI_MODEL not in chain:
        chain.append(config.GEMINI_MODEL)
    for m in config.GEMINI_FALLBACK_MODELS:
        if m and m not in chain:
            chain.append(m)
    return chain


def _model_url(model: str) -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


def _http_error_info(exc: httpx.HTTPStatusError) -> tuple[int, str]:
    """Google'ın döndürdüğü gerçek hata mesajını çıkarır (JSON değilse ham metin)."""
    status = exc.response.status_code
    try:
        message = exc.response.json().get("error", {}).get("message", exc.response.text[:300])
    except Exception:
        message = exc.response.text[:300]
    return status, message


async def _generate_with_fallback(
    body: dict, first_model: str | None = None,
) -> tuple[dict | None, str | None, list[tuple[str, int, str]]]:
    """
    Modelleri sırayla dener. Dönüş: (yanıt, cevaplayan_model, hatalar[(model, http_kodu, mesaj)]).
    - 500/502/503/504: aynı modeli kısa beklemeyle bir kez daha dener, olmazsa sonraki modele geçer.
    - 429 (kota) / 404 (model yok): hemen sonraki modele geçer.
    - 400/401/403 gibi diğer kodlar: model değiştirmek çözmez, zinciri keser.
    HTTP olmayan hatalar (SSL, bağlantı) yukarı fırlatılır.
    """
    errors: list[tuple[str, int, str]] = []
    for model in _model_chain(first_model):
        url = _model_url(model)
        for attempt in range(1, _SERVER_TRIES_PER_MODEL + 1):
            try:
                data = await _post_with_retry(url, body)
                if errors:
                    logger.warning("Gemini: '%s' yanıt vermedi, yedek model '%s' kullanıldı (%s).",
                                   errors[0][0], model, errors)
                return data, model, errors
            except httpx.HTTPStatusError as exc:
                status, message = _http_error_info(exc)
                logger.error("Gemini API hatası (model=%s, HTTP %s): %s", model, status, message)
                if status in _SERVER_ERROR_CODES and attempt < _SERVER_TRIES_PER_MODEL:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                errors.append((model, status, message))
                break
            except httpx.TimeoutException:
                logger.error("Gemini %s %.0f sn içinde cevap vermedi — yedek modele geçiliyor.", model, _REQUEST_TIMEOUT_SECONDS)
                errors.append((model, 408, f"{_REQUEST_TIMEOUT_SECONDS:.0f} sn içinde cevap vermedi (Google tarafı yoğun)"))
                break
        if errors[-1][1] not in _SWITCH_MODEL_CODES:
            break
    return None, None, errors

async def generate_json(system_text: str, user_text: str, model: str) -> str:
    """Tek seferlik JSON üretimi (flag taraması için). Hata olursa exception fırlatır; çağıran karar verir."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "systemInstruction": {"parts": [{"text": system_text}]},
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 2048},
    }
    data = await _post_with_retry(_model_url(model), body, attempts=2)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _fit_context(story_context: str) -> str:
    """Çok uzun hikâyede BAŞ kısmı atar, EN SON kısmı (en güncel yazılanları) korur."""
    if len(story_context) <= MAX_CONTEXT_CHARS:
        return story_context
    tail = story_context[-MAX_CONTEXT_CHARS:]
    cut = tail.find("\n\n")  # paragrafın ortasından başlamasın
    if 0 <= cut < 2000:
        tail = tail[cut + 2:]
    return "[… hikâyenin başı uzunluk sınırı nedeniyle kısaltıldı; aşağıda EN SON kısım var …]\n\n" + tail


def _build_system_text(story_context: str | None, allow_edits: bool, lore_terms: list[str] | None) -> str:
    text = _EDITOR_SYSTEM_PROMPT + (_EDITS_ON_PROMPT if allow_edits else _EDITS_OFF_PROMPT)
    if lore_terms:
        text += "\n\n--- LORE SÖZLÜĞÜ (yazarın onayladığı özel terimler) ---\n" + ", ".join(lore_terms[:300])
    if story_context:
        text += f"\n\n--- HİKÂYE METNİ (bağlam) ---\n{_fit_context(story_context)}"
    return text


def _parse_structured(text: str) -> dict:
    """Düzenleme modunda dönen JSON'u ayrıştırır; bozuksa düz metin cevap olarak kullanılır."""
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {"reply": text, "edits": []}
    if not isinstance(obj, dict):
        return {"reply": text, "edits": []}
    edits = obj.get("edits") or []
    return {
        "reply": str(obj.get("reply") or "").strip() or "(boş yanıt)",
        "edits": [e for e in edits if isinstance(e, dict)],
    }


def _build_body(message: str, story_context: str | None, history: list[dict], allow_edits: bool,
                lore_terms: list[str] | None) -> dict:
    contents = []
    for h in history[-20:]:  # son 20 mesajla sınırla, token tasarrufu
        contents.append({"role": "user" if h["role"] == "user" else "model", "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    # NOT: Gemini 3.x ailesinde "temperature/top_p/top_k" parametreleri artık
    # yok sayılıyor, bu yüzden sadece geçerli olan maxOutputTokens'ı gönderiyoruz.
    # (Düşünen modellerde "düşünme" token'ları da bu sınıra dahildir; 1024 cevabı
    # yarıda kesebiliyordu, bu yüzden 4096.)
    generation_config: dict = {"maxOutputTokens": 4096}
    if allow_edits:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = _EDIT_SCHEMA

    return {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": _build_system_text(story_context, allow_edits, lore_terms)}]},
        "generationConfig": generation_config,
    }


class GeminiError(Exception):
    """Kullanıcıya gösterilecek açıklamayla birlikte Gemini hatası (akışlı sohbet)."""


def _stream_parts(sse_line: str) -> list[str]:
    """Akıştaki tek bir 'data: {...}' satırındaki cevap metinleri (düşünme parçaları hariç)."""
    if not sse_line.startswith("data:"):
        return []
    try:
        data = json.loads(sse_line[5:])
        parts = data["candidates"][0]["content"]["parts"]
    except (ValueError, KeyError, IndexError, TypeError):
        return []
    return [p["text"] for p in parts if p.get("text") and not p.get("thought")]


async def chat_stream(
    message: str,
    story_context: str | None,
    history: list[dict],
    lore_terms: list[str] | None = None,
    fast: bool = False,
):
    """
    Cevabı Gemini yazdıkça parça parça verir (async generator): ("delta", metin) ya da ("status", açıklama).
    "status": Google yoğun olduğu için yeniden deneme / yedek modele geçiş — ekranda neden beklendiği görünür.
    Böylece ekran cevabı akarak gösterir
    ve sesli okuma ilk cümleden başlar — kalite aynı: aynı model, aynı istek, sadece beklemeden okunuyor.
    Model seçimi/yedek kuralları chat_full ile aynı. Hiç cevap alınamazsa GeminiError fırlatır.
    (Düzenleme izni açıkken cevap JSON olduğu için bu yol kullanılmaz; chat_full kullanılır.)
    """
    if not config.GEMINI_API_KEY:
        raise GeminiError("Gemini API anahtarı tanımlı değil. .env dosyasına GEMINI_API_KEY ekleyip sunucuyu yeniden başlat.")
    body = _build_body(message, story_context, history, False, lore_terms)
    errors: list[tuple[str, int, str]] = []
    for model in _model_chain(config.GEMINI_FAST_MODEL if fast else None):
        if errors:
            yield ("status", f"{errors[-1][0]} cevap veremedi — {model} deneniyor")
        url = _model_url(model).replace(":generateContent", ":streamGenerateContent") + "?alt=sse"
        for attempt in range(1, _SERVER_TRIES_PER_MODEL + 1):
            started = False
            try:
                async with new_client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                    async with client.stream("POST", url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}) as resp:
                        if resp.status_code != 200:
                            await resp.aread()
                            resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            for text in _stream_parts(line):
                                started = True
                                yield ("delta", text)
                if started:
                    if errors:
                        logger.warning("Gemini: yedek model '%s' kullanıldı (%s).", model, errors)
                    return
                errors.append((model, 0, "boş cevap (güvenlik filtresi ya da token sınırı olabilir)"))
                break
            except httpx.HTTPStatusError as exc:
                status, msg = _http_error_info(exc)
                logger.error("Gemini API hatası (model=%s, HTTP %s): %s", model, status, msg)
                if status in _SERVER_ERROR_CODES and attempt < _SERVER_TRIES_PER_MODEL:
                    yield ("status", "Google şu an yoğun — tekrar deneniyor")
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                errors.append((model, status, msg))
                break
            except httpx.TimeoutException:
                if started:
                    raise GeminiError("Cevap yarıda kesildi (Google zamanında devam etmedi).")
                logger.error("Gemini %s %.0f sn içinde cevap vermedi — yedek modele geçiliyor.", model, _REQUEST_TIMEOUT_SECONDS)
                errors.append((model, 408, f"{_REQUEST_TIMEOUT_SECONDS:.0f} sn içinde cevap vermedi (Google tarafı yoğun)"))
                break
            except Exception as exc:
                if started:
                    raise GeminiError(f"Cevap yarıda kesildi ({type(exc).__name__}).")
                raise GeminiError(f"Gemini API'ye ulaşılamadı ({type(exc).__name__}: {exc}). İnternet bağlantısını kontrol et.")
        if errors and errors[-1][1] not in _SWITCH_MODEL_CODES:
            break
    model, status, msg = errors[0]
    tried = ", ".join(f"{m} ({st})" for m, st, _ in errors)
    raise GeminiError(f"Gemini API hatası (HTTP {status}): {msg} — Google tarafı yoğun olabilir; "
                      f"Ayarlar > Model > Hızlı'yı dene. Denenen modeller: {tried}.")


async def chat_full(
    message: str,
    story_context: str | None,
    history: list[dict],
    allow_edits: bool = False,
    lore_terms: list[str] | None = None,
    fast: bool = False,
) -> dict:
    """
    fast=True: zincir GEMINI_FAST_MODEL ile başlar (Ayarlar > Editör modeli > Hızlı).
    Dönüş: {"reply": str, "edits": [{"action","paragraph_id","new_text"}, ...], "ok": bool,
            "model": cevabı veren model, "usage": Google'ın token sayımı (girdi/düşünme/çıktı)}

    history: [{"role": "user"|"assistant", "content": "..."}] — DİKKAT: geçerli
    mesajı İÇERMEMELİ (mesaj ayrıca eklenir), yoksa Gemini'ye iki kez gider.
    story_context: hikâye metni (düzenleme izni açıksa paragraflar [P<id>] etiketli).
    """
    if not config.GEMINI_API_KEY:
        return {
            "ok": False, "edits": [],
            "reply": (
                "Gemini API anahtarı tanımlı değil. `.env` dosyasına GEMINI_API_KEY ekleyip "
                "sunucuyu yeniden başlat (https://aistudio.google.com/apikey adresinden ücretsiz alınır)."
            ),
        }

    body = _build_body(message, story_context, history, allow_edits, lore_terms)

    def fail(text: str) -> dict:
        return {"ok": False, "edits": [], "reply": text}

    try:
        data, used_model, errors = await _generate_with_fallback(body, config.GEMINI_FAST_MODEL if fast else None)
    except Exception as exc:
        logger.error("Gemini API isteği başarısız (%s): %s", type(exc).__name__, exc)
        detail = str(exc) or "(ayrıntı yok)"
        if "SSL" in detail or "ssl" in type(exc).__name__.lower():
            return fail(
                f"SSL bağlantı hatası ({detail}) — 4 deneme sonrasında da başarısız oldu. "
                "Bu genelde bir antivirüs/güvenlik yazılımının HTTPS trafiğini incelemesinden "
                "(Kaspersky, ESET, Avast, Bitdefender vb. — 'Web Koruması' / 'HTTPS Tarama' "
                "özelliği) kaynaklanır ve genellikle ARALIKLI değil, SÜREKLİ bir sorundur. "
                "Çözüm: antivirüsündeki HTTPS/SSL tarama özelliğini generativelanguage.googleapis.com "
                "için istisna yap, ya da (en kesin teşhis için) o yazılımı GEÇİCİ olarak tamamen "
                "kapatıp bu mesajı tekrar göndermeyi dene."
            )
        return fail(
            f"Gemini API'ye ulaşılamadı ({type(exc).__name__}: {detail}). Kontrol et: "
            "1) .env dosyası proje kökünde mi ve GEMINI_API_KEY dolu mu, 2) sunucuyu .env'i "
            "düzenledikten sonra yeniden başlattın mı, 3) internet/VPN/antivirüs/güvenlik duvarı "
            "generativelanguage.googleapis.com'u engelliyor mu."
        )

    if data is None:
        # Tüm modeller başarısız. İlk (ana model) hatayı göster; denenen modelleri de listele.
        model, status, google_message = errors[0]
        if status == 429:
            hint = "Ücretsiz katmanın dakikalık/günlük istek limitine takıldın — bir süre bekleyip tekrar dene."
        elif status in (401, 403):
            hint = "API anahtarı geçersiz, kısıtlı veya bu modele izni yok — aistudio.google.com/apikey üzerinden anahtarı kontrol et."
        elif status == 404:
            hint = f"'{model}' modeli bulunamadı — .env dosyasında GEMINI_MODEL adını kontrol et."
        elif status in _SERVER_ERROR_CODES or status == 408:
            hint = "Google'ın sunucuları o model için yoğun (geçici). Birkaç dakika sonra tekrar dene ya da Ayarlar > Model > Hızlı'yı seç."
        else:
            hint = "Ayrıntı için Google'ın döndürdüğü mesaja bak."
        tried = ", ".join(f"{m} ({st})" for m, st, _ in errors)
        extra = f" Denenen modeller: {tried}." if len(errors) > 1 else ""
        return fail(f"Gemini API hatası (HTTP {status}): {google_message} — {hint}{extra}")

    try:
        candidate = data["candidates"][0]
        parts = candidate["content"]["parts"]
        # "düşünme" parçalarını (thought: true) atla, sadece asıl cevabı birleştir
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        logger.error("Beklenmeyen Gemini yanıt formatı: %s", data)
        return fail("Gemini'den beklenmeyen bir yanıt geldi.")

    if not text.strip():
        return fail("Gemini boş yanıt döndürdü (güvenlik filtresi ya da token sınırı olabilir) — mesajı biraz değiştirip tekrar dene.")

    usage = data.get("usageMetadata") or {}
    logger.info(
        "Gemini kullanım (%s): girdi=%s token, düşünme=%s, çıktı=%s",
        used_model, usage.get("promptTokenCount"), usage.get("thoughtsTokenCount"), usage.get("candidatesTokenCount"),
    )
    meta = {"model": used_model, "usage": usage}
    if allow_edits:
        parsed = _parse_structured(text)
        return {"ok": True, **parsed, **meta}
    return {"ok": True, "reply": text, "edits": [], **meta}

