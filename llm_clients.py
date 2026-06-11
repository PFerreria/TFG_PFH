"""
llm_clients.py
--------------
    ① Fireworks         — if FIREWORKS_API_KEY is set
    ② Ollama  (local)   — if the daemon is running AND the model is pulled
    ③ Groq              — if GROQ_API_KEY is set
    ④ HuggingFace       — if HF_TOKEN is set
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]

from smolagents import OpenAIServerModel

logger = logging.getLogger(__name__)


GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
HF_TOKEN          = os.getenv("HF_TOKEN")

_OLLAMA_BASE     = "http://localhost:11434/v1"
_FIREWORKS_BASE  = "https://api.fireworks.ai/inference/v1"
_GROQ_BASE       = "https://api.groq.com/openai/v1"
_HF_API_BASE     = "https://api-inference.huggingface.co/v1"

MODELS: dict[str, str] = {
    "manager":   "qwen/qwen3-32b",
    "procedure": "llama-3.3-70b-versatile",
    "dispatch":  "llama-3.3-70b-versatile",
    "geo":       "llama-3.3-70b-versatile",
    "default":   "llama-3.1-8b-instant",
}

_GROQ_TO_OLLAMA: dict[str, list[str]] = {
    "qwen/qwen3-32b": [
        "qwen3:32b", "qwen2.5:32b", "qwen2.5:14b", "qwen2.5:7b",
    ],
    "llama-3.3-70b-versatile": [
        "llama3.3:70b", "llama3.1:70b",
    ],
    "llama-3.1-8b-instant": ["llama3.1:8b", "llama3.2:3b"],
}

_GROQ_TO_FIREWORKS: dict[str, str] = {

    "llama-3.1-8b-instant":    "accounts/fireworks/models/llama-v3p1-8b-instruct",
}

_GROQ_TO_HF: dict[str, str] = {
    "qwen/qwen3-32b":          "Qwen/Qwen2.5-72B-Instruct",
    "llama-3.3-70b-versatile": "meta-llama/Llama-3.3-70B-Instruct",
    "llama-3.1-8b-instant":    "meta-llama/Meta-Llama-3.1-8B-Instruct",
}


_GROQ_CONCURRENCY = int(os.getenv("GROQ_MAX_CONCURRENT", "2"))
_groq_semaphore   = threading.Semaphore(_GROQ_CONCURRENCY)

_FIREWORKS_CONCURRENCY = int(os.getenv("FIREWORKS_MAX_CONCURRENT", "3"))
_fireworks_semaphore   = threading.Semaphore(_FIREWORKS_CONCURRENCY)

_fireworks_dead_models: set[str] = set()
_fireworks_dead_lock   = threading.Lock()

_OLLAMA_CACHE_TTL    = 30.0         
_ollama_cache_lock   = threading.Lock()
_ollama_last_check   = 0.0
_ollama_known_models: set[str] = set()


def _ollama_available_models() -> set[str]:
    """
    Return the set of model tags currently pulled in the local Ollama daemon.
    Result is cached for _OLLAMA_CACHE_TTL seconds.  Returns an empty set if
    the daemon is unreachable or the ``requests`` package is missing.
    """
    global _ollama_last_check, _ollama_known_models

    now = time.monotonic()
    with _ollama_cache_lock:
        if now - _ollama_last_check < _OLLAMA_CACHE_TTL:
            return _ollama_known_models

        _ollama_last_check = now
        if _requests is None:
            _ollama_known_models = set()
            return _ollama_known_models

        try:
            resp = _requests.get("http://localhost:11434/api/tags", timeout=2.0)
            if resp.status_code == 200:
                tags = {m["name"] for m in resp.json().get("models", [])}
                _ollama_known_models = tags
                if tags:
                    logger.debug(f"[Ollama] Available models: {', '.join(sorted(tags))}")
            else:
                _ollama_known_models = set()
        except Exception:
            _ollama_known_models = set()

        return _ollama_known_models


def _pick_ollama_model(groq_model_id: str) -> str | None:
    """
    Return the best available local Ollama tag for a given Groq model ID.

    Matching strategy (in priority order for each candidate):
      1. Exact tag match:       ``llama3.1:8b`` == ``llama3.1:8b``
      2. Quantized variant:     ``llama3.1:8b-instruct-q4_K_M``.startswith(``llama3.1:8b``)

    Returns ``None`` if Ollama not running or none of the preferred candidates
    are pulled, so the caller can fall through to cloud providers.
    """
    available = _ollama_available_models()
    if not available:
        return None

    for candidate in _GROQ_TO_OLLAMA.get(groq_model_id, []):
        for tag in available:
            if tag == candidate or tag.startswith(candidate):
                return tag
    return None


def log_provider_status() -> None:
    """Log which LLM providers are currently configured/reachable."""
    ollama_models = _ollama_available_models()
    providers = []
    if ollama_models:
        providers.append(f"Ollama ({len(ollama_models)} model(s) pulled)")
    if FIREWORKS_API_KEY:
        providers.append("Fireworks")
    if GROQ_API_KEY:
        providers.append("Groq")
    if HF_TOKEN:
        providers.append("HuggingFace")

    if providers:
        logger.info(f"[llm_clients] Active providers: {' → '.join(providers)}")
    else:
        logger.warning(
            "[llm_clients] No LLM provider configured! "
            "Set at least one of: GROQ_API_KEY, FIREWORKS_API_KEY, HF_TOKEN, "
            "or start Ollama locally."
        )

    _capable_prefixes = {"qwen2.5", "qwen3", "llama3.1:70b", "llama3.3", "mistral"}
    has_capable_local = any(
        any(m.startswith(p) for p in _capable_prefixes)
        for m in ollama_models
    )
    has_cloud = bool(FIREWORKS_API_KEY or GROQ_API_KEY or HF_TOKEN)
    fast_mode = os.getenv("IMERS_FAST_MODE", "0") == "1"

    if ollama_models and not has_capable_local and not has_cloud and not fast_mode:
        logger.warning(
            "[llm_clients] Only small local models detected (e.g. llama3.1:8b) "
            "and no cloud API keys are configured. "
            "Structured tool-calling may be unreliable. "
            "Options:\n"
            "  • Set IMERS_FAST_MODE=1 in .env  (bypasses LLM entirely, instant results)\n"
            "  • Pull a capable model: ollama pull qwen2.5:7b\n"
            "  • Set GROQ_API_KEY or FIREWORKS_API_KEY in .env for cloud inference"
        )


def _build_groq_fallback_chain(model_id: str) -> list[str]:
    """
    Return an ordered list of Groq model IDs to try, starting from the
    requested model and degrading through smaller models on the same key.
    Smaller models consume fewer tokens per minute so they are less likely
    to hit the rate limit after the primary model does.
    """
    chain = [model_id]
    degradation = {
        "qwen/qwen3-32b": "llama-3.3-70b-versatile",
    }
    seen    = {model_id}
    current = model_id
    while current in degradation:
        nxt = degradation[current]
        if nxt not in seen:
            chain.append(nxt)
            seen.add(nxt)
        current = nxt
    return chain


def _try_recover_final_answer(exc: Exception):
    """Try to reconstruct a valid ChatMessage from a Groq 'tool_use_failed' error.

    Returns a ChatMessage on success, None if the error is not recoverable this way.
    """
    msg = str(exc)
    if "tool_use_failed" not in msg or "final_answer" not in msg:
        return None

    for marker in ("'failed_generation'", '"failed_generation"'):
        pos = msg.find(marker)
        if pos != -1:
            break
    else:
        return None

    start = msg.find("{", pos + len(marker))
    if start == -1:
        return None

    depth, i = 0, start
    while i < len(msg):
        c = msg[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = msg[start : i + 1]
                candidate = candidate.replace("\\]", "]").replace("\\[", "[")
                fixed = candidate.replace(r'\\"', r'\"')
                for attempt in (fixed, candidate):
                    try:
                        parsed = json.loads(attempt)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(parsed, dict)
                        and parsed.get("name") == "final_answer"
                        and isinstance(parsed.get("arguments"), dict)
                    ):
                        answer_val = parsed["arguments"].get("answer", "")
                        try:
                            from smolagents.models import (
                                ChatMessage,
                                ChatMessageToolCall,
                                ChatMessageToolCallFunction,
                                MessageRole,
                            )
                            return ChatMessage(
                                role=MessageRole.ASSISTANT,
                                content=None,
                                tool_calls=[
                                    ChatMessageToolCall(
                                        id=f"recovered-{uuid.uuid4().hex[:8]}",
                                        type="function",
                                        function=ChatMessageToolCallFunction(
                                            name="final_answer",
                                            arguments={"answer": answer_val},
                                        ),
                                    )
                                ],
                            )
                        except Exception:
                            return None
                break
        i += 1
    return None


class OllamaModel(OpenAIServerModel):
    """
    Provider-cascade smolagents model for IMERS.

    On every ``generate()`` call the providers are tried in this order:

        ① Fireworks
        ② Ollama
        ③ Groq
        ④ HuggingFace
    """

    def __init__(
        self,
        model_id: str,
        api_key: str | None = None,
        custom_role_conversions: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            model_id=model_id,
            api_base=_GROQ_BASE,
            api_key=api_key or GROQ_API_KEY or "no-groq-key",
            custom_role_conversions=custom_role_conversions,
            **kwargs,
        )
        self._original_model_id = model_id

    def _ollama_client(self, tag: str) -> OpenAIServerModel:
        """Fresh OpenAIServerModel pointed at the local Ollama daemon."""
        return OpenAIServerModel(
            model_id=tag,
            api_base=_OLLAMA_BASE,
            api_key="ollama",
        )

    def _fireworks_client(self) -> OpenAIServerModel | None:
        """Return a Fireworks-backed model, or None if the key is not set or the
        model has no confirmed Fireworks mapping."""
        if not FIREWORKS_API_KEY:
            return None
        fid = _GROQ_TO_FIREWORKS.get(self._original_model_id)
        if not fid:
            return None
        return OpenAIServerModel(
            model_id=fid,
            api_base=_FIREWORKS_BASE,
            api_key=FIREWORKS_API_KEY,
        )

    def _hf_client(self) -> OpenAIServerModel | None:
        """Return a HuggingFace-backed model, or None if HF_TOKEN is not set."""
        if not HF_TOKEN:
            return None
        hid = _GROQ_TO_HF.get(self._original_model_id, "meta-llama/Llama-3.3-70B-Instruct")
        return OpenAIServerModel(
            model_id=hid,
            api_base=_HF_API_BASE,
            api_key=HF_TOKEN,
        )

    def generate(
        self,
        messages: list,
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list | None = None,
        **kwargs,
    ):
        import openai

        def _call(model_obj: OpenAIServerModel):
            """Invoke any OpenAIServerModel-compatible object with the current args."""
            return model_obj.generate(
                messages=messages,
                stop_sequences=stop_sequences,
                response_format=response_format,
                tools_to_call_from=tools_to_call_from,
                **kwargs,
            )

        fireworks = self._fireworks_client()
        if fireworks:
            with _fireworks_dead_lock:
                model_dead = fireworks.model_id in _fireworks_dead_models
            if model_dead:
                logger.debug(
                    f"[OllamaModel] Fireworks model '{fireworks.model_id}' previously "
                    "returned 404 — skipping to Ollama/Groq."
                )
            else:
                _fireworks_acquired = _fireworks_semaphore.acquire(blocking=False)
                if not _fireworks_acquired:
                    logger.debug(
                        "[OllamaModel] Fireworks at capacity — skipping to Ollama/Groq "
                        "(set FIREWORKS_MAX_CONCURRENT to raise the limit)."
                    )
                else:
                    try:
                        logger.info(f"[OllamaModel] ① Fireworks → {fireworks.model_id}")
                        return _call(fireworks)
                    except openai.RateLimitError:
                        logger.warning("[OllamaModel] Fireworks 429 — falling through to Ollama/Groq.")
                    except Exception as exc:
                        if "404" in str(exc) or "NOT_FOUND" in str(exc):
                            with _fireworks_dead_lock:
                                _fireworks_dead_models.add(fireworks.model_id)
                            logger.warning(
                                f"[OllamaModel] Fireworks model '{fireworks.model_id}' not found "
                                "on this account — marking dead, skipping on future calls. "
                                "Check FIREWORKS_API_KEY and model availability at "
                                "fireworks.ai/models"
                            )
                        else:
                            logger.warning(
                                f"[OllamaModel] Fireworks failed ({type(exc).__name__}: {exc}). "
                                "Falling through to Ollama/Groq."
                            )
                    finally:
                        _fireworks_semaphore.release()

        ollama_tag = _pick_ollama_model(self._original_model_id)
        if ollama_tag:
            _weak_models = {"llama3.1:8b", "llama3.2:3b", "llama3.2:1b"}
            if any(ollama_tag.startswith(w.split(":")[0] + ":") and ollama_tag != self._original_model_id for w in _weak_models) or ollama_tag in _weak_models:
                logger.warning(
                    f"[OllamaModel] ② Local Ollama → {ollama_tag} "
                    f"(small fallback — may not follow structured tool-call instructions reliably). "
                    f"For reliable local operation set IMERS_FAST_MODE=1 in .env, "
                    f"or pull a capable model: ollama pull qwen2.5:7b"
                )
            else:
                logger.info(f"[OllamaModel] ② Local Ollama → {ollama_tag}")
            try:
                return _call(self._ollama_client(ollama_tag))
            except Exception as exc:
                logger.warning(
                    f"[OllamaModel] Ollama call failed ({type(exc).__name__}: {exc}). "
                    "Falling through to Groq."
                )
        else:
            logger.debug("[OllamaModel] Ollama unavailable or no matching model pulled.")

        if GROQ_API_KEY:
            for groq_mid in _build_groq_fallback_chain(self._original_model_id):
                attempt = 0
                retries = 3
                while True:
                    with _groq_semaphore:
                        try:
                            logger.info(f"[OllamaModel] ③ Groq → {groq_mid}")
                            self.model_id = groq_mid
                            result = super().generate(
                                messages=messages,
                                stop_sequences=stop_sequences,
                                response_format=response_format,
                                tools_to_call_from=tools_to_call_from,
                                **kwargs,
                            )
                            self.model_id = self._original_model_id
                            return result
                        except openai.RateLimitError:
                            pass
                        except Exception as exc:
                            self.model_id = self._original_model_id
                            recovered = _try_recover_final_answer(exc)
                            if recovered is not None:
                                logger.info(
                                    f"[OllamaModel] Groq {groq_mid} tool_use_failed — "
                                    "recovered final_answer from failed_generation."
                                )
                                return recovered
                            logger.warning(
                                f"[OllamaModel] Groq {groq_mid} raised "
                                f"{type(exc).__name__}: {exc} — trying next provider."
                            )
                            break

                    if attempt < retries:
                        wait = 2 ** attempt
                        logger.warning(
                            f"[OllamaModel] Groq 429 on {groq_mid}. "
                            f"Retrying in {wait}s ({attempt + 1}/{retries})."
                        )
                        time.sleep(wait)
                        attempt += 1
                    else:
                        logger.warning(
                            f"[OllamaModel] Groq retries exhausted on {groq_mid}. "
                            "Trying next model in degradation chain."
                        )
                        break

        self.model_id = self._original_model_id

        hf = self._hf_client()
        if hf:
            logger.warning(
                f"[OllamaModel] ④ All primary providers exhausted → "
                f"HuggingFace {hf.model_id}"
            )
            return _call(hf)

        raise RuntimeError(
            "[OllamaModel] All providers exhausted and no fallback configured.\n"
            "Start Ollama locally, or set at least one of:\n"
            "  FIREWORKS_API_KEY, GROQ_API_KEY, HF_TOKEN"
        )


class GroqModel(OpenAIServerModel):
    """
    Legacy Groq-only smolagents model.
    """

    def __init__(
        self,
        model_id: str,
        api_key: str | None = None,
        api_base: str = _GROQ_BASE,
        custom_role_conversions: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        api_key = api_key or GROQ_API_KEY
        super().__init__(
            model_id=model_id,
            api_base=api_base,
            api_key=api_key,
            custom_role_conversions=custom_role_conversions,
            **kwargs,
        )
        self._original_model_id = model_id

    def generate(
        self,
        messages: list,
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list | None = None,
        **kwargs,
    ):
        import openai

        retries     = 3
        groq_models = _build_groq_fallback_chain(self._original_model_id)

        for groq_model_id in groq_models:
            attempt = 0
            while True:
                with _groq_semaphore:
                    try:
                        self.model_id = groq_model_id
                        result = super().generate(
                            messages=messages,
                            stop_sequences=stop_sequences,
                            response_format=response_format,
                            tools_to_call_from=tools_to_call_from,
                            **kwargs,
                        )
                        self.model_id = self._original_model_id
                        return result
                    except openai.RateLimitError:
                        pass
                    except Exception as exc:
                        self.model_id = self._original_model_id
                        logger.warning(
                            f"[GroqModel] {groq_model_id} raised "
                            f"{type(exc).__name__}: {exc} — trying next provider."
                        )
                        break

                if attempt < retries:
                    wait = 2 ** attempt
                    logger.warning(
                        f"[GroqModel] 429 on {groq_model_id}. "
                        f"Retrying in {wait}s (attempt {attempt + 1}/{retries})"
                    )
                    time.sleep(wait)
                    attempt += 1
                else:
                    logger.warning(
                        f"[GroqModel] Retries exhausted on {groq_model_id}. "
                        "Trying next Groq model in chain."
                    )
                    break

        self.model_id = self._original_model_id
        if not HF_TOKEN:
            raise RuntimeError(
                "[GroqModel] All Groq models rate-limited and HF_TOKEN is not set. "
                "Set HF_TOKEN in .env to enable provider failover."
            )

        hf_model_id = _GROQ_TO_HF.get(self._original_model_id, "meta-llama/Llama-3.3-70B-Instruct")
        logger.warning(
            f"[GroqModel] Groq rate-limit exhausted for {self._original_model_id}. "
            f"Failing over to HuggingFace: {hf_model_id}"
        )
        hf_model = OpenAIServerModel(
            model_id=hf_model_id,
            api_base=_HF_API_BASE,
            api_key=HF_TOKEN,
        )
        return hf_model.generate(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            **kwargs,
        )
