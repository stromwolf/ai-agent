"""Researcher agent: query planning, fetching, citation-grounded synthesis."""
from __future__ import annotations

from servicesmith.agents._base import load_prompt
from servicesmith.llm.ollama_client import chat
from servicesmith.memory.project import ProjectState
from servicesmith.tools import reddit, web_search
from servicesmith.tools.citations import CitationTracker
from servicesmith.config import RESEARCH_SOURCES_PER_PASS


SYSTEM = load_prompt("researcher")


def plan_queries(state: ProjectState) -> list[dict]:
    """Generate 4–6 targeted research queries from the intake. Mix of web and
    Reddit. Hardcoded plan rather than asking the model — saves an LLM call,
    and on a 4B model query planning is one of the things that goes wrong
    (over-generic queries).

    Returns list of {kind: 'web'|'reddit', query: str, subreddit?: str}."""
    industry = state.industry or "this industry"
    geo = state.intake_answers.get("geography", "")
    geo_str = f" {geo}" if geo else ""

    plan = [
        {"kind": "web", "query": f"{industry}{geo_str} market size growth trends"},
        {"kind": "web", "query": f"{industry}{geo_str} typical pricing service business"},
        {"kind": "web", "query": f"top {industry} competitors small business"},
        {"kind": "reddit", "query": f"{industry} frustrated problems"},
        {"kind": "reddit", "query": f"{industry} hiring looking for help"},
    ]
    # If user has named a sub-niche, add a focused query.
    sub = state.intake_answers.get("sub_area") or state.intake_answers.get("niche")
    if sub:
        plan.append({"kind": "web", "query": f"{sub} business opportunity gap"})
    return plan


def gather_sources(state: ProjectState, on_progress=None) -> CitationTracker:
    """Run the query plan, register all results with the tracker.

    `on_progress(step, total, msg)` is an optional callback for the CLI."""
    tracker = state.citations()
    plan = plan_queries(state)
    total = len(plan)

    for i, q in enumerate(plan, 1):
        if on_progress:
            on_progress(i, total, f"{q['kind']}: {q['query']}")

        if q["kind"] == "web":
            results = web_search.search(q["query"], n=RESEARCH_SOURCES_PER_PASS)
        else:
            posts = reddit.search_reddit(q["query"], limit=RESEARCH_SOURCES_PER_PASS)
            results = reddit.to_search_results(posts)

        tracker.add(results)

    state.save_citations(tracker)
    return tracker


def synthesize(state: ProjectState, tracker: CitationTracker) -> str:
    """Turn the gathered sources into the structured findings markdown."""
    intake_summary = "\n".join(f"- {k}: {v}" for k, v in state.intake_answers.items())
    sources_block = tracker.render_for_prompt(tracker.sources)

    user_prompt = (
        f"# Industry\n{state.industry}\n\n"
        f"# User intake\n{intake_summary}\n\n"
        f"# Available sources (cite by ID)\n{sources_block}\n\n"
        f"Produce the five-section research findings now. "
        f"Every paragraph must cite at least one source ID."
    )
    findings = chat(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": user_prompt}],
        temperature=0.3,
        num_predict=1500,
    )
    return findings + tracker.render_footnotes()
