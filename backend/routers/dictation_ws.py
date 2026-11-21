"""
Nicole — Dikte WebSocket
Tarayıcı, ses parçalarını (chunk) bu soket üzerinden binary olarak gönderir.
Bir "duraklama" sinyali geldiğinde, o ana kadar biriken ses tek seferde
faster-whisper'a verilir ve sonucu bir paragraf olarak kaydedilip flag
taraması tetiklenir.

Sinyal formatı (JSON, text mesaj):
  {"type": "end_utterance", "append_to": <paragraf_id> | null}
"append_to" doluysa, transkript YENİ bir paragraf açmak yerine mevcut
paragrafın SONUNA eklenir — bu, kullanıcının "her cümlede mikrofonu aç/kapat"
yaptığında her seferinde ayrı bir satıra düşmeden, doğal konuşma akışında
aynı paragrafın devamı olarak yazmasını sağlar. "append_to": null (veya
alan hiç yoksa) → yeni bir paragraf açılır.

Geriye dönük uyumluluk: eski istemcilerin gönderdiği düz "__END_UTTERANCE__"
string'i de desteklenir (append_to olmadan, her zaman yeni paragraf açar).
"""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, func

from .. import config
from ..database import AsyncSessionLocal, Chapter, Paragraph, LoreEntry
from ..services.stt_service import transcribe_audio_chunk
from ..services.lore_detection import detect_flags
from .flags import create_flags_for_paragraph
from .debug import record_error  # GEÇİCİ — proje bitince debug.py silinince bu satır da silinmeli

logger = logging.getLogger("nicole.dictation_ws")
router = APIRouter()


async def _lore_terms_for_chapter(session, chapter_id: int) -> list[str]:
    """Bu bölümün ait olduğu romanın Lore Sözlüğü terimleri (her romanın sözlüğü ayrıdır)."""
    result = await session.execute(
        select(LoreEntry.term)
        .join(Chapter, Chapter.project_id == LoreEntry.project_id)
        .where(Chapter.id == chapter_id)
    )
    return [row[0] for row in result.all()]


async def _build_vocabulary_hint(chapter_id: int) -> str:
    """
    Whisper'a "bu terimler geçebilir" diye önceden verilecek ipucu metnini
    hazırlar: bu romanın Lore Sözlüğü'ndeki onaylanmış terimler + kullanıcının
    .env'deki WHISPER_VOCABULARY_HINT ayarı birleştirilir. Boşsa "" döner
    (Whisper normal davranışına devam eder).
    """
    async with AsyncSessionLocal() as session:
        lore_terms = await _lore_terms_for_chapter(session, chapter_id)

    parts = []
    if config.WHISPER_VOCABULARY_HINT.strip():
        parts.append(config.WHISPER_VOCABULARY_HINT.strip())
    if lore_terms:
        parts.append(", ".join(lore_terms))

    return ". ".join(parts) if parts else ""


def _parse_control_message(raw_msg: str) -> tuple[bool, int | None]:
    """Dönüş: (is_end_utterance, append_to). append_to None ise yeni paragraf açılır."""
    if raw_msg == "__END_UTTERANCE__":
        return True, None
    try:
        parsed = json.loads(raw_msg)
    except (json.JSONDecodeError, TypeError):
        return False, None
    if isinstance(parsed, dict) and parsed.get("type") == "end_utterance":
        append_to = parsed.get("append_to")
        return True, (int(append_to) if append_to else None)
    return False, None


@router.websocket("/ws/dictate/{chapter_id}")
async def dictate(websocket: WebSocket, chapter_id: int):
    await websocket.accept()
    audio_buffer = bytearray()

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message and message["bytes"] is not None:
                audio_buffer.extend(message["bytes"])
                continue

            if "text" in message and message["text"] is not None:
                is_end_utterance, append_to = _parse_control_message(message["text"])

                if is_end_utterance:
                    if not audio_buffer:
                        continue

                    await websocket.send_text(json.dumps({"type": "status", "message": "Ses gönderildi, metne çevriliyor..."}))
                    try:
                        vocabulary_hint = await _build_vocabulary_hint(chapter_id)
                        text = await transcribe_audio_chunk(bytes(audio_buffer), vocabulary_hint=vocabulary_hint)
                    except Exception as exc:
                        logger.exception("Transkripsiyon hatası")
                        record_error("dictation_ws/transcribe", f"{type(exc).__name__}: {exc}")
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": f"Ses metne çevrilemedi: {exc}",
                        }))
                        audio_buffer.clear()
                        continue
                    audio_buffer.clear()

                    if not text.strip():
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Ses algılanamadı (boş transkript) — mikrofonu ve ses kaynağını kontrol et.",
                        }))
                        continue

                    await websocket.send_text(json.dumps({"type": "status", "message": f'Metin algılandı: "{text[:40]}..." — lore/flag taranıyor...'}))
                    try:
                        paragraph_payload = await _save_paragraph_and_scan(chapter_id, text, append_to=append_to)
                        await websocket.send_text(json.dumps({"type": "paragraph", "data": paragraph_payload}))
                    except Exception as exc:
                        logger.exception("Paragraf kaydetme/flag tarama hatası")
                        record_error("dictation_ws/save_paragraph", f"{type(exc).__name__}: {exc}")
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Paragraf kaydedilirken bir hata oluştu, sunucu loglarını kontrol et.",
                        }))

    except WebSocketDisconnect:
        logger.info("Dikte soketi kapandı (chapter_id=%s)", chapter_id)


async def _save_paragraph_and_scan(chapter_id: int, text: str, append_to: int | None = None) -> dict:
    """
    append_to verilmişse ve geçerliyse (aynı bölüme ait bir paragrafsa), yeni
    transkript o paragrafın SONUNA eklenir; aksi halde (append_to yoksa ya da
    geçersizse) yeni bir paragraf oluşturulur.
    """
    async with AsyncSessionLocal() as session:
        paragraph = None
        offset = 0

        if append_to:
            candidate = await session.get(Paragraph, append_to)
            if candidate is not None and candidate.chapter_id == chapter_id:
                offset = len(candidate.text) + 1  # +1: metinler arasına eklenen boşluk
                candidate.text = f"{candidate.text} {text}"
                await session.commit()
                await session.refresh(candidate)
                paragraph = candidate

        is_append = paragraph is not None

        if paragraph is None:
            count_result = await session.execute(
                select(func.count()).select_from(Paragraph).where(Paragraph.chapter_id == chapter_id)
            )
            order = count_result.scalar_one()
            paragraph = Paragraph(chapter_id=chapter_id, text=text, order=order)
            session.add(paragraph)
            await session.commit()
            await session.refresh(paragraph)

        known_terms = await _lore_terms_for_chapter(session, chapter_id)

        # detect_flags'e her zaman SADECE yeni eklenen kısım (text) gönderilir —
        # zaten flag'lenmiş/çözülmüş kısımlar tekrar taranıp gereksiz yere
        # yeniden işaretlenmesin. search_text/offset de aynı nedenle "text"
        # üzerinde arama yapıp gerçek pozisyonu (offset ile) hesaplar.
        detected = await detect_flags(text, known_terms)
        await create_flags_for_paragraph(session, paragraph, detected, search_text=text, offset=offset)
        await session.refresh(paragraph, attribute_names=["flags"])

        return {
            "id": paragraph.id,
            "chapter_id": paragraph.chapter_id,
            "text": paragraph.text,
            "order": paragraph.order,
            "is_append": is_append,
            "flags": [
                {
                    "id": f.id,
                    "raw_text": f.raw_text,
                    "suggested_text": f.suggested_text,
                    "status": f.status.value,
                    "position_start": f.position_start,
                    "position_end": f.position_end,
                    "order_in_paragraph": f.order_in_paragraph,
                }
                for f in paragraph.flags
            ],
        }