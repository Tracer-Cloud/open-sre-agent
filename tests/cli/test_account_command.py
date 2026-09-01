from __future__ import annotations

import base64
import hashlib
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from config.account import AccountRecord, load_account_record, save_account_record
from integrations.github import PersonalGitHubSnapshot
from surfaces.cli import account_auth


def _record() -> AccountRecord:
    return AccountRecord(
        user_id="user_123",
        organization_id="org_123",
        github_username="octocat",
        email="octocat@example.com",
        app_url="https://app.opensre.com",
        signed_in_at="2026-09-01T10:00:00+00:00",
        token_expires_at="2026-12-01T10:00:00+00:00",
        github_scopes=("read:org", "repo"),
    )


def test_account_record_is_owner_only_and_contains_no_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "account.json"
    monkeypatch.setenv("OPENSRE_ACCOUNT_METADATA_PATH", str(path))

    save_account_record(_record())

    assert load_account_record() == _record()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    content = path.read_text(encoding="utf-8")
    assert "access_token" not in content
    assert "osre_pat_" not in content


def test_login_uses_state_and_pkce_without_putting_tokens_in_browser_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_urls: list[str] = []
    exchanged: dict[str, str] = {}
    saved_tokens: list[str] = []
    saved_records: list[AccountRecord] = []

    def fake_wait(*_args: object, **_kwargs: object) -> account_auth._CallbackResult:
        return account_auth._CallbackResult(code="osre_code_one_time")

    def fake_exchange(app_url: str, code: str, verifier: str) -> account_auth._ExchangeResult:
        exchanged.update(app_url=app_url, code=code, verifier=verifier)
        return account_auth._ExchangeResult(
            access_token="osre_pat_secret",
            token_expires_at="2026-12-01T10:00:00+00:00",
            user_id="user_123",
            organization_id="org_123",
            github_username="octocat",
            github_access_token="gho_secret",
            github_scopes=("repo",),
            llm_provider="openai",
            llm_model="gpt-5.4-mini",
            email="octocat@example.com",
        )

    monkeypatch.setattr(account_auth, "_wait_for_callback", fake_wait)
    monkeypatch.setattr(account_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "")
    monkeypatch.setattr(account_auth, "save_account_token", saved_tokens.append)
    monkeypatch.setattr(account_auth, "save_account_record", saved_records.append)
    monkeypatch.setattr(account_auth, "_configure_hosted_openai", lambda _model: None)
    monkeypatch.setattr(
        account_auth,
        "configure_personal_github",
        lambda **_kwargs: PersonalGitHubSnapshot(None),
    )

    def open_browser(url: str) -> bool:
        opened_urls.append(url)
        return True

    result = account_auth.login_account(
        app_url="https://app.opensre.com",
        browser_open=open_browser,
    )

    assert result.record.github_username == "octocat"
    assert saved_tokens == ["osre_pat_secret"]
    assert saved_records == [result.record]
    assert len(opened_urls) == 1
    assert "osre_pat_secret" not in opened_urls[0]
    assert "gho_secret" not in opened_urls[0]

    query = parse_qs(urlsplit(opened_urls[0]).query)
    verifier = exchanged["verifier"]
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert query["code_challenge"] == [expected_challenge]
    assert len(query["state"][0]) >= 32


def test_login_warns_when_env_token_would_override_and_does_not_revoke_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revoked: list[tuple[str, str]] = []

    def fake_wait(*_args: object, **_kwargs: object) -> account_auth._CallbackResult:
        return account_auth._CallbackResult(code="osre_code_one_time")

    def fake_exchange(*_args: object, **_kwargs: object) -> account_auth._ExchangeResult:
        return account_auth._ExchangeResult(
            access_token="osre_pat_new",
            token_expires_at="2026-12-01T10:00:00+00:00",
            user_id="user_123",
            organization_id="org_123",
            github_username="octocat",
            github_access_token="gho_secret",
            github_scopes=("repo",),
            llm_provider="openai",
            llm_model="gpt-5.4-mini",
            email="octocat@example.com",
        )

    def fake_revoke(app_url: str, token: str) -> bool:
        revoked.append((app_url, token))
        return True

    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "osre_pat_from_env")
    monkeypatch.setattr(account_auth, "_wait_for_callback", fake_wait)
    monkeypatch.setattr(account_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(account_auth, "_revoke_remote", fake_revoke)
    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "osre_pat_file_old")
    monkeypatch.setattr(account_auth, "save_account_token", lambda _token: None)
    monkeypatch.setattr(account_auth, "save_account_record", lambda _record: None)
    monkeypatch.setattr(account_auth, "_configure_hosted_openai", lambda _model: None)
    monkeypatch.setattr(
        account_auth,
        "configure_personal_github",
        lambda **_kwargs: PersonalGitHubSnapshot(None),
    )

    result = account_auth.login_account(
        app_url="https://app.opensre.com",
        open_browser=False,
    )

    assert "OPENSRE_ACCOUNT_TOKEN" in result.warning
    assert revoked == [("https://app.opensre.com", "osre_pat_file_old")]


def test_logout_without_personal_account_preserves_manual_github_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disconnected = False

    def disconnect() -> bool:
        nonlocal disconnected
        disconnected = True
        return True

    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "resolve_account_token", lambda: "")
    monkeypatch.setattr(account_auth, "delete_account_token", lambda: None)
    monkeypatch.setattr(account_auth, "delete_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "disconnect_personal_github", disconnect)

    result = account_auth.logout_account()

    assert result.remote_revoked is True
    assert disconnected is False
