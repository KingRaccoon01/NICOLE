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
