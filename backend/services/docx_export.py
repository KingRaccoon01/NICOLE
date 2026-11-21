"""Nicole — Word (.docx) dışa aktarım servisi."""
from __future__ import annotations
import io

from docx import Document
from docx.shared import Pt

from ..database import Chapter, FlagStatus


def _resolved_text(paragraph) -> str:
    """
    Paragrafın nihai metnini üretir: çözülmüş flag'ler final_text ile değiştirilir,
    çözülmemiş (kırmızı) flag'ler için orijinal raw_text kullanılır.
    """
    text = paragraph.text
    # Pozisyonları sondan başa doğru işleyerek index kaymasını önle
    flags_sorted = sorted(paragraph.flags, key=lambda f: f.position_start, reverse=True)
    for flag in flags_sorted:
        replacement = flag.final_text if flag.status != FlagStatus.pending else flag.raw_text
        text = text[: flag.position_start] + (replacement or flag.raw_text) + text[flag.position_end :]
    return text


def export_project_to_docx(title: str, chapters: list[Chapter]) -> bytes:
    """Romanın tamamı tek dosyada. Bölüm başlıkları sadece birden fazla bölüm varsa yazılır."""
    doc = Document()
    doc.styles["Normal"].font.size = Pt(12)
    doc.add_heading(title, level=0)

    for chapter in chapters:
        if len(chapters) > 1:
            doc.add_heading(chapter.title, level=1)
        for paragraph in chapter.paragraphs:
            doc.add_paragraph(_resolved_text(paragraph))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def unresolved_flag_count(chapters: list[Chapter]) -> int:
    return sum(1 for ch in chapters for para in ch.paragraphs for f in para.flags if f.status == FlagStatus.pending)
