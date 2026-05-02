"""Shared infrastructure for all agents."""
from __future__ import annotations

import json
from pathlib import Path

from servicesmith.llm.ollama_client import chat, parse_json

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt by stem (e.g. 'researcher' -> prompts/researcher.md)."""
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text()


def run_json_agent(
    system_prompt: str,
    user_prompt: str,
    *,
    max_retries: int = 2,
    temperature: float = 0.3,
) -> dict | list:
    """Call the LLM, parse JSON, retry on parse failure with corrective hint.

    Why a retry loop: even with `format=json`, 4B models occasionally emit
    invalid JSON (truncation at num_predict, stray prose, etc). One auto-retry
    with the parse error as feedback recovers most cases."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_err = None
    for attempt in range(max_retries + 1):
        raw = chat(messages, temperature=temperature, json_mode=True,
                   use_cache=(attempt == 0))  # don't cache retries
        try:
            return parse_json(raw)
        except json.JSONDecodeError as e:
            last_err = e
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content":
                    f"Your previous response was not valid JSON: {e}. "
                    f"Return ONLY valid JSON matching the schema in the system prompt. No prose."},
            ]
    raise RuntimeError(f"Agent returned invalid JSON after {max_retries + 1} attempts: {last_err}")
