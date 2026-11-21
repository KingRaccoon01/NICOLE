"""Nicole — Word (.docx) dışa aktarım."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import io

from ..database import get_session, Chapter, Paragraph
from ..services.docx_export import export_chapter_to_docx, unresolved_flag_count

router = APIRouter(prefix="/api", tags=["export"])


@router.get("/chapters/{chapter_id}/export")
async def export_chapter(chapter_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Chapter)
        .where(Chapter.id == chapter_id)
        .options(selectinload(Chapter.paragraphs).selectinload(Paragraph.flags))
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Bölüm bulunamadı")

    docx_bytes = export_chapter_to_docx(chapter)
    filename = f"{chapter.title or 'bolum'}.docx".replace(" ", "_")

    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Unresolved-Flags": str(unresolved_flag_count(chapter)),
        },
    )
