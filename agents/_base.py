from __future__ import annotations

import json
import re as _re


def _extract_agent_json(raw) -> dict | None:
    """Extract the first JSON object from an agent output, stripping markdown fences.
    Returns the parsed dict, or None if no valid JSON object was found.
    """
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    for fence in ["```json", "```"]:
        if fence in text:
            text = text.split(fence)[-1].split("```")[0].strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start != -1 and end > start:
        candidate = text[start:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            fixed = _re.sub(r'\\"(?=\s*[,}\]])', '"', candidate)
            if fixed != candidate:
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    pass
    return None


def _extract_from_groq_error(exc: Exception) -> dict | None:
    """Salvage a valid JSON result from a Groq 400 'tool_use_failed' error.
    When the model computes the right answer but fails to wrap it in
    final_answer(answer='...'), Groq rejects the call and returns the raw
    output in 'failed_generation'. We extract and return that JSON directly
    so the pipeline gets the model's answer instead of falling back to stubs.
    """
    msg = str(exc)
    if "failed_generation" not in msg:
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
                        if (isinstance(parsed, dict)
                                and parsed.get("name") == "final_answer"
                                and isinstance(parsed.get("arguments"), dict)):
                            answer_str = parsed["arguments"].get("answer", "")
                            if answer_str:
                                try:
                                    inner = json.loads(answer_str)
                                    if isinstance(inner, dict):
                                        return inner
                                except json.JSONDecodeError:
                                    pass
                        elif isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                return None
        i += 1
    return None


def _extract_tool_call_from_groq_error(exc: Exception) -> tuple[str, dict] | None:
    """Extract (tool_name, args_dict) from a Groq 400 error where the model
    used a non-standard tool-call format.  Handles two variants:
      A) <function=name,{args}</function>  — args embedded in the opening tag
      B) <function=name> ... {args}        — name in tag, args follow
    Returns None when the error is not in either format.
    """
    msg = str(exc)
    m = _re.search(r"<function=([^>]+)>", msg)
    if not m:
        return None

    captured = m.group(1)

    comma = captured.find(",{")
    if comma != -1:
        name = captured[:comma].strip()
        args_str = captured[comma + 1:].strip()
        for suffix in ("</function>", "</function"):
            if args_str.endswith(suffix):
                args_str = args_str[: -len(suffix)].strip()
                break
        try:
            return name, json.loads(args_str)
        except json.JSONDecodeError:
            return None

    name = captured
    start = msg.find("{", m.end())
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
                try:
                    args = json.loads(msg[start : i + 1])
                    return name, args
                except json.JSONDecodeError:
                    return None
        i += 1
    return None
