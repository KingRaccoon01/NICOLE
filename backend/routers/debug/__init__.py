"""
Nicole — DEBUG router (GEÇİCİ)
Proje bitince bu dosya + routers'dan include satırı + frontend/debug.html silinebilir.

Her uç nokta, ilgili servisi gerçekten çalıştırıp ham sonucu/hatayı döner —
"çalışıyor mu, çalışmıyorsa neden" sorusuna hızlı cevap vermek içindir.

Dosyalar: debug.py (çekirdek: sistem bilgisi + temel servis testleri), common.py (yardımcılar),
debug_tests_*.py (test grupları — hepsi aşağıda /api/debug altına dahil edilir).
"""
import time
import traceback
from collections import deque

from fastapi import APIRouter
from sqlalchemy import select

from ... import config
from ...database import AsyncSessionLocal, Project, Chapter, Paragraph, FlaggedTerm, LoreEntry, FlagStatus
from ...services import stt_service, lore_detection, gemini_service, docx_export
from .common import _result

router = APIRouter(prefix="/api/debug", tags=["debug"])


# --- Gerçek zamanlı hata günlüğü ---
# Dikte WebSocket'i gibi arka planda çalışan akışlardaki hatalar debug testlerinin
# göremediği yerlerde oluşur (sentetik test verisi değil, gerçek kullanıcı verisiyle).
# Bu yüzden bu akışlar record_error() ile buraya da yazar, debug.html bunu listeler.
_recent_errors = deque(maxlen=30)


def record_error(source: str, message: str) -> None:
    _recent_errors.appendleft({
        "time": time.strftime("%H:%M:%S"),
        "source": source,
        "message": message,
    })


@router.get("/recent-errors")
async def recent_errors():
    return list(_recent_errors)


@router.post("/clear-errors")
async def clear_errors():
    _recent_errors.clear()
    return _result(True)


@router.post("/test-internet")
async def test_internet():
    """
    İki ayrı hedefi test eder: genel internet (google.com) ve asıl kullanılan
    Gemini API adresi (generativelanguage.googleapis.com). Her ikisi de 4 deneme
    hakkına sahip (Gemini sohbetindeki retry mantığıyla tutarlı olsun diye) —
    "SSL: INVALID_SESSION_ID" gibi aralıklı hatalarda bazı denemeler başarısız
    olup sonraki başarılı olabiliyor; bu durumda attempts_needed>1 görürsün, bu
    da sorunun hâlâ var ama aralıklı yaşandığının bir göstergesidir. 4 denemenin
    TAMAMI başarısız olursa, bu artık aralıklı değil SÜREKLİ bir sorun demektir.
    """
    from ...services.http_client import new_client
    import asyncio as _asyncio

    async def _check(url: str, attempts: int = 4):
        last_error = None
        for attempt in range(1, attempts + 1):
            start = time.time()
            try:
                async with new_client(timeout=10.0) as client:
                    resp = await client.get(url)
                return {
                    "ok": True,
                    "elapsed_seconds": round(time.time() - start, 2),
                    "status_code": resp.status_code,
                    "attempts_needed": attempt,
                }
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < attempts:
                    await _asyncio.sleep(0.5)
        return {"ok": False, "error": last_error, "attempts_needed": attempts}

    google_result = await _check("https://www.google.com")
    gemini_host_result = await _check("https://generativelanguage.googleapis.com")

    overall_ok = gemini_host_result["ok"]  # asıl önemli olan bu — Gemini API'nin kendisi
    note = (
        "generativelanguage.googleapis.com'a ulaşılabiliyor — Gemini API çalışabilir, "
        "sorun varsa muhtemelen model adı/API anahtarındadır (aşağıdaki Gemini testine bak)."
        if gemini_host_result["ok"] else
        "generativelanguage.googleapis.com'a 4 denemenin TAMAMINDA da ulaşılamadı — bu artık "
        "aralıklı değil SÜREKLİ bir sorun. 'SSL: INVALID_SESSION_ID' hatası görüyorsan bu genelde "
        "bir antivirüs/güvenlik yazılımının (Kaspersky, ESET, Avast, Bitdefender vb.) HTTPS "
        "trafiğini sürekli incelemesinden kaynaklanır. En kesin teşhis: o yazılımı GEÇİCİ olarak "
        "tamamen kapatıp bu testi tekrar çalıştır — o zaman başarılı olursa sorun kesinleşir, "
        "sonra yazılımın ayarlarından bu adres için istisna eklersin. Antivirüs yoksa/etkisizse, "
        "VPN veya kurumsal bir ağ güvenlik duvarı olabilir."
    )
    if gemini_host_result.get("attempts_needed", 1) > 1 and gemini_host_result["ok"]:
        note = f"İlk denemelerde SSL hatası alındı, {gemini_host_result['attempts_needed']}. denemede başarılı oldu — bağlantı sorunu ARALIKLI, tamamen çözülmedi (antivirüs/SSL incelemesi hâlâ zaman zaman araya giriyor olabilir)."
    if not google_result["ok"] and gemini_host_result["ok"]:
        note += (" (google.com'a ulaşılamaması ayrı, önemsiz bir sorun — o adrese özel "
                  "bir SSL/antivirüs incelemesi olabilir, Gemini'yi etkilemiyor.)")

    return _result(overall_ok, google_com=google_result, generativelanguage_googleapis_com=gemini_host_result, note=note)


@router.get("/system-info")
async def system_info():
    """Aktif yapılandırmayı gösterir (API anahtarı maskelenir)."""
    masked_key = (config.GEMINI_API_KEY[:6] + "..." + config.GEMINI_API_KEY[-4:]) if config.GEMINI_API_KEY else "(boş)"
    return {
        "whisper_model_size": config.WHISPER_MODEL_SIZE,
        "whisper_device_configured": config.WHISPER_DEVICE,
        "whisper_device_actual": stt_service.current_device(),
        "whisper_compute_type": config.WHISPER_COMPUTE_TYPE,
        "whisper_model_loaded": stt_service.is_model_loaded(),
        "whisper_cpu_threads": stt_service._cpu_threads() if config.WHISPER_DEVICE == "cpu" else None,
        "ollama_base_url": config.OLLAMA_BASE_URL,
        "ollama_model": config.OLLAMA_MODEL,
        "gemini_model": config.GEMINI_MODEL,
        "gemini_fallback_models": config.GEMINI_FALLBACK_MODELS,
        "gemini_api_key_masked": masked_key,
        "database_url": config.DATABASE_URL,
    }


@router.post("/test-whisper")
async def test_whisper():
    """
    1 saniyelik sessiz bir WAV üretip Whisper'a verir. Amaç: model gerçekten
    yükleniyor mu, GPU/CPU hangisinde çalışıyor, ne kadar sürüyor — bunu görmek.
    Sessiz ses genelde boş transkript döner, bu NORMALDİR; asıl önemli olan hata
    alıp almadığı ve hangi cihazda çalıştığı.
    """
    import io, wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)  # 1 saniye sessizlik

    start = time.time()
    try:
        text = await stt_service.transcribe_audio_chunk(buf.getvalue())
        elapsed = round(time.time() - start, 2)
        return _result(
            True,
            device_used=stt_service.current_device(),
            elapsed_seconds=elapsed,
            transcript=text or "(boş — sessizlik için normal)",
            note="Model hatasız çalıştı. Gerçek sesle test etmek için mikrofonu dene.",
        )
    except Exception as exc:
        return _result(False, error=str(exc), traceback=traceback.format_exc()[-2000:])


@router.post("/test-ollama")
async def test_ollama():
    """Ollama'ya basit bir flag tespit isteği gönderir."""
    start = time.time()
    try:
        result = await lore_detection.detect_flags(
            "Takeshi elindeki chidori ile saldirdi.", known_terms=[]
        )
        elapsed = round(time.time() - start, 2)
        if not result and elapsed < 0.5:
            return _result(
                False,
                error="Ollama'ya bağlanılamadı ('All connection attempts failed') — Ollama kurulu değil veya çalışmıyor.",
                hint=(
                    "1) https://ollama.com adresinden Ollama'yı kur (kurulu değilse), "
                    "2) bir terminalde `ollama serve` çalıştır (genelde kurulumla arka planda "
                    "otomatik başlar — görev çubuğunda Ollama ikonu var mı kontrol et), "
                    "3) başka bir terminalde `ollama pull llama3` ile modeli indir (bir kereye "
                    "mahsus, ~4-5GB), 4) bu testi tekrar dene. Ollama çalışmadığı sürece flag "
                    "sistemi devre dışı kalır ama uygulama çökmez — terimler işaretlenmeden "
                    "olduğu gibi yazılır."
                ),
            )
        return _result(True, elapsed_seconds=elapsed, detected_flags=result)
    except Exception as exc:
        return _result(False, error=str(exc), traceback=traceback.format_exc()[-2000:])


@router.post("/test-gemini")
async def test_gemini():
    """Gemini API'ye 'merhaba' benzeri kısa bir test mesajı gönderir."""
    start = time.time()
    try:
        result = await gemini_service.chat_full("Bu bir test mesajı, sadece 'test başarılı' diye cevap ver.", None, [])
        elapsed = round(time.time() - start, 2)
        return _result(result["ok"], elapsed_seconds=elapsed, reply=result["reply"],
                       model=result.get("model"), usage=result.get("usage"))
    except Exception as exc:
        return _result(False, error=str(exc), traceback=traceback.format_exc()[-2000:])


@router.post("/test-db")
async def test_db():
    """Proje -> Bölüm -> Paragraf -> Flag -> Lore zincirini uçtan uca oluşturup temizler."""
    steps = []
    try:
        async with AsyncSessionLocal() as session:
            project = Project(title="__debug_test__")
            session.add(project)
            await session.commit()
            await session.refresh(project)
            steps.append(f"Proje oluşturuldu (id={project.id})")

            chapter = Chapter(project_id=project.id, title="__debug_chapter__", order=0)
            session.add(chapter)
            await session.commit()
            await session.refresh(chapter)
            steps.append(f"Bölüm oluşturuldu (id={chapter.id})")

            paragraph = Paragraph(chapter_id=chapter.id, text="debug chidori test paragrafi", order=0)
            session.add(paragraph)
            await session.commit()
            await session.refresh(paragraph)
            steps.append(f"Paragraf oluşturuldu (id={paragraph.id})")

            flag = FlaggedTerm(
                paragraph_id=paragraph.id, raw_text="chidori", suggested_text="caki",
                status=FlagStatus.pending, position_start=6, position_end=13, order_in_paragraph=0,
            )
            session.add(flag)
            await session.commit()
            await session.refresh(flag)
            steps.append(f"Flag oluşturuldu (id={flag.id})")

            flag.status = FlagStatus.accepted_suggestion
            flag.final_text = "caki"
            await session.commit()
            steps.append("Flag çözüldü (accepted_suggestion)")

            # Temizlik
            await session.delete(project)  # cascade ile chapter/paragraph/flag de gider
            await session.commit()
            steps.append("Test verisi temizlendi")

        return _result(True, steps=steps)
    except Exception as exc:
        return _result(False, steps=steps, error=str(exc), traceback=traceback.format_exc()[-2000:])


@router.post("/test-docx")
async def test_docx():
    """Örnek bir bölümden geçici olarak DOCX üretir, boyutunu döner (dosyayı diske yazmaz)."""
    try:
        async with AsyncSessionLocal() as session:
            project = Project(title="__debug_docx__")
            session.add(project)
            await session.commit()
            await session.refresh(project)

            chapter = Chapter(project_id=project.id, title="Debug Bölüm", order=0)
            session.add(chapter)
            await session.commit()
            await session.refresh(chapter)

            paragraph = Paragraph(chapter_id=chapter.id, text="Bu bir debug test paragrafidir.", order=0)
            session.add(paragraph)
            await session.commit()

            from sqlalchemy.orm import selectinload
            result = await session.execute(
                select(Chapter).where(Chapter.id == chapter.id)
                .options(selectinload(Chapter.paragraphs).selectinload(Paragraph.flags))
            )
            fresh_chapter = result.scalar_one()

            docx_bytes = docx_export.export_chapter_to_docx(fresh_chapter)

            await session.delete(project)
            await session.commit()

        return _result(True, docx_size_bytes=len(docx_bytes))
    except Exception as exc:
        return _result(False, error=str(exc), traceback=traceback.format_exc()[-2000:])


# --- Test grupları (ayrı dosyalarda; hepsi /api/debug altına dahil edilir) ---
from . import tests_chat, tests_editor, tests_lore, tests_flags, tests_system  # noqa: E402

router.include_router(tests_chat.router)
router.include_router(tests_editor.router)
router.include_router(tests_lore.router)
router.include_router(tests_flags.router)
router.include_router(tests_system.router)
