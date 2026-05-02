"""servicesmith CLI.

Usage:
  servicesmith new <name> --industry "<industry>"
  servicesmith chat <name>           # interactive intake / Q&A
  servicesmith research <name>       # run the research stage
  servicesmith concepts <name>       # propose 3 service concepts
  servicesmith choose <name> <C1|C2|C3>
  servicesmith blueprint <name>      # generate full blueprint
  servicesmith status <name>         # show current stage + next action
  servicesmith ls                    # list all projects
  servicesmith doctor                # check Ollama / model setup
"""
from __future__ import annotations

import json
import sys
import time

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from servicesmith.agents import intake, orchestrator
from servicesmith.llm.ollama_client import chat_stream, health_check
from servicesmith.memory.project import ProjectState, Stage

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="A local AI agent that helps you design a service business.")
console = Console()


def _require_healthy() -> None:
    ok, msg = health_check()
    if not ok:
        console.print(f"[red]✗[/red] {msg}")
        raise typer.Exit(code=1)


def _require_state(name: str) -> ProjectState:
    try:
        return ProjectState.load(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(code=1)


# --- Commands ---

@app.command()
def doctor() -> None:
    """Check that Ollama is running and the model is pulled."""
    ok, msg = health_check()
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    console.print(f"{icon} {msg}")
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def new(name: str, industry: str = typer.Option(
        "", "--industry", "-i", help="Industry (can be vague — intake will refine).")) -> None:
    """Create a new project."""
    _require_healthy()
    try:
        ProjectState.load(name)
        console.print(f"[yellow]![/yellow] Project {name!r} already exists. "
                      f"Use `servicesmith status {name}`.")
        raise typer.Exit(code=1)
    except FileNotFoundError:
        pass

    state = ProjectState(name=name, industry=industry, stage=Stage.INTAKE)
    state.save()
    console.print(Panel.fit(
        f"[bold]Created project[/bold]: {name}\n"
        f"Industry seed: {industry or '[unspecified — chat will ask]'}\n"
        f"Folder: {state.dir()}\n\n"
        f"Next: [bold cyan]servicesmith chat {name}[/bold cyan]",
        title="✓ new project"))


@app.command(name="ls")
def list_projects() -> None:
    """List all projects."""
    states = ProjectState.list_all()
    if not states:
        console.print("[dim]No projects yet. Run `servicesmith new <name>`.[/dim]")
        return
    table = Table(title="Projects")
    table.add_column("name", style="cyan")
    table.add_column("industry")
    table.add_column("stage", style="magenta")
    table.add_column("updated")
    for s in states:
        table.add_row(
            s.name, s.industry or "—", s.stage.value,
            time.strftime("%Y-%m-%d %H:%M", time.localtime(s.updated_at)),
        )
    console.print(table)


@app.command()
def status(name: str) -> None:
    """Show project state and the single next action."""
    state = _require_state(name)
    console.print(Panel(
        f"[bold]{state.name}[/bold] — {state.industry or '[no industry yet]'}\n"
        f"Stage: [magenta]{state.stage.value}[/magenta]\n"
        f"Folder: {state.dir()}\n\n"
        f"[bold]Next:[/bold] {orchestrator.current_constraint(state)}",
        title="status"))


@app.command()
def chat(name: str) -> None:
    """Interactive intake / Q&A. Drives the project forward conversationally."""
    _require_healthy()
    state = _require_state(name)

    if state.stage != Stage.INTAKE:
        # Free-form chat — tied to the project state but doesn't block stages.
        _free_chat(state)
        return

    console.print(Panel(
        f"Intake for [bold]{state.name}[/bold]. "
        f"Type your answer; [dim]Ctrl-C to pause anytime[/dim].",
        title="intake"))

    # Seed: if industry is set, the agent jumps in. If not, we prompt for one.
    seed_msg = f"Starting intake. Industry: {state.industry or 'not yet given'}."
    state.append_chat("system", seed_msg)

    while True:
        try:
            user_msg = console.input("[bold cyan]you ›[/bold cyan] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]paused — your progress is saved[/dim]")
            return
        if not user_msg:
            continue
        state.append_chat("user", user_msg)

        with console.status("[dim]thinking...[/dim]", spinner="dots"):
            result = intake.next_turn(state, user_msg)

        if result.get("summary_so_far"):
            state.notes.append(result["summary_so_far"])
        state.save()

        if result.get("ready_to_proceed"):
            console.print(Panel(
                f"[green]Intake complete.[/green]\n\n"
                f"{result.get('summary_so_far', '')}\n\n"
                f"Next: [bold cyan]servicesmith research {name}[/bold cyan]",
                title="✓ intake done"))
            state.stage = Stage.RESEARCH
            state.save()
            return

        q = result.get("next_question") or "(no question — try again)"
        state.append_chat("agent", q)
        console.print(f"[bold magenta]agent ›[/bold magenta] {q}")


def _free_chat(state: ProjectState) -> None:
    """Free-form chat after intake. Loads project context as system prompt."""
    intake_summary = "\n".join(f"- {k}: {v}" for k, v in state.intake_answers.items())
    research = state.read_doc("research.md") or "(not yet generated)"
    blueprint = state.read_doc("blueprint.md") or "(not yet generated)"
    system = (
        f"You are advising on a service business in {state.industry}. "
        f"User intake:\n{intake_summary}\n\n"
        f"Research findings (excerpt):\n{research[:2000]}\n\n"
        f"Blueprint (excerpt):\n{blueprint[:1500]}\n\n"
        f"Be concise, specific, and grounded in the documents above. "
        f"If asked something the documents don't cover, say so."
    )

    console.print(Panel(
        f"Chat with [bold]{state.name}[/bold] (stage: {state.stage.value}). "
        f"[dim]Ctrl-C to exit.[/dim]",
        title="chat"))

    history: list[dict] = []
    while True:
        try:
            user_msg = console.input("[bold cyan]you ›[/bold cyan] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]bye[/dim]")
            return
        if not user_msg:
            continue
        state.append_chat("user", user_msg)
        history.append({"role": "user", "content": user_msg})

        console.print("[bold magenta]agent ›[/bold magenta] ", end="")
        full = []
        try:
            for tok in chat_stream([{"role": "system", "content": system}] + history):
                console.print(tok, end="")
                sys.stdout.flush()
                full.append(tok)
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")
            continue
        console.print()
        reply = "".join(full)
        history.append({"role": "assistant", "content": reply})
        state.append_chat("agent", reply)


@app.command()
def research(name: str) -> None:
    """Run the research stage — gather sources, synthesize findings."""
    _require_healthy()
    state = _require_state(name)

    if state.stage == Stage.INTAKE:
        console.print("[yellow]![/yellow] Intake not complete. Run `chat` first.")
        raise typer.Exit(code=1)

    def progress(msg: str) -> None:
        console.print(f"[dim]{msg}[/dim]")

    outcome = orchestrator.run_research(state, on_progress=progress)
    console.print(Panel.fit(
        f"[green]✓ research saved[/green]: {outcome.output_path}\n\n"
        f"Next: [bold cyan]servicesmith concepts {name}[/bold cyan]",
        title="research done"))


@app.command()
def concepts(name: str) -> None:
    """Propose 3 service concepts based on intake + research."""
    _require_healthy()
    state = _require_state(name)
    if state.stage not in (Stage.SYNTHESIS, Stage.CHOICE):
        console.print(f"[yellow]![/yellow] Wrong stage ({state.stage.value}). "
                      f"Run `research` first.")
        raise typer.Exit(code=1)

    def progress(msg: str) -> None:
        console.print(f"[dim]{msg}[/dim]")

    outcome = orchestrator.run_synthesis(state, on_progress=progress)
    md = state.read_doc("concepts.md")
    console.print(Markdown(md))
    console.print(Panel.fit(
        f"Choose one: [bold cyan]servicesmith choose {name} <C1|C2|C3>[/bold cyan]",
        title="✓ concepts ready"))


@app.command()
def choose(name: str, concept_id: str) -> None:
    """Pick one of the proposed concepts (e.g. C1, C2, C3)."""
    state = _require_state(name)
    if state.stage not in (Stage.CHOICE, Stage.BLUEPRINT, Stage.EXECUTION):
        console.print("[yellow]![/yellow] No concepts to choose from yet.")
        raise typer.Exit(code=1)

    cid = concept_id.upper()
    data = json.loads(state.read_doc("concepts.json") or "{}")
    valid_ids = [c["id"] for c in data.get("concepts", [])]
    if cid not in valid_ids:
        console.print(f"[red]✗[/red] {cid!r} not in {valid_ids}")
        raise typer.Exit(code=1)

    state.chosen_concept_id = cid
    state.stage = Stage.BLUEPRINT
    state.save()
    console.print(Panel.fit(
        f"[green]✓ chose {cid}[/green]\n\n"
        f"Next: [bold cyan]servicesmith blueprint {name}[/bold cyan]",
        title="concept locked in"))


@app.command()
def blueprint(name: str) -> None:
    """Generate the full blueprint for the chosen concept."""
    _require_healthy()
    state = _require_state(name)
    if state.stage not in (Stage.BLUEPRINT, Stage.EXECUTION):
        console.print("[yellow]![/yellow] Run `choose` first.")
        raise typer.Exit(code=1)

    def progress(msg: str) -> None:
        console.print(f"[dim]{msg}[/dim]")

    outcome = orchestrator.run_blueprint(state, on_progress=progress)
    console.print(Markdown(state.read_doc("blueprint.md")))
    console.print(Panel.fit(
        f"[green]✓ blueprint saved[/green]: {outcome.output_path}\n\n"
        f"Next: validate with real conversations. "
        f"Use [bold cyan]servicesmith chat {name}[/bold cyan] to ask follow-up questions.",
        title="blueprint done"))


if __name__ == "__main__":
    app()
