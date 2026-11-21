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


def export_chapter_to_docx(chapter: Chapter) -> bytes:
    doc = Document()
    title = doc.add_heading(chapter.title, level=1)

    for paragraph in chapter.paragraphs:
        p = doc.add_paragraph(_resolved_text(paragraph))
        p.style.font.size = Pt(12)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def unresolved_flag_count(chapter: Chapter) -> int:
    return sum(1 for para in chapter.paragraphs for f in para.flags if f.status == FlagStatus.pending)
