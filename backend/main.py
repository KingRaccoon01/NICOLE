"""
Nicole — Ana uygulama giriş noktası
Çalıştırmak için: uvicorn backend.main:app --reload
"""
import asyncio
import logging

import httpx

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import config
from .database import init_db
from .services import stt_service
from .routers import projects, chapters, flags, lore, chat, export, dictation_ws, debug, transcribe  # noqa: debug -> GEÇİCİ, proje bitince silinecek

# Python'ın varsayılan log seviyesi WARNING'dir — bu yüzden Ollama/Whisper/Gemini
# servislerindeki logger.info(...)/logger.warning(...) çağrıları (örn. "Ollama flag
# taraması — bilinen terimler: ... | ham yanıt: ...") şimdiye kadar terminalde HİÇ
# GÖRÜNMÜYORDU. INFO seviyesini açıkça ayarlayarak artık görünür hale getiriyoruz —
# "neden flag olmadı" gibi sorularda VSCode terminaline bakman artık işe yarayacak.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title=config.APP_NAME)

# CORS bilerek YOK: ön yüz aynı adresten sunuluyor. "*" açıkken tarayıcıda açık olan
# herhangi bir site localhost'taki romanları okuyup silebiliyordu.


@app.on_event("startup")
async def on_startup():
    await init_db()
    if config.WHISPER_PRELOAD:
        # Whisper'ı arka planda yükle: sunucu hemen açılır, ilk konuşmada bekleme olmaz.
        app.state.whisper_preload = asyncio.create_task(stt_service.preload_model())


# --- API router'ları ---
app.include_router(projects.router)
app.include_router(chapters.router)
app.include_router(flags.router)
app.include_router(lore.router)
app.include_router(chat.router)
app.include_router(export.router)
app.include_router(dictation_ws.router)
app.include_router(debug.router)  # GEÇİCİ — proje bitince bu satırı ve backend/routers/debug.py'yi sil
app.include_router(transcribe.router)


@app.middleware("http")
async def always_fresh_frontend(request, call_next):
    """
    Tarayıcı JS/CSS dosyalarını önbellekten kullanıp güncellemeyi görmeyebiliyordu (yeni index.html +
    eski settings.js -> "Ayarlar" düğmesi tepkisiz). no-cache: her açılışta "değişti mi?" diye sorar;
    değişmediyse dosya yine önbellekten gelir, yani yavaşlatmaz.
    """
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": config.APP_NAME}


@app.get("/api/status")
async def status():
    """Ayarlar panelindeki durum noktaları: Whisper / Ollama / Gemini hazır mı?"""
    ollama_ok, ollama_note = False, "ulaşılamadı — Ollama açık mı?"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            tags = (await client.get(f"{config.OLLAMA_BASE_URL}/api/tags")).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
        ollama_ok = any(n == config.OLLAMA_MODEL or n.split(":")[0] == config.OLLAMA_MODEL for n in names)
        ollama_note = config.OLLAMA_MODEL if ollama_ok else f"'{config.OLLAMA_MODEL}' indirilmemiş (ollama pull {config.OLLAMA_MODEL})"
    except Exception:
        pass
    return {
        "whisper": {"ok": stt_service.is_model_loaded(), "device": stt_service.current_device(),
                    "size": config.WHISPER_MODEL_SIZE},
        "ollama": {"ok": ollama_ok, "note": ollama_note},
        "gemini": {"ok": bool(config.GEMINI_API_KEY), "model": config.GEMINI_MODEL},
        "flag_engine": config.FLAG_ENGINE,
        "uncertain_prob": stt_service.UNCERTAIN_WORD_PROB,
    }


# --- Frontend'i statik dosya olarak sun ---
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")