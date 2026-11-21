"""
Nicole — Sesli okuma (Editör cevapları)

Ses sunucuda üretilir ve tarayıcıya mp3 olarak gider: böylece Brave/Chrome gibi doğal Türkçe sesi
olmayan tarayıcılarda da aynı ses ("Emel") çalışır. edge-tts, Microsoft Edge'in "Sesli oku"
özelliğinin kullandığı ücretsiz servistir — API anahtarı istemez ama internet ister. Servise
ulaşılamazsa ön yüz otomatik olarak tarayıcının kendi sesine düşer.
"""
from __future__ import annotations
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger("nicole.tts")
router = APIRouter(prefix="/api/tts", tags=["tts"])

DEFAULT_VOICE = "tr-TR-EmelNeural"
_voices_cache: list[dict] | None = None


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
async def speak(payload: TTSRequest):
    import edge_tts
    rate, pitch = edge_params(payload.rate, payload.pitch)
    try:
        communicate = edge_tts.Communicate(payload.text, payload.voice, rate=rate, pitch=pitch)
        audio = b"".join([chunk["data"] async for chunk in communicate.stream() if chunk["type"] == "audio"])
    except Exception as exc:
        logger.warning("Sesli okuma başarısız (%s: %s)", type(exc).__name__, exc)
        raise HTTPException(502, f"Ses üretilemedi: {exc}")
    if not audio:
        raise HTTPException(502, "Ses üretilemedi (boş yanıt)")
    return Response(audio, media_type="audio/mpeg")
