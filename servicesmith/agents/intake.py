"""Intake agent: structured interview to capture industry, geography, fit,
constraints, goals."""
from __future__ import annotations

from servicesmith.agents._base import load_prompt, run_json_agent
from servicesmith.memory.project import ProjectState


SYSTEM = load_prompt("intake")


def next_turn(state: ProjectState, user_message: str) -> dict:
    """Process one user turn. Returns dict with `next_question`,
    `ready_to_proceed`, etc. Updates state.intake_answers in place."""
    transcript = _format_transcript(state.intake_answers, user_message)
    user_prompt = (
        f"Project name: {state.name}\n"
        f"Industry seed (may be vague): {state.industry or '[not yet specified]'}\n\n"
        f"Conversation so far:\n{transcript}\n\n"
        f"Produce your JSON response per the schema."
    )
    result = run_json_agent(SYSTEM, user_prompt, temperature=0.5)
    if not isinstance(result, dict):
        raise RuntimeError(f"Intake agent returned non-object: {type(result)}")

    # Merge captured fields into state.
    for k, v in (result.get("captured") or {}).items():
        state.intake_answers[k] = v
    if "industry" in (result.get("captured") or {}):
        state.industry = result["captured"]["industry"]
    return result


def _format_transcript(answers: dict, latest: str) -> str:
    """Render the running intake as a transcript-ish thing for the model."""
    lines = []
    for k, v in answers.items():
        lines.append(f"- {k}: {v}")
    lines.append(f"\nLatest user message: {latest}")
    return "\n".join(lines)
