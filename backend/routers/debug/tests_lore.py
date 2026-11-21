"""
Nicole — Debug testleri: romana özel Lore Sözlüğü, kurtarma (durum/geri yükle uçları dahil)
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


@router.get("/lore-status")
async def lore_status_endpoint():
    """Teşhis: her romanın Lore Sözlüğü'nde kaç terim var, geri getirilebilecek kaynaklar neler?"""
    from ...database import _sqlite_file_from_url
    from ...services import lore_recovery
    async with AsyncSessionLocal() as session:
        return await lore_recovery.lore_status(session, _sqlite_file_from_url(config.DATABASE_URL))


@router.post("/lore-recover")
async def lore_recover_endpoint(include_flags: bool = True):
    """
    Kayıp Lore terimlerini geri yazar: yarım kalmış geçiş tablosu + yedek dosyalar + (isteğe bağlı)
    çözülmüş flag'ler. Sadece EKSİK terimleri ekler, mevcutlara dokunmaz.
    """
    from ...database import _sqlite_file_from_url
    from ...services import lore_recovery
    async with AsyncSessionLocal() as session:
        return await lore_recovery.recover_lore(
            session, _sqlite_file_from_url(config.DATABASE_URL), include_flags=include_flags)


@router.post("/test-lore-recovery")
async def test_lore_recovery():
    """
    Lore kurtarma: (1) çözülmüş flag'lerden geri yazma, (2) yedek dosyadan geri yazma,
    (3) yarım kalmış geçişin (lore_entries_old) açılışta kendini onarması.
    """
    import sqlite3
    import tempfile
    from pathlib import Path
    from sqlalchemy.ext.asyncio import create_async_engine
    from ...services import lore_recovery
    from ... import database as dbmod

    s = _Steps()
    uid = _uid()
    t_raw, t_sug, t_man, t_pen = (f"__dbg{uid}_{n}" for n in ("mavi", "yesil", "sari", "kirmizi"))
    t_bak1, t_bak2 = f"__dbg{uid}_yedek1", f"__dbg{uid}_yedek2"
    project_id = None
    try:
        async with AsyncSessionLocal() as session:
            text = " ".join([t_raw, t_sug, t_man, t_pen])
            project, _ch, para = await _make_chain(session, "__debug_recovery__", text)
            project_id, para_id = project.id, para.id
            for i, (term, status) in enumerate([
                (t_raw, FlagStatus.accepted_raw), (t_sug, FlagStatus.accepted_suggestion),
                (t_man, FlagStatus.manual), (t_pen, FlagStatus.pending),
            ]):
                st = text.find(term)
                session.add(FlaggedTerm(paragraph_id=para_id, raw_text=term, suggested_text="x", final_text="y",
                                        status=status, position_start=st, position_end=st + len(term), order_in_paragraph=i))
            await session.commit()

            others_before = (await session.execute(
                select(LoreEntry.id).where(LoreEntry.project_id != project_id))).scalars().all()

            status = await lore_recovery.lore_status(session, None)
            mine = next(p for p in status["projects"] if p["id"] == project_id)
            s.check("Durum: sözlük boş, çözülmüş flag terimleri 'eksik' görünüyor",
                    mine["lore_terms"] == 0 and mine["missing_from_lore"] == 2, str(mine))

            # 1) flag'lerden geri yazma
            rep = await lore_recovery.recover_lore(session, None, include_flags=True, project_ids=[project_id])
            mine_rep = next(p for p in rep["per_project"] if p["id"] == project_id)
            s.check("Flag'lerden: mavi + yeşil terimler geri yazıldı", mine_rep["added_from_flags"] == 2, str(mine_rep))
            s.check("Flag'lerden: sarı (manuel) ve kırmızı (bekleyen) terimler EKLENMEDİ",
                    await _lore_has(session, t_raw, project_id) and await _lore_has(session, t_sug, project_id)
                    and not await _lore_has(session, t_man, project_id) and not await _lore_has(session, t_pen, project_id))
            rep2 = await lore_recovery.recover_lore(session, None, include_flags=True, project_ids=[project_id])
            mine_rep2 = next(p for p in rep2["per_project"] if p["id"] == project_id)
            s.check("İkinci kez çalıştırınca tekrar eklemiyor (idempotent)",
                    mine_rep2["added_from_flags"] == 0 and mine_rep2["added_from_old_sources"] == 0)

            # 2) yedek dosyadan geri yazma (ESKİ şema: project_id yok)
            tmpdir = Path(tempfile.mkdtemp())
            bak = tmpdir / "nicole.db.bak-test"
            raw = sqlite3.connect(bak)
            raw.executescript(f"""
                CREATE TABLE lore_entries (id INTEGER PRIMARY KEY, term VARCHAR(200) NOT NULL, definition TEXT,
                    source VARCHAR(6) NOT NULL, created_at DATETIME NOT NULL);
                INSERT INTO lore_entries (term, definition, source, created_at) VALUES
                  ('{t_bak1}', 'tanım var', 'manual', '2026-01-01 00:00:00'),
                  ('{t_bak2}', NULL, 'auto', '2026-01-02 00:00:00');
            """)
            raw.commit(); raw.close()
            await lore_recovery.recover_lore(session, None, include_flags=False, extra_backups=[bak], project_ids=[project_id])
            row = (await session.execute(select(LoreEntry).where(
                LoreEntry.project_id == project_id, LoreEntry.term == t_bak1))).scalar_one_or_none()
            s.check("Yedek dosyadan: terimler geri yazıldı, tanım korundu",
                    row is not None and row.definition == "tanım var" and await _lore_has(session, t_bak2, project_id))
            others_after = (await session.execute(
                select(LoreEntry.id).where(LoreEntry.project_id != project_id))).scalars().all()
            s.check("Test, GERÇEK romanların sözlüğüne DOKUNMADI (kayıt sayısı aynı)",
                    sorted(others_before) == sorted(others_after), f"önce={len(others_before)} sonra={len(others_after)}")

        # 3) Yarım kalmış geçiş: lore_entries_old arta kalmış + yeni tablo boş
        db_file = tmpdir / "yarim.db"
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        try:
            async with eng.begin() as conn:
                await conn.run_sync(dbmod.Base.metadata.create_all)
            raw = sqlite3.connect(db_file)
            raw.executescript("""
                INSERT INTO projects (title, created_at, updated_at) VALUES ('R1', '2026-01-01', '2026-01-01'), ('R2', '2026-01-01', '2026-01-01');
                CREATE TABLE lore_entries_old (id INTEGER PRIMARY KEY, term VARCHAR(200), definition TEXT, source VARCHAR(6), created_at DATETIME);
                INSERT INTO lore_entries_old (term, definition, source, created_at) VALUES
                  ('Eski1', NULL, 'auto', '2026-01-01 00:00:00'), ('Eski2', 'tanım', 'manual', '2026-01-02 00:00:00');
            """)
            raw.commit(); raw.close()
            async with eng.begin() as conn:
                healed = await dbmod.migrate_lore_per_project(conn, db_file)
            chk = sqlite3.connect(db_file)
            n = chk.execute("SELECT COUNT(*) FROM lore_entries").fetchone()[0]
            tables = [r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            chk.close()
            s.check("Yarım kalmış geçiş açılışta kendini onardı (2 terim x 2 roman = 4 kayıt)", healed is True and n == 4, f"{n} kayıt")
            s.check("Onarım sonrası geçici eski tablo kaldırıldı", "lore_entries_old" not in tables)
        finally:
            await eng.dispose()
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)


@router.post("/test-lore-per-project")
async def test_lore_per_project():
    """
    Romana özel Lore Sözlüğü: iki roman birbirinin sözlüğünü görmüyor mu; aynı terim iki romanda
    ayrı ayrı durabiliyor mu; flag çözme/silme sadece kendi romanını etkiliyor mu; dikte (Whisper ipucu)
    ve Editör sohbeti sadece kendi romanının terimlerini kullanıyor mu; proje silinince sözlüğü de
    gidiyor mu; ve ESKİ (paylaşımlı) veritabanı şeması veri kaybı olmadan geçiriliyor mu?
    """
    from fastapi import HTTPException
    from ...schemas import FlagResolveRequest, LoreEntryCreate, ChatMessageIn
    from ..flags import resolve_flag
    from ..lore import list_lore, add_lore_entry, delete_lore_entry
    from ..chat import send_message
    from .. import dictation_ws

    s = _Steps()
    uid = _uid()
    t_a, t_b, t_shared = f"__dbg{uid}_sadeceA", f"__dbg{uid}_sadeceB", f"__dbg{uid}_ortak"
    captured: list[dict] = []

    async def fake_post(url, body, attempts=4):
        captured.append(body)
        return {"candidates": [{"content": {"parts": [{"text": "tamam"}]}}]}

    orig_post, orig_key = gemini_service._post_with_retry, config.GEMINI_API_KEY
    a_id = b_id = None
    try:
        async with AsyncSessionLocal() as session:
            proj_a, ch_a, para_a = await _make_chain(session, "__debug_loreA__", f"Metin {t_shared} burada.")
            proj_b, ch_b, para_b = await _make_chain(session, "__debug_loreB__", f"Metin {t_shared} burada.")
            a_id, b_id, ch_a_id, ch_b_id = proj_a.id, proj_b.id, ch_a.id, ch_b.id
            pa_id, pb_id = para_a.id, para_b.id

            # --- Sözlük izolasyonu ---
            entry_a = await add_lore_entry(a_id, LoreEntryCreate(term=t_a), session)
            await add_lore_entry(b_id, LoreEntryCreate(term=t_b), session)
            a_terms = [e.term for e in await list_lore(a_id, None, session)]
            b_terms = [e.term for e in await list_lore(b_id, None, session)]
            s.check("A'nın sözlüğünde sadece A'nın terimi var", t_a in a_terms and t_b not in a_terms, str(a_terms))
            s.check("B'nin sözlüğünde sadece B'nin terimi var", t_b in b_terms and t_a not in b_terms, str(b_terms))
            try:
                await add_lore_entry(a_id, LoreEntryCreate(term=t_a), session)
                s.check("Aynı romanda aynı terim tekrar eklenemez (400)", False)
            except HTTPException as exc:
                s.check("Aynı romanda aynı terim tekrar eklenemez (400)", exc.status_code == 400)
            try:
                await add_lore_entry(99999999, LoreEntryCreate(term="x"), session)
                s.check("Olmayan romana terim eklenemez (404)", False)
            except HTTPException as exc:
                s.check("Olmayan romana terim eklenemez (404)", exc.status_code == 404)

            # --- Flag çözme sadece kendi romanının sözlüğüne ekler ---
            flags = {}
            for pid, key in ((pa_id, "a"), (pb_id, "b")):
                text = f"Metin {t_shared} burada."
                st = text.find(t_shared)
                f = FlaggedTerm(paragraph_id=pid, raw_text=t_shared, suggested_text="x", status=FlagStatus.pending,
                                position_start=st, position_end=st + len(t_shared), order_in_paragraph=0)
                session.add(f)
                flags[key] = f
            await session.commit()
            await session.refresh(flags["a"])
            await session.refresh(flags["b"])
            fa_id, fb_id = flags["a"].id, flags["b"].id

            await resolve_flag(fa_id, FlagResolveRequest(action="accept_raw"), session)
            s.check("A'da flag çözülünce terim SADECE A'nın sözlüğüne eklendi",
                    await _lore_has(session, t_shared, a_id) and not await _lore_has(session, t_shared, b_id))
            await resolve_flag(fb_id, FlagResolveRequest(action="accept_raw"), session)
            s.check("Aynı terim B'nin sözlüğüne de AYRICA eklenebildi",
                    await _lore_has(session, t_shared, a_id) and await _lore_has(session, t_shared, b_id))

            # --- Silme sadece kendi romanını etkiler ---
            shared_entry_a = (await session.execute(
                select(LoreEntry).where(LoreEntry.project_id == a_id, LoreEntry.term == t_shared))).scalar_one()
            try:
                await delete_lore_entry(b_id, shared_entry_a.id, session)
                s.check("Başka romanın terimi silinemez (404)", False)
            except HTTPException as exc:
                s.check("Başka romanın terimi silinemez (404)", exc.status_code == 404)

            await delete_lore_entry(a_id, shared_entry_a.id, session)
            await session.refresh(flags["a"])
            await session.refresh(flags["b"])
            s.check("A'dan silince A'daki flag tekrar kırmızı (pending)", flags["a"].status == FlagStatus.pending)
            s.check("B'deki flag ve B'nin sözlüğü ETKİLENMEDİ",
                    flags["b"].status == FlagStatus.accepted_raw and await _lore_has(session, t_shared, b_id))

            # --- Dikte (Whisper ipucu + Ollama'ya bilinen terimler) romana özel ---
            hint_a = await dictation_ws._build_vocabulary_hint(ch_a_id)
            s.check("Whisper ipucu: A bölümü sadece A'nın terimlerini içeriyor", t_a in hint_a and t_b not in hint_a)
            hint_b = await dictation_ws._build_vocabulary_hint(ch_b_id)
            s.check("Whisper ipucu: B bölümü sadece B'nin terimlerini içeriyor", t_b in hint_b and t_a not in hint_b)

            # --- Editör sohbeti romana özel ---
            gemini_service._post_with_retry = fake_post
            config.GEMINI_API_KEY = orig_key or "debug-dummy-key"
            await send_message(a_id, ChatMessageIn(content="selam", include_context=False), session)
            sys_a = captured[-1]["systemInstruction"]["parts"][0]["text"]
            s.check("Editör sohbeti: sadece o romanın terimlerini görüyor", t_a in sys_a and t_b not in sys_a)

            # --- Proje silinince sözlüğü de gider ---
            await _drop_project(session, b_id)
            left = (await session.execute(select(LoreEntry).where(LoreEntry.project_id == b_id))).scalars().all()
            s.check("Roman silinince sözlüğü de silindi", len(left) == 0)
            b_id = None

        # --- Eski (paylaşımlı) şemadan geçiş: GERÇEK bir geçici SQLite dosyasında ---
        import sqlite3, tempfile, os as _os
        from pathlib import Path
        from sqlalchemy.ext.asyncio import create_async_engine
        from ... import database as dbmod

        tmpdir = tempfile.mkdtemp()
        db_file = Path(tmpdir) / "eski.db"
        raw = sqlite3.connect(db_file)
        raw.executescript("""
            CREATE TABLE projects (id INTEGER PRIMARY KEY, title VARCHAR(200));
            INSERT INTO projects (id, title) VALUES (1, 'Roman 1'), (2, 'Roman 2');
            CREATE TABLE lore_entries (
                id INTEGER PRIMARY KEY, term VARCHAR(200) NOT NULL, definition TEXT,
                source VARCHAR(6) NOT NULL, created_at DATETIME NOT NULL, UNIQUE (term));
            CREATE UNIQUE INDEX ix_lore_entries_term ON lore_entries (term);
            INSERT INTO lore_entries (term, definition, source, created_at) VALUES
              ('Rasengan', 'bir teknik', 'manual', '2026-01-01 00:00:00'),
              ('Gaara', NULL, 'auto', '2026-01-02 00:00:00'),
              ('Konoha', NULL, 'auto', '2026-01-03 00:00:00');
        """)
        raw.commit()
        raw.close()

        eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        try:
            async with eng.begin() as conn:
                migrated = await dbmod.migrate_lore_per_project(conn, db_file)
                await conn.run_sync(dbmod.Base.metadata.create_all)
            async with eng.begin() as conn:
                migrated_again = await dbmod.migrate_lore_per_project(conn, db_file)
            s.check("Eski şema algılandı ve geçiş yapıldı", migrated is True)
            s.check("Geçiş tekrar çalıştırılırsa bir şey yapmaz (idempotent)", migrated_again is False)
            s.check("Geçiş öncesi veritabanı yedeği alındı",
                    (Path(tmpdir) / "eski.db.bak-lore-per-project").exists())
            chk = sqlite3.connect(db_file)
            rows = chk.execute("SELECT project_id, term, definition, source FROM lore_entries ORDER BY project_id, term").fetchall()
            tables = [r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            try:
                chk.execute("INSERT INTO lore_entries (project_id, term, source, created_at) VALUES (1, 'Gaara', 'manual', '2026-01-04')")
                dup_blocked = False
            except sqlite3.IntegrityError:
                dup_blocked = True
            chk.close()
            s.check("Her terim HER romana kopyalandı (3 terim x 2 roman = 6 kayıt), veri kaybı yok",
                    len(rows) == 6 and {r[1] for r in rows if r[0] == 1} == {"Rasengan", "Gaara", "Konoha"}
                    and {r[1] for r in rows if r[0] == 2} == {"Rasengan", "Gaara", "Konoha"}, f"{len(rows)} kayıt")
            s.check("Tanım ve kaynak bilgisi korundu",
                    ("Rasengan", "bir teknik", "manual") in {(r[1], r[2], r[3]) for r in rows})
            s.check("Aynı romanda aynı terim artık veritabanı seviyesinde de engelli", dup_blocked)
            s.check("Geçici eski tablo temizlendi", "lore_entries_old" not in tables)
        finally:
            await eng.dispose()
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        gemini_service._post_with_retry = orig_post
        config.GEMINI_API_KEY = orig_key
        async with AsyncSessionLocal() as session:
            await _drop_project(session, a_id)
            await _drop_project(session, b_id)


@router.api_route("/cleanup-test-data", methods=["GET", "POST"])
async def cleanup_test_data():
    """
    Debug testlerinden kalmış artıkları siler: adı '__dbg' ile başlayan Lore terimleri ve
    '__debug' ile başlayan test romanları. Gerçek terimlerine/romanlarına dokunmaz.
    Tarayıcıdan doğrudan açabilirsin: http://127.0.0.1:8000/api/debug/cleanup-test-data
    """
    from sqlalchemy import delete
    async with AsyncSessionLocal() as session:
        lore_result = await session.execute(
            delete(LoreEntry).where(LoreEntry.term.like(r"\_\_dbg%", escape="\\"))
        )
        projects = (await session.execute(
            select(Project).where(Project.title.like(r"\_\_debug%", escape="\\"))
        )).scalars().all()
        for p in projects:
            await session.delete(p)
        await session.commit()
    return {"ok": True, "silinen_lore_terimi": lore_result.rowcount, "silinen_test_romani": len(projects)}