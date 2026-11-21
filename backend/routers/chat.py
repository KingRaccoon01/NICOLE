"""Nicole — AI Editör Sohbeti (sağ panel)."""
from __future__ import annotations
import datetime as dt
import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import (
    AsyncSessionLocal, get_session, ChatMessage, Chapter, Paragraph, FlaggedTerm, FlagStatus, LoreEntry, EditLog, touch_project,
)
from ..schemas import ChatMessageIn, ChatMessageOut, EditOut
from ..services import gemini_service
from ..services.docx_export import _resolved_text

logger = logging.getLogger("nicole.chat")
router = APIRouter(prefix="/api/projects/{project_id}/chat", tags=["chat"])

MAX_EDITS_PER_MESSAGE = 20


# ---------------------------------------------------------------------------
# Hikâye bağlamı
# ---------------------------------------------------------------------------

async def _build_story_context(session: AsyncSession, project_id: int, with_ids: bool = False) -> str:
    """
    'Hikaye bağlamını ekle' işaretliyken tüm projedeki metni tek stringe indirger
    (bölüm sırası -> paragraf sırası, bölüm başlıklarıyla).

    - Çözülmüş flag'ler nihai halleriyle yazılır (örn. 'chidori' -> 'çakı' kabul
      edildiyse 'çakı'), bekleyenler orijinal haliyle — DOCX export ile aynı mantık.
    - with_ids=True (düzenleme izni açıkken): her paragraf [P<id>] etiketiyle başlar,
      böylece Editör hangi paragrafı değiştireceğini id ile söyleyebilir.
    """
    result = await session.execute(
        select(Paragraph)
        .join(Chapter, Paragraph.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id)
        .options(selectinload(Paragraph.flags), selectinload(Paragraph.chapter))
        .order_by(Chapter.order, Paragraph.order)
    )
    blocks: list[str] = []
    last_chapter_id = None
    for para in result.scalars().all():
        if para.chapter_id != last_chapter_id:
            blocks.append(f"## Bölüm: {para.chapter.title}")
            last_chapter_id = para.chapter_id
        text = _resolved_text(para)
        blocks.append(f"[P{para.id}] {text}" if with_ids else text)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Düzenleme (Nicole Editör metne doğrudan müdahale) + geri alma
# ---------------------------------------------------------------------------

def _dump_flags(flags: list[FlaggedTerm]) -> str:
    return json.dumps([
        {
            "raw_text": f.raw_text, "suggested_text": f.suggested_text, "final_text": f.final_text,
            "status": f.status.value, "position_start": f.position_start,
            "position_end": f.position_end, "order_in_paragraph": f.order_in_paragraph,
        }
        for f in flags
    ], ensure_ascii=False)


def _restore_flags(session: AsyncSession, paragraph_id: int, flags_json: str | None) -> None:
    for d in json.loads(flags_json or "[]"):
        status = FlagStatus(d["status"])
        session.add(FlaggedTerm(
            paragraph_id=paragraph_id, raw_text=d["raw_text"], suggested_text=d["suggested_text"],
            final_text=d["final_text"], status=status, position_start=d["position_start"],
            position_end=d["position_end"], order_in_paragraph=d["order_in_paragraph"],
            resolved_at=None if status == FlagStatus.pending else dt.datetime.utcnow(),
        ))


async def _paragraph_flags(session: AsyncSession, paragraph_id: int) -> list[FlaggedTerm]:
    res = await session.execute(select(FlaggedTerm).where(FlaggedTerm.paragraph_id == paragraph_id))
    return list(res.scalars().all())


async def _apply_edits(session: AsyncSession, project_id: int, message_id: int, edits: list[dict]) -> list[EditLog]:
    """
    Model'in döndürdüğü düzenlemeleri uygular. Güvenlik: sadece bu projeye ait
    paragraflara dokunulur; geçersiz/uydurma id'ler ve boş metinler sessizce atlanır.
    Metni değişen paragrafın eski flag'leri geçersiz olduğu için silinir (klavyeyle
    düzenlemedeki davranışla aynı) — ama 'Geri al' için kayda alınır.
    """
    applied: list[EditLog] = []
    for item in edits[:MAX_EDITS_PER_MESSAGE]:
        action = item.get("action")
        pid = item.get("paragraph_id")
        new_text = (item.get("new_text") or "").strip()
        if action not in ("replace", "insert_after", "delete") or not isinstance(pid, int):
            continue
        if action in ("replace", "insert_after") and not new_text:
            continue

        para = await session.get(Paragraph, pid, populate_existing=True)
        if para is None:
            continue
        chapter = await session.get(Chapter, para.chapter_id)
        if chapter is None or chapter.project_id != project_id:
            continue

        if action == "replace":
            flags = await _paragraph_flags(session, para.id)
            log = EditLog(
                project_id=project_id, message_id=message_id, action="replace", paragraph_id=para.id,
                chapter_id=para.chapter_id, order=para.order, before_text=para.text, after_text=new_text,
                flags_json=_dump_flags(flags),
            )
            for f in flags:
                await session.delete(f)
            para.text = new_text
            para.uncertain_json = None

        elif action == "insert_after":
            await session.execute(
                update(Paragraph)
                .where(Paragraph.chapter_id == para.chapter_id, Paragraph.order > para.order)
                .values(order=Paragraph.order + 1)
            )
            new_para = Paragraph(chapter_id=para.chapter_id, text=new_text, order=para.order + 1)
            session.add(new_para)
            await session.flush()
            log = EditLog(
                project_id=project_id, message_id=message_id, action="insert_after", paragraph_id=new_para.id,
                chapter_id=para.chapter_id, order=new_para.order, before_text=None, after_text=new_text,
            )

        else:  # delete
            flags = await _paragraph_flags(session, para.id)
            log = EditLog(
                project_id=project_id, message_id=message_id, action="delete", paragraph_id=para.id,
                chapter_id=para.chapter_id, order=para.order, before_text=para.text, after_text=None,
                flags_json=_dump_flags(flags),
            )
            await session.delete(para)  # cascade: flag'ler de gider

        session.add(log)
        await session.flush()
        applied.append(log)

    if applied:
        await touch_project(session, applied[0].chapter_id)
    await session.commit()
    return applied


# ---------------------------------------------------------------------------
# Uç noktalar
# ---------------------------------------------------------------------------

async def _load_history(session: AsyncSession, project_id: int) -> list[dict]:
    result = await session.execute(
        select(ChatMessage).where(ChatMessage.project_id == project_id).order_by(ChatMessage.created_at, ChatMessage.id)
    )
    return [{"role": m.role, "content": m.content} for m in result.scalars().all()]


@router.get("", response_model=list[ChatMessageOut])
async def get_history(project_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(ChatMessage).where(ChatMessage.project_id == project_id).order_by(ChatMessage.created_at, ChatMessage.id)
    )
    messages = result.scalars().all()

    logs_result = await session.execute(select(EditLog).where(EditLog.project_id == project_id).order_by(EditLog.id))
    logs_by_message: dict[int, list[EditLog]] = {}
    for log in logs_result.scalars().all():
        logs_by_message.setdefault(log.message_id, []).append(log)

    return [
        ChatMessageOut(
            id=m.id, role=m.role, content=m.content, created_at=m.created_at,
            edits=[EditOut.model_validate(l) for l in logs_by_message.get(m.id, [])],
        )
        for m in messages
    ]


@router.delete("")
async def clear_history(project_id: int, session: AsyncSession = Depends(get_session)):
    """'Sohbeti temizle' — geçmişi (ve geri alma kayıtlarını) kalıcı olarak siler."""
    result = await session.execute(select(ChatMessage).where(ChatMessage.project_id == project_id))
    messages = result.scalars().all()
    for m in messages:
        await session.delete(m)
    await session.execute(delete(EditLog).where(EditLog.project_id == project_id))
    await session.commit()
    return {"ok": True, "deleted": len(messages)}


async def _prepare_turn(session: AsyncSession, project_id: int, payload: ChatMessageIn):
    """Ortak hazırlık: geçmiş, kullanıcı mesajının kaydı, hikâye bağlamı, lore terimleri."""
    # Geçmişi mesajı kaydetmeden ÖNCE al — yoksa geçerli mesaj hem geçmişte hem
    # ayrıca eklenerek Gemini'ye iki kez gidiyordu.
    history = await _load_history(session, project_id)

    user_msg = ChatMessage(project_id=project_id, role="user", content=payload.content)
    session.add(user_msg)
    await session.commit()

    # Düzenleme izni açıkken Editör paragraf id'lerini görmek zorunda — bağlam zorunlu eklenir.
    use_context = payload.include_context or payload.allow_edits
    context = await _build_story_context(session, project_id, with_ids=payload.allow_edits) if use_context else None

    lore_result = await session.execute(
        select(LoreEntry.term).where(LoreEntry.project_id == project_id).order_by(LoreEntry.term)
    )
    lore_terms = [row[0] for row in lore_result.all()]
    return history, user_msg, context, lore_terms


@router.post("/stream")
async def send_message_stream(project_id: int, payload: ChatMessageIn):
    """
    Düzenleme izni KAPALIYKEN kullanılan akışlı sohbet: her satır bir JSON (NDJSON).
      {"delta": "..."}                      — Gemini yazdıkça gelen metin parçası
      {"done": true, "message": {...}}      — son: kaydedilmiş mesaj (hata ise id=0 ve "⚠ ..." metni)
    Ekran cevabı akarak gösterir, sesli okuma ilk cümleden başlar.
    """
    async def events():
        async with AsyncSessionLocal() as session:  # istek bitince kapanan Depends oturumu akışta kullanılamaz
            history, user_msg, context, lore_terms = await _prepare_turn(session, project_id, payload)
            parts: list[str] = []
            error = None
            try:
                async for delta in gemini_service.chat_stream(
                    payload.content, context, history, lore_terms=lore_terms, fast=payload.fast,
                ):
                    parts.append(delta)
                    yield json.dumps({"delta": delta}, ensure_ascii=False) + "\n"
            except gemini_service.GeminiError as exc:
                error = str(exc)
            except Exception as exc:
                logger.exception("Akışlı sohbet hatası")
                error = f"Beklenmeyen hata: {type(exc).__name__}: {exc}"

            reply = "".join(parts).strip()
            if not reply:
                # send_message ile aynı kural: hata geçmişe yazılmaz, kullanıcı mesajı da silinir
                await session.delete(user_msg)
                await session.commit()
                out = ChatMessageOut(id=0, role="assistant", content=f"⚠ {error or 'Gemini boş yanıt döndürdü.'}",
                                     created_at=dt.datetime.utcnow())
            else:
                if error:
                    reply += f"\n\n⚠ {error}"
                msg = ChatMessage(project_id=project_id, role="assistant", content=reply)
                session.add(msg)
                await session.commit()
                await session.refresh(msg)
                out = ChatMessageOut(id=msg.id, role="assistant", content=msg.content, created_at=msg.created_at)
            yield json.dumps({"done": True, "message": out.model_dump(mode="json")}, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@router.post("", response_model=ChatMessageOut)
async def send_message(project_id: int, payload: ChatMessageIn, session: AsyncSession = Depends(get_session)):
    history, user_msg, context, lore_terms = await _prepare_turn(session, project_id, payload)

    result = await gemini_service.chat_full(
        payload.content, context, history, allow_edits=payload.allow_edits, lore_terms=lore_terms,
        fast=payload.fast,
    )

    if not result["ok"]:
        # Hata metni geçmişe girerse sonraki her istekte Gemini'ye "sohbet" olarak geri gider.
        # Kullanıcı mesajı da silinir: cevapsız kalırsa geçmişte art arda iki kullanıcı mesajı oluşur.
        await session.delete(user_msg)
        await session.commit()
        return ChatMessageOut(id=0, role="assistant", content=f"⚠ {result['reply']}", created_at=dt.datetime.utcnow())

    assistant_msg = ChatMessage(project_id=project_id, role="assistant", content=result["reply"])
    session.add(assistant_msg)
    await session.commit()
    await session.refresh(assistant_msg)

    applied: list[EditLog] = []
    if payload.allow_edits and result["edits"]:
        try:
            applied = await _apply_edits(session, project_id, assistant_msg.id, result["edits"])
        except Exception:
            logger.exception("Editör düzenlemeleri uygulanırken hata")
            await session.rollback()

    return ChatMessageOut(
        id=assistant_msg.id, role="assistant", content=assistant_msg.content, created_at=assistant_msg.created_at,
        edits=[EditOut.model_validate(l) for l in applied],
    )


@router.post("/{message_id}/undo")
async def undo_edits(project_id: int, message_id: int, session: AsyncSession = Depends(get_session)):
    """Bir asistan mesajının metinde yaptığı TÜM düzenlemeleri, ters sırayla geri alır."""
    result = await session.execute(
        select(EditLog)
        .where(EditLog.project_id == project_id, EditLog.message_id == message_id, EditLog.undone == False)  # noqa: E712
        .order_by(EditLog.id.desc())
    )
    undone = 0
    errors: list[str] = []
    for log in result.scalars().all():
        try:
            if log.action == "replace":
                para = await session.get(Paragraph, log.paragraph_id, populate_existing=True)
                if para is None:
                    errors.append(f"Paragraf {log.paragraph_id} artık yok, geri alınamadı.")
                    continue
                for f in await _paragraph_flags(session, para.id):
                    await session.delete(f)
                para.text = log.before_text or ""
                _restore_flags(session, para.id, log.flags_json)

            elif log.action == "insert_after":
                para = await session.get(Paragraph, log.paragraph_id)
                if para is not None:
                    await session.delete(para)

            elif log.action == "delete":
                if await session.get(Chapter, log.chapter_id) is None:
                    errors.append("Silinen paragrafın bölümü artık yok, geri alınamadı.")
                    continue
                await session.execute(
                    update(Paragraph)
                    .where(Paragraph.chapter_id == log.chapter_id, Paragraph.order >= log.order)
                    .values(order=Paragraph.order + 1)
                )
                restored = Paragraph(chapter_id=log.chapter_id, text=log.before_text or "", order=log.order)
                session.add(restored)
                await session.flush()
                _restore_flags(session, restored.id, log.flags_json)

            log.undone = True
            undone += 1
        except Exception as exc:
            logger.exception("Geri alma hatası")
            errors.append(f"{type(exc).__name__}: {exc}")

    await session.commit()
    return {"ok": not errors, "undone": undone, "errors": errors}