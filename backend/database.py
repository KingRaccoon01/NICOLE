"""
Nicole — Veritabanı katmanı
SQLite + SQLAlchemy 2.0 (async). Tek dosyalık, kuruluma gerek duymayan yerel DB.
"""
import enum
import json
import datetime as dt
import logging
import shutil
from pathlib import Path

from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Enum, Boolean, UniqueConstraint, text, select, update, func
from sqlalchemy.ext.asyncio import AsyncAttrs, create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from . import config

logger = logging.getLogger("nicole.database")


class Base(AsyncAttrs, DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


# ---------------------------------------------------------------------------
# Modeller
# ---------------------------------------------------------------------------

class Project(Base):
    """Bir roman/manga projesi ('Romanlar' listesindeki her kart)."""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    chapters: Mapped[list["Chapter"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    edit_logs: Mapped[list["EditLog"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    lore_entries: Mapped[list["LoreEntry"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Chapter(Base):
    """Bir projenin bölümü. Dikte/yazma bu seviyede yapılır."""
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(200), default="Yeni Bölüm")
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    project: Mapped["Project"] = relationship(back_populates="chapters")
    paragraphs: Mapped[list["Paragraph"]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan", order_by="Paragraph.order"
    )


class Paragraph(Base):
    """Dikte edilen veya yazılan tek bir paragraf."""
    __tablename__ = "paragraphs"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"))
    text: Mapped[str] = mapped_column(Text)  # ham metin (flag'li terimler dahil orijinal haliyle)
    order: Mapped[int] = mapped_column(Integer, default=0)
    # Whisper'ın emin olmadığı kelimeler (JSON liste). Metin elle/Editör'le değişince boşaltılır.
    uncertain_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    @property
    def uncertain_words(self) -> list[str]:
        return json.loads(self.uncertain_json or "[]")

    chapter: Mapped["Chapter"] = relationship(back_populates="paragraphs")
    flags: Mapped[list["FlaggedTerm"]] = relationship(back_populates="paragraph", cascade="all, delete-orphan")


class FlagStatus(str, enum.Enum):
    pending = "pending"                        # 🔴 kırmızı — bekliyor
    accepted_raw = "accepted_raw"               # 🔵 mavi — orijinal terim aynen kabul edildi
    accepted_suggestion = "accepted_suggestion"  # 🟢 yeşil — AI önerisi kabul edildi
    manual = "manual"                           # 🟡 sarı — kullanıcı manuel düzeltti


class FlaggedTerm(Base):
    """Lore sözlüğünde karşılığı olmayan, tespit edilmiş bir terim."""
    __tablename__ = "flagged_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    paragraph_id: Mapped[int] = mapped_column(ForeignKey("paragraphs.id"))

    raw_text: Mapped[str] = mapped_column(String(200))           # örn. "chidori"
    suggested_text: Mapped[str | None] = mapped_column(String(200), nullable=True)  # örn. "çakı"
    final_text: Mapped[str | None] = mapped_column(String(200), nullable=True)      # kullanıcının onayladığı nihai değer

    status: Mapped[FlagStatus] = mapped_column(Enum(FlagStatus), default=FlagStatus.pending)

    position_start: Mapped[int] = mapped_column(Integer)
    position_end: Mapped[int] = mapped_column(Integer)
    order_in_paragraph: Mapped[int] = mapped_column(Integer, default=0)  # üstteki "1, 2, 3" sayaçları için

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    paragraph: Mapped["Paragraph"] = relationship(back_populates="flags")


class LoreSource(str, enum.Enum):
    auto = "auto"
    manual = "manual"


class LoreEntry(Base):
    """
    Lore Sözlüğü — HER ROMANIN KENDİ SÖZLÜĞÜ VAR (proje bazlı).
    Aynı terim farklı projelerde ayrı ayrı bulunabilir; bir projede çözülen/silinen
    terim diğer projeleri etkilemez. (Eskiden tüm projeler arasında paylaşılıyordu;
    tekrar paylaşımlı yapmak istersen project_id'yi kaldırıp bu sınıfı eski haline getirmek yeterli.)
    """
    __tablename__ = "lore_entries"
    __table_args__ = (UniqueConstraint("project_id", "term", name="uq_lore_project_term"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    term: Mapped[str] = mapped_column(String(200), index=True)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[LoreSource] = mapped_column(Enum(LoreSource), default=LoreSource.manual)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    project: Mapped["Project"] = relationship(back_populates="lore_entries")


class ChatMessage(Base):
    """Nicole Editör sohbet geçmişi (proje bazlı)."""
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    project: Mapped["Project"] = relationship(back_populates="chat_messages")


class EditLog(Base):
    """
    Nicole Editör'ün (yazar 'düzenleme izni' verdiğinde) metinde yaptığı doğrudan
    düzenlemelerin kaydı. 'Geri al' bu kayıtlardan çalışır: önceki metin ve (varsa)
    silinen flag'ler burada saklanır.
    """
    __tablename__ = "edit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    message_id: Mapped[int] = mapped_column(Integer, index=True)  # düzenlemeyi yapan asistan mesajı
    action: Mapped[str] = mapped_column(String(20))  # replace | insert_after | delete
    paragraph_id: Mapped[int] = mapped_column(Integer)  # insert_after için: YENİ eklenen paragraf
    chapter_id: Mapped[int] = mapped_column(Integer)
    order: Mapped[int] = mapped_column(Integer, default=0)
    before_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # düzenleme sırasında silinen flag'ler
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    project: Mapped["Project"] = relationship(back_populates="edit_logs")


# ---------------------------------------------------------------------------
# Engine / Session
# ---------------------------------------------------------------------------

engine = create_async_engine(config.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _sqlite_file_from_url(url: str) -> Path | None:
    """'sqlite+aiosqlite:///./nicole.db' -> Path('./nicole.db'). SQLite değilse / bellekteyse None."""
    if not url.startswith("sqlite") or ":memory:" in url:
        return None
    path = url.split("///", 1)[-1]
    return Path(path) if path else None


async def _table_names(conn) -> set[str]:
    rows = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()
    return {r[0] for r in rows}


async def migrate_lore_per_project(conn, db_file: Path | None = None) -> bool:
    """
    Eski şema (tüm projeler arasında paylaşılan lore_entries, project_id YOK) -> yeni şema
    (proje bazlı). Veri kaybı olmasın diye mevcut her terim MEVCUT HER PROJEYE kopyalanır;
    istemediğin terimleri sonra proje sözlüğünden silebilirsin. Dönüş: bir şey yapıldı mı.

    Geçişten önce veritabanı dosyasının yedeği alınır (<dosya>.bak-lore-per-project).

    DAYANIKLILIK: SQLite'ta tablo yeniden adlandırma/oluşturma adımları ayrı ayrı kalıcı
    olabildiği için geçiş yarıda kesilirse eski veriler `lore_entries_old` tablosunda kalır.
    Bu fonksiyon her açılışta o tabloyu arar ve içindekileri (INSERT OR IGNORE ile, tekrar
    etmeden) yeni sözlüğe kopyalayıp tabloyu kaldırır — yani geçiş kendini onarır.
    """
    changed = False

    cols = (await conn.execute(text("PRAGMA table_info(lore_entries)"))).fetchall()
    if cols and not any(c[1] == "project_id" for c in cols):
        if db_file is not None and db_file.exists():
            backup = db_file.with_name(db_file.name + ".bak-lore-per-project")
            if not backup.exists():
                shutil.copy2(db_file, backup)
                logger.warning("Lore geçişi öncesi yedek alındı: %s", backup)

        logger.warning("Lore Sözlüğü proje bazlıya geçiriliyor (mevcut terimler her projeye kopyalanacak).")
        await conn.execute(text("DROP INDEX IF EXISTS ix_lore_entries_term"))
        await conn.execute(text("ALTER TABLE lore_entries RENAME TO lore_entries_old"))
        await conn.run_sync(Base.metadata.create_all)  # yeni lore_entries (project_id'li) oluşur
        changed = True

    if "lore_entries_old" in await _table_names(conn):
        await conn.run_sync(Base.metadata.create_all)  # yeni tablo (yarım kalmışsa) garanti olsun
        await conn.execute(text(
            "INSERT OR IGNORE INTO lore_entries (project_id, term, definition, source, created_at) "
            "SELECT p.id, o.term, o.definition, o.source, o.created_at "
            "FROM lore_entries_old o CROSS JOIN projects p"
        ))
        await conn.execute(text("DROP TABLE lore_entries_old"))
        logger.warning("Eski Lore tablosundaki terimler yeni sözlüğe kopyalandı ve eski tablo kaldırıldı.")
        changed = True

    return changed


async def add_missing_columns(conn) -> None:
    """create_all mevcut tablolara yeni sütun eklemez; eski veritabanlarına burada eklenir (veri silinmez)."""
    cols = {c[1] for c in (await conn.execute(text("PRAGMA table_info(paragraphs)"))).fetchall()}
    if cols and "uncertain_json" not in cols:
        await conn.execute(text("ALTER TABLE paragraphs ADD COLUMN uncertain_json TEXT"))
        logger.warning("paragraphs tablosuna uncertain_json sütunu eklendi.")


async def init_db() -> None:
    async with engine.begin() as conn:
        await migrate_lore_per_project(conn, _sqlite_file_from_url(config.DATABASE_URL))
        await conn.run_sync(Base.metadata.create_all)
        await add_missing_columns(conn)

    # Teşhis: her açılışta romanların sözlük boyutunu terminale yaz ("lore kayboldu mu?" sorusu için).
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT p.id, p.title, COUNT(l.id) FROM projects p "
            "LEFT JOIN lore_entries l ON l.project_id = p.id GROUP BY p.id, p.title ORDER BY p.id"
        ))).fetchall()
    for pid, title, n in rows:
        logger.info("Lore Sözlüğü: '%s' (id=%s) — %s terim", title, pid, n)


async def next_order(session: AsyncSession, model, *where) -> int:
    """Sıradaki 'order' = en büyük + 1. Sayım (count) kullanılamaz: silmeler boşluk bırakır, sayım çakışan sıra üretir."""
    result = await session.execute(select(func.coalesce(func.max(model.order), -1) + 1).where(*where))
    return result.scalar_one()


async def touch_project(session: AsyncSession, chapter_id: int) -> None:
    """Romanın 'Son güncelleme' tarihini ileri al (paragraf eklenince/değişince proje satırı kendiliğinden değişmez)."""
    project_id = select(Chapter.project_id).where(Chapter.id == chapter_id).scalar_subquery()
    await session.execute(update(Project).where(Project.id == project_id).values(updated_at=_now()))


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session