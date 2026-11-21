"""
Nicole — çekirdek mantık testleri. Gerçek romanlara dokunmaz: ayrı, geçici bir SQLite dosyası kullanır.
Çalıştır: venv\\Scripts\\python -m pytest tests
(Whisper/Ollama/Gemini'ye istek atmaz.)
"""
import asyncio
import os
import tempfile
from types import SimpleNamespace

_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB}"  # backend import edilmeden ÖNCE
os.environ["WHISPER_PRELOAD"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.services import lore_detection  # noqa: E402
from backend.services.stt_service import uncertain_words  # noqa: E402
from backend.database import AsyncSessionLocal, Paragraph  # noqa: E402
from backend.routers.flags import create_flags_for_paragraph  # noqa: E402

client = TestClient(app)
client.__enter__()  # startup -> tabloları oluştur


def _project(title="Test Romanı"):
    pid = client.post("/api/projects", json={"title": title}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/chapters", json={}).json()["id"]
    return pid, cid


def _texts(cid):
    return [p["text"] for p in client.get(f"/api/chapters/{cid}/paragraphs").json()]


def test_paragraph_order_after_delete():
    _, cid = _project()
    a, b, c = (client.post(f"/api/chapters/{cid}/paragraphs", json={"text": t}).json() for t in "ABC")
    client.delete(f"/api/paragraphs/{a['id']}")
    d = client.post(f"/api/chapters/{cid}/paragraphs", json={"text": "D"}).json()
    assert d["order"] > c["order"]  # eskiden count() -> C ile aynı sıra
    assert _texts(cid) == ["B", "C", "D"]


def test_export_turkish_title():
    pid, cid = _project("Başlangıç Şarkısı")
    client.post(f"/api/chapters/{cid}/paragraphs", json={"text": "Merhaba dünya."})
    res = client.get(f"/api/projects/{pid}/export")
    assert res.status_code == 200
    assert "UTF-8''Ba%C5%9Flang%C4%B1%C3%A7_%C5%9Eark%C4%B1s%C4%B1.docx" in res.headers["content-disposition"]


def test_updated_at_moves_when_text_added():
    pid, cid = _project()
    before = client.get(f"/api/projects/{pid}").json()["updated_at"]
    client.post(f"/api/chapters/{cid}/paragraphs", json={"text": "yeni"})
    assert client.get(f"/api/projects/{pid}").json()["updated_at"] > before


def test_lore_case_insensitive_turkish():
    pid, _ = _project()
    assert client.post(f"/api/projects/{pid}/lore", json={"term": "Işıkkılıç"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/lore", json={"term": "ışıkkılıç"}).status_code == 400


def test_chapters_rename_and_export_has_headings():
    import io
    from docx import Document

    pid, c1 = _project("İki Bölümlü")
    client.put(f"/api/chapters/{c1}", json={"title": "Başlangıç"})
    c2 = client.post(f"/api/projects/{pid}/chapters", json={"title": "Bölüm 2"}).json()["id"]
    client.post(f"/api/chapters/{c1}/paragraphs", json={"text": "birinci"})
    client.post(f"/api/chapters/{c2}/paragraphs", json={"text": "ikinci"})
    doc = Document(io.BytesIO(client.get(f"/api/projects/{pid}/export").content))
    assert [p.text for p in doc.paragraphs] == ["İki Bölümlü", "Başlangıç", "birinci", "Bölüm 2", "ikinci"]
    assert client.post("/api/projects/999999/chapters", json={}).status_code == 404


def test_lore_import_from_other_novel():
    a, _ = _project("Kaynak")
    b, _ = _project("Hedef")
    for t in ("Rasengan", "Gaara"):
        client.post(f"/api/projects/{a}/lore", json={"term": t, "definition": "tanım"})
    client.post(f"/api/projects/{b}/lore", json={"term": "gaara"})
    res = client.post(f"/api/projects/{b}/lore/import", json={"from_project_id": a}).json()
    assert res["added"] == 1  # "gaara" zaten vardı (büyük/küçük harf farkı sayılmaz)
    terms = {e["term"]: e["definition"] for e in client.get(f"/api/projects/{b}/lore").json()}
    assert terms == {"Rasengan": "tanım", "gaara": None}
    assert client.post(f"/api/projects/{b}/lore/import", json={"from_project_id": b}).status_code == 400


def test_flag_engine_chosen_in_ui_overrides_env(monkeypatch):
    from backend import config
    from backend.services import gemini_service

    async def fake_gemini(*_a, **_kw):
        return '{"flags": [{"term": "çey", "suggestion": "çay"}]}'

    monkeypatch.setattr(config, "FLAG_ENGINE", "ollama")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemini_service, "generate_json", fake_gemini)
    got = asyncio.run(lore_detection.detect_flags("Bir bardak çey.", [], uncertain_words=["çey"], engine="gemini"))
    assert got == [{"term": "çey", "suggestion": "çay"}]


def test_frontend_files_not_served_stale():
    assert client.get("/js/settings.js").headers["cache-control"] == "no-cache"


def test_no_cors_for_foreign_sites():
    res = client.get("/api/projects", headers={"Origin": "https://kotu-site.example"})
    assert "access-control-allow-origin" not in res.headers


def test_overlapping_flags_longest_wins_and_whole_words():
    _, cid = _project()

    async def run():
        async with AsyncSessionLocal() as s:
            p = Paragraph(chapter_id=cid, text="Kalibre Chidori Nagashi ile ali vurdu.", order=99)
            s.add(p)
            await s.commit()
            await create_flags_for_paragraph(s, p, [
                {"term": "chidori", "suggestion": ""},
                {"term": "Chidori Nagashi", "suggestion": ""},
                {"term": "ali", "suggestion": "Ali"},
            ])
            await s.refresh(p, attribute_names=["flags"])
            return sorted((f.raw_text, f.position_start) for f in p.flags)

    flags = asyncio.run(run())
    # "chidori" uzun terimle çakıştığı için atlandı; "ali", "Kalibre" içinde eşleşmedi
    assert flags == [("Chidori Nagashi", 8), ("ali", 28)]


def test_filter_keeps_uncertain_common_words():
    model_out = [{"term": "faiz", "suggestion": "filiz"}, {"term": "selam", "suggestion": "x"},
                 {"term": "Vorthan", "suggestion": "Vorthan"}, {"term": "gaara", "suggestion": "Gaara"}]
    got = lore_detection.filter_flags(model_out, known_terms=["Gaara"], uncertain_words=["faiz"])
    # yaygın "faiz" Whisper emin olmadığı için kaldı; "selam" elendi; sözlükteki "gaara" elendi;
    # önerisi kendisiyle aynı olan "Vorthan"ın önerisi boşaltıldı
    assert got == [{"term": "faiz", "suggestion": "filiz"}, {"term": "Vorthan", "suggestion": ""}]


def test_uncertain_words_from_whisper_segments():
    W = lambda word, prob: SimpleNamespace(word=word, probability=prob)  # noqa: E731
    segs = [SimpleNamespace(words=[W(" Küçük", 0.95), W(" bir", 0.99), W(" falız,", 0.31)]),
            SimpleNamespace(words=[W(" falız", 0.2), W(" yeşermişti.", 0.9)]), SimpleNamespace(words=None)]
    assert uncertain_words(segs) == ["falız"]


def test_uncertain_words_saved_and_cleared_and_ollama_warning(monkeypatch):
    from backend.routers import dictation_ws

    async def ollama_down(*_a, **_kw):
        return None

    monkeypatch.setattr(dictation_ws, "detect_flags", ollama_down)
    _, cid = _project()
    data = asyncio.run(dictation_ws._save_paragraph_and_scan(cid, "Küçük bir falız", uncertain_words=["falız"]))
    assert data["uncertain_words"] == ["falız"]
    assert "Ollama" in data["warning"]  # tarama yapılmadığı kullanıcıya söyleniyor

    data = asyncio.run(dictation_ws._save_paragraph_and_scan(cid, "yeşermişti", append_to=data["id"], uncertain_words=["yeşermişti"]))
    assert data["uncertain_words"] == ["falız", "yeşermişti"]

    edited = client.put(f"/api/paragraphs/{data['id']}", json={"text": "Küçük bir filiz yeşermişti"}).json()
    assert edited["uncertain_words"] == []  # elle düzeltilen metinde eski işaretler kalmaz


def test_old_database_gets_new_column():
    import sqlite3
    from sqlalchemy.ext.asyncio import create_async_engine
    from backend.database import add_missing_columns

    path = os.path.join(tempfile.mkdtemp(), "eski.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, chapter_id INT, text TEXT, \"order\" INT, created_at DATETIME)")
    raw.execute("INSERT INTO paragraphs (chapter_id, text, \"order\") VALUES (1, 'eski metin', 0)")
    raw.commit()
    raw.close()

    async def run():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with eng.begin() as conn:
            await add_missing_columns(conn)
            await add_missing_columns(conn)  # ikinci kez çalışınca hata vermemeli
        await eng.dispose()

    asyncio.run(run())
    raw = sqlite3.connect(path)
    assert raw.execute("SELECT text, uncertain_json FROM paragraphs").fetchall() == [("eski metin", None)]
    raw.close()


def test_flag_term_aligned_to_text_and_plain_common_word_dropped():
    out = lore_detection._parse_and_filter(
        '{"flags": [{"term": "faliz", "suggestion": "filiz"}, {"term": "mektup", "suggestion": ""}]}',
        "Bahçede ilk falızların yeşerdiğini gördü, masada bir mektup vardı.", [], ["falızların", "mektup"],
    )
    # model metindeki yazımı değiştirmişti -> metindeki kelimeye eşlendi; önerisiz "mektup" elendi
    assert out == [{"term": "falızların", "suggestion": "filiz"}]


def test_gemini_flag_failure_falls_back_to_ollama(monkeypatch):
    from backend import config
    from backend.services import gemini_service

    async def quota_gone(*_a, **_kw):
        raise RuntimeError("429 kota bitti")

    class FakeOllama:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw):
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"response": '{"flags": [{"term": "çey", "suggestion": "çay"}]}'})

    monkeypatch.setattr(config, "FLAG_ENGINE", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemini_service, "generate_json", quota_gone)
    monkeypatch.setattr(lore_detection.httpx, "AsyncClient", FakeOllama)
    got = asyncio.run(lore_detection.detect_flags("Bir bardak çey içtim.", [], uncertain_words=["çey"]))
    assert got == [{"term": "çey", "suggestion": "çay"}]
