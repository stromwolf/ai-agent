"""Strategist: 3 concepts. Blueprinter: full plan for chosen concept."""
from __future__ import annotations

from servicesmith.agents._base import load_prompt, run_json_agent
from servicesmith.llm.ollama_client import chat
from servicesmith.memory.project import ProjectState
from servicesmith.tools.citations import CitationTracker

STRATEGIST_SYSTEM = load_prompt("strategist")
BLUEPRINTER_SYSTEM = load_prompt("blueprinter")


def propose_concepts(state: ProjectState, findings: str, tracker: CitationTracker) -> dict:
    """Returns {"concepts": [...], "recommendation": "..."}"""
    intake = "\n".join(f"- {k}: {v}" for k, v in state.intake_answers.items())
    user_prompt = (
        f"# User intake\n{intake}\n\n"
        f"# Research findings\n{findings}\n\n"
        f"# Available source IDs\n{', '.join(s.id for s in tracker.sources)}\n\n"
        f"Propose exactly 3 concepts per the schema."
    )
    return run_json_agent(STRATEGIST_SYSTEM, user_prompt, temperature=0.4)


def render_concepts_md(concepts_data: dict) -> str:
    """Pretty-print the strategist's JSON output as markdown."""
    out = ["# Service concepts\n"]
    for c in concepts_data.get("concepts", []):
        out.append(f"## {c['id']}: {c['name']}\n")
        out.append(f"**One-liner.** {c['one_liner']}\n")
        out.append(f"**ICP.** {c['icp']}\n")
        out.append("**Offer:**")
        for b in c.get("offer", []):
            out.append(f"- {b}")
        out.append(f"\n**Pricing.** {c['pricing']}")
        out.append(f"\n**First customer.** {c['first_customer']}")
        out.append(f"\n**Why now.** {c['why_now']}")
        out.append(
            f"\n**Scores.** Fit: {c['fit_score']}/5 · "
            f"Demand: {c['demand_score']}/5 · "
            f"Defensibility: {c['defensibility_score']}/5"
        )
        out.append(f"\n*{c['rationale']}*\n")
    rec = concepts_data.get("recommendation", "")
    if rec:
        out.append(f"\n---\n**Recommendation:** {rec}\n")
    return "\n".join(out)


def build_blueprint(state: ProjectState, concept: dict, findings: str,
                    tracker: CitationTracker) -> str:
    """Generate the full blueprint markdown for a chosen concept."""
    intake = "\n".join(f"- {k}: {v}" for k, v in state.intake_answers.items())
    user_prompt = (
        f"# Chosen concept\n{concept['name']}\n{concept['one_liner']}\n\n"
        f"Full concept JSON:\n{concept}\n\n"
        f"# User intake\n{intake}\n\n"
        f"# Research findings\n{findings}\n\n"
        f"Produce the full blueprint per the eight-section schema."
    )
    blueprint = chat(
        [{"role": "system", "content": BLUEPRINTER_SYSTEM},
         {"role": "user", "content": user_prompt}],
        temperature=0.3,
        num_predict=2000,
    )
    return blueprint + tracker.render_footnotes()
