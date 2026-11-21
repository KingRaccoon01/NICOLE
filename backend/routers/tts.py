"""
Nicole — Sesli okuma (Editör cevapları)

Ses sunucuda üretilir ve tarayıcıya mp3 olarak gider: böylece Brave/Chrome gibi doğal Türkçe sesi
olmayan tarayıcılarda da aynı ses ("Emel") çalışır. edge-tts, Microsoft Edge'in "Sesli oku"
özelliğinin kullandığı ücretsiz servistir — API anahtarı istemez ama internet ister. Servise
ulaşılamazsa ön yüz otomatik olarak tarayıcının kendi sesine düşer.
"""
from __future__ import annotations
import itertools
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("nicole.tts")
router = APIRouter(prefix="/api/tts", tags=["tts"])

DEFAULT_VOICE = "tr-TR-EmelNeural"
_voices_cache: list[dict] | None = None
# Hazırlanan okumalar: POST metni bırakır, tarayıcının <audio> öğesi GET ile akış olarak çeker.
# (Ses çalar POST yapamaz; tamamını beklemek kısa bir cevapta 7, uzun bir cevapta 14 sn sürüyordu.)
_pending: dict[str, "TTSRequest"] = {}
_ids = itertools.count(1)
_MAX_PENDING = 20


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    voice: str = DEFAULT_VOICE
    rate: float = Field(1.0, ge=0.5, le=2.0)   # 1.0 = normal hız
    pitch: float = Field(1.0, ge=0.5, le=2.0)  # 1.0 = normal ton


def edge_params(rate: float, pitch: float) -> tuple[str, str]:
    """Arayüzdeki 1.0 tabanlı değerleri edge-tts biçimine çevirir: 1.2 -> '+20%' / '+10Hz'."""
    return f"{round((rate - 1) * 100):+d}%", f"{round((pitch - 1) * 50):+d}Hz"


@router.get("/voices")
async def voices():
    """Türkçe sesler + Türkçe konuşabilen çok dilli sesler (Ava, Emma, Vivienne...)."""
    global _voices_cache
    if _voices_cache is None:
        import edge_tts
        try:
            all_voices = await edge_tts.list_voices()
        except Exception as exc:
            raise HTTPException(502, f"Ses listesi alınamadı (internet?): {exc}")
        _voices_cache = [
            {"name": v["ShortName"], "gender": v.get("Gender", ""), "locale": v["Locale"]}
            for v in all_voices
            if v["Locale"].startswith("tr") or "Multilingual" in v["ShortName"]
        ]
        _voices_cache.sort(key=lambda v: (not v["locale"].startswith("tr"), v["gender"] != "Female", v["name"]))
    return {"default": DEFAULT_VOICE, "voices": _voices_cache}


@router.post("")
async def prepare(payload: TTSRequest):
    """Okunacak metni bırakır; dönen adres <audio src> olarak kullanılır ve ses akış halinde gelir."""
    while len(_pending) >= _MAX_PENDING:  # hiç çalınmayanlar birikmesin
        _pending.pop(next(iter(_pending)))
    token = str(next(_ids))
    _pending[token] = payload
    return {"url": f"/api/tts/stream/{token}"}


@router.get("/stream/{token}")
async def stream(token: str):
    payload = _pending.get(token)  # silinmez: tarayıcı aynı sesi tekrar isterse yeniden üretilir (en eski 20 tutulur)
    if payload is None:
        raise HTTPException(404, "Bu okuma bulunamadı (süresi geçti)")
    import edge_tts
    rate, pitch = edge_params(payload.rate, payload.pitch)
    chunks = edge_tts.Communicate(payload.text, payload.voice, rate=rate, pitch=pitch).stream()
    # İlk parçayı burada bekle: hata (internet yok, geçersiz ses) olursa 502 dönsün, ön yüz tarayıcı sesine geçsin
    try:
        first = None
        async for chunk in chunks:
            if chunk["type"] == "audio":
                first = chunk["data"]
                break
    except Exception as exc:
        logger.warning("Sesli okuma başarısız (%s: %s)", type(exc).__name__, exc)
        raise HTTPException(502, f"Ses üretilemedi: {exc}")
    if first is None:
        raise HTTPException(502, "Ses üretilemedi (boş yanıt)")

    async def rest():
        yield first
        try:
            async for chunk in chunks:
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as exc:  # yarıda kesilirse o ana kadarki kısım çalınmış olur
            logger.warning("Sesli okuma yarıda kesildi (%s: %s)", type(exc).__name__, exc)

    return StreamingResponse(rest(), media_type="audio/mpeg")
