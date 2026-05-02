# servicesmith

A local CLI agent that helps you design a service business in any industry.
Inspired by aicofounder, but personal, offline-friendly, and grounded in real
sources via citations.

Built around **Gemma 4 E4B running on Ollama** — runs on a CPU laptop, no GPU
required.

## What it does

You give it an industry (`real estate`, `B2B SaaS support`, `wedding planning`,
whatever), it asks you a structured intake, researches the market and customer
pain points, proposes 3 service concepts ranked for fit and demand, and turns
your chosen concept into a 30/60/90-day blueprint with a specific first-customer plan.

Every factual claim cites a source. Every concept ties back to either the
research or your stated constraints.

## Install

```bash
# 1. Install Ollama (https://ollama.com) and pull the model
ollama pull gemma4:e4b      # ~9.6 GB, runs on CPU

# 2. Install servicesmith (Python 3.10+)
pip install -e .

# 3. Sanity-check
servicesmith doctor
```

Optional but recommended:
```bash
export TAVILY_API_KEY=...   # better web search than free DDG fallback
```

## Use

```bash
servicesmith new my-thing --industry "rental property management"
servicesmith chat my-thing            # intake interview
servicesmith research my-thing        # gather sources, synthesize findings
servicesmith concepts my-thing        # propose 3 concepts
servicesmith choose my-thing C2       # pick one
servicesmith blueprint my-thing       # generate the action plan
servicesmith chat my-thing            # free-form Q&A about your project
servicesmith status my-thing          # what's the next single action
```

All output lives in `./projects/<slug>/` as plain markdown + JSON. Edit anything
by hand — the agent will pick up your changes on the next run.

## Design choices worth knowing

- **Sequential, not parallel.** On a CPU, "parallel" agents thrash each other.
  Each step runs one at a time with progress output.
- **Hardcoded stage machine, not autonomous planning.** A 4B model can't
  reliably plan its own workflow; the orchestrator does the routing.
- **Citation IDs (`[S1]`, `[S2]`) are enforced mechanically.** Hallucinated
  citation IDs are caught by regex before the LLM critic even runs.
- **Two-pass critic loop, capped at 2 revisions.** Catches the most common 4B
  failure modes (numerical hallucination, generic platitudes) without looping
  forever.
- **Disk caching everywhere.** Re-running a step is free. Searches cached for
  a week, LLM calls cached forever.
- **Plain files, no database.** `cat`, `grep`, `git diff` work on everything.
- **Prompts live in `.md` files** under `servicesmith/prompts/`. Tweak agent
  behavior without touching code.

## Honest limitations

- Gemma 4 E4B on CPU is slow: expect 30–60s for short outputs, 2–4 min for the
  blueprint stage. Not interactive-feeling for the heavy steps.
- 4B-class reasoning is meaningfully weaker than 26B+. The critic loop helps,
  but blueprint quality won't match Claude/GPT-4-class output. The grounding
  in real sources is what makes the output usable anyway.
- Default web search uses DuckDuckGo HTML scraping. Quality is mediocre. Set
  `TAVILY_API_KEY` (or `BRAVE_API_KEY`) for noticeably better grounding.
- Reddit's public JSON endpoint can rate-limit. The researcher degrades
  gracefully if Reddit returns nothing on a given run.

## When to upgrade

If output quality isn't enough:
1. First, try `gemma4:26b` (needs 16GB+ RAM). Same code, change the env var
   `SERVICESMITH_MODEL=gemma4:26b`.
2. Then, swap to a cloud model. Replace `llm/ollama_client.py` with a Google
   AI Studio or Anthropic client — the rest of the code doesn't care which
   backend it talks to.

## Repo layout

```
servicesmith/
├── cli.py                  # Typer entry: new/chat/research/concepts/...
├── config.py               # Model name, paths, all knobs
├── llm/
│   └── ollama_client.py    # Direct HTTP to Ollama, JSON mode, streaming, cache
├── tools/
│   ├── web_search.py       # DDG / Tavily / Brave with disk cache
│   ├── reddit.py           # Public JSON endpoint, no auth
│   └── citations.py        # Stable [S#] IDs, mechanical validation
├── memory/
│   └── project.py          # Per-project state — JSON + markdown files
├── agents/
│   ├── _base.py            # Prompt loading, JSON-with-retry
│   ├── intake.py           # Structured interview
│   ├── researcher.py       # Query plan + synthesis
│   ├── strategist.py       # 3 concepts + blueprint
│   ├── critic.py           # Mechanical + LLM adversarial review
│   └── orchestrator.py     # Stage machine + critic loop
└── prompts/                # All system prompts as .md (versionable)
    ├── intake.md
    ├── researcher.md
    ├── strategist.md
    ├── critic.md
    └── blueprinter.md
```
