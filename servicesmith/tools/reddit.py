"""Reddit pain-point mining. Reddit exposes JSON for any URL by appending `.json`,
no auth needed for read-only access. We use it to surface real customer
language — what people complain about in subreddits relevant to the industry.

Why Reddit specifically? Because aicofounder leans hard on it for validation,
and it's the cheapest source of unstructured customer pain in their own words.
For a service-design agent, "what do real customers in this industry hate?" is
the single highest-signal question, and Reddit answers it for free.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
import urllib.parse

import httpx

from servicesmith.tools.web_search import SearchResult


@dataclass
class RedditPost:
    title: str
    subreddit: str
    url: str
    score: int
    num_comments: int
    selftext: str


# Reddit blocks default UAs. A descriptive UA is required by their TOS.
HEADERS = {"User-Agent": "servicesmith/0.1 (personal research tool)"}


def search_reddit(query: str, limit: int = 10, subreddit: str | None = None) -> list[RedditPost]:
    """Search Reddit for posts matching the query. If `subreddit` is given,
    restrict to that sub. Otherwise search all of Reddit."""
    base = (
        f"https://www.reddit.com/r/{subreddit}/search.json"
        if subreddit
        else "https://www.reddit.com/search.json"
    )
    params = {"q": query, "limit": limit, "sort": "relevance", "t": "year"}
    if subreddit:
        params["restrict_sr"] = "1"

    try:
        with httpx.Client(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
            r = client.get(base, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:
        # Don't crash the agent on a Reddit hiccup — return empty list and
        # let the researcher note that Reddit was unavailable this run.
        return []

    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        out.append(RedditPost(
            title=d.get("title", ""),
            subreddit=d.get("subreddit", ""),
            url=f"https://reddit.com{d.get('permalink', '')}",
            score=d.get("score", 0),
            num_comments=d.get("num_comments", 0),
            # Truncate self-text — full posts blow up our context budget.
            selftext=(d.get("selftext", "") or "")[:600],
        ))
        # Be polite — Reddit rate-limits aggressively.
        time.sleep(0.1)
    return out


def to_search_results(posts: list[RedditPost]) -> list[SearchResult]:
    """Convert RedditPosts to the unified SearchResult shape so the citation
    tracker treats Reddit threads identically to web pages."""
    return [
        SearchResult(
            title=f"[r/{p.subreddit}] {p.title}",
            url=p.url,
            snippet=f"({p.score} upvotes, {p.num_comments} comments) {p.selftext}",
            backend="reddit",
        )
        for p in posts
    ]
