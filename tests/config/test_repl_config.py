"""Tests for REPL config three-tier resolution."""

from __future__ import annotations

import textwrap

import pytest

from config.repl_config import (
    ReplConfig,
    read_github_login_deferred,
    write_github_login_deferred,
)


class TestReplConfigDefaults:
    def test_default_enabled_is_true(self) -> None:
        cfg = ReplConfig.load()
        assert cfg.enabled is True

    def test_default_layout_is_classic(self) -> None:
        cfg = ReplConfig.load()
        assert cfg.layout == "classic"

    def test_default_theme_is_blue(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENSRE_THEME", raising=False)
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        cfg = ReplConfig.load()
        assert cfg.theme == "blue"


class TestEnvVarResolution:
    def test_opensre_interactive_0_disables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        assert ReplConfig.load().enabled is False

    def test_opensre_interactive_false_disables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "false")
        assert ReplConfig.load().enabled is False

    def test_opensre_interactive_off_disables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "off")
        assert ReplConfig.load().enabled is False

    def test_opensre_interactive_1_enables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "1")
        assert ReplConfig.load().enabled is True

    def test_opensre_layout_pinned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")
        assert ReplConfig.load().layout == "pinned"

    def test_opensre_layout_classic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "classic")
        assert ReplConfig.load().layout == "classic"

    def test_invalid_layout_falls_back_to_classic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "fullscreen")
        assert ReplConfig.load().layout == "classic"

    def test_opensre_theme_env_sets_theme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "blue")
        assert ReplConfig.load().theme == "blue"

    def test_invalid_theme_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "nope")
        assert ReplConfig.load().theme == "blue"

    def test_invalid_theme_logs_warning(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "chartreuse")

        with caplog.at_level("WARNING"):
            cfg = ReplConfig.load()

        assert cfg.theme == "blue"
        assert "OPENSRE_THEME='chartreuse' is not a valid theme" in caplog.text


class TestCliOverride:
    def test_cli_enabled_false_wins_over_env_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "1")
        cfg = ReplConfig.load(cli_enabled=False)
        assert cfg.enabled is False

    def test_cli_enabled_true_wins_over_env_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        cfg = ReplConfig.load(cli_enabled=True)
        assert cfg.enabled is True

    def test_cli_layout_pinned_wins_over_env_classic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "classic")
        cfg = ReplConfig.load(cli_layout="pinned")
        assert cfg.layout == "pinned"

    def test_cli_layout_classic_wins_over_env_pinned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")
        cfg = ReplConfig.load(cli_layout="classic")
        assert cfg.layout == "classic"

    def test_cli_none_does_not_override_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        cfg = ReplConfig.load(cli_enabled=None)
        assert cfg.enabled is False

    def test_cli_theme_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "green")
        cfg = ReplConfig.load(cli_theme="amber")
        assert cfg.theme == "amber"


class TestFileResolution:
    def test_file_enabled_false_is_read(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: false
                  layout: classic
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is False

    def test_file_layout_pinned_is_read(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: true
                  layout: pinned
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.layout == "pinned"

    def test_file_theme_is_read(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  theme: mono
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_THEME", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.theme == "mono"

    def test_invalid_file_theme_logs_warning(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  theme: chartreuse
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_THEME", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        with caplog.at_level("WARNING"):
            cfg = ReplConfig.load()

        assert cfg.theme == "blue"
        assert "interactive.theme='chartreuse' is not a valid theme" in caplog.text

    def test_env_overrides_file(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: false
                  layout: pinned
            """),
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "1")
        monkeypatch.setenv("OPENSRE_LAYOUT", "classic")

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is True
        assert cfg.layout == "classic"

    def test_cli_overrides_file_and_env(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: false
                  layout: pinned
            """),
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load(cli_enabled=True, cli_layout="classic")
        assert cfg.enabled is True
        assert cfg.layout == "classic"

    def test_missing_file_falls_back_to_defaults(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is True
        assert cfg.layout == "classic"

    def test_malformed_file_falls_back_to_defaults(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(":::not valid yaml:::", encoding="utf-8")
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is True
        assert cfg.layout == "classic"


class TestFromEnvAlias:
    def test_from_env_is_same_as_load_with_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")
        assert ReplConfig.from_env() == ReplConfig.load()


class TestThemeRegistry:
    def test_theme_registry_contains_expected_builtin_names(self) -> None:
        from platform.terminal.theme import list_theme_names

        assert list_theme_names() == (
            "green",
            "blue",
            "amber",
            "mono",
            "red",
            "pink",
            "purple",
            "orange",
            "teal",
            "lime",
            "nord",
            "dracula",
            "solarized",
            "gruvbox",
            "webflux",
            "sunset",
        )

    def test_theme_registry_entries_include_required_semantic_tokens(self) -> None:
        from platform.terminal.theme import get_theme, list_theme_names

        required = (
            "HIGHLIGHT",
            "BRAND",
            "TEXT",
            "SECONDARY",
            "DIM",
            "WARNING",
            "ERROR",
            "BG",
            "INPUT_SURFACE",
        )
        for name in list_theme_names():
            theme = get_theme(name)
            for token in required:
                value = getattr(theme, token)
                assert isinstance(value, str)
                assert value.startswith("#")
                assert len(value) == 7

    def test_lazy_rich_tokens_track_active_theme(self) -> None:
        from platform.terminal.theme import BOLD_BRAND, HIGHLIGHT, set_active_theme

        set_active_theme("green")
        green_highlight = str(HIGHLIGHT)
        green_brand = str(BOLD_BRAND)
        set_active_theme("purple")
        assert str(HIGHLIGHT) != green_highlight
        assert str(BOLD_BRAND) != green_brand
        assert str(HIGHLIGHT).startswith("#")

    def test_set_active_theme_falls_back_to_default_for_unknown_name(self) -> None:
        from platform.terminal.theme import (
            DEFAULT_THEME_NAME,
            get_active_theme,
            set_active_theme,
        )

        active = set_active_theme("does-not-exist")
        assert active.name == DEFAULT_THEME_NAME
        assert get_active_theme().name == DEFAULT_THEME_NAME

    def test_load_without_apply_active_theme_leaves_global_palette(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from platform.terminal.theme import get_active_theme_name, set_active_theme

        monkeypatch.delenv("OPENSRE_THEME", raising=False)
        set_active_theme("pink")
        ReplConfig.load(apply_active_theme=False)
        assert get_active_theme_name() == "pink"


class TestGithubLoginDeferral:
    def test_read_defaults_false(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        assert read_github_login_deferred() is False

    def test_write_and_read_round_trip(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        write_github_login_deferred(True)
        assert read_github_login_deferred() is True
        write_github_login_deferred(False)
        assert read_github_login_deferred() is False
