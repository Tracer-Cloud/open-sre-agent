"""Create and clean up the disposable GitHub CI durability demo fixture."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Final

BRANCH_PREFIX: Final = "codex/ci-durability-demo-"
PR_MARKER: Final = "<!-- opensre-ci-durability-demo -->"
STATE_PREFIX: Final = "opensre-ci-durability-demo-state-"
WORKTREE_PREFIX: Final = "opensre-ci-durability-demo-worktree-"

_WORKFLOW = """name: OpenSRE CI Durability Demo

on:
  pull_request:
    paths:
      - ".github/workflows/opensre-ci-durability-demo.yml"
      - ".opensre-ci-durability-demo/**"

permissions:
  contents: read

jobs:
  durability-stage-one:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Exercise the first defect
        run: python .opensre-ci-durability-demo/retry_delay.py

  durability-stage-two:
    needs: durability-stage-one
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Exercise the dependent defect
        run: python .opensre-ci-durability-demo/service_slug.py
"""

_STAGE_ONE = """def retry_delay(attempt: int) -> int:
    \"\"\"Return the cumulative delay before a retry attempt.\"\"\"
    return attempt


if __name__ == \"__main__\":
    assert retry_delay(3) == 6, \"three attempts should accumulate to six seconds\"
"""

_STAGE_TWO = """def service_slug(value: str) -> str:
    \"\"\"Return the canonical slug used in service URLs.\"\"\"
    return value.strip().lower()


if __name__ == \"__main__\":
    assert service_slug(\"Open SRE\") == \"open-sre\", \"spaces must become hyphens\"
"""


class DemoError(RuntimeError):
    """Raised when fixture safety or a subprocess operation fails."""


def _run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise DemoError(f"{' '.join(args[:3])} failed: {detail}")
    return completed.stdout.strip()


def _repo_scope(origin: str) -> str:
    match = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?$", origin.strip())
    if match is None:
        raise DemoError("origin must be a github.com repository")
    return f"{match.group(1)}/{match.group(2)}"


def _default_branch(repo_root: Path) -> str:
    try:
        symbolic = _run("git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=repo_root)
    except DemoError:
        symbolic = "origin/main"
    branch = symbolic.removeprefix("origin/")
    _run("git", "rev-parse", "--verify", f"origin/{branch}", cwd=repo_root)
    return branch


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create(repo_root: Path) -> dict[str, Any]:
    """Create one uniquely marked same-repository demo PR."""
    root = Path(_run("git", "rev-parse", "--show-toplevel", cwd=repo_root)).resolve()
    repo = _repo_scope(_run("git", "remote", "get-url", "origin", cwd=root))
    base = _default_branch(root)
    _run("git", "fetch", "origin", base, cwd=root)

    identifier = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    branch = f"{BRANCH_PREFIX}{identifier}"
    worktree = Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX)).resolve()
    state_file = Path(tempfile.gettempdir()) / f"{STATE_PREFIX}{identifier}.json"
    state: dict[str, Any] = {
        "base": base,
        "branch": branch,
        "pr_number": None,
        "pr_url": "",
        "repo": repo,
        "repo_root": str(root),
        "state_file": str(state_file),
        "worktree": str(worktree),
    }
    _write_state(state_file, state)

    try:
        _run("git", "worktree", "add", "-b", branch, str(worktree), f"origin/{base}", cwd=root)
        workflow = worktree / ".github/workflows/opensre-ci-durability-demo.yml"
        fixture = worktree / ".opensre-ci-durability-demo"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        fixture.mkdir(parents=True, exist_ok=True)
        workflow.write_text(_WORKFLOW, encoding="utf-8")
        (fixture / "retry_delay.py").write_text(_STAGE_ONE, encoding="utf-8")
        (fixture / "service_slug.py").write_text(_STAGE_TWO, encoding="utf-8")
        _run(
            "git",
            "add",
            str(workflow.relative_to(worktree)),
            str(fixture.relative_to(worktree)),
            cwd=worktree,
        )
        _run("git", "commit", "-m", "demo: add staged CI durability fixture", cwd=worktree)
        baseline_sha = _run("git", "rev-parse", "HEAD", cwd=worktree)
        state["baseline_sha"] = baseline_sha
        _write_state(state_file, state)
        _run("git", "push", "--set-upstream", "origin", branch, cwd=worktree)
        body = "\n".join(
            (
                PR_MARKER,
                "Disposable synthetic PR for the OpenSRE CI durability demo.",
                "It is closed and deleted automatically after evidence collection.",
            )
        )
        pr_url = _run(
            "gh",
            "pr",
            "create",
            "--repo",
            repo,
            "--base",
            base,
            "--head",
            branch,
            "--title",
            "Demo: staged CI durability experiment",
            "--body",
            body,
            cwd=worktree,
        )
        pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])
        state.update({"pr_number": pr_number, "pr_url": pr_url})
        _write_state(state_file, state)
        return {"ok": True, **state}
    except Exception:
        print(json.dumps({"ok": False, **state}), flush=True)
        raise


def _validated_state(state_file: Path) -> dict[str, Any]:
    if not state_file.name.startswith(STATE_PREFIX):
        raise DemoError("refusing a state file not created by this demo")
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DemoError("demo state must be a JSON object")
    branch = str(raw.get("branch") or "")
    worktree = Path(str(raw.get("worktree") or "")).resolve()
    if not branch.startswith(BRANCH_PREFIX):
        raise DemoError("refusing to clean a branch outside the demo prefix")
    if not worktree.name.startswith(WORKTREE_PREFIX):
        raise DemoError("refusing to remove a worktree outside the demo prefix")
    resolved_repo = _repo_scope(
        _run("git", "remote", "get-url", "origin", cwd=Path(str(raw["repo_root"])))
    )
    if resolved_repo != raw.get("repo"):
        raise DemoError("refusing cleanup because the repository no longer matches demo state")
    return raw


def _matching_open_pr(root: Path, repo: str, branch: str) -> dict[str, Any] | None:
    payload = json.loads(
        _run(
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--state",
            "open",
            "--limit",
            "2",
            "--json",
            "number,body,headRefName,state",
            cwd=root,
        )
    )
    matches = [
        pr
        for pr in payload
        if pr.get("headRefName") == branch and PR_MARKER in str(pr.get("body") or "")
    ]
    if len(matches) > 1:
        raise DemoError("refusing cleanup because multiple marked demo PRs use this branch")
    return matches[0] if matches else None


def cleanup(state_file: Path) -> dict[str, Any]:
    """Close and remove only resources carrying the demo's safety markers."""
    state = _validated_state(state_file.resolve())
    root = Path(str(state["repo_root"])).resolve()
    worktree = Path(str(state["worktree"])).resolve()
    branch = str(state["branch"])
    repo = str(state["repo"])
    pr_number = state.get("pr_number")

    if pr_number is not None:
        pr = json.loads(
            _run(
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo,
                "--json",
                "body,headRefName,state",
                cwd=root,
            )
        )
        if pr.get("headRefName") != branch or PR_MARKER not in str(pr.get("body") or ""):
            raise DemoError("refusing to close a PR without matching demo markers")
    else:
        pr = _matching_open_pr(root, repo, branch)
        pr_number = pr.get("number") if pr is not None else None
    if pr_number is not None and pr is not None and pr.get("state") == "OPEN":
        _run(
            "gh",
            "pr",
            "close",
            str(pr_number),
            "--repo",
            repo,
            "--delete-branch",
            cwd=root,
        )

    remote_ref = _run("git", "ls-remote", "--heads", "origin", branch, cwd=root)
    if remote_ref:
        _run("git", "push", "origin", "--delete", branch, cwd=root)
    registered_worktrees = _run("git", "worktree", "list", "--porcelain", cwd=root)
    if worktree.exists() and f"worktree {worktree}" in registered_worktrees:
        _run("git", "worktree", "remove", "--force", str(worktree), cwd=root)
    elif worktree.exists():
        worktree.rmdir()
    local_branches = _run("git", "branch", "--list", branch, cwd=root)
    if local_branches:
        _run("git", "branch", "-D", branch, cwd=root)
    state_file.unlink()
    return {
        "ok": True,
        "branch_deleted": True,
        "pr_closed": pr_number is not None,
        "state_file_deleted": True,
        "worktree_removed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--repo-root", type=Path, required=True)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--state-file", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = create(args.repo_root) if args.command == "create" else cleanup(args.state_file)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
