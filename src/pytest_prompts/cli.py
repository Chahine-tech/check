from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from pytest_prompts.config import settings
from pytest_prompts.snapshot import Snapshot, SnapshotStore

app = typer.Typer(
    help="pytest for LLM prompts — tests, regressions, CI.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def run(
    path: str = typer.Argument(".", help="Test path (file or directory)."),
    snapshot_dir: str = typer.Option(
        settings.snapshot_dir, "--snapshot-dir", help="Where to write snapshots."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run prompt tests via pytest and summarize results."""
    store = SnapshotStore(snapshot_dir)
    _clear_dir(store.root)

    args = [
        sys.executable,
        "-m",
        "pytest",
        path,
        f"--pytest-prompts-snapshot-dir={snapshot_dir}",
    ]
    if verbose:
        args.append("-v")
    else:
        args.append("-q")

    result = subprocess.run(args, check=False)  # noqa: S603

    snapshots = store.all()
    _print_summary(snapshots)

    raise typer.Exit(code=result.returncode)


@app.command()
def diff(
    base: str = typer.Argument(
        ...,
        help="Git ref (e.g. main) or path to baseline snapshot directory.",
    ),
    path: str = typer.Option(".", "--path", "-p", help="Test path when base is a git ref."),
    threshold: float = typer.Option(
        0.05, "--threshold", help="Regression threshold (fraction, default 0.05)."
    ),
    head_dir: str = typer.Option(
        "", "--head-dir", help="Head snapshot dir (default: current snapshot_dir)."
    ),
) -> None:
    """Compare against a git ref or snapshot directory and report regressions.

    Examples:
        pytest-prompts diff main
        pytest-prompts diff main --path tests/prompts/
        pytest-prompts diff .snapshots/base .snapshots/head
    """
    base_is_path = Path(base).exists()

    if base_is_path:
        # Legacy: both args are snapshot dirs — treat head_dir as second positional
        _diff_dirs(base_dir=base, head_dir=head_dir or settings.snapshot_dir, threshold=threshold)
    else:
        _diff_git_ref(ref=base, test_path=path, threshold=threshold, head_dir=head_dir)


def _diff_git_ref(ref: str, test_path: str, threshold: float, head_dir: str) -> None:
    """Run tests on a git ref, then compare against current snapshot dir."""
    if not _git_available():
        console.print("[red]git not found in PATH.[/red]")
        raise typer.Exit(code=2)

    if not _ref_exists(ref):
        console.print(f"[red]Git ref not found:[/red] {ref}")
        raise typer.Exit(code=2)

    current_snap = head_dir or settings.snapshot_dir

    # Run head tests first (current working tree)
    console.print(f"\n[bold]Running tests on HEAD[/bold] ({test_path})")
    head_store = SnapshotStore(current_snap)
    _clear_dir(head_store.root)
    _run_pytest(test_path, current_snap, quiet=True)

    head_map = {s.test_id: s for s in head_store.all()}
    if not head_map:
        console.print(
            "[red]No snapshots after running tests.[/red] Check that tests use @prompt_test."
        )
        raise typer.Exit(code=2)

    # Run base tests in a temporary worktree
    console.print(f"[bold]Running tests on[/bold] {ref}")
    with tempfile.TemporaryDirectory() as tmp:
        base_snap = str(Path(tmp) / "base-snaps")
        worktree = str(Path(tmp) / "worktree")

        ret = subprocess.run(  # noqa: S603
            ["git", "worktree", "add", "--detach", worktree, ref],
            capture_output=True,
        )
        if ret.returncode != 0:
            console.print(f"[red]Failed to create git worktree:[/red] {ret.stderr.decode()}")
            raise typer.Exit(code=2)

        try:
            _run_pytest(test_path, base_snap, cwd=worktree, quiet=True)
        finally:
            subprocess.run(  # noqa: S603
                ["git", "worktree", "remove", "--force", worktree],
                capture_output=True,
            )

        base_map = {s.test_id: s for s in SnapshotStore(base_snap).all()}

    if not base_map:
        console.print(f"[yellow]No snapshots found for ref {ref!r} — nothing to compare.[/yellow]")
        raise typer.Exit(code=0)

    regressions = _compute_regressions(base_map, head_map, threshold)
    _print_diff(base_map, head_map, regressions, base_label=ref, head_label="HEAD")
    raise typer.Exit(code=1 if regressions else 0)


def _diff_dirs(base_dir: str, head_dir: str, threshold: float) -> None:
    base_map = {s.test_id: s for s in SnapshotStore(base_dir).all()}
    head_map = {s.test_id: s for s in SnapshotStore(head_dir).all()}

    if not base_map:
        console.print(f"[red]No snapshots in baseline:[/red] {base_dir}")
        raise typer.Exit(code=2)
    if not head_map:
        console.print(f"[red]No snapshots in head:[/red] {head_dir}")
        raise typer.Exit(code=2)

    regressions = _compute_regressions(base_map, head_map, threshold)
    _print_diff(base_map, head_map, regressions)
    raise typer.Exit(code=1 if regressions else 0)


def _run_pytest(path: str, snapshot_dir: str, cwd: str | None = None, quiet: bool = True) -> int:
    args = [
        sys.executable, "-m", "pytest", path,
        f"--pytest-prompts-snapshot-dir={snapshot_dir}",
        "-q" if quiet else "-v",
    ]
    result = subprocess.run(args, check=False, cwd=cwd)  # noqa: S603
    return result.returncode


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)  # noqa: S603
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _ref_exists(ref: str) -> bool:
    result = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--verify", ref],
        capture_output=True,
    )
    return result.returncode == 0


def _clear_dir(path: Path) -> None:
    if not path.is_dir():
        return
    for f in path.glob("*.json"):
        f.unlink()


def _print_summary(snapshots: list[Snapshot]) -> None:
    if not snapshots:
        console.print("\n[yellow]No pytest-prompts snapshots recorded.[/yellow]")
        return

    passed = sum(1 for s in snapshots if s.passed)
    failed = len(snapshots) - passed
    total_tokens = sum(s.input_tokens + s.output_tokens for s in snapshots)
    total_cost = sum(s.cost_usd for s in snapshots)

    table = Table(title="pytest-prompts results", show_lines=False)
    table.add_column("Test", style="cyan", overflow="fold")
    table.add_column("Model", style="magenta")
    table.add_column("Tokens", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Status", justify="center")

    for s in snapshots:
        status = Text("PASS", style="green") if s.passed else Text("FAIL", style="red")
        table.add_row(
            s.test_id,
            s.model,
            str(s.input_tokens + s.output_tokens),
            f"{s.latency_ms}ms",
            status,
        )

    console.print()
    console.print(table)
    summary = (
        f"[bold]{passed}[/bold] passed, "
        f"[bold]{failed}[/bold] failed — "
        f"{total_tokens} tokens total — ${total_cost:.4f}"
    )
    console.print(summary)


def _compute_regressions(
    base: dict[str, Snapshot],
    head: dict[str, Snapshot],
    threshold: float,
) -> list[tuple[str, str]]:
    regressions: list[tuple[str, str]] = []
    for test_id, h in head.items():
        b = base.get(test_id)
        if b is None:
            continue
        if b.passed and not h.passed:
            regressions.append((test_id, "pass → fail"))
            continue
        # Judge regression: verdict true → false
        b_verdicts = [j.get("verdict") for j in (b.judge_calls or [])]
        h_verdicts = [j.get("verdict") for j in (h.judge_calls or [])]
        for i, (bv, hv) in enumerate(zip(b_verdicts, h_verdicts, strict=False)):
            if bv is True and hv is False:
                criterion = (h.judge_calls or [])[i].get("criterion", "")
                regressions.append((test_id, f"judge verdict false: {criterion}"))
                break
        else:
            base_tokens = b.input_tokens + b.output_tokens
            head_tokens = h.input_tokens + h.output_tokens
            if base_tokens > 0 and (head_tokens - base_tokens) / base_tokens > threshold:
                pct = (head_tokens - base_tokens) / base_tokens * 100
                regressions.append(
                    (test_id, f"tokens {base_tokens} → {head_tokens} (+{pct:.0f}%)")
                )
                continue
            if b.latency_ms > 0 and (h.latency_ms - b.latency_ms) / b.latency_ms > threshold:
                pct = (h.latency_ms - b.latency_ms) / b.latency_ms * 100
                regressions.append(
                    (test_id, f"latency {b.latency_ms}ms → {h.latency_ms}ms (+{pct:.0f}%)")
                )
    return regressions


def _print_diff(
    base: dict[str, Snapshot],
    head: dict[str, Snapshot],
    regressions: list[tuple[str, str]],
    base_label: str = "base",
    head_label: str = "head",
) -> None:
    table = Table(title="pytest-prompts diff", show_lines=False)
    table.add_column("Test", style="cyan", overflow="fold")
    table.add_column(base_label, justify="right")
    table.add_column(head_label, justify="right")
    table.add_column("Status", justify="center")

    reg_ids = {tid for tid, _ in regressions}

    for test_id in sorted(set(base) | set(head)):
        b = base.get(test_id)
        h = head.get(test_id)
        if test_id in reg_ids:
            status = Text("REGRESSION", style="red bold")
        elif b is None:
            status = Text("new", style="blue")
        elif h is None:
            status = Text("removed", style="yellow")
        else:
            status = Text("ok", style="green")
        table.add_row(test_id, _cell(b), _cell(h), status)

    console.print()
    console.print(table)

    if regressions:
        console.print()
        for tid, reason in regressions:
            console.print(f"[red bold]❌ REGRESSION[/red bold]  {tid}")
            console.print(f"   {reason}")
        console.print()
    else:
        console.print("\n[green]No regressions detected.[/green]")


def _cell(s: Snapshot | None) -> str:
    if s is None:
        return "-"
    status = "✓" if s.passed else "✗"
    tokens = s.input_tokens + s.output_tokens
    return f"{status} {tokens}t {s.latency_ms}ms"


if __name__ == "__main__":
    app()
