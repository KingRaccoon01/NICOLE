"""
Nicole — Konuşma → Metin (STT) servisi
faster-whisper kullanır: tamamen yerel, ücretsiz, GPU destekli (RTX 4060 için idealdir).

İlk çalıştırmada model dosyası internetten bir kez indirilir, sonrasında
tamamen çevrimdışı çalışır (model diskte varsa internete hiç sorulmaz).
"""
from __future__ import annotations
import asyncio
import io
import logging
import os
from pathlib import Path

from .. import config

logger = logging.getLogger("nicole.stt")

_model = None
_model_device = None  # gerçekte yüklenen cihaz — "cuda" istenip "cpu"ya düşülmüş olabilir
_model_lock = asyncio.Lock()

# Whisper her kelime için 0-1 arası bir "eminlik" verir. Bunun altındaki kelimeler "yanlış
# duyulmuş olabilir" diye flag taramasına özellikle bildirilir (örn. "filiz" -> "falız").
# Çok flag çıkıyorsa düşür (0.35), yanlış duyulanlar kaçıyorsa yükselt (0.65).
UNCERTAIN_WORD_PROB = float(os.getenv("WHISPER_UNCERTAIN_PROB", "0.5") or 0.5)
_WORD_PUNCT = " .,!?;:\"()[]{}«»“”‘’…-–—"


def _nvidia_dll_dirs(roots: list[str]) -> list[str]:
    """
    pip ile kurulan NVIDIA kütüphanelerinin (nvidia-cublas-cu12, nvidia-cudnn-cu12 ...)
    DLL klasörlerini bulur: <site-packages>/nvidia/<paket>/bin
    """
    found: list[str] = []
    for root in roots:
        base = Path(root) / "nvidia"
        if not base.is_dir():
            continue
        for sub in sorted(base.iterdir()):
            bin_dir = sub / "bin"
            if bin_dir.is_dir() and str(bin_dir) not in found:
                found.append(str(bin_dir))
    return found


def _register_nvidia_dll_dirs() -> None:
    """
    Windows'ta pip ile kurulan cuBLAS/cuDNN DLL'lerini Python'un arama yoluna ekler;
    böylece 'cublas64_12.dll bulunamadı' hatası için sistem PATH'ine elle bir şey eklemen gerekmez.
    """
    if os.name != "nt":
        return
    import site
    import sys
    roots = [*site.getsitepackages(), site.getusersitepackages(), *sys.path]
    dirs = _nvidia_dll_dirs([r for r in roots if r])
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except OSError:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    if dirs:
        logger.info("NVIDIA DLL klasörleri eklendi: %s", dirs)


def _effective_compute_type(device: str, compute_type: str) -> str:
    """GPU'da 'int8' (CPU ayarı) yerine float16 kullanılır: daha doğru ve GPU'da hızlı."""
    if device == "cuda" and compute_type == "int8":
        return "float16"
    return compute_type


def _cpu_threads() -> int:
    if config.WHISPER_CPU_THREADS > 0:
        return config.WHISPER_CPU_THREADS
    return max(4, (os.cpu_count() or 8) // 2)


def _load_model(device: str, compute_type: str):
    from faster_whisper import WhisperModel  # noqa: import burada yapılır, kurulu değilse app çökmesin
    if device == "cuda":
        _register_nvidia_dll_dirs()
    compute_type = _effective_compute_type(device, compute_type)
    kwargs: dict = {"device": device, "compute_type": compute_type}
    if device == "cpu":
        kwargs["cpu_threads"] = _cpu_threads()
    logger.info("Whisper modeli yükleniyor: size=%s %s", config.WHISPER_MODEL_SIZE, kwargs)
    try:
        # Model zaten diskte (önbellekte) ise internete hiç sormadan yükle.
        return WhisperModel(config.WHISPER_MODEL_SIZE, local_files_only=True, **kwargs)
    except Exception as exc:
        logger.info("Model yerel önbellekte yüklenemedi (%s) — Hugging Face'ten indirilecek/kontrol edilecek.", exc)
        return WhisperModel(config.WHISPER_MODEL_SIZE, **kwargs)


async def get_model():
    global _model, _model_device
    if _model is None:
        async with _model_lock:
            if _model is None:  # double-checked locking
                loop = asyncio.get_event_loop()
                device = config.WHISPER_DEVICE
                try:
                    _model = await loop.run_in_executor(None, _load_model, device, config.WHISPER_COMPUTE_TYPE)
                    _model_device = device
                except Exception as exc:
                    if device != "cpu":
                        logger.warning("'%s' cihazında Whisper yüklenemedi (%s) — CPU'ya düşülüyor.", device, exc)
                        _model = await loop.run_in_executor(None, _load_model, "cpu", "int8")
                        _model_device = "cpu"
                    else:
                        raise
    return _model


def is_model_loaded() -> bool:
    """Whisper modeli şu an bellekte mi? (Yüklüyse konuşma metne hemen çevrilir.)"""
    return _model is not None


def _run_warmup(model) -> None:
    """1 saniyelik sessizlikle modeli bir kez çalıştırır: ilk gerçek konuşma 'soğuk başlangıç' beklemez."""
    import numpy as np
    silence = np.zeros(16000, dtype=np.float32)
    segments, _info = model.transcribe(
        silence, language=config.WHISPER_LANGUAGE, vad_filter=False, beam_size=1, without_timestamps=True,
    )
    list(segments)  # üreteç tüketilmeden model gerçekten çalışmaz


async def preload_model() -> None:
    """
    Sunucu açılırken modeli arka planda yükler ve bir kez "ısıtır" (main.py başlatır).
    CUDA kütüphaneleri eksikse sorun ilk konuşmada değil burada ortaya çıkar ve CPU'ya
    düşülür. Başarısız olursa uygulama çalışmaya devam eder; model ilk kullanımda yeniden denenir.
    """
    try:
        model = await get_model()
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _run_warmup, model)
        except Exception as exc:
            if _model_device != "cuda":
                raise
            logger.warning("Isınma çalıştırması CUDA hatası verdi (%s) — CPU'ya düşülüyor.", exc)
            model = await _force_reload_cpu()
            await loop.run_in_executor(None, _run_warmup, model)
        logger.info("Whisper modeli önceden yüklendi ve ısıtıldı (cihaz: %s).", _model_device)
    except Exception as exc:
        logger.warning("Whisper ön yükleme başarısız (%s) — ilk kullanımda yeniden denenecek.", exc)


async def _force_reload_cpu():
    """Çalışma anında (transcribe sırasında) CUDA hatası alınırsa modeli CPU'da yeniden yükler."""
    global _model, _model_device
    async with _model_lock:
        loop = asyncio.get_event_loop()
        logger.warning("Çalışma anında CUDA hatası — model CPU'ya yeniden yükleniyor.")
        _model = await loop.run_in_executor(None, _load_model, "cpu", "int8")
        _model_device = "cpu"
    return _model


def _run_transcribe(model, audio_bytes: bytes, vocabulary_hint: str | None = None, with_words: bool = False):
    segments, _info = model.transcribe(
        io.BytesIO(audio_bytes),
        language=config.WHISPER_LANGUAGE,
        vad_filter=True,  # sessizlikleri otomatik ele, gereksiz metin üretmesin
        initial_prompt=vocabulary_hint or None,  # özel isim/terimleri doğru tanıması için ipucu
        beam_size=5,  # daha geniş arama -> daha tutarlı/doğru sonuç (varsayılandan biraz yüksek)
        temperature=0.0,  # deterministik çıktı — aynı sesi her seferinde aynı şekilde yazsın
        condition_on_previous_text=False,  # önceki (yanlış) tahminin sonraki tahmini bozmasını önler
        word_timestamps=with_words,  # kelime bazlı eminlik puanı için gerekli
    )
    segments = list(segments)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    if not with_words:
        return text
    return text, uncertain_words(segments)


def uncertain_words(segments) -> list[str]:
    """Whisper'ın eminlik puanı UNCERTAIN_WORD_PROB altında kalan kelimeler (noktalama atılmış, tekrarsız)."""
    found: list[str] = []
    for seg in segments:
        for w in getattr(seg, "words", None) or []:
            word = w.word.strip(_WORD_PUNCT)
            if word and w.probability < UNCERTAIN_WORD_PROB and word not in found:
                found.append(word)
    return found


async def transcribe_audio_chunk(audio_bytes: bytes, vocabulary_hint: str | None = None, with_words: bool = False):
    """
    Ham ses baytlarını (webm/opus veya wav) metne çevirir.

    vocabulary_hint: Whisper'a "bu terimler geçebilir" diye önceden verilen bir
    ipucu metni (initial_prompt). Whisper, az duyduğu özel isimleri/jargon
    kelimeleri (örn. "Rasengan", "Gaara") olduğundan farklı yazma eğilimindedir
    (örn. "Rahatli garayı") — bu ipucu, o kelimelerin geçme ihtimalini modele
    önceden belirterek doğru tanıma şansını belirgin şekilde artırır.

    CUDA kütüphaneleri eksikse (örn. cublas64_12.dll bulunamazsa) model yüklenmiş
    olsa bile transcribe() anında hata fırlatabilir — bu durumda otomatik olarak
    CPU'ya geçilip işlem bir kez daha denenir.

    with_words=True ise (metin, emin_olunmayan_kelimeler) döner; aksi halde sadece metin.
    """
    model = await get_model()
    loop = asyncio.get_event_loop()

    try:
        return await loop.run_in_executor(None, _run_transcribe, model, audio_bytes, vocabulary_hint, with_words)
    except Exception as exc:
        global _model_device
        if _model_device == "cuda":
            logger.warning("Transcribe CUDA hatası (%s) — CPU'ya düşülüp tekrar deneniyor.", exc)
            model = await _force_reload_cpu()
            return await loop.run_in_executor(None, _run_transcribe, model, audio_bytes, vocabulary_hint, with_words)
        raise


def current_device() -> str | None:
    """Debug paneli için: modelin gerçekte hangi cihazda çalıştığını döner."""
    return _model_device