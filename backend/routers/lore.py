"""Nicole — Lore Sözlüğü. Her romanın (projenin) kendi sözlüğü vardır."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import (
    get_session, Project, Chapter, Paragraph, LoreEntry, LoreSource, FlaggedTerm, FlagStatus,
)
from ..schemas import LoreEntryCreate, LoreEntryOut
from ..services.lore_detection import tr_lower
from .flags import lore_has_term

router = APIRouter(prefix="/api/projects/{project_id}/lore", tags=["lore"])


async def _require_project(session: AsyncSession, project_id: int) -> None:
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "Proje bulunamadı")


@router.get("", response_model=list[LoreEntryOut])
async def list_lore(project_id: int, q: str | None = None, session: AsyncSession = Depends(get_session)):
    await _require_project(session, project_id)
    stmt = select(LoreEntry).where(LoreEntry.project_id == project_id).order_by(LoreEntry.term)
    if q:
        stmt = stmt.where(or_(LoreEntry.term.ilike(f"%{q}%"), LoreEntry.definition.ilike(f"%{q}%")))
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=LoreEntryOut)
async def add_lore_entry(project_id: int, payload: LoreEntryCreate, session: AsyncSession = Depends(get_session)):
    await _require_project(session, project_id)
    term = payload.term.strip()
    if not term:
        raise HTTPException(400, "Terim boş olamaz")
    if await lore_has_term(session, project_id, term):
        raise HTTPException(400, "Bu terim bu romanın sözlüğünde zaten var")
    entry = LoreEntry(project_id=project_id, term=term, definition=payload.definition, source=LoreSource.manual)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


@router.delete("/{entry_id}")
async def delete_lore_entry(project_id: int, entry_id: int, session: AsyncSession = Depends(get_session)):
    """
    Bir terimi bu romanın Lore Sözlüğü'nden kaldırır. Terim artık "bilinen" sayılmadığı
    için, bu romanda daha önce bu terimle çözülmüş (mavi/yeşil/sarı) TÜM flag kayıtları
    tekrar "pending" (kırmızı) durumuna döner — böylece o terim geçen her yerde flag
    yeniden görünür ve gerekirse tekrar farklı şekilde çözülebilir. Diğer romanlar etkilenmez.
    """
    entry = await session.get(LoreEntry, entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(404, "Kayıt bulunamadı")

    term = entry.term
    await session.delete(entry)

    affected_result = await session.execute(
        select(FlaggedTerm)
        .join(Paragraph, FlaggedTerm.paragraph_id == Paragraph.id)
        .join(Chapter, Paragraph.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id)
    )
    affected_paragraph_ids = set()
    # Python'da tr_lower: SQLite'ın lower()'ı sadece ASCII küçültür ("IŞIK" ile "ışık" eşleşmez).
    for flag in (f for f in affected_result.scalars().all() if tr_lower(f.raw_text) == tr_lower(term)):
        flag.status = FlagStatus.pending
        flag.final_text = None
        flag.resolved_at = None
        affected_paragraph_ids.add(flag.paragraph_id)

    await session.commit()
    return {"ok": True, "affected_paragraph_ids": list(affected_paragraph_ids)}