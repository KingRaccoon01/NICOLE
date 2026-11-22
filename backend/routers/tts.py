"""
Nicole — Sesli okuma (Editör cevapları)

Ses sunucuda üretilir ve tarayıcıya mp3 olarak gider: böylece Brave/Chrome gibi doğal Türkçe sesi
olmayan tarayıcılarda da aynı ses ("Emel") çalışır. edge-tts, Microsoft Edge'in "Sesli oku"
özelliğinin kullandığı ücretsiz servistir — API anahtarı istemez ama internet ister. Servise
ulaşılamazsa ön yüz otomatik olarak tarayıcının kendi sesine düşer.
"""
from __future__ import annotations
import asyncio
import itertools
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("nicole.tts")
router = APIRouter(prefix="/api/tts", tags=["tts"])

DEFAULT_VOICE = "tr-TR-EmelNeural"
_voices_cache: list[dict] | None = None
# Hazırlanan okumalar: POST metni bırakır (üretim hemen başlar), tarayıcının <audio> öğesi GET ile akış
# olarak çeker. (Ses çalar POST yapamaz; tamamını beklemek kısa cevapta 7, uzun cevapta 14 sn sürüyordu.)
_pending: dict[str, "_Job"] = {}
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


class _Job:
    """
    Bir okuma: metin gelir gelmez arka planda üretilmeye başlar (tarayıcının ses isteğini beklemez —
    o gidiş-dönüş ve <audio> hazırlığı üretimle üst üste biner). Parçalar saklanır: tarayıcı aynı adresi
    tekrar isterse baştan, yeniden üretmeden verilir.
    """

    def __init__(self, payload: TTSRequest):
        self.chunks: list[bytes] = []
        self.done = False
        self.error: Exception | None = None
        self.cond = asyncio.Condition()
        self.task = asyncio.create_task(self._run(payload))

    async def _run(self, payload: TTSRequest) -> None:
        import edge_tts
        rate, pitch = edge_params(payload.rate, payload.pitch)
        try:
            for attempt in range(3):  # Microsoft ara sıra (çoğu zaman ilk istekte) hiç ses göndermiyor; tekrarı çalışıyor
                try:
                    async for chunk in edge_tts.Communicate(payload.text, payload.voice, rate=rate, pitch=pitch).stream():
                        if chunk["type"] == "audio":
                            async with self.cond:
                                self.chunks.append(chunk["data"])
                                self.cond.notify_all()
                    break
                except edge_tts.exceptions.NoAudioReceived:
                    if self.chunks or attempt == 2:  # ses başladıysa tekrar deneme: cümle iki kez okunurdu
                        raise
                    logger.info("Sunucu ses göndermedi, tekrar deneniyor (%d)", attempt + 1)
        except Exception as exc:
            self.error = exc
            logger.warning("Sesli okuma başarısız (%s: %s)", type(exc).__name__, exc)
        finally:
            async with self.cond:
                self.done = True
                self.cond.notify_all()

    async def first_ready(self) -> bool:
        async with self.cond:
            await self.cond.wait_for(lambda: self.chunks or self.done)
        return bool(self.chunks)

    async def iterate(self):
        sent = 0
        while True:
            async with self.cond:
                await self.cond.wait_for(lambda: sent < len(self.chunks) or self.done)
                new, finished = self.chunks[sent:], self.done
            sent += len(new)
            for data in new:
                yield data
            if finished and sent >= len(self.chunks):
                return


@router.post("")
async def prepare(payload: TTSRequest):
    """Okunacak metni bırakır ve üretimi hemen başlatır; dönen adres <audio src> olarak kullanılır."""
    while len(_pending) >= _MAX_PENDING:  # hiç çalınmayanlar birikmesin
        _pending.pop(next(iter(_pending))).task.cancel()
    token = str(next(_ids))
    _pending[token] = _Job(payload)
    return {"url": f"/api/tts/stream/{token}"}


@router.get("/stream/{token}")
async def stream(token: str):
    job = _pending.get(token)  # silinmez: tarayıcı aynı sesi tekrar isterse saklanan parçalar verilir
    if job is None:
        raise HTTPException(404, "Bu okuma bulunamadı (süresi geçti)")
    # İlk parçayı bekle: hata (internet yok, geçersiz ses) olursa 502 dönsün, ön yüz tarayıcı sesine geçsin
    if not await job.first_ready():
        raise HTTPException(502, f"Ses üretilemedi: {job.error or 'boş yanıt'}")
    return StreamingResponse(job.iterate(), media_type="audio/mpeg")
