"""
Nicole — Debug testleri: flag filtresi, müdahale modalı (flag çözme), Lore silme, Editör mikrofonu ipucu
(GEÇİCİ debug parçası — debug.py tarafından dahil edilir; proje bitince hepsi birlikte silinebilir.)
"""
import time  # noqa: F401
import traceback

from fastapi import APIRouter
from sqlalchemy import select

from ... import config
from ...database import AsyncSessionLocal, Project, Chapter, Paragraph, FlaggedTerm, LoreEntry, FlagStatus  # noqa: F401
from ...services import stt_service, lore_detection, gemini_service, docx_export  # noqa: F401
from .common import _result, _Steps, _uid, _make_chain, _drop_project, _drop_lore, _lore_has  # noqa: F401

router = APIRouter()


@router.post("/test-flag-filter")
async def test_flag_filter():
    """
    Flag filtresi: 'selam', 'ben', 'nasılsınız' gibi yaygın Türkçe kelimeler flag'lenmiyor mu; gerçek
    kurgusal terimler (Rasengan, Hino kami kagura) ve Türkçe harf kuralları (İ/ı) doğru çalışıyor mu?
    """
    from ...services import lore_detection as ld

    s = _Steps()
    cands = [{"term": t, "suggestion": "x"} for t in [
        "selam", "ben", "nasılsınız", "arkadaşlar", "hey selam", "Rasengan", "chidori", "Hino kami kagura",
        "rasengan", "ab", "Sasuke'nin", "Tsukuyomi", "ışık",
    ]]
    kept = [f["term"] for f in ld.filter_flags(cands, ["Chidori", "IŞIK"])]
    for common in ("selam", "ben", "nasılsınız", "arkadaşlar", "hey selam"):
        s.check(f"Yaygın kelime elendi: {common!r}", common not in kept)
    s.check("Gerçek kurgusal terimler korundu (Rasengan, Hino kami kagura, Tsukuyomi)",
            all(t in kept for t in ("Rasengan", "Hino kami kagura", "Tsukuyomi")), str(kept))
    s.check("Zaten sözlükte olan terim elendi (Chidori, büyük/küçük harf fark etmez)", "chidori" not in kept)
    s.check("Tekrar eden terim (rasengan) tek kez geçiyor", sum(1 for t in kept if t.lower() == "rasengan") == 1)
    s.check("Çok kısa aday ('ab') elendi", "ab" not in kept)
    s.check("Türkçe harf kuralı: 'ışık' ile sözlükteki 'IŞIK' eşleşiyor", "ışık" not in kept)
    s.check("Apostroflu isim ek alınca korundu (Sasuke'nin)", "Sasuke'nin" in kept)
    s.check("tr_lower: 'İSTANBUL' -> 'istanbul', 'IŞIK' -> 'ışık'",
            ld.tr_lower("İSTANBUL") == "istanbul" and ld.tr_lower("IŞIK") == "ışık")
    s.info(f"Kelime sıklığı kaynağı: {'wordfreq (eşik ' + str(ld.COMMON_WORD_ZIPF) + ')' if ld._zipf_frequency else 'yerleşik kısa liste (pip install wordfreq ile genişler)'}")
    return _result(s.ok, steps=s.lines)


@router.post("/test-flag-resolve")
async def test_flag_resolve():
    """Müdahale modalının 4 aksiyonu + hata durumları + Lore'a otomatik ekleme kuralları."""
    from fastapi import HTTPException
    from ...schemas import FlagResolveRequest
    from ..flags import resolve_flag

    s = _Steps()
    uid = _uid()
    terms: list[str] = []

    def T(name: str) -> str:
        t = f"__dbg{uid}_{name}"
        terms.append(t)
        return t

    async def expect_400(coro) -> bool:
        try:
            await coro
            return False
        except HTTPException as exc:
            return exc.status_code == 400

    project_id = None
    try:
        async with AsyncSessionLocal() as session:
            raws = [T("raw"), T("sug"), T("man"), T("man2"), T("iptal"), T("onerisiz")]
            manual_fixed = T("duzeltilmis")
            text = " ".join(raws)
            project, _chapter, paragraph = await _make_chain(session, "__debug_flags__", text)
            project_id = project.id

            flags = []
            for i, raw in enumerate(raws):
                start = text.find(raw)
                flag = FlaggedTerm(
                    paragraph_id=paragraph.id, raw_text=raw,
                    suggested_text=None if raw.endswith("onerisiz") else f"oneri{i}",
                    status=FlagStatus.pending, position_start=start, position_end=start + len(raw),
                    order_in_paragraph=i,
                )
                session.add(flag)
                flags.append(flag)
            await session.commit()
            for f in flags:
                await session.refresh(f)
            f_raw, f_sug, f_man, f_man2, f_iptal, f_nosug = flags

            r = await resolve_flag(f_raw.id, FlagResolveRequest(action="accept_raw"), session)
            s.check("🔵 accept_raw: mavi + final=orijinal + lore'a eklendi",
                    r.status == FlagStatus.accepted_raw and r.final_text == raws[0] and await _lore_has(session, raws[0], project_id))

            r = await resolve_flag(f_sug.id, FlagResolveRequest(action="accept_suggestion"), session)
            s.check("🟢 accept_suggestion: yeşil + final=öneri + yanlış duyulan kelime lore'a EKLENMEDİ",
                    r.status == FlagStatus.accepted_suggestion and r.final_text == "oneri1" and not await _lore_has(session, raws[1], project_id))

            r = await resolve_flag(f_man.id, FlagResolveRequest(action="manual", manual_text="elle1", add_to_lore=False), session)
            s.check("🟡 manual (lore kapalı): sarı + lore'a EKLENMEDİ",
                    r.status == FlagStatus.manual and r.final_text == "elle1"
                    and not await _lore_has(session, raws[2], project_id) and not await _lore_has(session, "elle1", project_id))

            r = await resolve_flag(f_man2.id, FlagResolveRequest(action="manual", manual_text=manual_fixed, add_to_lore=True), session)
            s.check("🟡 manual (lore açık): SADECE düzeltilmiş terim lore'a eklendi",
                    r.final_text == manual_fixed and await _lore_has(session, manual_fixed, project_id) and not await _lore_has(session, raws[3], project_id))

            r = await resolve_flag(f_iptal.id, FlagResolveRequest(action="cancel"), session)
            s.check("🔴 cancel: flag pending kaldı, lore'a eklenmedi",
                    r.status == FlagStatus.pending and r.final_text is None and not await _lore_has(session, raws[4], project_id))

            s.check("Önerisi olmayan flag'de accept_suggestion -> 400",
                    await expect_400(resolve_flag(f_nosug.id, FlagResolveRequest(action="accept_suggestion"), session)))
            s.check("manual_text boşken manual -> 400",
                    await expect_400(resolve_flag(f_iptal.id, FlagResolveRequest(action="manual"), session)))
            s.check("Bilinmeyen aksiyon -> 400",
                    await expect_400(resolve_flag(f_iptal.id, FlagResolveRequest(action="uydurma"), session)))
            try:
                await resolve_flag(99999999, FlagResolveRequest(action="accept_raw"), session)
                s.check("Olmayan flag -> 404", False)
            except HTTPException as exc:
                s.check("Olmayan flag -> 404", exc.status_code == 404)
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)
            await _drop_lore(session, terms)


@router.post("/test-lore-delete")
async def test_lore_delete():
    """Lore'dan terim silince, o terimle çözülmüş flag'ler tekrar kırmızıya (pending) dönüyor mu?"""
    from ..lore import delete_lore_entry

    s = _Steps()
    term = f"__dbg{_uid()}_lore"
    project_id = None
    try:
        async with AsyncSessionLocal() as session:
            project, _chapter, paragraph = await _make_chain(session, "__debug_lore__", f"Bir {term} var.")
            project_id = project.id
            start = paragraph.text.find(term)
            flag = FlaggedTerm(
                paragraph_id=paragraph.id, raw_text=term, suggested_text="x", final_text=term,
                status=FlagStatus.accepted_raw, position_start=start, position_end=start + len(term),
                order_in_paragraph=0,
            )
            entry = LoreEntry(project_id=project_id, term=term)
            session.add_all([flag, entry])
            await session.commit()
            await session.refresh(flag)
            await session.refresh(entry)

            result = await delete_lore_entry(project_id, entry.id, session)
            await session.refresh(flag)
            s.check("Terim sözlükten silindi", not await _lore_has(session, term, project_id))
            s.check("Çözülmüş flag tekrar pending oldu",
                    flag.status == FlagStatus.pending and flag.final_text is None and flag.resolved_at is None)
            s.check("Etkilenen paragraf id'si döndü", result.get("affected_paragraph_ids") == [paragraph.id],
                    str(result.get("affected_paragraph_ids")))
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)
            await _drop_lore(session, [term])


@router.post("/test-transcribe-hint")
async def test_transcribe_hint():
    """/api/transcribe (Editör sohbeti mikrofonu): romanın Lore terimleri Whisper'a ipucu olarak gidiyor mu?"""
    import io
    from fastapi import UploadFile
    from .. import transcribe as transcribe_router

    s = _Steps()
    seen: list = []

    async def fake_transcribe(audio_bytes, vocabulary_hint=None):
        seen.append(vocabulary_hint)
        return "tamam"

    orig = transcribe_router.transcribe_audio_chunk
    term = f"__dbg{_uid()}_isim"
    project_id = None
    try:
        transcribe_router.transcribe_audio_chunk = fake_transcribe
        async with AsyncSessionLocal() as session:
            project, _ch, _p = await _make_chain(session, "__debug_hint__", "x")
            project_id = project.id
            session.add(LoreEntry(project_id=project_id, term=term))
            await session.commit()

        up = lambda: UploadFile(file=io.BytesIO(b"ses"), filename="a.webm")
        res = await transcribe_router.transcribe_only(up(), project_id)
        s.check("Cevap dönüyor", res == {"text": "tamam"})
        s.check("Roman verilince Lore terimi Whisper ipucuna giriyor", term in (seen[-1] or ""), f"ipucu: {seen[-1]!r}")
        await transcribe_router.transcribe_only(up(), None)
        s.check("Roman verilmezse terim girmiyor", term not in (seen[-1] or ""))
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        transcribe_router.transcribe_audio_chunk = orig
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)

