"""
Nicole — Flag (tutarlılık) sistemi
Müdahale modalindeki 4 aksiyonu işler:
  accept_raw          -> 🔵 mavi  (orijinal terim aynen kalır, lore'a otomatik eklenir)
  accept_suggestion   -> 🟢 yeşil (öneri kullanılır; lore'a EKLENMEZ — yanlış duyulan kelime sözlüğe girmesin)
  manual              -> 🟡 sarı  (kullanıcının yazdığı metin; isterse SADECE düzeltilmiş hali lore'a eklenir)
  cancel              -> 🔴 kırmızı flag olduğu gibi kalır, hiçbir şey değişmez
  reopen              -> çözülmüş flag tekrar kırmızı olur (kararı geri al)
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session, FlaggedTerm, FlagStatus, LoreEntry, LoreSource, Paragraph, Chapter
from ..schemas import FlagResolveRequest, FlaggedTermOut
from ..services.lore_detection import find_term_positions, tr_lower

router = APIRouter(prefix="/api/flags", tags=["flags"])


async def create_flags_for_paragraph(
    session: AsyncSession,
    paragraph: Paragraph,
    detected: list[dict],
    search_text: str | None = None,
    offset: int = 0,
) -> None:
    """
    dictation_ws.py tarafından çağrılır. Tespit edilen her terim için bir FlaggedTerm satırı açar.
    Çakışan terimlerde ("Chidori" ve "Chidori Nagashi") uzun olan kazanır — üst üste binen
    flag'ler hem ekranda hem DOCX'te metni bozuyordu.

    search_text/offset: paragraf, önceki bir metnin ÜZERİNE eklenen (append)
    bir parça içeriyorsa, pozisyon aramasının SADECE yeni eklenen kısımda
    yapılmasını ve bulunan pozisyonlara "offset" eklenmesini sağlar — böylece
    paragrafın başındaki zaten çözülmüş bir terim, sonda tekrar geçse bile
    yanlışlıkla ikinci kez flag'lenmez.
    """
    text_to_search = search_text if search_text is not None else paragraph.text

    # order_in_paragraph, paragrafta zaten var olan flag sayısından devam
    # etsin (append durumunda numaralandırma sıfırlanıp çakışmasın).
    existing_count = await session.execute(
        select(func.count()).select_from(FlaggedTerm).where(FlaggedTerm.paragraph_id == paragraph.id)
    )
    order = existing_count.scalar_one()

    taken: list[tuple[int, int]] = []
    for item in sorted(detected, key=lambda d: len(d["term"]), reverse=True):
        for start, end in find_term_positions(text_to_search, item["term"]):
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            taken.append((start, end))
            flag = FlaggedTerm(
                paragraph_id=paragraph.id,
                raw_text=text_to_search[start:end],  # metinde yazdığı haliyle (büyük/küçük harf korunur)
                suggested_text=item.get("suggestion") or None,
                status=FlagStatus.pending,
                position_start=start + offset,
                position_end=end + offset,
                order_in_paragraph=order,
            )
            session.add(flag)
            order += 1
    if detected:
        await session.commit()


async def _project_id_for_paragraph(session: AsyncSession, paragraph_id: int) -> int:
    """Flag'in ait olduğu romanın (projenin) id'si — Lore Sözlüğü romana özel olduğu için gerekli."""
    result = await session.execute(
        select(Chapter.project_id).join(Paragraph, Paragraph.chapter_id == Chapter.id).where(Paragraph.id == paragraph_id)
    )
    return result.scalar_one()


async def lore_has_term(session: AsyncSession, project_id: int, term: str) -> bool:
    """Büyük/küçük harf duyarsız (Türkçe kurallı: I/ı, İ/i) — "Gaara" varken "gaara" ikinci kez eklenmesin."""
    result = await session.execute(select(LoreEntry.term).where(LoreEntry.project_id == project_id))
    key = tr_lower(term.strip())
    return any(tr_lower(t) == key for t in result.scalars())


async def _add_to_lore_if_missing(session: AsyncSession, project_id: int, term: str, source: LoreSource) -> None:
    if not await lore_has_term(session, project_id, term):
        session.add(LoreEntry(project_id=project_id, term=term.strip(), source=source))


async def _drop_auto_lore_term(session: AsyncSession, project_id: int, flag: FlaggedTerm) -> None:
    """
    "Orijinali kabul et" kararı değiştirilince o kararın Lore'a eklediği terimi geri al — yoksa
    yanlış duyulmuş hal ("Kel") sözlükte kalıp Whisper'a ipucu oluyor ve sonraki önerileri bozuyordu.
    Aynı terimi "orijinali kabul et" ile onaylamış başka bir flag varsa terim kalır.
    """
    key = tr_lower(flag.raw_text)
    others = await session.execute(
        select(FlaggedTerm.raw_text)
        .join(Paragraph, FlaggedTerm.paragraph_id == Paragraph.id)
        .join(Chapter, Paragraph.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, FlaggedTerm.status == FlagStatus.accepted_raw, FlaggedTerm.id != flag.id)
    )
    if any(tr_lower(t) == key for t in others.scalars()):
        return
    entries = await session.execute(
        select(LoreEntry).where(LoreEntry.project_id == project_id, LoreEntry.source == LoreSource.auto)
    )
    for entry in entries.scalars().all():
        if tr_lower(entry.term) == key:
            await session.delete(entry)


@router.get("/{flag_id}", response_model=FlaggedTermOut)
async def get_flag(flag_id: int, session: AsyncSession = Depends(get_session)):
    flag = await session.get(FlaggedTerm, flag_id)
    if not flag:
        raise HTTPException(404, "Flag bulunamadı")
    return flag


@router.post("/{flag_id}/resolve", response_model=FlaggedTermOut)
async def resolve_flag(flag_id: int, payload: FlagResolveRequest, session: AsyncSession = Depends(get_session)):
    flag = await session.get(FlaggedTerm, flag_id)
    if not flag:
        raise HTTPException(404, "Flag bulunamadı")

    if payload.action == "cancel":
        # Kırmızı flag olduğu gibi kalır — hiçbir alan değişmez.
        return flag

    project_id = await _project_id_for_paragraph(session, flag.paragraph_id)
    if flag.status == FlagStatus.accepted_raw and payload.action != "accept_raw":
        await _drop_auto_lore_term(session, project_id, flag)

    if payload.action == "reopen":
        flag.status, flag.final_text, flag.resolved_at = FlagStatus.pending, None, None
        await session.commit()
        await session.refresh(flag)
        return flag

    if payload.action == "accept_raw":
        flag.status = FlagStatus.accepted_raw
        flag.final_text = flag.raw_text
        await _add_to_lore_if_missing(session, project_id, flag.raw_text, LoreSource.auto)

    elif payload.action == "accept_suggestion":
        if not flag.suggested_text:
            raise HTTPException(400, "Bu flag için bir AI önerisi yok")
        flag.status = FlagStatus.accepted_suggestion
        flag.final_text = flag.suggested_text
        # Lore'a eklenmez: raw_text yanlış duyulmuş kelimedir ("falız"). Sözlüğe girerse hem bir daha
        # işaretlenmez hem de Whisper'a ipucu olarak gidip aynı hatayı pekiştirirdi.

    elif payload.action == "manual":
        if not payload.manual_text:
            raise HTTPException(400, "manual_text zorunlu")
        flag.status = FlagStatus.manual
        flag.final_text = payload.manual_text
        if payload.add_to_lore:
            # SADECE düzeltilmiş (doğru) terim eklenir. Yanlış duyulmuş hali eklenmez: eklenirse
            # Whisper'a ipucu olarak gidip hatayı pekiştirirdi. Aynı hata tekrar gelirse flag
            # taraması onu sözlükteki doğru terime benzetip doğru yazımı önerir.
            await _add_to_lore_if_missing(session, project_id, payload.manual_text, LoreSource.manual)

    else:
        raise HTTPException(400, f"Bilinmeyen aksiyon: {payload.action}")

    flag.resolved_at = dt.datetime.utcnow()
    await session.commit()
    await session.refresh(flag)
    return flag