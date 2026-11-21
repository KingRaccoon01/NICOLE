"""
Nicole — Debug testleri: kurulum, Ollama modeli, Whisper yükleme/durum, dikte hattı, DOCX içeriği
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


@router.post("/test-env")
async def test_env():
    """Kurulum kontrolü: .env, API anahtarı, Python sürümü, gerekli paketler."""
    import importlib.util
    import shutil
    import sys

    s = _Steps()
    env_path = config.BASE_DIR / ".env"
    s.check(".env dosyası proje kökünde", env_path.exists(), str(env_path))
    s.check("GEMINI_API_KEY dolu", bool(config.GEMINI_API_KEY), "boşsa Editör sohbeti çalışmaz")
    s.check(f"Python sürümü ({sys.version.split()[0]}) >= 3.10", sys.version_info >= (3, 10))

    packages = [
        ("fastapi", ("fastapi",)),
        ("sqlalchemy", ("sqlalchemy",)),
        ("aiosqlite", ("aiosqlite",)),
        ("httpx", ("httpx",)),
        ("python-docx", ("docx",)),
        ("faster-whisper", ("faster_whisper",)),
        ("python-multipart", ("multipart", "python_multipart")),
        ("python-dotenv", ("dotenv",)),
    ]
    for pip_name, modules in packages:
        found = any(importlib.util.find_spec(m) is not None for m in modules)
        s.check(f"paket: {pip_name}", found, "" if found else f"pip install {pip_name}")

    s.info(f"ffmpeg PATH'te: {'evet' if shutil.which('ffmpeg') else 'hayır'} "
           "(faster-whisper kendi ses çözücüsünü getirir, çoğu zaman gerekmez)")
    s.info(f"Whisper: model={config.WHISPER_MODEL_SIZE}, cihaz={config.WHISPER_DEVICE}, dil={config.WHISPER_LANGUAGE}")
    return _result(s.ok, steps=s.lines)


@router.post("/test-ollama-model")
async def test_ollama_model():
    """Ollama çalışıyor mu VE .env'deki model gerçekten indirilmiş mi?"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{config.OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return _result(
            False,
            error=f"{type(exc).__name__}: {exc}",
            hint="Ollama çalışmıyor veya adres yanlış. `ollama serve` çalıştır, OLLAMA_BASE_URL'i kontrol et.",
        )

    names = [m.get("name", "") for m in data.get("models", [])]
    wanted = config.OLLAMA_MODEL
    installed = any(n == wanted or n == f"{wanted}:latest" for n in names)
    return _result(
        installed,
        wanted_model=wanted,
        installed_models=names,
        hint=None if installed else f"Model kurulu değil — terminalde: ollama pull {wanted}",
    )


@router.post("/test-transcribe-status")
async def test_transcribe_status():
    """
    Whisper 'model yüklü mü' durumu: /api/transcribe/status doğru raporluyor mu ve yüklü model
    her konuşmada YENİDEN yüklenmiyor mu? (Gerçek model yüklemez; sahte bir model nesnesiyle dener,
    sonra gerçek durumu geri koyar.)
    """
    from .. import transcribe as transcribe_router
    from ...services import stt_service

    s = _Steps()
    orig = (stt_service._model, stt_service._model_device, stt_service._load_model)
    loads: list[int] = []
    try:
        stt_service._model = None
        stt_service._model_device = None
        st = await transcribe_router.transcribe_status()
        s.check("Model yüklü değilken status: loaded=false", st["loaded"] is False, str(st))

        stt_service._model = object()
        stt_service._model_device = "cpu"
        st = await transcribe_router.transcribe_status()
        s.check("Model yüklüyken status: loaded=true + cihaz bilgisi", st["loaded"] is True and st["device"] == "cpu", str(st))

        stt_service._load_model = lambda device, compute: loads.append(1) or object()
        m1 = await stt_service.get_model()
        m2 = await stt_service.get_model()
        s.check("Yüklü model her konuşmada yeniden yüklenmiyor", m1 is m2 and not loads, f"{len(loads)} kez yüklendi")
        s.info(f"Ön yükleme (WHISPER_PRELOAD): {'açık' if config.WHISPER_PRELOAD else 'kapalı'}")
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        stt_service._model, stt_service._model_device, stt_service._load_model = orig


@router.post("/test-whisper-loading")
async def test_whisper_loading():
    """
    Whisper yükleme mantığı (GERÇEK model yüklemeden, sahte faster_whisper ile): model diskte varsa
    internete hiç sorulmuyor mu, yoksa indirmeye düşüyor mu; CPU iş parçacıkları; GPU'da float16;
    pip ile kurulan NVIDIA DLL klasörleri bulunuyor mu; ısınma + CUDA hatasında CPU'ya düşme.
    """
    import os
    import sys
    import tempfile
    import types
    from pathlib import Path

    from ...services import stt_service

    s = _Steps()
    calls: list[dict] = []
    state = {"cached": True}

    class FakeModel:
        def __init__(self, device, fail_cuda=False):
            self.device, self.fail_cuda = device, fail_cuda

        def transcribe(self, audio, **kw):
            if self.device == "cuda" and self.fail_cuda:
                raise RuntimeError("Library cublas64_12.dll is not found")
            return iter([]), None

    class FakeWhisperModel:
        def __new__(cls, size, **kwargs):
            calls.append(dict(kwargs))
            if kwargs.get("local_files_only") and not state["cached"]:
                raise FileNotFoundError("model önbellekte yok")
            return FakeModel(kwargs["device"], fail_cuda=state.get("fail_cuda", False))

    orig_module = sys.modules.get("faster_whisper")
    orig_state = (stt_service._model, stt_service._model_device, config.WHISPER_DEVICE,
                  config.WHISPER_COMPUTE_TYPE, config.WHISPER_CPU_THREADS)
    try:
        sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=FakeWhisperModel)

        # 1) Model önbellekteyse: tek çağrı, yerel dosyalardan -> internete HİÇ sorulmaz
        calls.clear(); state["cached"] = True
        stt_service._load_model("cpu", "int8")
        s.check("Model diskte varsa internete sorulmadan yükleniyor (local_files_only=True, tek çağrı)",
                len(calls) == 1 and calls[0].get("local_files_only") is True, str(calls))

        # 2) Önbellekte yoksa: indirmeye düşer
        calls.clear(); state["cached"] = False
        stt_service._load_model("cpu", "int8")
        s.check("Model diskte yoksa indirme moduna düşüyor (2. çağrı local_files_only olmadan)",
                len(calls) == 2 and "local_files_only" not in calls[1], str(calls))
        state["cached"] = True

        # 3) CPU iş parçacıkları
        config.WHISPER_CPU_THREADS = 0
        auto = stt_service._cpu_threads()
        s.check("CPU iş parçacığı otomatik: en az 4", auto >= 4, f"{auto} iş parçacığı ({os.cpu_count()} mantıksal çekirdek)")
        config.WHISPER_CPU_THREADS = 6
        s.check("WHISPER_CPU_THREADS=6 verilince 6 kullanılıyor", stt_service._cpu_threads() == 6)
        calls.clear()
        stt_service._load_model("cpu", "int8")
        s.check("CPU yüklemesine cpu_threads iletiliyor", calls[0].get("cpu_threads") == 6, str(calls[0]))

        # 4) GPU: float16 + cpu_threads yok
        calls.clear()
        stt_service._load_model("cuda", "int8")
        s.check("GPU'da 'int8' ayarı otomatik float16'ya çevriliyor",
                calls[0].get("compute_type") == "float16" and "cpu_threads" not in calls[0], str(calls[0]))
        calls.clear()
        stt_service._load_model("cuda", "int8_float16")
        s.check("GPU'da açıkça seçilen tür (int8_float16) korunuyor", calls[0].get("compute_type") == "int8_float16")

        # 5) pip ile kurulan NVIDIA DLL klasörlerini bulma
        root = Path(tempfile.mkdtemp())
        for pkg in ("cublas", "cudnn"):
            (root / "nvidia" / pkg / "bin").mkdir(parents=True)
        (root / "nvidia" / "bos_paket").mkdir()  # bin yok -> atlanmalı
        dirs = stt_service._nvidia_dll_dirs([str(root), str(root / "yok")])
        s.check("NVIDIA DLL klasörleri bulundu (cublas, cudnn), bin'i olmayan paket atlandı",
                len(dirs) == 2 and all(d.endswith("bin") for d in dirs), str(dirs))

        # 6) Isınma + CUDA hatasında CPU'ya düşme (sunucu açılışında, ilk konuşmada değil)
        stt_service._model = None
        stt_service._model_device = None
        config.WHISPER_DEVICE = "cuda"
        state["fail_cuda"] = True
        calls.clear()
        await stt_service.preload_model()
        s.check("CUDA kütüphanesi eksikse açılışta CPU'ya düşülüyor, uygulama çalışmaya devam ediyor",
                stt_service.current_device() == "cpu" and stt_service.is_model_loaded(),
                f"cihaz: {stt_service.current_device()}")

        stt_service._model = None
        stt_service._model_device = None
        config.WHISPER_DEVICE = "cuda"
        state["fail_cuda"] = False
        await stt_service.preload_model()
        s.check("CUDA sağlamsa model GPU'da yüklenip ısıtılıyor", stt_service.current_device() == "cuda")
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        if orig_module is None:
            sys.modules.pop("faster_whisper", None)
        else:
            sys.modules["faster_whisper"] = orig_module
        (stt_service._model, stt_service._model_device, config.WHISPER_DEVICE,
         config.WHISPER_COMPUTE_TYPE, config.WHISPER_CPU_THREADS) = orig_state


@router.post("/test-dictation-flow")
async def test_dictation_flow():
    """
    Dikte hattının Whisper/Ollama OLMADAN test edilebilen kısmı: kontrol mesajı
    ayrıştırma, yeni paragraf, mevcut paragrafa EKLEME (append) sırasında flag
    pozisyonları, geçersiz append_to ve Whisper ipucu metni.
    """
    from .. import dictation_ws

    s = _Steps()
    uid = _uid()
    term = f"__dbg{uid}_chidori"
    lore_term = f"__dbg{uid}_lore"

    cases = [
        ("__END_UTTERANCE__", (True, None)),
        ('{"type":"end_utterance","append_to":7}', (True, 7)),
        ('{"type":"end_utterance","append_to":null}', (True, None)),
        ('{"type":"end_utterance"}', (True, None)),
        ("saçma metin", (False, None)),
        ('{"type":"baska"}', (False, None)),
    ]
    for raw, expected in cases:
        got = dictation_ws._parse_control_message(raw)
        s.check(f"Kontrol mesajı {raw[:38]!r}", got == expected, f"dönen: {got}")

    seen_known: list[list[str]] = []

    async def fake_detect(text, known_terms):
        seen_known.append(list(known_terms))
        return [{"term": term, "suggestion": "caki"}] if term.lower() in text.lower() else []

    orig_detect = dictation_ws.detect_flags
    project_id = None
    try:
        dictation_ws.detect_flags = fake_detect
        async with AsyncSessionLocal() as session:
            project, chapter, _ = await _make_chain(session, "__debug_dictation__", None)
            project_id = project.id
            session.add(LoreEntry(project_id=project_id, term=lore_term))
            await session.commit()

        p1 = await dictation_ws._save_paragraph_and_scan(chapter.id, f"Takeshi {term} kullandi.")
        s.check("Yeni paragraf: append değil, 1 flag", not p1["is_append"] and len(p1["flags"]) == 1)
        f1 = p1["flags"][0]
        s.check("Yeni paragraf: flag pozisyonu metinle uyuşuyor",
                p1["text"][f1["position_start"]:f1["position_end"]] == term)

        p2 = await dictation_ws._save_paragraph_and_scan(chapter.id, f"Sonra {term} tekrar.", append_to=p1["id"])
        s.check("Append: aynı paragraf, is_append=True, toplam 2 flag",
                p2["is_append"] and p2["id"] == p1["id"] and len(p2["flags"]) == 2)
        ok_pos = all(p2["text"][f["position_start"]:f["position_end"]] == term for f in p2["flags"])
        s.check("Append: İKİ flag'in pozisyonu da birleşik metinle uyuşuyor", ok_pos)
        s.check("Append: order_in_paragraph kesintisiz (0,1)",
                sorted(f["order_in_paragraph"] for f in p2["flags"]) == [0, 1])

        p3 = await dictation_ws._save_paragraph_and_scan(chapter.id, "Düz cümle.", append_to=999999999)
        s.check("Geçersiz append_to -> yeni paragraf açılır", not p3["is_append"] and p3["id"] != p1["id"])

        s.check("Ollama'ya bilinen terimler (lore) iletiliyor", any(lore_term in k for k in seen_known))

        hint = await dictation_ws._build_vocabulary_hint(chapter.id)
        s.check("Whisper ipucu metni lore terimlerini içeriyor", lore_term in hint)
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        dictation_ws.detect_flags = orig_detect
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)
            await _drop_lore(session, [lore_term])


@router.post("/test-docx-content")
async def test_docx_content():
    """DOCX'in İÇERİĞİNİ doğrular: çözülmüş flag'ler final_text ile, bekleyenler orijinal haliyle yazılıyor mu?"""
    import io
    from docx import Document
    from sqlalchemy.orm import selectinload

    s = _Steps()
    project_id = None
    try:
        async with AsyncSessionLocal() as session:
            text = "Takeshi chidori ile rasengan kullandi."
            project, chapter, paragraph = await _make_chain(session, "__debug_docx_content__", text)
            project_id = project.id
            chapter.title = "Debug Baslik"

            a0 = text.find("chidori")
            b0 = text.find("rasengan")
            session.add_all([
                FlaggedTerm(paragraph_id=paragraph.id, raw_text="chidori", suggested_text="caki", final_text="caki",
                            status=FlagStatus.accepted_suggestion, position_start=a0, position_end=a0 + 7, order_in_paragraph=0),
                FlaggedTerm(paragraph_id=paragraph.id, raw_text="rasengan", suggested_text="top",
                            status=FlagStatus.pending, position_start=b0, position_end=b0 + 8, order_in_paragraph=1),
            ])
            await session.commit()

            result = await session.execute(
                select(Chapter).where(Chapter.id == chapter.id)
                .options(selectinload(Chapter.paragraphs).selectinload(Paragraph.flags))
            )
            fresh = result.scalar_one()
            docx_bytes = docx_export.export_chapter_to_docx(fresh)
            unresolved = docx_export.unresolved_flag_count(fresh)

            doc = Document(io.BytesIO(docx_bytes))
            paragraphs = [p.text for p in doc.paragraphs]
            joined = "\n".join(paragraphs)
            s.check("Bölüm başlığı yazıldı", "Debug Baslik" in paragraphs)
            s.check("Çözülmüş flag: 'caki' yazıldı, 'chidori' yazılmadı", "caki" in joined and "chidori" not in joined)
            s.check("Bekleyen flag: orijinal 'rasengan' korundu", "rasengan" in joined)
            s.check("Paragraf tam beklenen metin",
                    "Takeshi caki ile rasengan kullandi." in paragraphs, f"yazılan: {paragraphs[1:] }")
            s.check("Çözülmemiş flag sayacı = 1", unresolved == 1, f"dönen: {unresolved}")
        return _result(s.ok, steps=s.lines)
    except Exception as exc:
        return _result(False, steps=s.lines, error=str(exc), traceback=traceback.format_exc()[-2000:])
    finally:
        async with AsyncSessionLocal() as session:
            await _drop_project(session, project_id)

