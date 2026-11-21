"""Nicole — Bölüm ve paragraf yönetimi (dikte/klavye ile üretilen metnin deposu)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_session, Project, Chapter, Paragraph, FlaggedTerm, next_order, touch_project
from ..schemas import ChapterCreate, ChapterOut, ParagraphOut, ParagraphCreate

router = APIRouter(prefix="/api", tags=["chapters"])


@router.post("/projects/{project_id}/chapters", response_model=ChapterOut)
async def create_chapter(project_id: int, payload: ChapterCreate, session: AsyncSession = Depends(get_session)):
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "Proje bulunamadı")
    order = await next_order(session, Chapter, Chapter.project_id == project_id)
    chapter = Chapter(project_id=project_id, title=payload.title, order=order)
    session.add(chapter)
    await session.commit()
    await session.refresh(chapter)
    return chapter


@router.put("/chapters/{chapter_id}", response_model=ChapterOut)
async def rename_chapter(chapter_id: int, payload: ChapterCreate, session: AsyncSession = Depends(get_session)):
    chapter = await session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "Bölüm bulunamadı")
    chapter.title = payload.title.strip() or chapter.title
    await session.commit()
    await session.refresh(chapter)
    return chapter


@router.delete("/chapters/{chapter_id}")
async def delete_chapter(chapter_id: int, session: AsyncSession = Depends(get_session)):
    """Bölümü paragrafları ve flag'leriyle siler. Romanın son bölümü silinemez (dikte ekranı bir bölüme bağlı)."""
    chapter = await session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "Bölüm bulunamadı")
    remaining = await session.execute(
        select(Chapter.id).where(Chapter.project_id == chapter.project_id, Chapter.id != chapter_id).order_by(Chapter.order)
    )
    next_id = remaining.scalars().first()
    if next_id is None:
        raise HTTPException(400, "Romanın son bölümü silinemez")
    await touch_project(session, chapter_id)
    await session.delete(chapter)  # cascade: paragraflar ve flag'ler de gider
    await session.commit()
    return {"ok": True, "next_chapter_id": next_id}


@router.get("/projects/{project_id}/chapters", response_model=list[ChapterOut])
async def list_chapters(project_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order)
    )
    return result.scalars().all()


@router.get("/chapters/{chapter_id}/paragraphs", response_model=list[ParagraphOut])
async def get_chapter_paragraphs(chapter_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Paragraph)
        .where(Paragraph.chapter_id == chapter_id)
        .options(selectinload(Paragraph.flags))
        .order_by(Paragraph.order)
    )
    return result.scalars().all()


@router.get("/paragraphs/{paragraph_id}", response_model=ParagraphOut)
async def get_paragraph(paragraph_id: int, session: AsyncSession = Depends(get_session)):
    """
    Tek bir paragrafı (flag'leri dahil) taze olarak çeker. Frontend bunu, bir
    flag çözüldükten sonra o paragrafın görünümünü güncellemek için kullanır —
    sadece DOM'u yamalamak yerine paragrafı baştan çekmek, sayfa üzerinde
    tutulan önbelleğin (düzenleme/iptal akışının kullandığı) her zaman güncel
    kalmasını garanti eder.
    """
    result = await session.execute(
        select(Paragraph).where(Paragraph.id == paragraph_id).options(selectinload(Paragraph.flags))
    )
    paragraph = result.scalar_one_or_none()
    if not paragraph:
        raise HTTPException(404, "Paragraf bulunamadı")
    return paragraph


@router.post("/chapters/{chapter_id}/paragraphs", response_model=ParagraphOut)
async def add_paragraph(chapter_id: int, payload: ParagraphCreate, session: AsyncSession = Depends(get_session)):
    """
    Klavye modunun kullandığı uç nokta. Whisper (sesli dikte) akışı bunu
    KULLANMAZ — kendi WebSocket'i (dictation_ws.py) üzerinden ayrı işler.

    NOT: Klavye ile YAZILAN metinlerde flag taraması YAPILMAZ — kullanıcı
    zaten doğru terimi kendi eliyle yazdığı için, bunu "kurgusal terim, emin
    misin?" diye tekrar sormanın bir anlamı yok. Flag sistemi SADECE Whisper
    ile (konuşarak) yazılan, yanlış transkript riski taşıyan metinler içindir.
    """
    order = await next_order(session, Paragraph, Paragraph.chapter_id == chapter_id)

    paragraph = Paragraph(chapter_id=chapter_id, text=payload.text, order=order)
    session.add(paragraph)
    await touch_project(session, chapter_id)
    await session.commit()
    await session.refresh(paragraph)
    await session.refresh(paragraph, attribute_names=["flags"])
    return paragraph


@router.put("/paragraphs/{paragraph_id}", response_model=ParagraphOut)
async def update_paragraph(paragraph_id: int, payload: ParagraphCreate, session: AsyncSession = Depends(get_session)):
    """
    Klavye modunda mevcut bir paragrafı doğrudan (yerinde) düzenlemek için.

    NOT: Burada da flag taraması YAPILMAZ (yukarıdaki add_paragraph'taki
    gerekçeyle aynı — kullanıcı elle yazdı/düzeltti). Metin değiştiği için
    ESKİ flag'ler (varsa, örn. paragraf Whisper ile oluşturulup sonradan
    klavyeyle düzenlendiyse) artık geçersiz sayılır ve silinir.
    """
    paragraph = await session.get(Paragraph, paragraph_id)
    if not paragraph:
        raise HTTPException(404, "Paragraf bulunamadı")

    paragraph.text = payload.text
    paragraph.uncertain_json = None  # elle yazılan metin Whisper'dan gelmiyor

    old_flags_result = await session.execute(select(FlaggedTerm).where(FlaggedTerm.paragraph_id == paragraph_id))
    for old_flag in old_flags_result.scalars().all():
        await session.delete(old_flag)
    await touch_project(session, paragraph.chapter_id)
    await session.commit()
    await session.refresh(paragraph)
    await session.refresh(paragraph, attribute_names=["flags"])
    return paragraph


@router.delete("/paragraphs/{paragraph_id}")
async def delete_paragraph(paragraph_id: int, session: AsyncSession = Depends(get_session)):
    """Klavye modunda bir paragrafı tamamen kaldırmak için."""
    paragraph = await session.get(Paragraph, paragraph_id)
    if not paragraph:
        raise HTTPException(404, "Paragraf bulunamadı")
    await touch_project(session, paragraph.chapter_id)
    await session.delete(paragraph)
    await session.commit()
    return {"ok": True}