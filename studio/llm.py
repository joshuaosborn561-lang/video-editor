"""Optional Claude or OpenAI calls. Missing keys are not an error."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def vendor_status() -> dict:
    return {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "deepgram": bool(os.environ.get("DEEPGRAM_API_KEY")),
        "epidemic": bool(os.environ.get("EPIDEMIC_API_KEY")),
        "suggestion_mode": suggestion_mode(),
    }


def suggestion_mode() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "template"


def complete_json(system: str, user: str) -> dict:
    mode = suggestion_mode()
    if mode == "template":
        raise RuntimeError("no LLM key")
    if mode == "anthropic":
        text = _anthropic(system, user)
    else:
        text = _openai(system, user)
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return JSON")
    return json.loads(text[start : end + 1])


def _post(url: str, headers: dict, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:400]
        raise RuntimeError(f"{exc.code} from model provider: {body}") from exc


def _anthropic(system: str, user: str) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    payload = {
        "model": model,
        "max_tokens": 2500,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    result = _post(
        "https://api.anthropic.com/v1/messages",
        {
            "content-type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        payload,
    )
    parts = result.get("content") or []
    return "".join(part.get("text", "") for part in parts if part.get("type") == "text")


def _openai(system: str, user: str) -> str:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    result = _post(
        "https://api.openai.com/v1/chat/completions",
        {
            "content-type": "application/json",
            "authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        },
        payload,
    )
    return result["choices"][0]["message"]["content"]
