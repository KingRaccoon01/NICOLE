"""
Nicole — Genel amaçlı ses-metin dönüşümü

Dikte akışındaki (dictation_ws.py) WebSocket'ten farklı olarak, bu uç nokta
flag/paragraf sistemine hiç dokunmaz — sadece ham sesi metne çevirip döner.
Şu an tek kullanım yeri: Nicole Editör sohbetinde mikrofonla mesaj yazmak.

project_id verilirse, o romanın Lore Sözlüğü'ndeki terimler (ve .env'deki
WHISPER_VOCABULARY_HINT) Whisper'a "bu kelimeler geçebilir" ipucu olarak verilir —
özel isimler (örn. karakter adları) böylece daha doğru tanınır.
"""
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from sqlalchemy import select

from .. import config
from ..database import AsyncSessionLocal, LoreEntry
from ..services import stt_service
from ..services.stt_service import transcribe_audio_chunk

router = APIRouter(prefix="/api", tags=["transcribe"])
logger = logging.getLogger("nicole.transcribe")

# Teşhis: son Editör mikrofonu kaydı (her seferinde üzerine yazılır, sadece bu bilgisayarda kalır).
LAST_AUDIO = Path(tempfile.gettempdir()) / "nicole_son_editor_kaydi.webm"
last_info: dict = {}


def _audio_stats(path: Path) -> dict:
    """Süre ve ses seviyesi: kayıtta konuşma var mı, mikrofon sessiz mi?"""
    import numpy as np
    from faster_whisper.audio import decode_audio
    samples = decode_audio(str(path))
    if len(samples) == 0:
        return {"sure_sn": 0}
    frame = 1600  # 0.1 sn
    levels = [float(np.sqrt(np.mean(samples[i:i + frame] ** 2))) for i in range(0, len(samples) - frame + 1, frame)] or [0.0]
    first_loud = next((i / 10 for i, v in enumerate(levels) if v > 0.02), None)
    return {
        "sure_sn": round(len(samples) / 16000, 2),
        "ortalama_seviye": round(float(np.sqrt(np.mean(samples ** 2))), 4),
        "en_yuksek_seviye": round(max(levels), 4),
        "ilk_ses_sn": first_loud,  # konuşmanın kaçıncı saniyede başladığı (None: hiç yüksek ses yok)
    }


async def _project_vocabulary_hint(project_id: int | None) -> str:
    parts = []
    if config.WHISPER_VOCABULARY_HINT.strip():
        parts.append(config.WHISPER_VOCABULARY_HINT.strip())
    if project_id:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(LoreEntry.term).where(LoreEntry.project_id == project_id))
            terms = [row[0] for row in result.all()]
        if terms:
            parts.append(", ".join(terms))
    return ". ".join(parts)


@router.post("/transcribe")
async def transcribe_only(
    file: UploadFile = File(...), project_id: int | None = Form(None), device: str = Form(""),
):
    """Ham ses dosyasını SADECE metne çevirir — kayıt/flag oluşturmaz."""
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(400, "Ses verisi boş")
    try:
        hint = await _project_vocabulary_hint(project_id)
        text = await transcribe_audio_chunk(audio_bytes, vocabulary_hint=hint)
    except Exception as exc:
        raise HTTPException(500, f"Ses metne çevrilemedi: {exc}")
    try:
        LAST_AUDIO.write_bytes(audio_bytes)
        last_info.clear()
        last_info.update({"cihaz": device, "metin": text, "dosya": str(LAST_AUDIO), **_audio_stats(LAST_AUDIO)})
        logger.info("Editör mikrofonu: %s", last_info)
    except Exception:
        logger.exception("Teşhis bilgisi kaydedilemedi")
    return {"text": text}


@router.get("/transcribe/last")
async def transcribe_last():
    """Teşhis: son Editör mikrofonu kaydının bilgileri (cihaz, süre, ses seviyesi, çıkan metin)."""
    return last_info


@router.get("/transcribe/status")
async def transcribe_status():
    """Whisper modeli bellekte mi? Arayüz, 'model yükleniyor' uyarısını sadece gerekirse göstermek için kullanır."""
    return {"loaded": stt_service.is_model_loaded(), "device": stt_service.current_device()}