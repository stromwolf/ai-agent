"""Citation tracker. The single most important defense against small-model
hallucination in this whole codebase.

How it works:
  1. Researcher agent collects search results.
  2. We assign each result a stable ID: S1, S2, S3...
  3. Researcher's job is to write findings that ALWAYS reference [S1] etc.
  4. Critic verifies every claim names at least one source.
  5. Final output renders [S1] -> footnoted URLs.

Why this works on a 4B model where vague "be sure to cite" prompts fail:
  - The IDs are short, so the model reliably copies them through.
  - We can mechanically detect uncited claims with a regex, no LLM judge needed.
  - Sources stay consistent across the whole project lifetime.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from servicesmith.tools.web_search import SearchResult

CITATION_RE = re.compile(r"\[S(\d+)\]")


@dataclass
class CitationTracker:
    """Mutable across a session. Save to JSON for project persistence."""
    sources: list[SearchResult] = field(default_factory=list)
    _by_url: dict[str, str] = field(default_factory=dict)  # url -> id

    def add(self, results: list[SearchResult]) -> list[SearchResult]:
        """Register new sources, dedupe by URL, return them with .id filled in."""
        out = []
        for r in results:
            if not r.url:
                continue  # skip placeholder/error rows
            if r.url in self._by_url:
                r.id = self._by_url[r.url]
            else:
                r.id = f"S{len(self.sources) + 1}"
                self._by_url[r.url] = r.id
                self.sources.append(r)
            out.append(r)
        return out

    def render_for_prompt(self, results: list[SearchResult]) -> str:
        """Format sources for inclusion in an LLM prompt. Compact — every line
        a separate source, ID first, easy for the model to copy."""
        lines = []
        for r in results:
            snippet = r.snippet.replace("\n", " ")[:300]
            lines.append(f"[{r.id}] {r.title} — {snippet} ({r.url})")
        return "\n".join(lines)

    def render_footnotes(self) -> str:
        """Markdown footnote block for the bottom of generated documents."""
        if not self.sources:
            return ""
        lines = ["", "---", "", "**Sources**", ""]
        for s in self.sources:
            lines.append(f"- [{s.id}] [{s.title}]({s.url}) — *{s.backend}*")
        return "\n".join(lines)

    def used_ids(self, text: str) -> set[str]:
        """All citation IDs the model actually referenced in `text`."""
        return {f"S{m.group(1)}" for m in CITATION_RE.finditer(text)}

    def unused_sources(self, text: str) -> list[SearchResult]:
        used = self.used_ids(text)
        return [s for s in self.sources if s.id not in used]

    def find_uncited_claims(self, text: str) -> list[str]:
        """Best-effort detection of statement-like sentences with no [S#].
        Heuristic, not a hard guarantee — the critic agent does the real check.
        We flag sentences that contain numbers or strong claim words but no
        citation, since those are the highest-risk hallucinations."""
        suspect = []
        # Split on sentence-ish boundaries. Regex, not nltk, to keep deps minimal.
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            s = sentence.strip()
            if not s or CITATION_RE.search(s):
                continue
            # Numeric claim or strong assertion -> needs a citation.
            if re.search(r"\d+%|\$\d|\bmillion\b|\bbillion\b|\baccording to\b|"
                         r"\bsurvey\b|\breport\b|\bstudy\b|\bdata\b", s, re.I):
                suspect.append(s)
        return suspect

    def to_json(self) -> str:
        return json.dumps({
            "sources": [
                {"id": s.id, "title": s.title, "url": s.url,
                 "snippet": s.snippet, "backend": s.backend}
                for s in self.sources
            ]
        }, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "CitationTracker":
        data = json.loads(text)
        t = cls()
        for s in data["sources"]:
            r = SearchResult(
                title=s["title"], url=s["url"], snippet=s["snippet"],
                backend=s["backend"], id=s["id"],
            )
            t.sources.append(r)
            t._by_url[r.url] = r.id
        return t

    def save(self, path: Path) -> None:
        path.write_text(self.to_json())

    @classmethod
    def load(cls, path: Path) -> "CitationTracker":
        if not path.exists():
            return cls()
        return cls.from_json(path.read_text())
