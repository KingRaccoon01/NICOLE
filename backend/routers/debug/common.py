"""
Nicole — DEBUG yardımcıları (GEÇİCİ)
debug.py ve debug_tests_*.py dosyalarının ortak kullandığı küçük yardımcılar.
Proje bitince debug.py ile birlikte silinebilir.
"""
from sqlalchemy import select

from ...database import AsyncSessionLocal, Project, Chapter, Paragraph, FlaggedTerm, LoreEntry, FlagStatus  # noqa: F401


def _result(ok: bool, **kwargs):
    return {"ok": ok, **kwargs}


# ---------------------------------------------------------------------------
# Test yardımcıları (yerel mantık testleri — Ollama/Whisper/Gemini'ye GERÇEK istek
# atmazlar; gerektiğinde servis fonksiyonlarını geçici olarak "sahte" ile
# değiştirip sadece Nicole'nin KENDİ kodunu sınarlar. Test verisi her zaman
# temizlenir.)
# ===========================================================================

class _Steps:
    """Test adımlarını ✔ / ✘ / ⚠ satırları olarak biriktirir."""

    def __init__(self):
        self.lines: list[str] = []
        self.failed = 0

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        self.lines.append(f"{'✔' if passed else '✘'} {name}" + (f" — {detail}" if detail else ""))
        if not passed:
            self.failed += 1
        return passed

    def warn(self, text: str) -> None:
        self.lines.append(f"⚠ {text}")

    def info(self, text: str) -> None:
        self.lines.append(f"ℹ {text}")

    @property
    def ok(self) -> bool:
        return self.failed == 0


def _uid() -> str:
    import uuid
    return uuid.uuid4().hex[:6]


async def _make_chain(session, title: str, paragraph_text: str | None = None):
    """Proje -> Bölüm (-> Paragraf) oluşturur. paragraph_text None ise paragraf açılmaz."""
    project = Project(title=title)
    session.add(project)
    await session.commit()
    await session.refresh(project)

    chapter = Chapter(project_id=project.id, title="__debug_chapter__", order=0)
    session.add(chapter)
    await session.commit()
    await session.refresh(chapter)

    paragraph = None
    if paragraph_text is not None:
        paragraph = Paragraph(chapter_id=chapter.id, text=paragraph_text, order=0)
        session.add(paragraph)
        await session.commit()
        await session.refresh(paragraph)
    return project, chapter, paragraph


async def _drop_project(session, project_id: int | None) -> None:
    if project_id is None:
        return
    await session.rollback()
    obj = await session.get(Project, project_id)
    if obj is not None:
        await session.delete(obj)  # cascade: bölüm/paragraf/flag/sohbet de gider
        await session.commit()


async def _drop_lore(session, terms: list[str]) -> None:
    if not terms:
        return
    from sqlalchemy import delete
    await session.rollback()
    await session.execute(delete(LoreEntry).where(LoreEntry.term.in_(terms)))
    await session.commit()


async def _lore_has(session, term: str, project_id: int | None = None) -> bool:
    """Terim sözlükte var mı? project_id verilirse sadece o romanın sözlüğüne bakar."""
    stmt = select(LoreEntry).where(LoreEntry.term == term)
    if project_id is not None:
        stmt = stmt.where(LoreEntry.project_id == project_id)
    res = await session.execute(stmt)
    return res.scalars().first() is not None
