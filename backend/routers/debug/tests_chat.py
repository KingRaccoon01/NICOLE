"""
Nicole — Debug testleri: Editör sohbeti (hikâye bağlamı ve sohbet akışı)
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


@router.post("/test-story-context")
async def test_story_context():
    """
    'Hikaye bağlamını ekle' kutusunun backend tarafı: sıralama, bölüm başlıkları,
    çözülmüş flag'lerin nihai hali, [P<id>] etiketleri ve uzun hikâyede sınır davranışı.
    """
    from ..chat import _build_story_context

    s = _Steps()
    project_id = None
    try:
        async with AsyncSessionLocal() as session:
            project = Project(title="__debug_context__")
            session.add(project)
            await session.commit()
            await session.refresh(project)
            project_id = project.id

            # Bilerek ters sırada ekle: 2. bölüm önce, 1. bölüm sonra
            ch2 = Chapter(project_id=project.id, title="B", order=1)
            ch1 = Chapter(project_id=project.id, title="A", order=0)
            session.add_all([ch2, ch1])
            await session.commit()
            await session.refresh(ch1)
            await session.refresh(ch2)
            ch1_id, ch2_id = ch1.id, ch2.id  # rollback sonrası nesneler expire olur, id'leri şimdiden sakla
            p_flag = Paragraph(chapter_id=ch1_id, text="Takeshi chidori kullandi.", order=2)
            session.add_all([
                Paragraph(chapter_id=ch2_id, text="IKINCI-1", order=0),
                Paragraph(chapter_id=ch1_id, text="BIRINCI-2", order=1),
                Paragraph(chapter_id=ch1_id, text="BIRINCI-1", order=0),
                p_flag,
            ])
            await session.commit()
            await session.refresh(p_flag)
            p_flag_id = p_flag.id
            session.add(FlaggedTerm(
                paragraph_id=p_flag_id, raw_text="chidori", suggested_text="caki", final_text="caki",
                status=FlagStatus.accepted_suggestion, position_start=8, position_end=15, order_in_paragraph=0,
            ))
            await session.commit()

            ctx = await _build_story_context(session, project_id)
            idx = [ctx.find(x) for x in ("## Bölüm: A", "BIRINCI-1", "BIRINCI-2", "## Bölüm: B", "IKINCI-1")]
            s.check("Sıralama: bölüm sırası -> paragraf sırası, bölüm başlıklarıyla",
                    all(i >= 0 for i in idx) and idx == sorted(idx), f"konumlar: {idx}")
            s.check("Çözülmüş flag nihai haliyle yazılıyor ('caki'), 'chidori' yok",
                    "Takeshi caki kullandi." in ctx and "chidori" not in ctx)

            ctx_ids = await _build_story_context(session, project_id, with_ids=True)
            s.check("with_ids=True: paragraflar [P<id>] etiketli", f"[P{p_flag_id}] Takeshi caki kullandi." in ctx_ids)
            s.check("with_ids=False: etiket yok", "[P" not in ctx)

            empty = Project(title="__debug_context_empty__")
            session.add(empty)
            await session.commit()
            await session.refresh(empty)
            empty_id = empty.id
            empty_ctx = await _build_story_context(session, empty_id)
            s.check("Boş proje -> boş bağlam", empty_ctx == "", f"dönen: {empty_ctx!r}")
            await _drop_project(session, empty_id)

        limit = gemini_service.MAX_CONTEXT_CHARS
        medium = "x" * 9000 + " SON_ISARET"
        s.check("9000+ karakterlik hikâye artık kesilmiyor", gemini_service._fit_context(medium) == medium)
        huge = "BASLANGIC_ISARET " + "y" * (limit + 5000) + " SON_ISARET"
        fitted = gemini_service._fit_context(huge)
        s.check(f"Aşırı uzun hikâye (> {limit} karakter): SON korunuyor, BAŞ atılıyor",
                "SON_ISARET" in fitted and "BASLANGIC_ISARET" not in fitted and len(fitted) < limit + 300,
                f"{len(fitted)} karakter")
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)


@router.post("/test-chat-flow")
async def test_chat_flow():
    """
    Editör sohbetinin uçtan uca akışı (Gemini'ye gerçek istek ATMADAN):
    mesaj kaydı, bağlam açık/kapalı, Gemini'ye giden gövde (mesaj tek kez mi?),
    lore terimleri, geçmiş, temizleme.
    """
    from ...schemas import ChatMessageIn
    from ..chat import send_message, get_history, clear_history

    s = _Steps()
    captured: list[dict] = []

    async def fake_post(url, body, attempts=4):
        captured.append(body)
        return {"candidates": [{"content": {"parts": [{"text": "sahte-yanit"}]}}]}

    orig_post = gemini_service._post_with_retry
    orig_key = config.GEMINI_API_KEY
    project_id = None
    lore_term = f"__dbg{_uid()}_lore"
    try:
        async with AsyncSessionLocal() as session:
            project, _chapter, _para = await _make_chain(session, "__debug_chat__", "GIZLI_HIKAYE_METNI burada.")
            project_id = project.id
            session.add(LoreEntry(project_id=project_id, term=lore_term))
            await session.commit()

            gemini_service._post_with_retry = fake_post
            config.GEMINI_API_KEY = orig_key or "debug-dummy-key"

            reply = await send_message(project.id, ChatMessageIn(content="SORU_A", include_context=True), session)
            body = captured[-1]
            sys_text = body["systemInstruction"]["parts"][0]["text"]
            s.check("Cevap kaydedildi ve döndü", reply.content == "sahte-yanit" and reply.role == "assistant")
            s.check("Bağlam AÇIK -> hikâye metni Gemini'ye gidiyor", "GIZLI_HIKAYE_METNI" in sys_text)
            s.check("Lore terimleri Editör'e iletiliyor", lore_term in sys_text)
            s.check("Düzenleme izni kapalıyken JSON modu istenmiyor",
                    "responseMimeType" not in body["generationConfig"] and "DÜZENLEME İZNİ KAPALI" in sys_text)
            a_count = sum(1 for c in body["contents"] if c["parts"][0]["text"] == "SORU_A")
            s.check("Kullanıcı mesajı Gemini'ye TEK kez gidiyor", a_count == 1, f"{a_count} kez gitti")

            await send_message(project.id, ChatMessageIn(content="SORU_B", include_context=False), session)
            body2 = captured[-1]
            sys_text_2 = body2["systemInstruction"]["parts"][0]["text"]
            s.check("Bağlam KAPALI -> hikâye metni Gemini'ye gitmiyor", "GIZLI_HIKAYE_METNI" not in sys_text_2)
            texts2 = [c["parts"][0]["text"] for c in body2["contents"]]
            s.check("2. mesajda geçmiş doğru: [SORU_A, cevap, SORU_B]",
                    texts2 == ["SORU_A", "sahte-yanit", "SORU_B"], f"gidenler: {texts2}")

            history = await get_history(project.id, session)
            s.check("Geçmiş: 2 soru + 2 cevap = 4 kayıt", len(history) == 4, f"{len(history)} kayıt")

            cleared = await clear_history(project.id, session)
            after = await get_history(project.id, session)
            s.check("Sohbeti temizle: hepsi silindi", cleared.get("deleted") == 4 and len(after) == 0)
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        gemini_service._post_with_retry = orig_post
        config.GEMINI_API_KEY = orig_key
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)
            await _drop_lore(session, [lore_term])

