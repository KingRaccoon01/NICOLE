"""Nicole — Word (.docx) dışa aktarım: romanın tamamı tek dosya."""
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_session, Project, Chapter, Paragraph
from ..services.docx_export import export_project_to_docx, unresolved_flag_count

router = APIRouter(prefix="/api", tags=["export"])


@router.get("/projects/{project_id}/export")
async def export_project(project_id: int, session: AsyncSession = Depends(get_session)):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Proje bulunamadı")
    result = await session.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.order)
        .options(selectinload(Chapter.paragraphs).selectinload(Paragraph.flags))
    )
    chapters = list(result.scalars().all())

    docx_bytes = export_project_to_docx(project.title, chapters)
    filename = f"{project.title or 'roman'}.docx".replace(" ", "_")

    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            # HTTP başlıkları latin-1: Türkçe harfler (ş, ğ, ı) RFC 5987 ile kodlanmalı.
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Unresolved-Flags": str(unresolved_flag_count(chapters)),
        },
    )
