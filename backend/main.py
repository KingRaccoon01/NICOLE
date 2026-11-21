"""
Nicole — Ana uygulama giriş noktası
Çalıştırmak için: uvicorn backend.main:app --reload
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": config.APP_NAME}


# --- Frontend'i statik dosya olarak sun ---
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")