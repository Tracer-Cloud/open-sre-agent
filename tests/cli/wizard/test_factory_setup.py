"""Tests for factory-style first-run setup (GitHub → LLM)."""

from __future__ import annotations

from integrations.github.login import GitHubLoginResult
from surfaces.cli.wizard import factory_setup


def test_run_factory_setup_requires_github_then_runs_llm(monkeypatch) -> None:
    github_calls: list[bool] = []
    llm_calls: list[dict[str, object]] = []

    monkeypatch.setattr(factory_setup, "render_factory_setup_header", lambda: None)

    def _github(*, step: int, total_steps: int) -> bool:
        github_calls.append(True)
        assert step == 1
        assert total_steps == factory_setup.FACTORY_SETUP_TOTAL_STEPS
        return True

    def _llm(**kwargs: object) -> int:
        llm_calls.append(kwargs)
        return 0

    monkeypatch.setattr(factory_setup, "_run_github_signup_step", _github)
    monkeypatch.setattr(factory_setup, "run_llm_setup", _llm)

    assert factory_setup.run_factory_setup() == 0
    assert github_calls == [True]
    assert llm_calls == [
        {
            "show_header": False,
            "start_step": 2,
            "total_steps": factory_setup.FACTORY_SETUP_TOTAL_STEPS,
        }
    ]


def test_run_factory_setup_stops_when_github_cancelled(monkeypatch) -> None:
    monkeypatch.setattr(factory_setup, "render_factory_setup_header", lambda: None)
    monkeypatch.setattr(
        factory_setup,
        "_run_github_signup_step",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        factory_setup,
        "run_llm_setup",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM setup must not run")),
    )

    assert factory_setup.run_factory_setup() == 1


def test_github_signup_skips_prompt_when_already_signed_in(monkeypatch) -> None:
    import integrations.github as github

    monkeypatch.setattr(github, "saved_github_username", lambda: "octocat")
    monkeypatch.setattr(factory_setup, "confirm", lambda *_a, **_k: False)
    called: list[bool] = []
    monkeypatch.setattr(
        github,
        "authenticate_and_configure_github",
        lambda **_kwargs: called.append(True) or GitHubLoginResult(ok=True, username="octocat"),
    )

    assert factory_setup._run_github_signup_step(step=1, total_steps=3) is True
    assert called == []


def test_github_signup_retries_then_succeeds(monkeypatch) -> None:
    import integrations.github as github

    monkeypatch.setattr(github, "saved_github_username", lambda: "")
    attempts = {"n": 0}

    def _auth(**_kwargs: object) -> GitHubLoginResult:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return GitHubLoginResult(ok=False, detail="missing tools")
        return GitHubLoginResult(ok=True, username="octocat", detail="OK")

    monkeypatch.setattr(github, "authenticate_and_configure_github", _auth)
    monkeypatch.setattr(factory_setup, "choose", lambda *_a, **_k: "retry")

    assert factory_setup._run_github_signup_step(step=1, total_steps=3) is True
    assert attempts["n"] == 2
