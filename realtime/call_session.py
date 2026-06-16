"""Wires audio capture, transcription and pipeline runs into one call lifecycle."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    IDLE        = "idle"
    RECEIVING   = "receiving"
    PRELIMINARY = "preliminary"
    FINALISING  = "finalising"
    COMPLETE    = "complete"


class CallSession:
    """One call's audio → transcript → pipeline reports.

    Args:
        session_id:    Unique session identifier.
        pipeline:      Shared IMERSPipeline instance.
        on_report:     Callback (session_id, report_type, report) for each
                       finished pipeline run. report_type is "preliminary" or
                       "final".
        source:        Audio source name passed to CallReceiver.
        source_kwargs: Extra kwargs for CallReceiver (file_path, host, port…).
    """

    def __init__(
        self,
        session_id:    str,
        pipeline,
        on_report:     Optional[Callable] = None,
        source:        str                = "mic",
        **source_kwargs,
    ):
        self.session_id = session_id
        self.pipeline   = pipeline
        self.on_report  = on_report or (lambda sid, rtype, r: None)
        self.source     = source
        self.source_kw  = source_kwargs

        self.state      = SessionState.IDLE
        self.started_at: Optional[str]  = None
        self.ended_at:   Optional[str]  = None

        self.incident_id: str = "INC-" + session_id[len("CALL-"):]

        self.preliminary_report: Optional[dict] = None
        self.final_report:       Optional[dict] = None
        self.full_transcript:    str            = ""

        self._receiver    = None
        self._transcriber = None


    def start(self) -> None:
        try:
            from realtime.call_receiver import CallReceiver
            from realtime.streaming_transcriber import StreamingTranscriber
        except ImportError:
            from realtime.call_receiver import CallReceiver
            from realtime.streaming_transcriber import StreamingTranscriber

        self.state      = SessionState.RECEIVING
        self.started_at = datetime.now(timezone.utc).isoformat()
        logger.info(f"[CallSession {self.session_id}] Started (source={self.source})")

        self._transcriber = StreamingTranscriber(
            on_partial       = self._on_partial,
            on_early_trigger = self._on_early_trigger,
            on_final         = self._on_final_transcript,
        )

        self._receiver = CallReceiver(
            source    = self.source,
            on_chunk  = self._transcriber.push_chunk,
            on_end    = self._transcriber.finalise,
            on_pause  = self._on_pause,
            **self.source_kw,
        )
        self._receiver.start()

    def stop(self) -> None:
        logger.info(f"[CallSession {self.session_id}] Stopped by operator")
        if self._receiver:
            self._receiver.flush()
            self._receiver.stop()

    def push_audio(self, audio_bytes: bytes) -> None:
        if self._receiver:
            self._receiver.push_bytes(audio_bytes)

    def hangup(self) -> None:
        if self._receiver:
            self._receiver.flush()

    @property
    def transcript(self) -> str:
        if self._transcriber:
            return self._transcriber.current_transcript
        return self.full_transcript

    def to_dict(self) -> dict:
        return {
            "session_id":   self.session_id,
            "state":        self.state.value,
            "started_at":   self.started_at,
            "ended_at":     self.ended_at,
            "transcript":   self.transcript,
            "has_preliminary": self.preliminary_report is not None,
            "has_final":       self.final_report is not None,
        }


    def _on_partial(self, partial: str) -> None:
        logger.debug(f"[CallSession {self.session_id}] Partial ({len(partial.split())} words)")

    def _on_pause(self) -> None:
        if self._transcriber is None:
            return
        if self.state not in (SessionState.RECEIVING, SessionState.PRELIMINARY):
            return
        words = len(self._transcriber.current_transcript.split())
        logger.info(
            f"[CallSession {self.session_id}] Speech pause — "
            f"flushing transcriber ({words} words so far)"
        )
        self._transcriber.flush_for_pause()

    def _on_early_trigger(self, transcript: str) -> None:
        if self.state == SessionState.PRELIMINARY:
            logger.info(
                f"[CallSession {self.session_id}] Skipping duplicate early trigger "
                f"({len(transcript.split())} words) — preliminary run already in progress"
            )
            return
        self.state = SessionState.PRELIMINARY
        logger.info(
            f"[CallSession {self.session_id}] Early trigger — "
            f"running preliminary pipeline ({len(transcript.split())} words)"
        )
        threading.Thread(
            target = self._run_pipeline,
            args   = (transcript, "preliminary"),
            daemon = True,
            name   = f"pipeline-prelim-{self.session_id}",
        ).start()

    def _on_final_transcript(self, transcript: str) -> None:
        self.full_transcript = transcript
        self.ended_at        = datetime.now(timezone.utc).isoformat()
        self.state           = SessionState.FINALISING

        logger.info(
            f"[CallSession {self.session_id}] Call ended — "
            f"running final pipeline ({len(transcript.split())} words)"
        )
        threading.Thread(
            target = self._run_pipeline,
            args   = (transcript, "final"),
            daemon = True,
            name   = f"pipeline-final-{self.session_id}",
        ).start()

    def _run_pipeline(self, transcript: str, report_type: str) -> None:
        t0 = time.perf_counter()
        try:
            report = self.pipeline.run_transcript(
                transcript,
                incident_id=self.incident_id,
                is_preliminary=(report_type == "preliminary"),
            )
            elapsed = time.perf_counter() - t0

            report["session_id"]   = self.session_id
            report["report_type"]  = report_type
            report["pipeline_elapsed_s"] = round(elapsed, 2)

            if report_type == "preliminary":
                self.preliminary_report = report
                if self.state == SessionState.PRELIMINARY:
                    self.state = SessionState.RECEIVING
            else:
                self.final_report = report
                self.state        = SessionState.COMPLETE

            logger.info(
                f"[CallSession {self.session_id}] {report_type.upper()} report ready "
                f"in {elapsed:.1f}s — "
                f"type={report.get('incident_type')} sev={report.get('severity')}"
            )

            self.on_report(self.session_id, report_type, report)

        except Exception as e:
            logger.error(
                f"[CallSession {self.session_id}] Pipeline error ({report_type}): {e}",
                exc_info=True,
            )
            error_report = {
                "session_id":  self.session_id,
                "report_type": report_type,
                "status":      "error",
                "error":       str(e),
                "transcript":  transcript[:500],
            }
            if report_type == "final":
                self.state = SessionState.COMPLETE
            self.on_report(self.session_id, report_type, error_report)



class CallSessionManager:
    """Registry of active CallSession instances.

    Args:
        pipeline:  Shared IMERSPipeline.
        on_report: Callback invoked for every pipeline report from any session
                   with signature (session_id, report_type, report).
    """

    def __init__(self, pipeline, on_report: Optional[Callable] = None):
        self.pipeline   = pipeline
        self.on_report  = on_report or (lambda *a: None)
        self._sessions: dict[str, CallSession] = {}
        self._counter   = 0
        self._lock      = threading.Lock()

    def create(self, source: str = "mic", **source_kwargs) -> CallSession:
        with self._lock:
            self._counter += 1
            session_id = (
                f"CALL-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
                f"-{self._counter:03d}"
            )

        session = CallSession(
            session_id = session_id,
            pipeline   = self.pipeline,
            on_report  = self._handle_report,
            source     = source,
            **source_kwargs,
        )
        with self._lock:
            self._sessions[session_id] = session

        logger.info(f"[CallSessionManager] Created session {session_id}")
        return session

    def get(self, session_id: str) -> Optional[CallSession]:
        return self._sessions.get(session_id)

    def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            session.stop()
            logger.info(f"[CallSessionManager] Closed session {session_id}")

    def active_sessions(self) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self._sessions.values()]

    def _handle_report(self, session_id: str, report_type: str, report: dict) -> None:
        try:
            self.on_report(session_id, report_type, report)
        except Exception as e:
            logger.error(f"[CallSessionManager] on_report callback error: {e}")



if __name__ == "__main__":
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    parser = argparse.ArgumentParser(description="CallSession test (no LLM — pipeline stub)")
    parser.add_argument("--file", required=True, help="WAV file to simulate a call")
    parser.add_argument("--model", default="base")
    args = parser.parse_args()

    class _StubPipeline:
        def run_transcript(self, transcript: str, incident_id: str = None) -> dict:
            words = transcript.split()
            return {
                "status":        "processed (stub)",
                "incident_type": "unknown",
                "severity":      "unknown",
                "transcript":    transcript,
                "word_count":    len(words),
                "location":      {"address": "stub — no geocoding"},
                "dispatch":      {"units": [], "total_units": 0},
            }

    reports = []

    def on_report(session_id, report_type, report):
        reports.append((report_type, report))
        print(f"\n{'='*60}")
        print(f"  REPORT: {report_type.upper()}  |  session: {session_id}")
        print(f"{'='*60}")
        print(json.dumps(report, indent=2, ensure_ascii=True)[:800])
        print(f"{'-'*60}\n")

    pipeline = _StubPipeline()
    manager  = CallSessionManager(pipeline=pipeline, on_report=on_report)
    session  = manager.create(source="file", file_path=args.file, realtime=True)

    print(f"\nSimulating call from file: {args.file}\n" + "-" * 60)
    session.start()
    session._receiver.wait()

    print(f"\nSession complete. {len(reports)} report(s) generated.")
    for rtype, _ in reports:
        print(f"  - {rtype}")