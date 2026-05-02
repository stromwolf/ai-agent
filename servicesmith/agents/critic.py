"""Critic agent: reviews other agents' output for hallucinated citations,
unfounded claims, platitudes, and constraint violations.

Combines mechanical checks (regex for hallucinated IDs, missing citations on
numerical claims) with an LLM judgment pass. The mechanical checks are 100%
reliable and fast; the LLM pass catches the subtler stuff."""
from __future__ import annotations

from servicesmith.agents._base import load_prompt, run_json_agent
from servicesmith.tools.citations import CitationTracker, CITATION_RE

SYSTEM = load_prompt("critic")


def mechanical_check(text: str, tracker: CitationTracker) -> list[dict]:
    """Fast, deterministic checks. Run before the LLM pass — if these fail,
    don't waste an LLM call until they're fixed."""
    issues = []
    valid_ids = {s.id for s in tracker.sources}

    # Hallucinated citations
    for m in CITATION_RE.finditer(text):
        sid = f"S{m.group(1)}"
        if sid not in valid_ids:
            issues.append({
                "severity": "critical",
                "category": "hallucinated_citation",
                "quote": text[max(0, m.start()-40):m.end()+40].strip(),
                "fix": f"Citation {sid} doesn't exist. Valid IDs: {sorted(valid_ids)}",
            })

    # Numerical claims without citations
    suspect = tracker.find_uncited_claims(text)
    for s in suspect[:5]:  # cap to avoid drowning the model in feedback
        issues.append({
            "severity": "major",
            "category": "missing_citation",
            "quote": s,
            "fix": "Add a [S#] citation or remove the numerical claim.",
        })

    return issues


def review(task_description: str, output: str, tracker: CitationTracker) -> dict:
    """Full critic pass: mechanical + LLM. Returns the same shape as the
    LLM agent — dict with verdict, issues, summary."""
    mech = mechanical_check(output, tracker)

    available_ids = ", ".join(s.id for s in tracker.sources) or "(none)"
    user_prompt = (
        f"# Task\n{task_description}\n\n"
        f"# Output to review\n{output}\n\n"
        f"# Available citation IDs\n{available_ids}\n\n"
        f"# Mechanical check found these issues already\n"
        f"{mech if mech else '(none)'}\n\n"
        f"Now do your full review. Include the mechanical issues in your "
        f"output if they're real."
    )
    result = run_json_agent(SYSTEM, user_prompt, temperature=0.2)
    if not isinstance(result, dict):
        result = {"verdict": "needs_revision", "issues": mech, "summary": "Critic returned non-object."}

    # Always include mechanical issues — they're ground truth.
    if mech:
        existing_quotes = {i.get("quote") for i in result.get("issues", [])}
        for m in mech:
            if m["quote"] not in existing_quotes:
                result.setdefault("issues", []).append(m)
        if any(i.get("severity") == "critical" for i in mech):
            result["verdict"] = "needs_revision"

    return result
