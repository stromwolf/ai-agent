"""Central config. One place to change models, paths, limits."""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

# --- Model ---
# Gemma 4 E4B instruction-tuned via Ollama. Pull with:
#   ollama pull gemma4:e4b   (9.6GB, 128K context, runs on CPU)
# We deliberately keep ONE model name in one place so swapping is a one-line change.
OLLAMA_MODEL = os.environ.get("SERVICESMITH_MODEL", "gemma4:e4b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# --- Generation defaults ---
# Lower temp for the strategist/critic; higher for intake conversation.
DEFAULT_TEMPERATURE = 0.4
DEFAULT_NUM_PREDICT = 1024  # cap output tokens — small models ramble on CPU
CONTEXT_BUDGET_TOKENS = 16_000  # well under E4B's 128K to keep CPU latency sane

# --- Paths ---
# Projects live in CWD/projects so they're git-able alongside other work.
# Cache lives in user data dir so it survives moving the project folder.
PROJECTS_DIR = Path(os.environ.get("SERVICESMITH_PROJECTS", "./projects")).resolve()
CACHE_DIR = Path(user_data_dir("servicesmith", "servicesmith")) / "cache"

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- Search ---
# DuckDuckGo HTML endpoint — no API key, rate-limited but fine for personal use.
# Swap to Tavily/Brave by setting TAVILY_API_KEY or BRAVE_API_KEY.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY")

# --- Behavior ---
# Max sources the researcher fetches per research pass. Higher = better grounding,
# slower and burns more context. 5–8 is the sweet spot on CPU.
RESEARCH_SOURCES_PER_PASS = 6
# How many service concepts the strategist proposes. aicofounder picks 1; we show 3
# so you can compare and push back.
NUM_CONCEPTS = 3
