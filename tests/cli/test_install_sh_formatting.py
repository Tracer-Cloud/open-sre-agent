"""Source-level contract tests for install.sh output formatting."""

from __future__ import annotations

from pathlib import Path

INSTALL_SH = Path(__file__).parents[2] / "install.sh"


def test_install_sh_enables_colors_only_for_tty_output() -> None:
    source = INSTALL_SH.read_text()
    assert '[ "${TERM:-}" != "dumb" ] && [ -t 1 ]' in source
    assert '[ "${TERM:-}" != "dumb" ] && [ -t 2 ]' in source


def test_install_sh_defines_styled_output_helpers() -> None:
    source = INSTALL_SH.read_text()
    assert "log_step()" in source
    assert "log_success()" in source
    assert "log_highlight()" in source
    assert "print_separator()" in source
    assert 'stderr_line "${ANSI_YELLOW}${ANSI_BOLD}" "Warning: $*"' in source
    assert 'stderr_line "${ANSI_RED}${ANSI_BOLD}" "Error: $*"' in source


def test_install_sh_success_block_lists_next_steps() -> None:
    source = INSTALL_SH.read_text()
    assert 'log_step "4/4"' in source
    assert 'log_success "OpenSRE main build installed successfully."' in source
    assert 'log_success "OpenSRE v${version} installed successfully."' in source
    assert 'log_highlight "Next steps:"' in source
    assert "log \"  1. Run 'opensre onboard' to complete setup.\"" in source
    assert "log \"  2. Then run 'opensre investigate -i <alert.json>' with a real alert.\"" in source
    assert 'log "Docs: https://www.opensre.com/docs"' in source
