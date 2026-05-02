"""Web search abstraction. Default backend is DuckDuckGo's HTML endpoint (no key,
rate-limited, fine for personal use). Set TAVILY_API_KEY or BRAVE_API_KEY to
upgrade — both have free tiers and return cleaner results.

Why scrape DDG instead of using an API by default?  Because the user said pure
local, and a free no-key default keeps the install path one command. Search
quality on DDG-HTML isn't great; the citation system below compensates by
forcing the model to ground every claim.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from servicesmith.config import BRAVE_API_KEY, CACHE_DIR, TAVILY_API_KEY

SEARCH_CACHE = CACHE_DIR / "search"
SEARCH_CACHE.mkdir(parents=True, exist_ok=True)
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 1 week — research stays fresh, not stale


@dataclass
class SearchResult:
    """One search hit. `id` is assigned by the citation tracker, not the search
    backend, so IDs are stable across a single research session."""
    title: str
    url: str
    snippet: str
    backend: str
    id: str = ""  # filled in by CitationTracker


def _cache_path(query: str, backend: str) -> Path:
    h = hashlib.sha256(f"{backend}::{query}".encode()).hexdigest()[:24]
    return SEARCH_CACHE / f"{h}.json"


def _load_cached(query: str, backend: str) -> list[SearchResult] | None:
    p = _cache_path(query, backend)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    if time.time() - raw["ts"] > CACHE_TTL_SECONDS:
        return None
    return [SearchResult(**r) for r in raw["results"]]


def _save_cached(query: str, backend: str, results: list[SearchResult]) -> None:
    p = _cache_path(query, backend)
    p.write_text(json.dumps({
        "ts": time.time(),
        "query": query,
        "backend": backend,
        "results": [asdict(r) for r in results],
    }))


# --- Backends ---

def _search_duckduckgo(query: str, n: int) -> list[SearchResult]:
    # DDG's `html.duckduckgo.com` endpoint returns server-rendered results.
    # No API, no key. Will rate-limit if hammered — fine for our usage pattern.
    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 servicesmith/0.1"}
    with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
        r = client.post(url, data={"q": query})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

    out: list[SearchResult] = []
    for result in soup.select("div.result")[:n]:
        a = result.select_one("a.result__a")
        snippet = result.select_one(".result__snippet")
        if not a:
            continue
        href = a.get("href", "")
        # DDG wraps real URLs in a redirect; unwrap.
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=", 1)[1].split("&", 1)[0])
        out.append(SearchResult(
            title=a.get_text(strip=True),
            url=href,
            snippet=snippet.get_text(" ", strip=True) if snippet else "",
            backend="ddg",
        ))
    return out


def _search_tavily(query: str, n: int) -> list[SearchResult]:
    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": n,
                "search_depth": "basic",
            },
        )
        r.raise_for_status()
        data = r.json()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("content", ""),
            backend="tavily",
        )
        for item in data.get("results", [])[:n]
    ]


def _search_brave(query: str, n: int) -> list[SearchResult]:
    with httpx.Client(timeout=20.0) as client:
        r = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": n},
            headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("description", ""),
            backend="brave",
        )
        for item in (data.get("web", {}).get("results", []) or [])[:n]
    ]


def search(query: str, n: int = 6) -> list[SearchResult]:
    """Search the web. Picks the best available backend automatically."""
    if TAVILY_API_KEY:
        backend = "tavily"
        fn = _search_tavily
    elif BRAVE_API_KEY:
        backend = "brave"
        fn = _search_brave
    else:
        backend = "ddg"
        fn = _search_duckduckgo

    cached = _load_cached(query, backend)
    if cached is not None:
        return cached[:n]

    try:
        results = fn(query, n)
    except Exception as e:
        # Search failure is recoverable — researcher agent will note "no results".
        # We never want a transient network blip to crash the whole pipeline.
        return [SearchResult(
            title="[search failed]",
            url="",
            snippet=f"Search backend {backend} error: {e}",
            backend=backend,
        )]

    _save_cached(query, backend, results)
    return results
