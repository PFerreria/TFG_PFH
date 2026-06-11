"""
STT Agent (Speech-to-Text) — transcribes emergency call audio into clean text.

Intentionally NOT a ToolCallingAgent: transcription is a deterministic
audio→text transform that needs no LLM reasoning. Wraps Whisper directly
and exposes a .run() interface compatible with the LangGraph pipeline.

Backend priority: Fireworks Whisper API → Groq Whisper API → HuggingFace API
                  → faster-whisper (local) → openai-whisper (local)

Set WHISPER_MODEL in .env to override the default ("medium").
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import wave
import struct
import math
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WHISPER_MODEL  = os.getenv("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
WHISPER_LANG   = os.getenv("WHISPER_LANG", None)
SUPPORTED_AUDIO_FORMATS = {".wav", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".webm", ".ogg", ".flac", ".aac", ".wma"}
DEFAULT_AUDIO_FOLDER = os.getenv("AUDIO_FOLDER", "data/recordings")

_model_cache: dict = {}
_model_cache_lock = threading.Lock()



@dataclass
class TranscriptionResult:
    transcript:     str
    language:       str
    language_prob:  float
    duration_sec:   float
    segments:       list = field(default_factory=list)
    model_used:     str  = WHISPER_MODEL
    backend:        str  = "none"
    processing_ms:  int  = 0
    audio_path:     str  = ""
    error:          Optional[str] = None

    def to_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Return the result serialised as a JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)



def _run_fireworks_whisper(audio_path: str, model_size: str = WHISPER_MODEL) -> TranscriptionResult:
    """Transcribe audio using the Fireworks Whisper API (whisper-v3)."""
    from openai import OpenAI

    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        raise ValueError("FIREWORKS_API_KEY environment variable not set")

    client = OpenAI(
        base_url="https://api.fireworks.ai/inference/v1",
        api_key=api_key
    )
    logger.info("[TTS] Fireworks Whisper 'whisper-v3'")

    t0 = time.perf_counter()
    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-v3",
            file=audio_file,
            language=WHISPER_LANG or "es",
        )
    processing_ms = int((time.perf_counter() - t0) * 1000)

    text = getattr(transcription, "text", "")

    return TranscriptionResult(
        transcript=text.strip(),
        language=WHISPER_LANG or "es",
        language_prob=1.0,
        duration_sec=0.0,
        segments=[],
        model_used="whisper-v3",
        backend="fireworks_whisper",
        processing_ms=processing_ms,
        audio_path=audio_path,
    )



def _run_groq_whisper(audio_path: str, model_size: str = WHISPER_MODEL) -> TranscriptionResult:
    """Transcribe audio using the Groq Whisper API (whisper-large-v3-turbo)."""
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable not set")

    client = Groq(api_key=api_key)
    logger.info("[TTS] Groq Whisper 'whisper-large-v3-turbo'")

    t0 = time.perf_counter()
    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            language=WHISPER_LANG or "es",
            response_format="verbose_json"
        )
    processing_ms = int((time.perf_counter() - t0) * 1000)

    seg_data = []
    raw_segments = getattr(transcription, "segments", []) or []
    for s in raw_segments:
        if isinstance(s, dict):
            start = s.get("start", 0.0)
            end = s.get("end", 0.0)
            text_val = s.get("text", "")
        else:
            start = getattr(s, "start", 0.0)
            end = getattr(s, "end", 0.0)
            text_val = getattr(s, "text", "")
        seg_data.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text_val.strip()
        })

    text = getattr(transcription, "text", "")
    language = getattr(transcription, "language", WHISPER_LANG or "es")

    duration = getattr(transcription, "duration", 0.0)
    if not duration and seg_data:
        duration = seg_data[-1]["end"]

    lang_prob = getattr(transcription, "language_probability", 1.0)
    if lang_prob is None:
        lang_prob = 1.0

    return TranscriptionResult(
        transcript=text.strip(),
        language=language,
        language_prob=round(lang_prob, 3),
        duration_sec=round(duration, 2),
        segments=seg_data,
        model_used="whisper-large-v3-turbo",
        backend="groq_whisper",
        processing_ms=processing_ms,
        audio_path=audio_path,
    )



def _run_hf_whisper(audio_path: str, model_size: str = WHISPER_MODEL) -> TranscriptionResult:
    """Transcribe audio using HuggingFace Inference API (openai/whisper-large-v3).

    The HF InferenceClient ASR endpoint does not accept a language parameter;
    language detection is handled server-side by the model.
    """
    from huggingface_hub import InferenceClient

    api_key = os.getenv("HF_TOKEN")
    if not api_key:
        raise ValueError("HF_TOKEN environment variable not set")

    client = InferenceClient(api_key=api_key)
    logger.info("[TTS] HuggingFace Whisper 'openai/whisper-large-v3'")

    t0 = time.perf_counter()
    result = client.automatic_speech_recognition(
        audio_path,
        model="openai/whisper-large-v3"
    )
    processing_ms = int((time.perf_counter() - t0) * 1000)

    text = getattr(result, "text", "")

    return TranscriptionResult(
        transcript=text.strip(),
        language=WHISPER_LANG or "es",
        language_prob=1.0,
        duration_sec=0.0,
        segments=[],
        model_used="openai/whisper-large-v3",
        backend="hf_whisper",
        processing_ms=processing_ms,
        audio_path=audio_path,
    )



def _run_faster_whisper(audio_path: str, model_size: str = WHISPER_MODEL) -> TranscriptionResult:
    """Transcribe audio using the faster-whisper local backend."""
    from faster_whisper import WhisperModel

    device = WHISPER_DEVICE
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    compute_type = "float16" if device == "cuda" else "int8"
    cache_key = ("faster", model_size, device, compute_type)

    with _model_cache_lock:
        if cache_key not in _model_cache:
            logger.info(f"[TTS] Loading faster-whisper '{model_size}' on {device}/{compute_type}")
            _model_cache[cache_key] = WhisperModel(model_size, device=device, compute_type=compute_type)
    model = _model_cache[cache_key]

    logger.info(f"[TTS] faster-whisper '{model_size}' on {device}/{compute_type}")

    t0 = time.perf_counter()
    segments_iter, info = model.transcribe(
        audio_path,
        language=WHISPER_LANG,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
        word_timestamps=False,
    )
    segments = list(segments_iter)
    processing_ms = int((time.perf_counter() - t0) * 1000)

    transcript = " ".join(s.text.strip() for s in segments)
    seg_data = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segments
    ]

    return TranscriptionResult(
        transcript=transcript,
        language=info.language,
        language_prob=round(info.language_probability, 3),
        duration_sec=round(info.duration, 2),
        segments=seg_data,
        model_used=model_size,
        backend="faster_whisper",
        processing_ms=processing_ms,
        audio_path=audio_path,
    )



def _run_openai_whisper(audio_path: str, model_size: str = WHISPER_MODEL) -> TranscriptionResult:
    """Transcribe audio using the openai-whisper local backend (fallback)."""
    import whisper

    cache_key = ("openai", model_size)
    with _model_cache_lock:
        if cache_key not in _model_cache:
            logger.info(f"[TTS] Loading openai-whisper '{model_size}'")
            _model_cache[cache_key] = whisper.load_model(model_size)
    model = _model_cache[cache_key]

    logger.info(f"[TTS] openai-whisper '{model_size}'")

    t0 = time.perf_counter()
    result = model.transcribe(audio_path, language=WHISPER_LANG, verbose=False)
    processing_ms = int((time.perf_counter() - t0) * 1000)

    segments = result.get("segments", [])
    seg_data = [
        {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
        for s in segments
    ]

    return TranscriptionResult(
        transcript=result["text"].strip(),
        language=result.get("language", "unknown"),
        language_prob=1.0,
        duration_sec=seg_data[-1]["end"] if seg_data else 0.0,
        segments=seg_data,
        model_used=model_size,
        backend="openai_whisper",
        processing_ms=processing_ms,
        audio_path=audio_path,
    )



class TTSAgent:
    """
    Emergency call transcription agent.

    Accepted audio formats: .wav, .m4a, .mp3, .mp4, .mpeg, .mpga, .webm, .ogg, .flac, .aac, .wma

    Default audio folder: data/recordings/
    (Override via AUDIO_FOLDER environment variable)
    """

    def __init__(self, model_size: str = WHISPER_MODEL):
        self.model_size = model_size

    def _resolve_audio_path(self, audio_path: str) -> str:
        """
        Resolve audio file path.
        """
        path = Path(audio_path)

        if path.is_absolute():
            return audio_path

        if ".." in audio_path or audio_path.startswith("."):
            return audio_path

        full_path = Path(DEFAULT_AUDIO_FOLDER) / audio_path
        return str(full_path)

    def _validate_audio_file(self, audio_path: str) -> Optional[str]:
        """
        Validate audio file existence and format.
        Returns error message if validation fails, None if valid.
        """
        path = Path(audio_path)

        if not path.exists():
            return f"Audio file not found: {audio_path}"

        file_ext = path.suffix.lower()
        if not file_ext:
            return f"File has no extension. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"

        if file_ext not in SUPPORTED_AUDIO_FORMATS:
            return f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"

        file_size_mb = path.stat().st_size / (1024 * 1024)
        if file_size_mb > 25:
            logger.warning(
                f"[TTSAgent] Large audio file ({file_size_mb:.1f} MB) may exceed "
                f"API size limits (Groq: 25 MB) — consider chunking or local backend"
            )

        return None

    def run(self, audio_path: str) -> TranscriptionResult:
        """Transcribe audio file → TranscriptionResult."""
        resolved_path = self._resolve_audio_path(audio_path)

        validation_error = self._validate_audio_file(resolved_path)
        if validation_error:
            return TranscriptionResult(
                transcript="", language="unknown", language_prob=0.0, duration_sec=0.0,
                audio_path=resolved_path, error=validation_error,
            )

        backends = []
        if os.getenv("FIREWORKS_API_KEY"):
            backends.append((_run_fireworks_whisper, "fireworks-whisper"))
        if os.getenv("GROQ_API_KEY"):
            backends.append((_run_groq_whisper, "groq-whisper"))
        if os.getenv("HF_TOKEN"):
            backends.append((_run_hf_whisper, "hf-whisper"))
        backends.append((_run_faster_whisper, "faster-whisper"))
        backends.append((_run_openai_whisper, "openai-whisper"))

        for fn, name in backends:
            try:
                result = fn(resolved_path, model_size=self.model_size)
                logger.info(
                    f"[TTSAgent] Done via {name} | {result.processing_ms}ms | "
                    f"lang={result.language}({result.language_prob:.0%}) | "
                    f"{len(result.transcript)} chars"
                )
                return result
            except ImportError:
                logger.warning(f"[TTSAgent] {name} not installed, trying next backend")
            except Exception as exc:
                logger.warning(f"[TTSAgent] {name} failed ({exc}), trying next backend")

        return TranscriptionResult(
            transcript="", language="unknown", language_prob=0.0, duration_sec=0.0,
            audio_path=resolved_path,
            error="No Whisper backend available. Run: pip install faster-whisper",
        )

    def run_to_dict(self, audio_path: str) -> dict:
        """Transcribe and return result as a plain dict."""
        return self.run(audio_path).to_dict()

    def run_to_json(self, audio_path: str) -> str:
        """Transcribe and return result as a JSON string."""
        return self.run(audio_path).to_json()


tts_agent = TTSAgent()



if __name__ == "__main__":
    import sys

    def _make_test_wav(path: str, duration: float = 2.0):
        """Generate a synthetic sine-wave WAV (no speech, tests import chain only)."""
        sr = 16000
        with wave.open(path, "w") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
            for i in range(int(sr * duration)):
                val = int(32767 * 0.3 * math.sin(2 * math.pi * 440 * i / sr))
                wf.writeframes(struct.pack("<h", val))
        return path

    audio = sys.argv[1] if len(sys.argv) > 1 else _make_test_wav("/tmp/imers_test.wav")
    print(f"[test] Audio: {audio}")

    result = TTSAgent(model_size="base").run(audio)

    print(f"Backend     : {result.backend}")
    print(f"Language    : {result.language} ({result.language_prob:.0%})")
    print(f"Duration    : {result.duration_sec}s")
    print(f"Processing  : {result.processing_ms}ms")
    print(f"Transcript  : {result.transcript or '(empty — synthetic audio has no speech)'}")
    if result.error:
        print(f"Error       : {result.error}")
