"""
Unit tests for agents/tts_agent.py
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.tts_agent import (
    SUPPORTED_AUDIO_FORMATS,
    DEFAULT_AUDIO_FOLDER,
    TranscriptionResult,
    TTSAgent,
    tts_agent,
)



def _make_wav(path: Path, duration: float = 1.0, frequency: float = 440.0) -> Path:
    sr = 16000
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for i in range(int(sr * duration)):
            val = int(16000 * math.sin(2 * math.pi * frequency * i / sr))
            wf.writeframes(struct.pack("<h", val))
    return path



class TestTranscriptionResult:

    def _make(self, **kwargs) -> TranscriptionResult:
        defaults = dict(
            transcript="Mi padre no respira.",
            language="es",
            language_prob=0.99,
            duration_sec=5.3,
            segments=[{"start": 0.0, "end": 5.3, "text": "Mi padre no respira."}],
            model_used="medium",
            backend="groq_whisper",
            processing_ms=420,
            audio_path="/tmp/test.wav",
            error=None,
        )
        defaults.update(kwargs)
        return TranscriptionResult(**defaults)

    def test_to_dict_returns_dict(self):
        result = self._make()
        d = result.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_has_all_fields(self):
        result = self._make()
        d = result.to_dict()
        for field in ("transcript", "language", "language_prob", "duration_sec",
                      "segments", "model_used", "backend", "processing_ms",
                      "audio_path", "error"):
            assert field in d, f"Missing field: {field}"

    def test_to_json_returns_valid_json(self):
        result = self._make()
        raw = result.to_json()
        assert isinstance(raw, str)
        data = json.loads(raw)
        assert data["transcript"] == "Mi padre no respira."

    def test_to_json_handles_none_error(self):
        result = self._make(error=None)
        data = json.loads(result.to_json())
        assert data["error"] is None

    def test_to_json_handles_error_string(self):
        result = self._make(error="File not found")
        data = json.loads(result.to_json())
        assert data["error"] == "File not found"

    def test_empty_segments_default(self):
        result = TranscriptionResult(
            transcript="", language="es", language_prob=0.0,
            duration_sec=0.0,
        )
        assert result.segments == []

    def test_unicode_transcript_survives_json_roundtrip(self):
        result = self._make(transcript="Paro cardíaco en la Cañada, número 3.")
        data = json.loads(result.to_json())
        assert data["transcript"] == "Paro cardíaco en la Cañada, número 3."



class TestResolveAudioPath:

    def setup_method(self):
        self.agent = TTSAgent.__new__(TTSAgent)
        self.agent.model_size = "medium"

    def test_absolute_path_unchanged(self, tmp_path):
        p = str(tmp_path / "call.wav")
        assert self.agent._resolve_audio_path(p) == p

    def test_relative_with_dotdot_unchanged(self):
        result = self.agent._resolve_audio_path("../data/recordings/call.wav")
        assert result == "../data/recordings/call.wav"

    def test_relative_with_dot_unchanged(self):
        result = self.agent._resolve_audio_path("./recordings/call.wav")
        assert result == "./recordings/call.wav"

    def test_simple_filename_prepends_default_folder(self):
        result = self.agent._resolve_audio_path("call_001.mp3")
        expected = str(Path(DEFAULT_AUDIO_FOLDER) / "call_001.mp3")
        assert result == expected

    def test_sub_path_filename_prepends_default_folder(self):
        result = self.agent._resolve_audio_path("emergency_calls/call.m4a")
        expected = str(Path(DEFAULT_AUDIO_FOLDER) / "emergency_calls/call.m4a")
        assert result == expected



class TestValidateAudioFile:

    def setup_method(self):
        self.agent = TTSAgent.__new__(TTSAgent)
        self.agent.model_size = "medium"

    def test_valid_wav_file(self, tmp_path):
        p = tmp_path / "call.wav"
        _make_wav(p)
        error = self.agent._validate_audio_file(str(p))
        assert error is None

    def test_valid_mp3_extension(self, tmp_path):
        p = tmp_path / "call.mp3"
        p.write_bytes(b"\xff\xfb" + b"\x00" * 100)
        error = self.agent._validate_audio_file(str(p))
        assert error is None

    def test_non_existent_file_returns_error(self, tmp_path):
        error = self.agent._validate_audio_file(str(tmp_path / "ghost.wav"))
        assert error is not None
        assert "not found" in error.lower() or "cannot" in error.lower() or "no" in error.lower()

    def test_unsupported_extension_returns_error(self, tmp_path):
        p = tmp_path / "call.xyz"
        p.write_bytes(b"\x00" * 10)
        error = self.agent._validate_audio_file(str(p))
        assert error is not None
        assert "unsupported" in error.lower() or "format" in error.lower()

    def test_no_extension_returns_error(self, tmp_path):
        p = tmp_path / "call_no_ext"
        p.write_bytes(b"\x00" * 10)
        error = self.agent._validate_audio_file(str(p))
        assert error is not None

    def test_all_supported_formats_pass_extension_check(self, tmp_path):
        for fmt in SUPPORTED_AUDIO_FORMATS:
            p = tmp_path / f"test{fmt}"
            p.write_bytes(b"\x00" * 50)
            error = self.agent._validate_audio_file(str(p))
            assert error is None, f"Format {fmt} should be accepted"

    def test_large_file_logs_warning_not_error(self, tmp_path):
        p = tmp_path / "big.wav"
        _make_wav(p)
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value = MagicMock(st_size=600 * 1024 * 1024)
            error = self.agent._validate_audio_file(str(p))
        assert error is None



class TestTTSAgentRun:

    def setup_method(self):
        self.agent = TTSAgent(model_size="base")


    def test_nonexistent_file_returns_error_result(self, tmp_path):
        result = self.agent.run(str(tmp_path / "ghost.wav"))
        assert result.error is not None
        assert result.transcript == ""

    def test_unsupported_extension_returns_error_result(self, tmp_path):
        p = tmp_path / "file.xyz"
        p.write_bytes(b"\x00" * 10)
        result = self.agent.run(str(p))
        assert result.error is not None

    def test_error_result_has_correct_fields(self, tmp_path):
        result = self.agent.run(str(tmp_path / "ghost.wav"))
        assert result.language == "unknown"
        assert result.language_prob == 0.0
        assert result.duration_sec == 0.0


    def test_all_backends_fail_returns_error(self, tmp_path):
        p = _make_wav(tmp_path / "call.wav")
        with patch("agents.tts_agent._run_fireworks_whisper", side_effect=ImportError), \
             patch("agents.tts_agent._run_groq_whisper",      side_effect=ImportError), \
             patch("agents.tts_agent._run_hf_whisper",        side_effect=ImportError), \
             patch("agents.tts_agent._run_faster_whisper",    side_effect=ImportError), \
             patch("agents.tts_agent._run_openai_whisper",    side_effect=ImportError), \
             patch.dict(os.environ, {"FIREWORKS_API_KEY": "x", "GROQ_API_KEY": "x", "HF_TOKEN": "x"}):
            result = self.agent.run(str(p))
        assert result.error is not None
        assert "backend" in result.error.lower() or "whisper" in result.error.lower()

    def test_groq_exception_falls_back_to_faster(self, tmp_path):
        p = _make_wav(tmp_path / "call.wav")
        good_result = TranscriptionResult(
            transcript="Hay un incendio.", language="es",
            language_prob=0.98, duration_sec=2.0,
            backend="faster_whisper", processing_ms=300,
            audio_path=str(p),
        )
        with patch("agents.tts_agent._run_fireworks_whisper",
                   side_effect=Exception("fireworks error")), \
             patch("agents.tts_agent._run_groq_whisper",
                   side_effect=Exception("Groq API error")), \
             patch("agents.tts_agent._run_hf_whisper",
                   side_effect=Exception("HF error")), \
             patch("agents.tts_agent._run_faster_whisper",
                   return_value=good_result), \
             patch.dict(os.environ, {"GROQ_API_KEY": "x", "FIREWORKS_API_KEY": "x", "HF_TOKEN": "x"}, clear=False):
            result = self.agent.run(str(p))
        assert result.backend == "faster_whisper"
        assert result.error is None

    def test_successful_backend_returns_transcription(self, tmp_path):
        p = _make_wav(tmp_path / "call.wav")
        good_result = TranscriptionResult(
            transcript="Mi padre no respira.", language="es",
            language_prob=0.97, duration_sec=3.0,
            backend="faster_whisper", processing_ms=180,
            audio_path=str(p),
        )
        with patch("agents.tts_agent._run_fireworks_whisper", side_effect=Exception), \
             patch("agents.tts_agent._run_groq_whisper",      side_effect=Exception), \
             patch("agents.tts_agent._run_hf_whisper",        side_effect=Exception), \
             patch("agents.tts_agent._run_faster_whisper",    return_value=good_result):
            result = self.agent.run(str(p))
        assert result.transcript == "Mi padre no respira."
        assert result.error is None
        assert result.language == "es"

    def test_run_returns_transcription_result_type(self, tmp_path):
        p = _make_wav(tmp_path / "call.wav")
        with patch("agents.tts_agent._run_groq_whisper",   side_effect=ImportError), \
             patch("agents.tts_agent._run_faster_whisper",  side_effect=ImportError), \
             patch("agents.tts_agent._run_openai_whisper",  side_effect=ImportError):
            result = self.agent.run(str(p))
        assert isinstance(result, TranscriptionResult)


    def test_simple_filename_resolved_to_default_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agents.tts_agent.DEFAULT_AUDIO_FOLDER", str(tmp_path))
        wav = _make_wav(tmp_path / "call.wav")
        good_result = TranscriptionResult(
            transcript="Test.", language="es", language_prob=0.9,
            duration_sec=1.0, backend="faster_whisper", processing_ms=100,
            audio_path=str(wav),
        )
        with patch("agents.tts_agent._run_faster_whisper", return_value=good_result):
            result = self.agent.run("call.wav")
        assert result.error is None


    def test_run_to_dict_returns_dict(self, tmp_path):
        p = _make_wav(tmp_path / "call.wav")
        with patch("agents.tts_agent._run_groq_whisper",   side_effect=ImportError), \
             patch("agents.tts_agent._run_faster_whisper",  side_effect=ImportError), \
             patch("agents.tts_agent._run_openai_whisper",  side_effect=ImportError):
            d = self.agent.run_to_dict(str(p))
        assert isinstance(d, dict)
        assert "transcript" in d

    def test_run_to_json_returns_valid_json(self, tmp_path):
        p = _make_wav(tmp_path / "call.wav")
        with patch("agents.tts_agent._run_groq_whisper",   side_effect=ImportError), \
             patch("agents.tts_agent._run_faster_whisper",  side_effect=ImportError), \
             patch("agents.tts_agent._run_openai_whisper",  side_effect=ImportError):
            raw = self.agent.run_to_json(str(p))
        assert isinstance(raw, str)
        data = json.loads(raw)
        assert "transcript" in data

    def test_run_to_dict_equals_run_to_dict_via_result(self, tmp_path):
        p = _make_wav(tmp_path / "call.wav")
        with patch("agents.tts_agent._run_groq_whisper",   side_effect=ImportError), \
             patch("agents.tts_agent._run_faster_whisper",  side_effect=ImportError), \
             patch("agents.tts_agent._run_openai_whisper",  side_effect=ImportError):
            d_dict  = self.agent.run_to_dict(str(p))
            d_json  = json.loads(self.agent.run_to_json(str(p)))
        assert d_dict == d_json



class TestModuleSingleton:

    def test_tts_agent_singleton_exists(self):
        assert tts_agent is not None
        assert isinstance(tts_agent, TTSAgent)

    def test_singleton_is_same_class(self):
        assert type(tts_agent) is TTSAgent



class TestSupportedFormats:

    def test_wav_supported(self):
        assert ".wav" in SUPPORTED_AUDIO_FORMATS

    def test_mp3_supported(self):
        assert ".mp3" in SUPPORTED_AUDIO_FORMATS

    def test_ogg_supported(self):
        assert ".ogg" in SUPPORTED_AUDIO_FORMATS

    def test_m4a_supported(self):
        assert ".m4a" in SUPPORTED_AUDIO_FORMATS

    def test_all_formats_start_with_dot(self):
        for fmt in SUPPORTED_AUDIO_FORMATS:
            assert fmt.startswith("."), f"Format '{fmt}' should start with '.'"

    def test_all_formats_lowercase(self):
        for fmt in SUPPORTED_AUDIO_FORMATS:
            assert fmt == fmt.lower(), f"Format '{fmt}' should be lowercase"



if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
