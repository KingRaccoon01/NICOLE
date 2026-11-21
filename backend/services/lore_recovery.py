"""
Nicole — Lore Sözlüğü kurtarma
Lore terimleri bir şekilde kaybolduysa (örn. veritabanı geçişi yarıda kaldıysa) şu kaynaklardan
geri getirilir:
  1) Yarım kalmış geçişten arta kalan `lore_entries_old` tablosu
  2) Veritabanı dosyasının yanındaki yedekler (örn. nicole.db.bak-lore-per-project)
  3) Romandaki ÇÖZÜLMÜŞ flag'ler: "orijinali kabul et" (mavi) ve "öneriyi kabul et" (yeşil)
     her zaman terimi Lore'a eklediği için, o flag'lerin terimleri sözlüğe geri yazılır.
Mevcut terimlere ve başka kayıtlara dokunulmaz; sadece EKSİK olanlar eklenir.
"""
from __future__ import annotations
import logging
import sqlite3
from pathlib import Path

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Project, Chapter, Paragraph, FlaggedTerm, FlagStatus, LoreEntry, LoreSource
from .lore_detection import tr_lower

logger = logging.getLogger("nicole.lore_recovery")

_AUTO_RESOLVED = (FlagStatus.accepted_raw, FlagStatus.accepted_suggestion)


def find_backups(db_file: Path | None) -> list[Path]:
    """Veritabanı dosyasının yanındaki yedekler: nicole.db.bak*"""
    if db_file is None:
        return []
    return sorted(db_file.parent.glob(db_file.name + ".bak*"))


def read_backup_terms(path: Path) -> list[dict]:
    """Bir yedek SQLite dosyasındaki lore_entries tablosundan terimleri (salt okunur) okur."""
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lore_entries)")]
        if "term" not in cols:
            return []
        definition = "definition" if "definition" in cols else "NULL"
        source = "source" if "source" in cols else "'manual'"
        rows = conn.execute(f"SELECT term, {definition}, {source} FROM lore_entries").fetchall()
    finally:
        conn.close()
    return [{"term": r[0], "definition": r[1], "source": r[2]} for r in rows if r[0]]


def _source(value) -> LoreSource:
    return LoreSource.auto if str(value).lower().endswith("auto") else LoreSource.manual


async def _table_names(session: AsyncSession) -> set[str]:
    rows = (await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()
    return {r[0] for r in rows}


async def _resolved_flag_terms(session: AsyncSession, project_id: int) -> list[str]:
    """Bu romanda mavi/yeşil çözülmüş flag'lerin (yani Lore'a otomatik eklenmiş olması gereken) terimleri."""
    result = await session.execute(
        select(FlaggedTerm.raw_text)
        .join(Paragraph, FlaggedTerm.paragraph_id == Paragraph.id)
        .join(Chapter, Paragraph.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, FlaggedTerm.status.in_(_AUTO_RESOLVED))
        .distinct()
    )
    return [r[0] for r in result.all()]


async def lore_status(session: AsyncSession, db_file: Path | None) -> dict:
    """Teşhis: roman başına sözlük sayısı, geri getirilebilecek kaynaklar."""
    projects = (await session.execute(select(Project).order_by(Project.id))).scalars().all()
    report: dict = {"projects": [], "old_table_rows": None, "backups": []}

    for p in projects:
        lore_terms = (await session.execute(select(LoreEntry.term).where(LoreEntry.project_id == p.id))).scalars().all()
        have = {tr_lower(t) for t in lore_terms}
        resolved = await _resolved_flag_terms(session, p.id)
        missing = [t for t in resolved if tr_lower(t) not in have]
        report["projects"].append({
            "id": p.id, "title": p.title, "lore_terms": len(lore_terms),
            "resolved_flag_terms": len(resolved), "missing_from_lore": len(missing),
        })

    if "lore_entries_old" in await _table_names(session):
        report["old_table_rows"] = (await session.execute(text("SELECT COUNT(*) FROM lore_entries_old"))).scalar_one()

    for path in find_backups(db_file):
        try:
            report["backups"].append({"file": path.name, "terms": len(read_backup_terms(path))})
        except Exception as exc:
            report["backups"].append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
    return report


async def recover_lore(
    session: AsyncSession,
    db_file: Path | None,
    include_flags: bool = True,
    extra_backups: list[Path] | None = None,
    project_ids: list[int] | None = None,
) -> dict:
    """
    Eksik Lore terimlerini geri yazar. Eski ortak sözlük kaynakları (lore_entries_old + yedekler)
    HER romana kopyalanır (geçişteki mantıkla aynı); flag kaynağı ise sadece kendi romanına eklenir.

    project_ids: verilirse SADECE bu romanlara dokunulur (testler gerçek romanları kirletmesin diye).
    Verilmezse tüm romanlar işlenir (debug panelindeki "Geri Yükle" düğmesi böyle çalışır).
    """
    old_terms: dict[str, dict] = {}
    used_sources: list[str] = []

    if "lore_entries_old" in await _table_names(session):
        rows = (await session.execute(text("SELECT term, definition, source FROM lore_entries_old"))).fetchall()
        for term, definition, source in rows:
            old_terms.setdefault(tr_lower(term), {"term": term, "definition": definition, "source": source})
        used_sources.append(f"lore_entries_old ({len(rows)} kayıt)")

    for path in [*find_backups(db_file), *(extra_backups or [])]:
        try:
            rows = read_backup_terms(Path(path))
        except Exception as exc:
            logger.warning("Yedek okunamadı (%s): %s", path, exc)
            continue
        for row in rows:
            old_terms.setdefault(tr_lower(row["term"]), row)
        used_sources.append(f"{Path(path).name} ({len(rows)} kayıt)")

    project_query = select(Project).order_by(Project.id)
    if project_ids is not None:
        project_query = project_query.where(Project.id.in_(project_ids))
    projects = (await session.execute(project_query)).scalars().all()
    added_old = added_flags = 0
    per_project = []
    for p in projects:
        existing = {
            tr_lower(t)
            for t in (await session.execute(select(LoreEntry.term).where(LoreEntry.project_id == p.id))).scalars().all()
        }
        a_old = a_flags = 0
        for key, row in old_terms.items():
            if key in existing:
                continue
            session.add(LoreEntry(
                project_id=p.id, term=row["term"], definition=row.get("definition"), source=_source(row.get("source")),
            ))
            existing.add(key)
            a_old += 1
        if include_flags:
            for term in await _resolved_flag_terms(session, p.id):
                key = tr_lower(term)
                if key in existing:
                    continue
                session.add(LoreEntry(project_id=p.id, term=term, source=LoreSource.auto))
                existing.add(key)
                a_flags += 1
        added_old += a_old
        added_flags += a_flags
        per_project.append({"id": p.id, "title": p.title, "added_from_old_sources": a_old, "added_from_flags": a_flags})

    await session.commit()
    logger.warning("Lore kurtarma: %d terim eski kaynaklardan, %d terim flag'lerden geri yazıldı.", added_old, added_flags)
    return {
        "sources_used": used_sources, "added_from_old_sources": added_old,
        "added_from_flags": added_flags, "per_project": per_project,
    }