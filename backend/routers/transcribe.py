"""
Nicole — Genel amaçlı ses-metin dönüşümü

Dikte akışındaki (dictation_ws.py) WebSocket'ten farklı olarak, bu uç nokta
flag/paragraf sistemine hiç dokunmaz — sadece ham sesi metne çevirip döner.
Şu an tek kullanım yeri: Nicole Editör sohbetinde mikrofonla mesaj yazmak.

project_id verilirse, o romanın Lore Sözlüğü'ndeki terimler (ve .env'deki
WHISPER_VOCABULARY_HINT) Whisper'a "bu kelimeler geçebilir" ipucu olarak verilir —
özel isimler (örn. karakter adları) böylece daha doğru tanınır.
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from sqlalchemy import select

from .. import config
from ..database import AsyncSessionLocal, LoreEntry
from ..services import stt_service
from ..services.stt_service import transcribe_audio_chunk

router = APIRouter(prefix="/api", tags=["transcribe"])


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
async def transcribe_only(file: UploadFile = File(...), project_id: int | None = Form(None)):
    """Ham ses dosyasını SADECE metne çevirir — kayıt/flag oluşturmaz."""
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(400, "Ses verisi boş")
    try:
        hint = await _project_vocabulary_hint(project_id)
        text = await transcribe_audio_chunk(audio_bytes, vocabulary_hint=hint)
    except Exception as exc:
        raise HTTPException(500, f"Ses metne çevrilemedi: {exc}")
    return {"text": text}


@router.get("/transcribe/status")
async def transcribe_status():
    """Whisper modeli bellekte mi? Arayüz, 'model yükleniyor' uyarısını sadece gerekirse göstermek için kullanır."""
    return {"loaded": stt_service.is_model_loaded(), "device": stt_service.current_device()}