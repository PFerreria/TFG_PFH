"""Incremental Whisper transcription with silence-driven pipeline triggers."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE             = 16_000
SAMPLE_WIDTH            = 2
FLUSH_SECONDS           = float(os.getenv("IMERS_FLUSH_SECONDS",           "1.5"))
OVERLAP_SECONDS         = float(os.getenv("IMERS_OVERLAP_SECONDS",         "0.5"))
EARLY_TRIGGER_WORDS     = int(  os.getenv("IMERS_EARLY_TRIGGER_WORDS",     "20"))
SILENCE_TRIGGER_WORDS   = int(  os.getenv("IMERS_SILENCE_TRIGGER_WORDS",   "10"))

SILENT_FLUSHES_FOR_TRIGGER = int(  os.getenv("IMERS_SILENT_FLUSHES",     "3"))
SILENCE_WATCHDOG_S         = float(os.getenv("IMERS_SILENCE_WATCHDOG_S", "5.0"))

FLUSH_BYTES    = int(SAMPLE_RATE * FLUSH_SECONDS * SAMPLE_WIDTH)
OVERLAP_BYTES  = int(SAMPLE_RATE * OVERLAP_SECONDS * SAMPLE_WIDTH)

_LANG = os.getenv("WHISPER_LANG", "es")

_SEVILLA_PROMPT = (
    "Emergencia 112 Sevilla. "
    "Unidad enviada a Barriada Hytasa, Eritaña, Rochelambert, Borceguinería. "
    "Zona de Heliópolis, Barrio de los Bermejales, Ronda del Tamarguillo. "
    "Avenida Doctor Fedriani, Calle Ximénez de Enciso, Calle Jáuregui. "
    "Avenida Kansas City, Calle Peris Mencheta, Barriada de Barzola. "
    "Barriada Worner, Avenida Cardenal Ilundain, Avenida Roberto Osborne. "
    "Avenida General Merry, Avenida Marqués de Pickman, Calle Molini. "
    "Calle Recaredo, Calle Daoíz, Calle Fabie. "
    "Avenida de la Buhaira, Calle Alhóndiga, Calle Almotamid, Calle Levíes. "
    "Calle Matahacas, Calle Tarfia, Calle Chicarreros, Calle Molviedro. "
    "Bami, Amate, Tharsis, Calle Enladrillada, Avenida del Guadalhorce. "
    "Calle Parsi, Calle Pero Mingo, Calle Soleá, Polígono Sur, Polígono Norte. "
    "Hospital Virgen del Rocío, Hospital Virgen de Valme, Hospital Virgen Macarena. "
    "El incidente está en Calle Sierpes, frente a Calle Laraña, dirección Calle Sinaí."
)

WHISPER_PROMPT = os.getenv("IMERS_WHISPER_PROMPT") or _SEVILLA_PROMPT

_SEVILLA_HOTWORDS = " ".join([
    "Sierpes", "Sierpes",
    "Laraña",  "Laraña",
    "Sinaí",   "Sinaí",
    "Buhaira", "Bami", "Amate", "Tharsis",
    "Enladrillada", "Tamarguillo", "Fedriani", "Ximénez",
    "Bermejales", "Jáuregui", "Heliópolis", "Rochelambert",
    "Borceguinería", "Mencheta", "Barzola",
    "Laraña", "Alhóndiga", "Levíes", "Almotamid", "Molviedro",
    "Chicarreros", "Tarfia", "Matahacas", "Recaredo",
    "Daoíz", "Fabie", "Worner", "Ilundain", "Osborne", "Merry", "Pickman",
    "Hytasa", "Eritaña", "Molini", "Parsi", "Guadalhorce", "Soleá",
    "Rocío", "Valme",
])
WHISPER_HOTWORDS = os.getenv("IMERS_WHISPER_HOTWORDS") or _SEVILLA_HOTWORDS

FULL_RETRANSCRIBE       = os.getenv("IMERS_FULL_RETRANSCRIBE", "1") == "1"
FULL_RETRANSCRIBE_MAX_S = float(os.getenv("IMERS_FULL_RETRANSCRIBE_MAX_S", "600"))

_HALLUCINATION_PATTERNS = [
    r"subt[ií]tul\w*[^.!?\n]*amara\.?org",
    r"¡?\s*gracias por ver(?:\s+(?:el|este)\s+v[ií]deo)?\s*[.!]*",
    r"no olvides suscribirte[^.!?\n]*",
    r"suscr[ií]bete[^.!?\n]*",
    r"\[(?:m[úu]sica|aplausos|silencio|risas)[^\]]*\]",
    r"♪+",
]
_HALLUCINATION_RES = [re.compile(p, re.IGNORECASE) for p in _HALLUCINATION_PATTERNS]


def _strip_hallucinations(text: str) -> str:
    """Remove known Whisper hallucination phrases (YouTube-style closers, music tags)."""
    if not text:
        return text
    for rx in _HALLUCINATION_RES:
        text = rx.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


_DEDUP_PUNCT = ".,;:!?¿¡\"'«»()…"


def _norm_word(word: str) -> str:
    """Normalise a word for overlap comparison: strip punctuation, lowercase."""
    return word.strip(_DEDUP_PUNCT).lower()

_LOCATION_CUES = [
    "calle", "avenida", "plaza", "carretera", "esquina",
    "número", "portal", "piso", "km ", "autopista", "en la ", "en el ",
]
_INCIDENT_CUES = [
    "accidente", "herido", "fuego", "incendio", "robo", "agresión",
    "atropello", "colapso", "desmayo", "no respira", "sangre",
    "golpe", "caído", "paro", "fractura",
]



class StreamingTranscriber:
    """Builds a running transcript from audio chunks and triggers the pipeline.

    Args:
        on_partial:       Called with the updated transcript after each flush.
        on_early_trigger: Called with the transcript when a silence or keyword
                          trigger fires (mid-call, before hang-up).
        on_final:         Called with the full transcript at call end.
        whisper_model:    Whisper model size (e.g. "base", "medium").
    """

    def __init__(
        self,
        on_partial:        Optional[Callable[[str], None]]  = None,
        on_early_trigger:  Optional[Callable[[str], None]]  = None,
        on_final:          Optional[Callable[[str], None]]  = None,
        whisper_model:     str                               = os.getenv("WHISPER_MODEL", "large-v3-turbo"),
    ):
        self.on_partial        = on_partial        or (lambda t: None)
        self.on_early_trigger  = on_early_trigger  or (lambda t: None)
        self.on_final          = on_final          or (lambda t: None)
        self.whisper_model     = whisper_model

        self._buffer: bytes   = b""
        self._overlap: bytes  = b""
        self._transcript: str = ""
        self._early_fired     = False
        self._lock            = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None
        self._finalised       = False

        self._whisper = None
        self._whisper_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()

        self._full_audio = bytearray()
        self._full_audio_overflow = False

        self._fw_client   = None
        self._groq_client = None
        self._hf_client   = None

        self._empty_flushes   = 0
        self._last_speech_ts  = time.monotonic()
        self._silence_triggered_at: float = 0.0

        self._watchdog_stop   = threading.Event()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="silence-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

        logger.info(
            f"[StreamingTranscriber] Ready "
            f"(flush={FLUSH_SECONDS}s, overlap={OVERLAP_SECONDS}s, "
            f"early_trigger={EARLY_TRIGGER_WORDS} words, "
            f"silence_trigger={SILENCE_TRIGGER_WORDS} words, "
            f"empty_flushes_trigger={SILENT_FLUSHES_FOR_TRIGGER}, "
            f"watchdog={SILENCE_WATCHDOG_S}s)"
        )


    def push_chunk(self, audio_bytes: bytes) -> None:
        """Append one audio chunk to the buffer. Thread-safe."""
        with self._lock:
            self._buffer += audio_bytes
            if FULL_RETRANSCRIBE and not self._full_audio_overflow:
                self._full_audio += audio_bytes
                if len(self._full_audio) > FULL_RETRANSCRIBE_MAX_S * SAMPLE_RATE * SAMPLE_WIDTH:
                    self._full_audio = bytearray()
                    self._full_audio_overflow = True
                    logger.warning(
                        "[StreamingTranscriber] Call exceeds "
                        f"{FULL_RETRANSCRIBE_MAX_S:.0f}s — full re-transcription disabled, "
                        "keeping incremental transcript"
                    )
            should_flush = len(self._buffer) >= FLUSH_BYTES

        if should_flush:
            self._schedule_flush(immediate=True)

    def flush_for_pause(self) -> None:
        """Transcribe buffered audio and fire the early trigger on a pause.

        Called by CallReceiver.on_pause. Does not end the call.
        """
        if self._finalised:
            return

        if self._flush_timer and self._flush_timer.is_alive():
            self._flush_timer.cancel()

        with self._lock:
            pending = self._overlap + self._buffer
            self._overlap = self._buffer[-OVERLAP_BYTES:] if len(self._buffer) >= OVERLAP_BYTES else self._buffer
            self._buffer  = b""

        transcript_snapshot = None
        if pending and len(pending) >= SAMPLE_RATE * SAMPLE_WIDTH * 0.5:
            partial = self._transcribe_bytes(pending)
            if partial:
                with self._lock:
                    self._empty_flushes  = 0
                    self._last_speech_ts = time.monotonic()
                    partial = self._deduplicate(self._transcript, partial)
                    if partial:
                        self._transcript = (self._transcript + " " + partial).strip()
                        logger.debug(
                            f"[StreamingTranscriber] Pause flush: ...{self._transcript[-80:]}"
                        )
                    transcript_snapshot = self._transcript

        if transcript_snapshot is not None:
            self.on_partial(transcript_snapshot)

        with self._lock:
            words = self._transcript.split()
        logger.info(
            f"[StreamingTranscriber] Pause detected — "
            f"{len(words)} words in transcript so far"
        )

        if len(words) >= SILENCE_TRIGGER_WORDS:
            self._early_fired = False

            self._check_early_trigger()

            if not self._early_fired:
                self._early_fired = True
                self._silence_triggered_at = time.monotonic()
                self._retranscribe_full()
                with self._lock:
                    current_transcript = self._transcript
                logger.info(
                    f"[StreamingTranscriber] Silence trigger fired at "
                    f"{len(words)} words (keyword gate bypassed on pause)"
                )
                try:
                    self.on_early_trigger(current_transcript)
                except Exception as e:
                    logger.error(
                        f"[StreamingTranscriber] on_early_trigger (pause) error: {e}"
                    )

    def finalise(self) -> None:
        """Flush remaining audio and fire on_final. Idempotent."""
        if self._finalised:
            return
        self._finalised = True

        self._watchdog_stop.set()

        if self._flush_timer and self._flush_timer.is_alive():
            self._flush_timer.cancel()

        with self._lock:
            remaining = self._overlap + self._buffer
            self._buffer = b""

        if remaining and len(remaining) >= SAMPLE_RATE * SAMPLE_WIDTH * 0.5:
            partial = self._transcribe_bytes(remaining)
            if partial:
                with self._lock:
                    partial = self._deduplicate(self._transcript, partial)
                    if partial:
                        self._transcript = (self._transcript + " " + partial).strip()
                    snapshot = self._transcript
                self.on_partial(snapshot)

        self._retranscribe_full()

        with self._lock:
            final_transcript = self._transcript

        logger.info(
            f"[StreamingTranscriber] Call finalised — "
            f"{len(final_transcript.split())} words total"
        )
        self.on_final(final_transcript)

    @property
    def current_transcript(self) -> str:
        """The transcript built so far."""
        return self._transcript


    def _schedule_flush(self, immediate: bool = False) -> None:
        """Schedule a buffer flush, cancelling any pending one."""
        if self._flush_timer and self._flush_timer.is_alive():
            self._flush_timer.cancel()
        delay = 0.05 if immediate else FLUSH_SECONDS
        self._flush_timer = threading.Timer(delay, self._flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush(self) -> None:
        """Transcribe the buffered audio, append to transcript, advance silence state."""
        with self._lock:
            if len(self._buffer) < SAMPLE_RATE * SAMPLE_WIDTH * 0.5:
                return

            audio_to_transcribe = self._overlap + self._buffer
            self._overlap = self._buffer[-OVERLAP_BYTES:] if len(self._buffer) >= OVERLAP_BYTES else self._buffer
            self._buffer  = b""

        partial = self._transcribe_bytes(audio_to_transcribe)

        fire_silence = False
        transcript_snapshot = None

        with self._lock:
            if not partial:
                self._empty_flushes += 1
                logger.debug(
                    f"[StreamingTranscriber] Empty flush "
                    f"{self._empty_flushes}/{SILENT_FLUSHES_FOR_TRIGGER} "
                    f"(transcript={len(self._transcript.split())} words)"
                )
                fire_silence = True
            else:
                self._empty_flushes  = 0
                self._last_speech_ts = time.monotonic()

                partial = self._deduplicate(self._transcript, partial)
                if partial:
                    self._transcript = (self._transcript + " " + partial).strip()
                    logger.debug(f"[StreamingTranscriber] Partial: ...{self._transcript[-80:]}")
                transcript_snapshot = self._transcript

        if fire_silence:
            self._maybe_fire_silence_trigger(reason="empty-flush")
            return

        if transcript_snapshot is not None:
            self.on_partial(transcript_snapshot)

    def _transcribe_bytes(self, audio_bytes: bytes) -> str:
        """Serialises Whisper calls via _transcribe_lock and strips hallucinations."""
        with self._transcribe_lock:
            text = self._transcribe_raw(audio_bytes)
        return _strip_hallucinations(text)

    def _transcribe_raw(self, audio_bytes: bytes) -> str:
        """Tries each Whisper backend in cascade order and returns the transcript text."""
        if not audio_bytes:
            return ""

        backends = []
        if os.getenv("FIREWORKS_API_KEY"):
            backends.append("fireworks")
        backends.append("local")
        if os.getenv("GROQ_API_KEY"):
            backends.append("groq")
        if os.getenv("HF_TOKEN"):
            backends.append("hf")

        for backend in backends:
            try:
                if backend == "fireworks":
                    if self._fw_client is None:
                        from openai import OpenAI
                        self._fw_client = OpenAI(
                            base_url="https://api.fireworks.ai/inference/v1",
                            api_key=os.getenv("FIREWORKS_API_KEY")
                        )
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(SAMPLE_WIDTH)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(audio_bytes)
                    buffer.seek(0)
                    buffer.name = "audio.wav"

                    t0 = time.perf_counter()
                    transcription = self._fw_client.audio.transcriptions.create(
                        model="whisper-v3",
                        file=buffer,
                        language=_LANG,
                        prompt=WHISPER_PROMPT,
                    )
                    text = getattr(transcription, "text", "").strip()
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    if text:
                        logger.debug(
                            f"[StreamingTranscriber] (Fireworks) Transcribed "
                            f"{len(audio_bytes) // SAMPLE_WIDTH / SAMPLE_RATE:.1f}s "
                            f"in {elapsed_ms}ms -> '{text[:60]}...'"
                        )
                    return text

                elif backend == "local":
                    model = self._load_whisper()
                    t0    = time.perf_counter()

                    try:
                        from faster_whisper import WhisperModel as _FasterWhisper
                        if isinstance(model, _FasterWhisper):
                            audio_np = (
                                np.frombuffer(audio_bytes, dtype=np.int16)
                                .astype(np.float32) / 32768.0
                            )
                            _peak = float(np.abs(audio_np).max())
                            _rms  = float(np.sqrt(np.mean(audio_np ** 2)))
                            if _peak < 1e-4:
                                return ""
                            if _rms > 0.01 and _peak < 0.9:
                                audio_np = audio_np * (0.9 / _peak)
                            _fw_kwargs: dict = dict(
                                language=_LANG,
                                initial_prompt=WHISPER_PROMPT,
                                vad_filter=True,
                                vad_parameters=dict(
                                    threshold=0.2,
                                    min_silence_duration_ms=500,
                                    speech_pad_ms=200,
                                ),
                                condition_on_previous_text=False,
                            )
                            if WHISPER_HOTWORDS:
                                _fw_kwargs["hotwords"] = WHISPER_HOTWORDS
                            try:
                                segments, _ = model.transcribe(audio_np, **_fw_kwargs)
                            except TypeError:
                                _fw_kwargs.pop("hotwords", None)
                                _fw_kwargs.pop("condition_on_previous_text", None)
                                segments, _ = model.transcribe(audio_np, **_fw_kwargs)
                            _texts = []
                            for _s in segments:
                                if getattr(_s, "no_speech_prob", 0.0) > 0.6:
                                    continue
                                if getattr(_s, "avg_logprob", 0.0) < -1.0:
                                    logger.debug(
                                        "[StreamingTranscriber] Low-confidence segment "
                                        f"(avg_logprob={_s.avg_logprob:.2f}): "
                                        f"'{_s.text.strip()[:60]}'"
                                    )
                                _texts.append(_s.text.strip())
                            text = " ".join(_texts)
                            elapsed_ms = int((time.perf_counter() - t0) * 1000)
                            if text:
                                logger.debug(
                                    f"[StreamingTranscriber] (Local-FW) Transcribed "
                                    f"{len(audio_bytes) // SAMPLE_WIDTH / SAMPLE_RATE:.1f}s "
                                    f"in {elapsed_ms}ms -> '{text[:60]}...'"
                                )
                            return text.strip()
                    except ImportError:
                        pass

                    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
                    os.close(tmp_fd)
                    try:
                        with wave.open(tmp_path, "wb") as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(SAMPLE_WIDTH)
                            wf.setframerate(SAMPLE_RATE)
                            wf.writeframes(audio_bytes)

                        result = model.transcribe(tmp_path, language=_LANG,
                                                  initial_prompt=WHISPER_PROMPT)
                        text = result.get("text", "").strip()
                        elapsed_ms = int((time.perf_counter() - t0) * 1000)
                        if text:
                            logger.debug(
                                f"[StreamingTranscriber] (Local-OW) Transcribed "
                                f"{len(audio_bytes) // SAMPLE_WIDTH / SAMPLE_RATE:.1f}s "
                                f"in {elapsed_ms}ms -> '{text[:60]}...'"
                            )
                        return text
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                elif backend == "groq":
                    if self._groq_client is None:
                        from groq import Groq
                        self._groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(SAMPLE_WIDTH)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(audio_bytes)
                    buffer.seek(0)
                    buffer.name = "audio.wav"

                    t0 = time.perf_counter()
                    transcription = self._groq_client.audio.transcriptions.create(
                        model="whisper-large-v3-turbo",
                        file=buffer,
                        language=_LANG,
                        prompt=WHISPER_PROMPT[:896],
                        response_format="verbose_json"
                    )
                    text = getattr(transcription, "text", "").strip()
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    if text:
                        logger.debug(
                            f"[StreamingTranscriber] (Groq) Transcribed "
                            f"{len(audio_bytes) // SAMPLE_WIDTH / SAMPLE_RATE:.1f}s "
                            f"in {elapsed_ms}ms -> '{text[:60]}...'"
                        )
                    return text

                elif backend == "hf":
                    if self._hf_client is None:
                        from huggingface_hub import InferenceClient
                        self._hf_client = InferenceClient(api_key=os.getenv("HF_TOKEN"))
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(SAMPLE_WIDTH)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(audio_bytes)
                    buffer.seek(0)
                    buffer.name = "audio.wav"

                    t0 = time.perf_counter()
                    result = self._hf_client.automatic_speech_recognition(
                        buffer.read(),
                        model="openai/whisper-large-v3"
                    )
                    text = getattr(result, "text", "").strip()
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    if text:
                        logger.debug(
                            f"[StreamingTranscriber] (HF) Transcribed "
                            f"{len(audio_bytes) // SAMPLE_WIDTH / SAMPLE_RATE:.1f}s "
                            f"in {elapsed_ms}ms -> '{text[:60]}...'"
                        )
                    return text

            except Exception as e:
                logger.warning(f"[StreamingTranscriber] Backend {backend} failed: {e}. Trying next fallback.")

        return ""

    def _load_whisper(self):
        """Lazy-load and cache the Whisper model. Thread-safe."""
        with self._whisper_lock:
            if self._whisper is not None:
                return self._whisper

            logger.info(f"[StreamingTranscriber] Loading Whisper '{self.whisper_model}'...")
            try:
                from faster_whisper import WhisperModel
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                compute = "float16" if device == "cuda" else "int8_float32"
                self._whisper = WhisperModel(self.whisper_model, device=device, compute_type=compute)
                logger.info(f"[StreamingTranscriber] faster-whisper loaded on {device}")
            except ImportError:
                import whisper
                self._whisper = whisper.load_model(self.whisper_model)
                logger.info("[StreamingTranscriber] openai-whisper loaded")
            return self._whisper

    def _deduplicate(self, existing: str, new_text: str) -> str:
        """Removes overlap-window words already present at the end of the running transcript (punctuation-insensitive)."""
        if not existing or not new_text:
            return new_text

        existing_norm = [_norm_word(w) for w in existing.split()]
        new_words     = new_text.split()
        new_norm      = [_norm_word(w) for w in new_words]

        max_overlap = min(8, len(existing_norm), len(new_norm))
        for overlap_len in range(max_overlap, 0, -1):
            if existing_norm[-overlap_len:] == new_norm[:overlap_len]:
                return " ".join(new_words[overlap_len:])

        return new_text

    def _retranscribe_full(self) -> Optional[str]:
        """Re-transcribes all buffered audio in one pass for better street name accuracy; replaces the stitched transcript."""
        if not FULL_RETRANSCRIBE:
            return None
        with self._lock:
            if self._full_audio_overflow:
                return None
            audio = bytes(self._full_audio)

        duration_s = len(audio) / (SAMPLE_RATE * SAMPLE_WIDTH)
        if duration_s < 1.0:
            return None

        t0   = time.perf_counter()
        text = self._transcribe_bytes(audio)
        if not text:
            return None

        with self._lock:
            self._transcript = text
        logger.info(
            f"[StreamingTranscriber] Full-audio re-transcription: {duration_s:.1f}s "
            f"audio in {int((time.perf_counter() - t0) * 1000)}ms -> "
            f"{len(text.split())} words"
        )
        self.on_partial(text)
        return text


    def _maybe_fire_silence_trigger(self, reason: str) -> None:
        """Fire on_early_trigger once per silence period if the transcript is long enough.

        Args:
            reason: "empty-flush" or "watchdog".
        """
        if self._finalised:
            return

        if reason == "empty-flush" and self._empty_flushes < SILENT_FLUSHES_FOR_TRIGGER:
            return

        with self._lock:
            words = self._transcript.split()
            if len(words) < SILENCE_TRIGGER_WORDS:
                return
            if self._silence_triggered_at >= self._last_speech_ts:
                return
            self._silence_triggered_at = time.monotonic()
            self._early_fired = False
            current_transcript = self._transcript

        improved = self._retranscribe_full()
        if improved:
            current_transcript = improved

        logger.info(
            f"[StreamingTranscriber] Silence trigger ({reason}) at "
            f"{len(current_transcript.split())} words — sending transcript to pipeline"
        )
        try:
            self.on_early_trigger(current_transcript)
        except Exception as e:
            logger.error(
                f"[StreamingTranscriber] on_early_trigger ({reason}) error: {e}"
            )

    def _watchdog_loop(self) -> None:
        """Fire the silence trigger after SILENCE_WATCHDOG_S seconds with no new speech."""
        while not self._watchdog_stop.wait(timeout=1.0):
            if self._finalised:
                return
            with self._lock:
                elapsed = time.monotonic() - self._last_speech_ts
                has_content = bool(self._transcript.strip())
            if elapsed < SILENCE_WATCHDOG_S:
                continue
            if not has_content:
                continue
            self._maybe_fire_silence_trigger(reason="watchdog")

    def _check_early_trigger(self) -> None:
        """Fire on_early_trigger if transcript has both a location and an incident keyword."""
        if self._early_fired:
            return

        with self._lock:
            words = self._transcript.split()
            transcript_lower = self._transcript.lower()

        if len(words) < EARLY_TRIGGER_WORDS:
            return

        has_location = any(cue in transcript_lower for cue in _LOCATION_CUES)
        has_incident = any(cue in transcript_lower for cue in _INCIDENT_CUES)

        if has_location and has_incident:
            self._early_fired = True
            self._silence_triggered_at = time.monotonic()
            logger.info(
                f"[StreamingTranscriber] Early trigger fired at "
                f"{len(words)} words — sending preliminary transcript to pipeline"
            )
            with self._lock:
                current_transcript = self._transcript
            try:
                self.on_early_trigger(current_transcript)
            except Exception as e:
                logger.error(f"[StreamingTranscriber] on_early_trigger error: {e}")



if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    parser = argparse.ArgumentParser(description="StreamingTranscriber test")
    parser.add_argument("--file",  required=True, help="WAV file to stream")
    parser.add_argument("--model", default="base", help="Whisper model size")
    args = parser.parse_args()

    print(f"\nStreaming transcription of: {args.file}\n" + "-" * 60)

    def on_partial(t):
        print(f"  [partial] {t[-100:]}")

    def on_early(t):
        print(f"\n  *** EARLY TRIGGER FIRED ***")
        print(f"  Pipeline would receive: {t[:200]}\n")

    def on_final(t):
        print(f"\n  [FINAL] {t}")

    transcriber = StreamingTranscriber(
        on_partial       = on_partial,
        on_early_trigger = on_early,
        on_final         = on_final,
        whisper_model    = args.model,
    )

    from realtime.call_receiver import CallReceiver
    receiver = CallReceiver(
        source    = "file",
        file_path = args.file,
        on_chunk  = transcriber.push_chunk,
        on_end    = transcriber.finalise,
        realtime  = True,
    )

    receiver.start()
    receiver.wait()
    print("\nDone.")
