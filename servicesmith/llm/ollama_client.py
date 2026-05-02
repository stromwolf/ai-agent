"""Thin Ollama client. No SDK — Ollama's HTTP API is small enough that depending
on a separate package isn't worth the extra dependency.

Why not the `ollama` python package? Two reasons:
  1. One less dependency to pin.
  2. We need fine control over caching and timeouts that the SDK abstracts away.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterator

import httpx

from servicesmith.config import (
    CACHE_DIR,
    CONTEXT_BUDGET_TOKENS,
    DEFAULT_NUM_PREDICT,
    DEFAULT_TEMPERATURE,
    OLLAMA_HOST,
    OLLAMA_MODEL,
)

LLM_CACHE = CACHE_DIR / "llm"
LLM_CACHE.mkdir(parents=True, exist_ok=True)


def _cache_key(messages: list[dict], **gen_kwargs) -> str:
    """Stable hash of (messages + generation params). Deterministic prompts
    => same key => cache hit. Different temperature/format => miss."""
    payload = json.dumps(
        {"messages": messages, "model": OLLAMA_MODEL, **gen_kwargs},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def chat(
    messages: list[dict],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    num_predict: int = DEFAULT_NUM_PREDICT,
    json_mode: bool = False,
    use_cache: bool = True,
) -> str:
    """Single-shot chat. Returns the assistant's text.

    `messages` is the OpenAI/Ollama-style list of {"role": "system|user|assistant",
    "content": "..."}. Gemma 4 supports the system role natively (unlike Gemma 3),
    so we use it.

    `json_mode=True` asks Ollama to constrain output to valid JSON via its `format`
    parameter. Useful for structured agents (intake, strategist) where free-form
    text would force fragile parsing.
    """
    gen_kwargs = {
        "temperature": temperature,
        "num_predict": num_predict,
        "json_mode": json_mode,
    }
    cache_path = LLM_CACHE / f"{_cache_key(messages, **gen_kwargs)}.json"

    if use_cache and cache_path.exists():
        return json.loads(cache_path.read_text())["content"]

    body = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": CONTEXT_BUDGET_TOKENS,
        },
    }
    if json_mode:
        body["format"] = "json"

    # Long timeout — CPU inference of 1024 tokens on a 4B model can take 60–120s.
    with httpx.Client(timeout=httpx.Timeout(600.0)) as client:
        r = client.post(f"{OLLAMA_HOST}/api/chat", json=body)
        r.raise_for_status()
        data = r.json()

    content = data["message"]["content"]

    if use_cache:
        cache_path.write_text(json.dumps({
            "content": content,
            "ts": time.time(),
            "model": OLLAMA_MODEL,
        }))

    return content


def chat_stream(
    messages: list[dict],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    num_predict: int = DEFAULT_NUM_PREDICT,
) -> Iterator[str]:
    """Streaming version for the interactive `chat` command. No caching —
    streamed responses are for live UX, not reproducibility."""
    body = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": CONTEXT_BUDGET_TOKENS,
        },
    }
    with httpx.Client(timeout=httpx.Timeout(600.0)) as client:
        with client.stream("POST", f"{OLLAMA_HOST}/api/chat", json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("done"):
                    return
                yield chunk["message"]["content"]


def parse_json(text: str) -> dict | list:
    """Tolerant JSON parser. 4B models occasionally wrap JSON in ```json fences
    or add a trailing comment despite json_mode. We strip the obvious cruft and
    fall through to a best-effort parse."""
    text = text.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fence.
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return json.loads(text)


def health_check() -> tuple[bool, str]:
    """Quick check before the orchestrator runs anything. Returns
    (ok, message). Catches the two most common setup mistakes:
    Ollama not running, and the model not pulled yet."""
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{OLLAMA_HOST}/api/tags")
            r.raise_for_status()
            tags = {m["name"] for m in r.json().get("models", [])}
    except httpx.HTTPError as e:
        return False, f"Ollama not reachable at {OLLAMA_HOST}. Is `ollama serve` running? ({e})"

    if OLLAMA_MODEL not in tags and not any(t.startswith(OLLAMA_MODEL) for t in tags):
        return False, f"Model {OLLAMA_MODEL!r} not found locally. Run: ollama pull {OLLAMA_MODEL}"

    return True, f"Ollama OK, model {OLLAMA_MODEL} ready."
