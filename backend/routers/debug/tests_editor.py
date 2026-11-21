"""
Nicole — Debug testleri: Editör düzenleme izni + Geri Al ve Gemini 503/429 yedek model zinciri
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


@router.post("/test-editor-edits")
async def test_editor_edits():
    """
    'Düzenleme izni' akışı (sahte Gemini ile): replace / insert_after / delete uygulanıyor mu,
    uydurma/yabancı/boş düzenlemeler reddediliyor mu, izin kapalıyken hiçbir şey değişmiyor mu,
    'Geri al' metni VE flag'leri eski haline getiriyor mu?
    """
    import json as _json
    from ...schemas import ChatMessageIn
    from ..chat import send_message, get_history, undo_edits

    s = _Steps()
    captured: list[dict] = []
    next_text = {"value": ""}

    async def fake_post(url, body, attempts=4):
        captured.append(body)
        return {"candidates": [{"content": {"parts": [{"text": next_text["value"]}]}}]}

    async def snapshot(project_id: int):
        async with AsyncSessionLocal() as sess:
            res = await sess.execute(
                select(Paragraph).join(Chapter, Paragraph.chapter_id == Chapter.id)
                .where(Chapter.project_id == project_id).order_by(Chapter.order, Paragraph.order)
            )
            return [(p.id, p.text, p.order) for p in res.scalars().all()]

    orig_post = gemini_service._post_with_retry
    orig_key = config.GEMINI_API_KEY
    project_id = foreign_id = None
    try:
        gemini_service._post_with_retry = fake_post
        config.GEMINI_API_KEY = orig_key or "debug-dummy-key"

        async with AsyncSessionLocal() as session:
            project, chapter, p1 = await _make_chain(session, "__debug_edits__", "Birinci chidori paragraf.")
            project_id, chapter_id, p1_id = project.id, chapter.id, p1.id
            p2 = Paragraph(chapter_id=chapter_id, text="İkinci paragraf.", order=1)
            p3 = Paragraph(chapter_id=chapter_id, text="Üçüncü paragraf.", order=2)
            session.add_all([p2, p3])
            await session.commit()
            await session.refresh(p2)
            await session.refresh(p3)
            p2_id, p3_id = p2.id, p3.id
            session.add(FlaggedTerm(
                paragraph_id=p1_id, raw_text="chidori", suggested_text="caki", final_text="caki",
                status=FlagStatus.accepted_suggestion, position_start=8, position_end=15, order_in_paragraph=0,
            ))
            foreign, _fc, fpara = await _make_chain(session, "__debug_edits_foreign__", "YABANCI paragraf.")
            foreign_id, foreign_para_id = foreign.id, fpara.id
            await session.commit()

        original = await snapshot(project_id)
        s.check("Başlangıç: 3 paragraf", [t for _, t, _ in original] ==
                ["Birinci chidori paragraf.", "İkinci paragraf.", "Üçüncü paragraf."])

        # --- 1) İzin AÇIK: geçerli + geçersiz düzenlemeler karışık ---
        next_text["value"] = _json.dumps({
            "reply": "Üç şeyi değiştirdim.",
            "edits": [
                {"action": "replace", "paragraph_id": p1_id, "new_text": "Yeni birinci."},
                {"action": "insert_after", "paragraph_id": p2_id, "new_text": "Araya eklenen."},
                {"action": "delete", "paragraph_id": p3_id},
                {"action": "replace", "paragraph_id": foreign_para_id, "new_text": "SALDIRI"},   # başka projenin paragrafı
                {"action": "replace", "paragraph_id": 99999999, "new_text": "uydurma id"},        # olmayan id
                {"action": "replace", "paragraph_id": p2_id, "new_text": "   "},                  # boş metin
                {"action": "yok_boyle_aksiyon", "paragraph_id": p2_id, "new_text": "x"},          # bilinmeyen aksiyon
            ],
        }, ensure_ascii=False)
        async with AsyncSessionLocal() as session:
            reply = await send_message(
                project_id, ChatMessageIn(content="Şunu düzelt", include_context=False, allow_edits=True), session)
        body = captured[-1]
        sys_text = body["systemInstruction"]["parts"][0]["text"]
        s.check("Düzenleme modu: JSON çıktısı isteniyor", body["generationConfig"].get("responseMimeType") == "application/json")
        s.check("Düzenleme modu: bağlam zorunlu eklendi ve paragraflar [P<id>] etiketli",
                f"[P{p1_id}]" in sys_text and "DÜZENLEME İZNİ AÇIK" in sys_text)
        s.check("Sohbet cevabı düz metin olarak kaydedildi (JSON değil)", reply.content == "Üç şeyi değiştirdim.")
        s.check("Sadece 3 GEÇERLİ düzenleme uygulandı (4 geçersiz reddedildi)", len(reply.edits) == 3,
                f"{len(reply.edits)} düzenleme: {[e.action for e in reply.edits]}")

        after = await snapshot(project_id)
        s.check("Metin doğru: replace + insert_after + delete",
                [t for _, t, _ in after] == ["Yeni birinci.", "İkinci paragraf.", "Araya eklenen."],
                f"şu an: {[t for _, t, _ in after]}")
        s.check("Paragraf sıraları (order) benzersiz", len({o for _, _, o in after}) == len(after))
        foreign_after = await snapshot(foreign_id)
        s.check("Başka projenin paragrafına DOKUNULMADI", [t for _, t, _ in foreign_after] == ["YABANCI paragraf."])
        async with AsyncSessionLocal() as session:
            flags_left = (await session.execute(select(FlaggedTerm).where(FlaggedTerm.paragraph_id == p1_id))).scalars().all()
        s.check("Metni değişen paragrafın eski flag'leri temizlendi", len(flags_left) == 0)

        async with AsyncSessionLocal() as session:
            hist = await get_history(project_id, session)
        s.check("Geçmişte düzenlemeler mesaja bağlı görünüyor", len(hist[-1].edits) == 3 and not any(e.undone for e in hist[-1].edits))

        # --- 2) Geri al ---
        async with AsyncSessionLocal() as session:
            undo = await undo_edits(project_id, reply.id, session)
        s.check("Geri al: 3 düzenleme geri alındı, hata yok", undo["undone"] == 3 and not undo["errors"], str(undo))
        restored = await snapshot(project_id)
        s.check("Geri al: metinler VE sıra eski haline döndü",
                [t for _, t, _ in restored] == [t for _, t, _ in original], f"şu an: {[t for _, t, _ in restored]}")
        async with AsyncSessionLocal() as session:
            fl = (await session.execute(select(FlaggedTerm).where(FlaggedTerm.paragraph_id == p1_id))).scalars().all()
            hist2 = await get_history(project_id, session)
        s.check("Geri al: silinen flag geri geldi (yeşil, 'caki')",
                len(fl) == 1 and fl[0].status == FlagStatus.accepted_suggestion and fl[0].final_text == "caki"
                and (fl[0].position_start, fl[0].position_end) == (8, 15))
        s.check("Geri al: kayıtlar 'geri alındı' işaretlendi", all(e.undone for e in hist2[-1].edits))
        async with AsyncSessionLocal() as session:
            undo2 = await undo_edits(project_id, reply.id, session)
        s.check("İkinci kez geri al: bir şey yapmaz", undo2["undone"] == 0)

        # --- 3) İzin KAPALI: model düzenleme dönse bile uygulanmaz ---
        next_text["value"] = "Fikrim şu: bu paragraf zayıf."
        async with AsyncSessionLocal() as session:
            reply_off = await send_message(
                project_id, ChatMessageIn(content="Bu nasıl?", include_context=True, allow_edits=False), session)
        s.check("İzin kapalı: düzenleme yok, metin değişmedi",
                reply_off.edits == [] and [t for _, t, _ in await snapshot(project_id)] == [t for _, t, _ in original])

        # --- 4) İzin açık ama model bozuk JSON döndü ---
        next_text["value"] = "{bozuk json"
        async with AsyncSessionLocal() as session:
            reply_bad = await send_message(
                project_id, ChatMessageIn(content="Düzelt", include_context=True, allow_edits=True), session)
        s.check("Bozuk JSON: çökmeden düz metin cevap olarak gösterildi, düzenleme yok",
                reply_bad.edits == [] and reply_bad.content == "{bozuk json")
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        gemini_service._post_with_retry = orig_post
        config.GEMINI_API_KEY = orig_key
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)
            await _drop_project(session, foreign_id)


@router.post("/test-gemini-fallback")
async def test_gemini_fallback():
    """
    Gemini 503/429 dayanıklılığı (sahte Gemini ile): ana model "çok talep var" derse aynı modeli
    bir kez daha dener, sonra yedek modele geçiyor mu; anahtar hatasında (401) boşuna model
    değiştirmeyi bırakıyor mu; hepsi başarısızsa anlaşılır hata veriyor mu?
    """
    import httpx

    s = _Steps()
    calls: list[str] = []
    plan: dict[str, list] = {}  # model -> sırayla verilecek sonuçlar (HTTP kodu ya da "ok")

    def model_of(url: str) -> str:
        return url.split("/models/")[1].split(":")[0]

    async def fake_post(url, body, attempts=4):
        model = model_of(url)
        calls.append(model)
        outcome = plan[model].pop(0) if len(plan.get(model, [])) > 1 else plan[model][0]
        if outcome == "ok":
            return {
                "candidates": [{"content": {"parts": [{"text": f"cevap-{model}"}]}}],
                "usageMetadata": {"promptTokenCount": 1234, "candidatesTokenCount": 5},
            }
        request = httpx.Request("POST", url)
        response = httpx.Response(outcome, json={"error": {"message": f"sahte {outcome} hatası"}}, request=request)
        raise httpx.HTTPStatusError("sahte", request=request, response=response)

    orig = (gemini_service._post_with_retry, config.GEMINI_API_KEY, config.GEMINI_MODEL,
            list(config.GEMINI_FALLBACK_MODELS), gemini_service._RETRY_DELAY_SECONDS)
    try:
        gemini_service._post_with_retry = fake_post
        gemini_service._RETRY_DELAY_SECONDS = 0  # testte bekleme yok
        config.GEMINI_API_KEY = "debug-dummy-key"
        config.GEMINI_MODEL = "ana"
        config.GEMINI_FALLBACK_MODELS = ["yedek1", "yedek2"]

        async def run(new_plan):
            calls.clear()
            plan.clear()
            plan.update({k: list(v) for k, v in new_plan.items()})
            return await gemini_service.chat_full("selam", None, [])

        r = await run({"ana": ["ok"]})
        s.check("Normal durum: ana model cevap veriyor, yedeğe geçilmiyor",
                r["ok"] and r["model"] == "ana" and calls == ["ana"], f"çağrılar: {calls}")
        s.check("Token kullanımı (usageMetadata) döndürülüyor", r["usage"].get("promptTokenCount") == 1234)

        r = await run({"ana": [503, "ok"]})
        s.check("503 geçici: aynı model bir kez daha denenip cevap alındı",
                r["ok"] and r["model"] == "ana" and calls == ["ana", "ana"], f"çağrılar: {calls}")

        r = await run({"ana": [503], "yedek1": ["ok"]})
        s.check("503 kalıcı: 2 deneme sonrası yedek1'e geçildi",
                r["ok"] and r["model"] == "yedek1" and calls == ["ana", "ana", "yedek1"], f"çağrılar: {calls}")

        r = await run({"ana": [429], "yedek1": ["ok"]})
        s.check("429 (kota): beklemeden hemen yedek1'e geçildi",
                r["ok"] and r["model"] == "yedek1" and calls == ["ana", "yedek1"], f"çağrılar: {calls}")

        r = await run({"ana": [503], "yedek1": [404], "yedek2": ["ok"]})
        s.check("Yedek model kapanmışsa (404) bir sonrakine geçildi",
                r["ok"] and r["model"] == "yedek2", f"çağrılar: {calls}")

        r = await run({"ana": [403], "yedek1": ["ok"]})
        s.check("403 (anahtar/izin): yedek denenmedi, anlaşılır hata döndü",
                not r["ok"] and calls == ["ana"] and "HTTP 403" in r["reply"], f"çağrılar: {calls}")

        r = await run({"ana": [503], "yedek1": [503], "yedek2": [429]})
        s.check("Hepsi başarısız: ana modelin hatası + denenen modeller listeleniyor",
                not r["ok"] and "HTTP 503" in r["reply"] and "yedek2 (429)" in r["reply"], r["reply"][:160])
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        (gemini_service._post_with_retry, config.GEMINI_API_KEY, config.GEMINI_MODEL,
         config.GEMINI_FALLBACK_MODELS, gemini_service._RETRY_DELAY_SECONDS) = orig

