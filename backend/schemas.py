"""Nicole — Pydantic şemaları (API giriş/çıkış modelleri)."""
from __future__ import annotations
import datetime as dt
from pydantic import BaseModel, ConfigDict


# --- Project ---
class ProjectCreate(BaseModel):
    title: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime


# --- Chapter ---
class ChapterCreate(BaseModel):
    title: str = "Yeni Bölüm"


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    title: str
    order: int
    created_at: dt.datetime


# --- FlaggedTerm ---
class FlaggedTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    paragraph_id: int
    raw_text: str
    suggested_text: str | None
    final_text: str | None
    status: str
    position_start: int
    position_end: int
    order_in_paragraph: int


class FlagResolveRequest(BaseModel):
    """
    Müdahale modalindeki 4 aksiyondan biri:
    - accept_raw          -> mavi, orijinal terim aynen kalır, lore'a otomatik eklenir
    - accept_suggestion   -> yeşil, AI önerisi kullanılır, lore'a otomatik eklenir
    - manual              -> sarı, kullanıcının yazdığı metin kullanılır
    - cancel              -> hiçbir şey değişmez, kırmızı flag kalır
    """
    action: str  # "accept_raw" | "accept_suggestion" | "manual" | "cancel"
    manual_text: str | None = None
    add_to_lore: bool = False  # sadece action == "manual" iken anlamlı


# --- Paragraph ---
class ParagraphOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    chapter_id: int
    text: str
    order: int
    flags: list[FlaggedTermOut] = []
    uncertain_words: list[str] = []


class ParagraphCreate(BaseModel):
    """Klavye modunda veya dikte tamamlandığında yeni paragraf eklemek için."""
    text: str


# --- LoreEntry ---
class LoreEntryCreate(BaseModel):
    term: str
    definition: str | None = None


class LoreImportRequest(BaseModel):
    from_project_id: int


class LoreEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    term: str
    definition: str | None
    source: str
    created_at: dt.datetime


# --- Chat ---
class ChatMessageIn(BaseModel):
    content: str
    include_context: bool = True
    # True ise Nicole Editör metni DOĞRUDAN düzenleyebilir (bağlam otomatik eklenir)
    allow_edits: bool = False


class EditOut(BaseModel):
    """Nicole Editör'ün metinde yaptığı tek bir düzenleme."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    action: str  # "replace" | "insert_after" | "delete"
    paragraph_id: int
    chapter_id: int
    before_text: str | None
    after_text: str | None
    undone: bool


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    created_at: dt.datetime
    edits: list[EditOut] = []
