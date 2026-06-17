"""Captures audio from mic/file/socket/WebSocket and emits 16 kHz mono PCM chunks."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import socket
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE   = 16_000
CHANNELS      = 1
SAMPLE_WIDTH  = 2
CHUNK_SECONDS = float(os.getenv("IMERS_CHUNK_SECONDS", "1.0"))
CHUNK_FRAMES  = int(SAMPLE_RATE * CHUNK_SECONDS)
CHUNK_BYTES   = CHUNK_FRAMES * SAMPLE_WIDTH

SILENCE_CHUNKS_FOR_HANGUP = int(os.getenv("IMERS_SILENCE_HANGUP", "2"))



class _VAD:
    FRAME_MS    = 30
    FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * SAMPLE_WIDTH

    def __init__(self, aggressiveness: int = 2):
        self._vad     = None
        self._backend = "energy"
        # Skip webrtcvad if explicitly disabled (broken DLL on some systems).
        if os.getenv("IMERS_USE_WEBRTCVAD", "0") != "1":
            logger.debug("[VAD] Using energy backend (IMERS_USE_WEBRTCVAD!=1)")
            return
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(aggressiveness)
            self._backend = "webrtcvad"
        except Exception:
            self._vad     = None
            self._backend = "energy"
        logger.debug(f"[VAD] Using {self._backend} backend")

    def is_speech(self, chunk_bytes: bytes) -> bool:
        if self._backend == "webrtcvad":
            return self._vad_check(chunk_bytes)
        return self._energy_check(chunk_bytes)

    def _vad_check(self, chunk_bytes: bytes) -> bool:
        speech_frames = 0
        total_frames  = 0
        for i in range(0, len(chunk_bytes) - self.FRAME_BYTES, self.FRAME_BYTES):
            frame = chunk_bytes[i : i + self.FRAME_BYTES]
            if len(frame) == self.FRAME_BYTES:
                try:
                    if self._vad.is_speech(frame, SAMPLE_RATE):
                        speech_frames += 1
                except Exception:
                    pass
                total_frames += 1
        return (speech_frames / max(total_frames, 1)) > 0.30

    def _energy_check(self, chunk_bytes: bytes) -> bool:
        samples = np.frombuffer(chunk_bytes, dtype=np.int16).astype(np.float32)
        rms     = np.sqrt(np.mean(samples ** 2))
        return rms > 500



class CallReceiver:
    """Reads audio from a source and emits fixed-size PCM chunks via callbacks.

    Args:
        source:    "mic" | "file" | "socket" | "websocket".
        on_chunk:  Called with each CHUNK_BYTES PCM window.
        on_end:    Called once when the call terminates.
        on_pause:  Optional. If set, called after SILENCE_CHUNKS_FOR_HANGUP
                   consecutive silent chunks instead of ending the call.
        file_path: Required for source="file".
        host/port: Required for source="socket".
        realtime:  In file mode, sleep between chunks to play at real speed.
    """

    def __init__(
        self,
        source:    str                       = "mic",
        on_chunk:  Optional[Callable]        = None,
        on_end:    Optional[Callable]        = None,
        on_pause:  Optional[Callable]        = None,
        file_path: Optional[str]             = None,
        host:      str                       = "0.0.0.0",
        port:      int                       = 9999,
        realtime:  bool                      = True,
    ):
        self.source    = source
        self.on_chunk  = on_chunk  or (lambda _: None)
        self.on_end    = on_end    or (lambda: None)
        self.on_pause  = on_pause
        self.file_path = file_path
        self.host      = host
        self.port      = port
        self.realtime  = realtime

        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._vad      = _VAD()
        self._silent_chunks = 0
        self._push_buffer: bytes = b""


    def start(self) -> None:
        self._running = True
        self._silent_chunks = 0
        target = {
            "mic":       self._run_mic,
            "file":      self._run_file,
            "socket":    self._run_socket,
            "websocket": self._run_websocket_stub,
        }.get(self.source)

        if target is None:
            raise ValueError(f"Unknown source: {self.source!r}. "
                             f"Choose from: mic, file, socket, websocket")

        self._thread = threading.Thread(target=target, daemon=True, name="CallReceiver")
        self._thread.start()
        logger.info(f"[CallReceiver] Started — source={self.source}")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("[CallReceiver] Stopped")

    def wait(self) -> None:
        if self._thread:
            self._thread.join()


    def _deliver(self, chunk: bytes) -> None:
        is_speech = self._vad.is_speech(chunk)
        if is_speech:
            self._silent_chunks = 0
        else:
            self._silent_chunks += 1

        try:
            self.on_chunk(chunk)
        except Exception as e:
            logger.error(f"[CallReceiver] on_chunk callback error: {e}")

        if self._silent_chunks >= SILENCE_CHUNKS_FOR_HANGUP:
            if self.on_pause is not None:
                logger.info(
                    f"[CallReceiver] {SILENCE_CHUNKS_FOR_HANGUP} silent chunks — "
                    "speech pause detected (call stays open)"
                )
                self._silent_chunks = 0
                try:
                    self.on_pause()
                except Exception as e:
                    logger.error(f"[CallReceiver] on_pause callback error: {e}")
            else:
                logger.info(
                    f"[CallReceiver] {SILENCE_CHUNKS_FOR_HANGUP} silent chunks — "
                    "declaring call end"
                )
                self._running = False
                try:
                    self.on_end()
                except Exception as e:
                    logger.error(f"[CallReceiver] on_end callback error: {e}")

    def _run_mic(self) -> None:
        try:
            import pyaudio
        except ImportError:
            logger.error("[CallReceiver] pyaudio not installed: pip install pyaudio")
            self.on_end()
            return

        pa      = pyaudio.PyAudio()
        stream  = pa.open(
            format            = pyaudio.paInt16,
            channels          = CHANNELS,
            rate              = SAMPLE_RATE,
            input             = True,
            frames_per_buffer = CHUNK_FRAMES,
        )
        logger.info("[CallReceiver] Microphone open")
        buffer = b""

        try:
            while self._running:
                data    = stream.read(CHUNK_FRAMES, exception_on_overflow=False)
                buffer += data
                if len(buffer) >= CHUNK_BYTES:
                    self._deliver(buffer[:CHUNK_BYTES])
                    buffer = buffer[CHUNK_BYTES:]
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
            if self._running:
                self.on_end()

    def _run_file(self) -> None:
        if not self.file_path or not Path(self.file_path).exists():
            logger.error(f"[CallReceiver] File not found: {self.file_path}")
            self.on_end()
            return

        try:
            samples, src_rate = _decode_audio_file(self.file_path)
            duration = len(samples) / src_rate
            logger.info(
                f"[CallReceiver] Playing file: {self.file_path} "
                f"({src_rate}Hz, {duration:.1f}s)"
            )

            pcm16 = _float32_to_int16(samples, src_rate)
            buffer = pcm16.tobytes()

            idx = 0
            while self._running and idx < len(buffer):
                chunk_raw = buffer[idx : idx + CHUNK_BYTES]
                idx += CHUNK_BYTES
                if not chunk_raw:
                    break

                self._deliver(chunk_raw)

                if self.realtime:
                    time.sleep(CHUNK_SECONDS)

            if self._running:
                self._running = False
                self.on_end()

        except Exception as e:
            logger.error(f"[CallReceiver] File read error: {e}", exc_info=True)
        finally:
            if self._running:
                self._running = False
                self.on_end()

    def _run_socket(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(1)
        srv.settimeout(30.0)
        logger.info(f"[CallReceiver] TCP socket listening on {self.host}:{self.port}")

        try:
            conn, addr = srv.accept()
            logger.info(f"[CallReceiver] Connection from {addr}")
            buffer = b""

            while self._running:
                try:
                    data = conn.recv(CHUNK_BYTES * 2)
                except (ConnectionResetError, OSError):
                    break
                if not data:
                    break

                buffer += data
                while len(buffer) >= CHUNK_BYTES:
                    self._deliver(buffer[:CHUNK_BYTES])
                    buffer = buffer[CHUNK_BYTES:]

        except socket.timeout:
            logger.warning("[CallReceiver] Socket accept timed out")
        except Exception as e:
            logger.error(f"[CallReceiver] Socket error: {e}")
        finally:
            srv.close()
            if self._running:
                self._running = False
                self.on_end()

    def _run_websocket_stub(self) -> None:
        logger.info(
            "[CallReceiver] source='websocket' — audio is pushed from api.py. "
            "Use push_bytes() to feed data externally."
        )

    def push_bytes(self, data: bytes) -> None:
        self._push_buffer += data
        while len(self._push_buffer) >= CHUNK_BYTES:
            self._deliver(self._push_buffer[:CHUNK_BYTES])
            self._push_buffer = self._push_buffer[CHUNK_BYTES:]

    def flush(self) -> None:
        self._silent_chunks = 0
        if self._push_buffer:
            padded = self._push_buffer + b"\x00" * (CHUNK_BYTES - len(self._push_buffer))
            self._deliver(padded[:CHUNK_BYTES])
            self._push_buffer = b""
        self._running = False
        self.on_end()




def _decode_audio_file(file_path: str) -> tuple:
    """Decode any audio file to mono float32 samples in [-1, 1].

    Returns:
        (samples, sample_rate) — samples is np.ndarray[float32].
    """
    path = Path(file_path)
    ext  = path.suffix.lower()

    if ext == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                sr       = wf.getframerate()
                n_ch     = wf.getnchannels()
                sw       = wf.getsampwidth()
                raw      = wf.readframes(wf.getnframes())
            dtype    = {1: np.int8, 2: np.int16, 4: np.int32}.get(sw, np.int16)
            samples  = np.frombuffer(raw, dtype=dtype).astype(np.float32)
            if n_ch == 2:
                samples = samples.reshape(-1, 2).mean(axis=1)
            peak = np.abs(samples).max()
            if peak > 0:
                samples /= peak
            return samples, sr
        except Exception as e:
            logger.debug(f"[decode] wave failed ({e}), trying soundfile")

    try:
        import soundfile as sf
        samples, sr = sf.read(str(path), always_2d=True, dtype="float32")
        if samples.shape[1] > 1:
            samples = samples.mean(axis=1)
        else:
            samples = samples[:, 0]
        return samples, sr
    except Exception as e:
        logger.debug(f"[decode] soundfile failed ({e}), trying pydub")

    try:
        import subprocess, shutil
        if shutil.which("ffmpeg") is None:
            raise FileNotFoundError("ffmpeg not found on PATH")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        src_rate = int(probe.stdout.strip()) if probe.returncode == 0 and probe.stdout.strip() else 44100
        result = subprocess.run(
            ["ffmpeg", "-i", str(path), "-ac", "1", "-ar", str(src_rate),
             "-f", "s16le", "-"],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        raw = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
        peak = np.abs(raw).max()
        if peak > 0:
            raw /= 32767.0
        return raw, src_rate
    except Exception as e:
        logger.debug(f"[decode] ffmpeg subprocess failed ({e}), trying pydub")

    try:
        from pydub import AudioSegment
        seg     = AudioSegment.from_file(str(path))
        seg     = seg.set_channels(1).set_sample_width(2)
        raw     = np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32)
        peak    = np.abs(raw).max()
        if peak > 0:
            raw /= peak
        return raw, seg.frame_rate
    except Exception as e:
        raise RuntimeError(f"Could not decode audio file '{file_path}': {e}") from e


def _float32_to_int16(samples: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate != SAMPLE_RATE:
        n_src    = len(samples)
        n_target = int(n_src * SAMPLE_RATE / src_rate)
        x_src    = np.linspace(0, 1, n_src)
        x_target = np.linspace(0, 1, n_target)
        samples  = np.interp(x_target, x_src, samples)
    return np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)


def _normalise_raw_pcm(data: bytes, src_rate: int) -> bytes:
    if src_rate == SAMPLE_RATE:
        return data
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    n_src    = len(samples)
    n_target = int(n_src * SAMPLE_RATE / src_rate)
    x_src    = np.linspace(0, 1, n_src)
    x_target = np.linspace(0, 1, n_target)
    resampled = np.interp(x_target, x_src, samples)
    return resampled.astype(np.int16).tobytes()



if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    parser = argparse.ArgumentParser(description="CallReceiver test")
    parser.add_argument("--source", choices=["mic", "file", "socket"], default="mic")
    parser.add_argument("--file",   help="WAV file path (for --source file)")
    parser.add_argument("--host",   default="0.0.0.0")
    parser.add_argument("--port",   type=int, default=9999)
    parser.add_argument("--duration", type=float, default=10.0, help="Max recording seconds")
    args = parser.parse_args()

    chunks_received = []

    def on_chunk(data: bytes):
        chunks_received.append(len(data))
        print(f"  [chunk] {len(data)} bytes  (total chunks: {len(chunks_received)})")

    def on_end():
        print(f"\n[CallReceiver] Call ended — {len(chunks_received)} chunks received")

    receiver = CallReceiver(
        source    = args.source,
        on_chunk  = on_chunk,
        on_end    = on_end,
        file_path = args.file,
        host      = args.host,
        port      = args.port,
    )

    print(f"Starting CallReceiver (source={args.source}) — recording up to {args.duration}s...")
    receiver.start()

    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        receiver.stop()