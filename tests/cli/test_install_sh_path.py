"""Tests for the configure_path() function in install.sh."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# install.sh is a POSIX shell script that exercises zsh/bash/fish rc-file
# behaviour, and these tests drive it via ``subprocess.run(["bash", "-c", ...])``.
# On the GitHub Actions ``windows-latest`` runner, ``bash`` is resolved to
# ``wsl.exe`` and the runner has no installed WSL distribution — every
# ``_run`` call exits 1 with a "Windows Subsystem for Linux has no installed
# distributions" message and none of the asserted rc files get written.
# Skip the whole module rather than chase a Windows analogue for a Unix-only
# installer script.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "install.sh is POSIX-only; the Windows runner has no usable bash "
        "(resolves to unconfigured WSL), so this module's subprocess-driven "
        "tests cannot run there. See issue #1099."
    ),
)

INSTALL_SH = Path(__file__).parents[2] / "install.sh"
_INSTALL_SH_SHELL = shlex.quote(str(INSTALL_SH))
_LOCAL_BIN = ".local/bin"
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _visible_terminal_text(value: str) -> str:
    return _strip_ansi(value).replace("\r", "").replace("\n", "")


def _run(
    tmp_path: Path, shell: str, platform: str = "linux", install_dir: str | None = None
) -> subprocess.CompletedProcess[str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    idir = install_dir if install_dir is not None else str(fake_home / _LOCAL_BIN)
    install_sh = _INSTALL_SH_SHELL
    idir_shell = shlex.quote(idir)
    home_shell = shlex.quote(str(fake_home))

    script = textwrap.dedent(f"""\
        __fn=$(awk 'p&&/^}}$/{{print;exit}} /^configure_path\\(\\)/{{p=1}} p{{print}}' {install_sh})
        if [ -z "$__fn" ]; then
            echo "configure_path not found in install.sh" >&2
            exit 1
        fi
        log()  {{ printf '%s\\n' "$*"; }}
        warn() {{ printf 'Warning: %s\\n' "$*" >&2; }}
        eval "$__fn"
        INSTALL_DIR={idir_shell} platform="{platform}" HOME={home_shell} SHELL="{shell}" configure_path
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _run_logging_snippet(body: str) -> subprocess.CompletedProcess[str]:
    install_sh = _INSTALL_SH_SHELL
    script = textwrap.dedent(f"""\
        eval "$(awk '/^REPO=/{{exit}} {{print}}' {install_sh})"
        eval "$(awk '
            /^[a-z_][a-z_]*\\(\\)/ {{ in_fn=1 }}
            in_fn {{ print }}
            in_fn && /^\\}}$/ {{ in_fn=0 }}
        ' {install_sh})"
        {body}
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _find_release_metadata_step_block() -> str:
    lines = INSTALL_SH.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip() != 'release_tag=""':
            continue

        block = []
        for candidate in lines[i + 1 :]:
            block.append(candidate)
            if candidate.strip() == "fi":
                return "\n".join(block)

    raise RuntimeError(f"Could not locate release metadata step block in {INSTALL_SH}.")


def _run_release_metadata_step(
    install_channel: str = "release", version: str = ""
) -> subprocess.CompletedProcess[str]:
    block = _find_release_metadata_step_block()
    install_sh = _INSTALL_SH_SHELL
    script = textwrap.dedent(f"""\
        eval "$(awk '/^REPO=/{{exit}} {{print}}' {install_sh})"
        eval "$(awk '
            /^[a-z_][a-z_]*\\(\\)/ {{ in_fn=1 }}
            in_fn {{ print }}
            in_fn && /^\\}}$/ {{ in_fn=0 }}
        ' {install_sh})"
        INSTALL_CHANNEL="{install_channel}"
        version="{version}"
        {block}
        printf '%s\\n' "$metadata_step"
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_install_sh_logging_falls_back_to_plain_text_when_not_tty() -> None:
    result = _run_logging_snippet(
        """
        warn "check config"
        success "installed"
        step "[1/4] Fetching metadata"
        """
    )

    assert result.returncode == 0, result.stderr
    assert "\x1b[" not in result.stdout + result.stderr
    assert "Warning: check config" in result.stderr
    assert "Success: installed" in result.stdout
    assert "[1/4] Fetching metadata" in result.stdout


def test_install_sh_die_falls_back_to_plain_text_when_not_tty() -> None:
    result = _run_logging_snippet('die "missing curl"')

    assert result.returncode == 1
    assert "\x1b[" not in result.stderr
    assert "Error: missing curl" in result.stderr


def test_install_sh_defines_tty_aware_ansi_formatting() -> None:
    source = INSTALL_SH.read_text()

    assert "if [ -t 1 ]; then" in source
    assert "COLOR_GREEN=$'\\033[32m'" in source
    assert "COLOR_YELLOW=$'\\033[33m'" in source
    assert "COLOR_RED=$'\\033[31m'" in source
    assert "success()" in source


def test_install_sh_success_screen_has_visual_structure() -> None:
    result = _run_logging_snippet("print_success_screen 2026.4.1")
    output = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert "--------------------------------------------" in output
    assert "Success: Welcome to OpenSRE" in output
    assert "opensre v2026.4.1 installed successfully" in output
    assert "Next steps:" in output


def test_install_sh_has_step_for_explicit_version_fetch() -> None:
    result = _run_release_metadata_step(version="2026.4.29")

    assert result.returncode == 0, result.stderr
    assert "[1/6] Fetching release metadata for v2026.4.29" in result.stdout


def test_install_sh_defaults_to_main_build_channel() -> None:
    source = INSTALL_SH.read_text()

    assert 'INSTALL_CHANNEL="${OPENSRE_INSTALL_CHANNEL:-main}"' in source
    assert 'MAIN_RELEASE_TAG="${OPENSRE_MAIN_RELEASE_TAG:-main-build}"' in source
    assert "releases/tags/${MAIN_RELEASE_TAG}" in source
    assert "releases/tags/nightly" not in source


def test_install_sh_defines_progress_helpers() -> None:
    source = INSTALL_SH.read_text()

    for helper in (
        "is_interactive_terminal()",
        "intro_disabled()",
        "terminal_supports_unicode()",
        "terminal_columns()",
        "truncate_text()",
        "friendly_progress_label()",
        "draw_intro_frame()",
        "show_installer_intro()",
        "progress_frame()",
        "draw_progress()",
        "finish_progress()",
        "run_with_progress()",
        "capture_with_progress()",
    ):
        assert helper in source

    assert "OPENSRE_INSTALL_VERBOSE" in source
    assert "OPENSRE_INSTALL_NO_INTRO" in source
    assert "\\033[?25h" in source
    assert 'trap \'kill "$command_pid"' in source
    assert "trap 'printf \"\\033[0m\\033[?25h\\033[2J\\033[H\"; exit 130'" in source


def test_install_sh_draw_progress_fits_terminal_width_with_long_labels() -> None:
    long_checksum = (
        "[4/6] Downloading and verifying checksum (opensre_main_darwin-arm64.tar.gz.sha256)"
    )
    result = _run_logging_snippet(
        f"""
        terminal_columns() {{ printf '60\\n'; }}
        terminal_supports_unicode() {{ return 1; }}
        draw_progress {shlex.quote(long_checksum)} 9
        """
    )
    output = result.stdout + result.stderr
    visible_segments = [
        _visible_terminal_text(segment) for segment in re.split(r"[\r\n]", output) if segment
    ]

    assert result.returncode == 0, result.stderr
    assert visible_segments
    assert all(len(segment) <= 60 for segment in visible_segments)
    assert "verifying checksum" in visible_segments[-1]
    assert "opensre_main_darwin-arm64" not in visible_segments[-1]


def test_install_sh_animated_repaints_do_not_wrap_or_leave_long_label_residue() -> None:
    long_checksum = (
        "[4/6] Downloading and verifying checksum (opensre_main_darwin-arm64.tar.gz.sha256)"
    )
    result = _run_logging_snippet(
        f"""
        is_interactive_terminal() {{ return 0; }}
        terminal_columns() {{ printf '56\\n'; }}
        terminal_supports_unicode() {{ return 1; }}
        run_with_progress {shlex.quote(long_checksum)} bash -c 'sleep 0.25'
        """
    )
    output = result.stdout + result.stderr
    animated_segments = [
        _visible_terminal_text(segment)
        for segment in re.split(r"[\r\n]", output)
        if "Installing OpenSRE" in _visible_terminal_text(segment)
    ]

    assert result.returncode == 0, result.stderr
    assert animated_segments
    assert all(len(segment) <= 56 for segment in animated_segments)
    assert all("opensre_main_darwin-arm64" not in segment for segment in animated_segments)


def test_install_sh_no_intro_disables_intro_only() -> None:
    result = _run_logging_snippet(
        """
        is_interactive_terminal() { return 0; }
        OPENSRE_INSTALL_NO_INTRO=1
        print_installer_header
        """
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert "OpenSRE Installer" in output
    assert "Installing the OpenSRE CLI" in output
    assert "\x1b[2J" not in output


def test_install_sh_progress_plain_when_not_tty() -> None:
    result = _run_logging_snippet(
        """
        run_with_progress "Plain progress step" bash -c 'printf "work complete\\\\n"'
        """
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert "Plain progress step" in output
    assert "work complete" in output
    assert "\x1b[" not in output
    assert "\r" not in output


def test_install_sh_capture_with_progress_keeps_stdout_value_clean() -> None:
    result = _run_logging_snippet(
        """
        is_interactive_terminal() { return 0; }
        terminal_supports_unicode() { return 1; }
        capture_with_progress captured_value "Capture value" bash -c 'printf "release-json"'
        printf '\\nRESULT:%s\\n' "$captured_value"
        """
    )

    assert result.returncode == 0, result.stderr
    assert "RESULT:release-json" in result.stdout
    assert "RESULT:Capture value" not in result.stdout


def test_install_sh_capture_with_progress_preserves_failure_status_and_logs() -> None:
    result = _run_logging_snippet(
        """
        if capture_with_progress captured_value "Failing capture step" bash -c 'echo hidden-out; echo hidden-err >&2; exit 7'; then
            exit 99
        else
            progress_status=$?
        fi
        exit "$progress_status"
        """
    )
    output = result.stdout + result.stderr

    assert result.returncode == 7
    assert "Failing capture step" in output
    assert "hidden-out" in output
    assert "hidden-err" in output


def test_install_sh_run_with_progress_prints_captured_logs_on_failure() -> None:
    result = _run_logging_snippet(
        """
        is_interactive_terminal() { return 0; }
        terminal_supports_unicode() { return 1; }
        if run_with_progress "Failing progress step" bash -c 'echo hidden-out; echo hidden-err >&2; exit 7'; then
            exit 99
        else
            progress_status=$?
        fi
        exit "$progress_status"
        """
    )
    output = result.stdout + result.stderr

    assert result.returncode == 7
    assert "Failing progress step failed" in output
    assert "hidden-out" in output
    assert "hidden-err" in output


def test_install_sh_uses_six_step_extract_verify_install_labels() -> None:
    source = INSTALL_SH.read_text()

    assert "[4/6] Downloading and verifying checksum" in source
    assert "[5/6] Extracting and verifying binary" in source
    assert "[6/6] Installing ${BIN_NAME} to ${INSTALL_DIR}" in source
    assert "[6/6] Extracting release archive" not in source
    assert 'capture_with_progress installed_version "Verifying installed binary"' not in source


def test_zsh_writes_export_to_zshrc(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    zshrc = tmp_path / "home" / ".zshrc"
    assert zshrc.exists()
    assert _LOCAL_BIN in zshrc.read_text()


def test_bash_linux_writes_to_bashrc(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/bash", platform="linux")
    assert result.returncode == 0, result.stderr
    bashrc = tmp_path / "home" / ".bashrc"
    assert bashrc.exists()
    assert _LOCAL_BIN in bashrc.read_text()


def test_bash_macos_writes_to_bash_profile(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/bash", platform="darwin")
    assert result.returncode == 0, result.stderr
    bash_profile = tmp_path / "home" / ".bash_profile"
    assert bash_profile.exists()
    assert _LOCAL_BIN in bash_profile.read_text()


def test_fish_uses_fish_add_path(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/usr/bin/fish")
    assert result.returncode == 0, result.stderr
    fish_config = tmp_path / "home" / ".config" / "fish" / "config.fish"
    assert fish_config.exists()
    assert "fish_add_path" in fish_config.read_text()


def test_unknown_shell_prints_manual_instructions(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/dash")
    assert result.returncode == 0, result.stderr
    home = tmp_path / "home"
    assert not (home / ".zshrc").exists()
    assert not (home / ".bashrc").exists()
    assert not (home / ".bash_profile").exists()
    assert "export PATH" in result.stdout or "export PATH" in result.stderr


def test_idempotent_no_duplicate_on_rerun(tmp_path: Path) -> None:
    _run(tmp_path, shell="/bin/zsh")
    _run(tmp_path, shell="/bin/zsh")
    content = (tmp_path / "home" / ".zshrc").read_text()
    export_lines = [ln for ln in content.splitlines() if _LOCAL_BIN in ln and "export PATH" in ln]
    assert len(export_lines) == 1


def test_skips_when_install_dir_already_in_rc(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    idir = str(home / _LOCAL_BIN)
    zshrc = home / ".zshrc"
    zshrc.write_text(f'export PATH="$PATH:{idir}"\n')
    original = zshrc.read_text()

    result = _run(tmp_path, shell="/bin/zsh", install_dir=idir)
    assert result.returncode == 0, result.stderr
    assert zshrc.read_text() == original


def test_creates_rc_file_when_missing(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / ".zshrc").exists()


def test_marker_comment_present(tmp_path: Path) -> None:
    _run(tmp_path, shell="/bin/zsh")
    content = (tmp_path / "home" / ".zshrc").read_text()
    assert "# Added by opensre installer" in content


def test_post_install_message_mentions_source(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "source" in combined


def test_fish_creates_parent_dirs(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/usr/bin/fish")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / ".config" / "fish" / "config.fish").exists()


def test_readds_export_when_marker_present_but_line_removed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    zshrc = home / ".zshrc"
    zshrc.write_text("# Added by opensre installer\n")

    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    content = zshrc.read_text()
    assert _LOCAL_BIN in content


# ---------------------------------------------------------------------------
# Helpers and tests for the post-install onboarding hint (issue #1153)
# ---------------------------------------------------------------------------


def _find_post_install_start_line() -> int:
    """Return the line number where the post-install output block starts in install.sh.

    We look for the first line of the version-print block that immediately
    follows the ``install_binary`` call — i.e. the ``if [ "$INSTALL_CHANNEL"``
    line that opens the "Installed opensre ..." log statement.  Everything from
    that line to EOF is the post-install output block that we want to run in
    tests.
    """
    marker = 'if [ "$INSTALL_CHANNEL" = "main" ]; then'
    lines = INSTALL_SH.read_text().splitlines()
    # Walk backwards from EOF so we pick up the last (main-script-level)
    # occurrence, not any occurrence inside a function body.
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == marker:
            return i + 1  # 1-indexed for tail / awk
    raise RuntimeError(
        f"Could not locate post-install block in {INSTALL_SH}. Did the script structure change?"
    )


def _run_post_install(
    tmp_path: Path,
    shell: str,
    platform: str = "linux",
    install_channel: str = "release",
    installed_version: str = "2026.4.1",
    dir_already_on_path: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the real post-install output block of install.sh with side-effects stubbed.

    Unlike ``_run()``, which only calls ``configure_path()`` in isolation, this
    helper sources *the actual lines* that sit at the bottom of install.sh
    (version print + configure_path + onboarding hint) rather than copying
    them into the test.  That means if the hint is removed from install.sh
    the assertions will correctly fail — there is no tautology.

    The approach:
      1. Load all function definitions from install.sh via awk.
      2. Stub the four side-effect functions so no network/binary calls occur.
      3. Set every shell variable the output block needs.
      4. Use ``tail -n +N`` to feed the real post-install lines from install.sh
         to bash, so the test drives install.sh source directly.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    idir = str(fake_home / _LOCAL_BIN)

    # When dir_already_on_path=True, configure_path() hits the early return
    # and prints nothing.  The onboarding hint must still appear.
    path_value = f"{idir}:/usr/bin:/bin" if dir_already_on_path else "/usr/bin:/bin"

    start_line = _find_post_install_start_line()
    install_sh = _INSTALL_SH_SHELL
    idir_shell = shlex.quote(idir)
    home_shell = shlex.quote(str(fake_home))

    script = textwrap.dedent(f"""\
        # 1. Load every function definition from install.sh
        eval "$(awk '
            /^[a-z_][a-z_]*\\(\\)/ {{ in_fn=1 }}
            in_fn {{ print }}
            in_fn && /^\\}}$/ {{ in_fn=0 }}
        ' {install_sh})"

        # 2. Stub side-effect functions — no binary or network calls
        install_binary()               {{ :; }}
        get_binary_path_from_archive() {{ printf '/tmp/fake-opensre\\n'; }}
        verify_binary_version()        {{ printf '%s\\n' "${{2:-{installed_version}}}"; }}
        run_with_privilege()           {{ "$@"; }}

        # 3. Set every variable the output block reads
        BIN_NAME="opensre"
        INSTALL_DIR={idir_shell}
        INSTALL_CHANNEL="{install_channel}"
        installed_version="{installed_version}"
        platform="{platform}"
        HOME={home_shell}
        SHELL="{shell}"
        PATH="{path_value}"
        export HOME SHELL PATH

        # 4. Execute the real post-install lines sourced directly from install.sh.
        #    tail -n +{start_line} feeds everything from the version-print block
        #    to EOF, so any change to those lines in install.sh is immediately
        #    reflected here — no copy-paste tautology.
        eval "$(tail -n +{start_line} {install_sh})"
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_install_sh_contains_onboarding_hint() -> None:
    """Contract test: the hint string must be present in install.sh source.

    This is a direct grep of the script file — independent of any subprocess
    execution — so it will fail immediately if the hint is removed from
    install.sh even if the subprocess-based tests are somehow still passing.
    """
    source = INSTALL_SH.read_text()
    assert "${BIN_NAME:-opensre} onboard" in source, (
        "install.sh does not contain the onboarding hint "
        "(expected ``${BIN_NAME:-opensre} onboard`` in Next steps output)."
    )


def test_install_ps1_contains_onboarding_hint() -> None:
    """Contract test: the hint string must be present in install.ps1 source."""
    install_ps1 = Path(__file__).parents[2] / "install.ps1"
    source = install_ps1.read_text()
    assert "$exe onboard" in source, (
        "install.ps1 does not contain the onboarding step "
        '(expected a line with ``$exe onboard``, e.g. ``Write-Host "  1. Run  $exe onboard"``).'
    )


def test_onboarding_hint_shown_when_path_not_set(tmp_path: Path) -> None:
    """Hint appears on a first install where configure_path writes the rc file."""
    result = _run_post_install(tmp_path, shell="/bin/zsh", dir_already_on_path=False)
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_shown_when_path_already_set(tmp_path: Path) -> None:
    """Hint appears even when configure_path returns early (install dir already on PATH).

    This is the silent-upgrade scenario that the old configure_path-only
    helper could never cover: configure_path() hits the early return at
    line 490 and outputs nothing, yet the user must still see the hint.
    """
    result = _run_post_install(tmp_path, shell="/bin/zsh", dir_already_on_path=True)
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_shown_for_bash_linux(tmp_path: Path) -> None:
    """Hint appears on bash/linux installs."""
    result = _run_post_install(tmp_path, shell="/bin/bash", platform="linux")
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_shown_for_main_channel(tmp_path: Path) -> None:
    """Hint appears when installing the rolling main build (not a versioned release)."""
    result = _run_post_install(
        tmp_path,
        shell="/bin/zsh",
        install_channel="main",
        installed_version="main",
    )
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_appears_after_version_line(tmp_path: Path) -> None:
    """The onboarding hint must appear AFTER the 'Installed opensre v...' line."""
    result = _run_post_install(tmp_path, shell="/bin/zsh", installed_version="2026.4.1")
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    installed_pos = output.find("Installed opensre")
    onboard_pos = output.find("opensre onboard")
    assert installed_pos != -1, "'Installed opensre' line missing from output"
    assert onboard_pos != -1, "'opensre onboard' hint missing from output"
    assert onboard_pos > installed_pos, (
        "Onboarding hint must come after the install confirmation line"
    )
