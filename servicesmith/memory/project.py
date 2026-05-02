"""Per-project state. One folder per project, with:
  - state.json     : structured state (industry, intake answers, current step)
  - intake.md      : human-readable intake summary
  - research.md    : findings with citations
  - concepts.md    : 3 ranked service concepts
  - blueprint.md   : the chosen concept's full blueprint
  - citations.json : the CitationTracker
  - chat-log.md    : append-only conversation log

Why files-not-database: the user wanted a CLI for personal use. SQLite would be
fine, but plain files mean you can `cat`, `grep`, `git diff`, and edit anything
in your text editor without learning a query language.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from servicesmith.config import PROJECTS_DIR
from servicesmith.tools.citations import CitationTracker


class Stage(str, Enum):
    """Stages map to the aicofounder-style "single constraint" pipeline.
    The orchestrator's job is to figure out which stage the project is in
    and run the next agent for that stage."""
    INTAKE = "intake"
    RESEARCH = "research"
    SYNTHESIS = "synthesis"   # propose 3 concepts
    CHOICE = "choice"         # waiting for user to pick one
    BLUEPRINT = "blueprint"
    EXECUTION = "execution"   # working through MVP/customer-acquisition
    DONE = "done"


@dataclass
class ProjectState:
    name: str
    industry: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stage: Stage = Stage.INTAKE
    intake_answers: dict = field(default_factory=dict)
    chosen_concept_id: str | None = None
    notes: list[str] = field(default_factory=list)  # user free-form additions

    def slug(self) -> str:
        return slugify(self.name)

    def dir(self) -> Path:
        return PROJECTS_DIR / self.slug()

    # --- Persistence ---

    def save(self) -> None:
        self.updated_at = time.time()
        self.dir().mkdir(parents=True, exist_ok=True)
        (self.dir() / "state.json").write_text(json.dumps({
            **asdict(self),
            "stage": self.stage.value,
        }, indent=2))

    @classmethod
    def load(cls, name: str) -> "ProjectState":
        path = PROJECTS_DIR / slugify(name) / "state.json"
        if not path.exists():
            raise FileNotFoundError(f"No project named {name!r}. Run `servicesmith new {name}` first.")
        data = json.loads(path.read_text())
        data["stage"] = Stage(data["stage"])
        return cls(**data)

    @classmethod
    def list_all(cls) -> list["ProjectState"]:
        out = []
        for p in PROJECTS_DIR.iterdir():
            sj = p / "state.json"
            if sj.exists():
                try:
                    out.append(cls.load(p.name))
                except Exception:
                    pass  # corrupted state — skip silently
        return sorted(out, key=lambda s: s.updated_at, reverse=True)

    # --- File helpers ---

    def write_doc(self, name: str, content: str) -> Path:
        """Write a markdown doc into the project folder."""
        self.dir().mkdir(parents=True, exist_ok=True)
        p = self.dir() / name
        p.write_text(content)
        return p

    def read_doc(self, name: str) -> str:
        p = self.dir() / name
        return p.read_text() if p.exists() else ""

    def append_chat(self, role: str, content: str) -> None:
        log = self.dir() / "chat-log.md"
        with log.open("a") as f:
            f.write(f"\n### {role} ({time.strftime('%Y-%m-%d %H:%M')})\n\n{content}\n")

    def citations(self) -> CitationTracker:
        return CitationTracker.load(self.dir() / "citations.json")

    def save_citations(self, tracker: CitationTracker) -> None:
        tracker.save(self.dir() / "citations.json")


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s or "project"
