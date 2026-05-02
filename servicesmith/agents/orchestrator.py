"""Orchestrator: the planner-executor that drives the project through stages.

Design choice: explicit state machine, not a free-form planning agent. On a
4B model, "plan your next action" prompts produce wandering output. Hardcoding
the stage transitions keeps things deterministic and debuggable; the LLM does
the hard creative work *within* each stage, not the routing between them.

This mirrors aicofounder's "single constraint" pattern: at any given moment
there's exactly ONE thing to do next, and the orchestrator's job is to surface
it clearly to the user.
"""
from __future__ import annotations

from dataclasses import dataclass

from servicesmith.agents import critic, intake, researcher, strategist
from servicesmith.llm.ollama_client import chat
from servicesmith.memory.project import ProjectState, Stage


# Max critic-revise loops per stage. More than 2 means the model is stuck;
# better to ship the flagged output and let the user judge than burn an hour.
MAX_REVISIONS = 2


@dataclass
class StageOutcome:
    stage: Stage
    next_action_hint: str
    output_path: str | None = None


def current_constraint(state: ProjectState) -> str:
    """The 'single constraint' — what's the one thing blocking progress?
    Implements the aicofounder-style next-best-action surfacing."""
    if state.stage == Stage.INTAKE:
        return ("Finish the intake interview so research has the right "
                "industry, geography, and constraints to work with.")
    if state.stage == Stage.RESEARCH:
        return ("Run market & customer-pain research. This grounds every "
                "later decision — without it the strategist will hallucinate.")
    if state.stage == Stage.SYNTHESIS:
        return "Generate 3 concrete service concepts ranked for fit and demand."
    if state.stage == Stage.CHOICE:
        return ("Choose one concept. Until you do, the blueprint can't be "
                "specific enough to act on.")
    if state.stage == Stage.BLUEPRINT:
        return "Generate the full blueprint for the chosen concept."
    if state.stage == Stage.EXECUTION:
        return ("Validate demand for the chosen concept with real "
                "conversations. Everything else is premature.")
    return "Project complete."


# --- Stage runners ---

def run_research(state: ProjectState, on_progress=None) -> StageOutcome:
    """Stage: RESEARCH. Gather sources, synthesize findings, run critic loop."""
    assert state.stage in (Stage.RESEARCH, Stage.INTAKE), \
        f"run_research called in wrong stage: {state.stage}"

    if on_progress:
        on_progress("Gathering sources from web and Reddit...")
    tracker = researcher.gather_sources(state, on_progress=lambda i, t, m:
        on_progress(f"  [{i}/{t}] {m}") if on_progress else None)

    findings = researcher.synthesize(state, tracker)
    findings = _critic_loop(
        task="Research findings synthesis from gathered sources",
        output=findings,
        tracker=tracker,
        revise_fn=lambda fb: _revise_research(state, tracker, fb),
        on_progress=on_progress,
    )

    path = state.write_doc("research.md", findings)
    state.stage = Stage.SYNTHESIS
    state.save()
    return StageOutcome(state.stage, current_constraint(state), str(path))


def run_synthesis(state: ProjectState, on_progress=None) -> StageOutcome:
    """Stage: SYNTHESIS. Strategist proposes 3 concepts."""
    findings = state.read_doc("research.md")
    if not findings:
        raise RuntimeError("No research.md yet — run research first.")
    tracker = state.citations()

    if on_progress:
        on_progress("Strategist is proposing concepts (this takes 1-2 min on CPU)...")
    concepts_data = strategist.propose_concepts(state, findings, tracker)
    md = strategist.render_concepts_md(concepts_data)

    md = _critic_loop(
        task="Three service concepts proposed from research and intake",
        output=md,
        tracker=tracker,
        revise_fn=lambda fb: _revise_concepts(state, findings, tracker, fb),
        on_progress=on_progress,
    )

    # Save both the JSON (for downstream blueprinter) and the markdown (for user).
    import json
    state.write_doc("concepts.json", json.dumps(concepts_data, indent=2))
    path = state.write_doc("concepts.md", md)

    state.stage = Stage.CHOICE
    state.save()
    return StageOutcome(state.stage, current_constraint(state), str(path))


def run_blueprint(state: ProjectState, on_progress=None) -> StageOutcome:
    """Stage: BLUEPRINT. Generate full blueprint for the chosen concept."""
    import json
    if not state.chosen_concept_id:
        raise RuntimeError("No concept chosen yet. Run `servicesmith choose <id>`.")
    concepts_data = json.loads(state.read_doc("concepts.json"))
    concept = next(
        (c for c in concepts_data["concepts"] if c["id"] == state.chosen_concept_id),
        None,
    )
    if not concept:
        raise RuntimeError(f"Concept {state.chosen_concept_id} not found.")

    findings = state.read_doc("research.md")
    tracker = state.citations()

    if on_progress:
        on_progress(f"Building blueprint for {concept['name']} (this takes 2-3 min on CPU)...")
    bp = strategist.build_blueprint(state, concept, findings, tracker)

    bp = _critic_loop(
        task=f"Service blueprint for chosen concept {concept['name']}",
        output=bp,
        tracker=tracker,
        revise_fn=lambda fb: _revise_blueprint(state, concept, findings, tracker, fb),
        on_progress=on_progress,
    )

    path = state.write_doc("blueprint.md", bp)
    state.stage = Stage.EXECUTION
    state.save()
    return StageOutcome(state.stage, current_constraint(state), str(path))


# --- Critic loop ---

def _critic_loop(*, task: str, output: str, tracker, revise_fn, on_progress=None) -> str:
    """Run critic; if it flags issues, ask the producer to revise.
    Cap at MAX_REVISIONS — small models can loop forever otherwise."""
    for attempt in range(MAX_REVISIONS):
        if on_progress:
            on_progress(f"  Critic reviewing... (pass {attempt + 1}/{MAX_REVISIONS})")
        review = critic.review(task, output, tracker)

        if review.get("verdict") == "ok":
            return output

        critical = [i for i in review.get("issues", [])
                    if i.get("severity") == "critical"]
        if not critical and attempt > 0:
            # Only minor issues remain after a revision — ship it.
            return output

        if on_progress:
            on_progress(f"  Critic flagged {len(review.get('issues', []))} issue(s); revising...")
        feedback = "\n".join(
            f"- [{i.get('severity')}] {i.get('category')}: {i.get('quote')[:120]} "
            f"-> {i.get('fix')}"
            for i in review.get("issues", [])
        )
        output = revise_fn(feedback)

    return output  # ship after MAX_REVISIONS


def _revise_research(state: ProjectState, tracker, feedback: str) -> str:
    """Re-run synthesis with critic feedback baked in."""
    intake_summary = "\n".join(f"- {k}: {v}" for k, v in state.intake_answers.items())
    sources = tracker.render_for_prompt(tracker.sources)
    user_prompt = (
        f"# Industry\n{state.industry}\n\n"
        f"# User intake\n{intake_summary}\n\n"
        f"# Available sources\n{sources}\n\n"
        f"# Critic feedback on your previous draft\n{feedback}\n\n"
        f"Rewrite the findings, fixing every flagged issue. Cite IDs only "
        f"from the available sources."
    )
    from servicesmith.agents.researcher import SYSTEM as R_SYSTEM
    return chat(
        [{"role": "system", "content": R_SYSTEM},
         {"role": "user", "content": user_prompt}],
        temperature=0.2, num_predict=1500,
    ) + tracker.render_footnotes()


def _revise_concepts(state: ProjectState, findings, tracker, feedback: str) -> str:
    intake = "\n".join(f"- {k}: {v}" for k, v in state.intake_answers.items())
    user_prompt = (
        f"# User intake\n{intake}\n\n"
        f"# Research\n{findings}\n\n"
        f"# Available IDs\n{', '.join(s.id for s in tracker.sources)}\n\n"
        f"# Critic feedback\n{feedback}\n\n"
        f"Re-propose 3 concepts addressing every flagged issue. JSON only."
    )
    from servicesmith.agents._base import run_json_agent
    from servicesmith.agents.strategist import STRATEGIST_SYSTEM
    data = run_json_agent(STRATEGIST_SYSTEM, user_prompt, temperature=0.3)
    from servicesmith.agents.strategist import render_concepts_md
    return render_concepts_md(data)


def _revise_blueprint(state, concept, findings, tracker, feedback: str) -> str:
    intake = "\n".join(f"- {k}: {v}" for k, v in state.intake_answers.items())
    user_prompt = (
        f"# Concept\n{concept}\n\n"
        f"# Intake\n{intake}\n\n"
        f"# Research\n{findings}\n\n"
        f"# Critic feedback\n{feedback}\n\n"
        f"Rewrite the full blueprint addressing every flagged issue."
    )
    from servicesmith.agents.strategist import BLUEPRINTER_SYSTEM
    return chat(
        [{"role": "system", "content": BLUEPRINTER_SYSTEM},
         {"role": "user", "content": user_prompt}],
        temperature=0.3, num_predict=2000,
    ) + tracker.render_footnotes()
