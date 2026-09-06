"""Tests for the Python sandbox runner."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from config.constants import (
    OPENSRE_TMP_DIR,
    SANDBOX_BASE_ENV_KEYS,
    SANDBOXED_TEMP_ENV_KEYS,
    ensure_opensre_tmp_dir,
)
from infrastructure.safety.sandbox.runner import (
    MAX_TIMEOUT,
    SandboxResult,
    _sandbox_env,
    run_python_sandbox,
)


class TestSandboxRunnerBasicExecution:
    def test_runs_simple_code(self) -> None:
        result = run_python_sandbox("print('hello')")
        assert result.success
        assert result.stdout.strip() == "hello"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert not result.timed_out

    def test_captures_stderr(self) -> None:
        result = run_python_sandbox("import sys; sys.stderr.write('err\\n')")
        assert result.success
        assert "err" in result.stderr

    def test_captures_exit_code_on_failure(self) -> None:
        result = run_python_sandbox("raise ValueError('boom')")
        assert not result.success
        assert result.exit_code != 0
        assert "ValueError" in result.stderr

    def test_empty_code_succeeds(self) -> None:
        result = run_python_sandbox("")
        assert result.success
        assert result.stdout == ""

    def test_result_stores_original_code(self) -> None:
        code = "x = 1 + 1\nprint(x)"
        result = run_python_sandbox(code)
        assert result.code == code

    def test_result_stores_inputs(self) -> None:
        inputs = {"threshold": 42}
        result = run_python_sandbox("print(inputs['threshold'])", inputs=inputs)
        assert result.success
        assert result.inputs == inputs
        assert "42" in result.stdout

    def test_no_inputs_stores_empty_dict(self) -> None:
        result = run_python_sandbox("pass")
        assert result.inputs == {}


class TestSandboxRunnerInputInjection:
    def test_inputs_injected_as_variable(self) -> None:
        code = "print(inputs['key'])"
        result = run_python_sandbox(code, inputs={"key": "value123"})
        assert result.success
        assert "value123" in result.stdout

    def test_inputs_supports_nested_structures(self) -> None:
        code = "print(inputs['data'][0])"
        result = run_python_sandbox(code, inputs={"data": [99, 100]})
        assert result.success
        assert "99" in result.stdout

    def test_none_inputs_not_injected(self) -> None:
        result = run_python_sandbox("x = 1", inputs=None)
        assert result.success
        assert result.inputs == {}


class TestSandboxNetworkRestrictions:
    def test_socket_creation_blocked(self) -> None:
        code = "import socket; socket.socket()"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout

    def test_create_connection_blocked(self) -> None:
        code = "import socket; socket.create_connection(('localhost', 80))"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout

    def test_getaddrinfo_blocked(self) -> None:
        code = "import socket; socket.getaddrinfo('localhost', 80)"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout


class TestSandboxFilesystemRestrictions:
    def test_read_allowed(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            path = f.name
        try:
            result = run_python_sandbox(f"open({path!r}).read()")
            assert result.success
        finally:
            os.unlink(path)

    def test_write_outside_tmp_blocked(self) -> None:
        code = "open('/etc/sandbox_test_file', 'w').write('x')"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout

    def test_write_inside_opensre_tmp_allowed(self) -> None:
        ensure_opensre_tmp_dir()
        target = os.path.join(os.fspath(OPENSRE_TMP_DIR), "sandbox_write_test.txt")
        code = f"open({target!r}, 'w').write('ok')"
        result = run_python_sandbox(code)
        assert result.success
        if os.path.exists(target):
            os.unlink(target)

    def test_append_outside_tmp_blocked(self) -> None:
        code = "open('/etc/sandbox_append_test', 'a').write('x')"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout


class TestSandboxTimeout:
    def test_timeout_enforced(self) -> None:
        result = run_python_sandbox("import time; time.sleep(10)", timeout=1)
        assert not result.success
        assert result.timed_out
        assert result.exit_code == -1
        assert result.error is not None
        assert "timed out" in result.error.lower()

    def test_timeout_capped_at_max(self) -> None:
        result = run_python_sandbox("pass", timeout=MAX_TIMEOUT + 9999)
        assert result.success

    def test_fast_code_does_not_time_out(self) -> None:
        result = run_python_sandbox("print('fast')", timeout=30)
        assert result.success
        assert not result.timed_out


# The Windows keys the child receives verbatim: everything in the shared
# allowlist except the temp keys, which are sandboxed rather than forwarded.
_WINDOWS_ENV_KEYS = tuple(
    key for key in SANDBOX_BASE_ENV_KEYS if key not in SANDBOXED_TEMP_ENV_KEYS
)


class TestSandboxEnvironment:
    # Set the values explicitly rather than reading os.environ: the Windows keys
    # are absent on POSIX and vice versa, so the allowlist is asserted the same
    # way on every platform.
    def test_non_temp_keys_are_forwarded_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in _WINDOWS_ENV_KEYS:
            monkeypatch.setenv(key, f"value-for-{key}")

        env = _sandbox_env(None)

        for key in _WINDOWS_ENV_KEYS:
            assert env[key] == f"value-for-{key}"

    def test_temp_keys_are_sandboxed_not_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Forwarding the host TEMP/TMP/USERPROFILE (and TMPDIR on POSIX) lets
        # ordinary tempfile calls write outside OPENSRE_TMP_DIR: the injected
        # guard intercepts only builtins.open, while tempfile uses lower-level
        # file APIs. The values are rewritten to the sandbox root instead.
        for key in SANDBOXED_TEMP_ENV_KEYS:
            monkeypatch.setenv(key, r"C:\host-cwd\tmp-should-not-cross")

        env = _sandbox_env(None)

        for key in SANDBOXED_TEMP_ENV_KEYS:
            assert os.path.realpath(env[key]) == os.path.realpath(str(OPENSRE_TMP_DIR))

    def test_child_tempfile_writes_stay_inside_sandbox_tmp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression for the P1 review finding: with the host value forwarded, an
        # ordinary tempfile.mkstemp() in generated code landed in the host temp
        # directory, which builtins.open-guarding cannot see.
        monkeypatch.setenv("TEMP", r"C:\host-cwd\tmp-should-not-cross")

        code = (
            "import os, tempfile;"
            "fd, path = tempfile.mkstemp();"
            "os.close(fd); os.remove(path);"
            "print(os.path.realpath(path))"
        )
        result = run_python_sandbox(code)

        assert result.success, result.stderr
        assert os.path.realpath(result.stdout.strip()).startswith(
            os.path.realpath(str(OPENSRE_TMP_DIR))
        )

    @pytest.mark.skipif(os.name != "nt", reason="%SystemDrive% expansion is Windows-only")
    def test_child_can_expand_systemdrive(self) -> None:
        # The invariant SYSTEMDRIVE actually buys. An unexpanded "%SystemDrive%"
        # is a path with no drive letter and no leading separator — a *relative*
        # path — which is what lets a shell-folder write land under the child's
        # cwd instead of on the system drive. Asserting the expansion rather than
        # the escape keeps this failing without the key on every Windows
        # interpreter; the escape itself only manifests on some (see
        # test_sandbox_run_leaves_no_directory_behind).
        result = run_python_sandbox(r"import os; print(os.path.expandvars(r'%SystemDrive%'))")

        assert result.success, result.stderr
        assert result.stdout.strip() != "%SystemDrive%"

    @pytest.mark.skipif(os.name != "nt", reason="shell folder lookup is Windows-only")
    def test_child_shell_folder_lookup_does_not_fail_silently(self) -> None:
        # Without SYSTEMDRIVE, SHGetFolderPathW cannot resolve CSIDL_COMMON_APPDATA
        # and returns 0x80070003 with an empty buffer rather than raising. Generated
        # code then acts on "" as if it were a directory, which is the quieter half
        # of the bug: a wrong path, not a crash.
        code = (
            "import ctypes;"
            "b = ctypes.create_unicode_buffer(260);"
            "ctypes.windll.shell32.SHGetFolderPathW(None, 0x0023, None, 0, b);"
            "print(b.value)"
        )
        result = run_python_sandbox(code)

        assert result.success, result.stderr
        assert result.stdout.strip() != ""

    def test_unlisted_variables_are_still_not_forwarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SANDBOX_BASE_ENV_KEYS is a security control. Widening it must not
        # turn it into
        # a passthrough, so the keys held back on purpose are named here.
        for key in ("AWS_SECRET_ACCESS_KEY", "COMSPEC", "APPDATA", "LOCALAPPDATA"):
            monkeypatch.setenv(key, "should-not-cross")

        env = _sandbox_env(None)

        assert set(env) <= set(SANDBOX_BASE_ENV_KEYS)
        for key in ("AWS_SECRET_ACCESS_KEY", "COMSPEC", "APPDATA", "LOCALAPPDATA"):
            assert key not in env

    def test_sandbox_can_open_a_socket_when_network_is_allowed(self) -> None:
        # Regression for the Windows failure in #4937: without SYSTEMROOT the
        # child raised OSError [WinError 10106] before reaching this assertion.
        code = "import socket; s = socket.socket(); print(s.fileno() >= 0); s.close()"
        result = run_python_sandbox(code, allow_network=True)
        assert result.success, result.stderr
        assert "True" in result.stdout

    def test_sandbox_run_leaves_no_directory_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The %SystemDrive% escape appeared in the *cwd* of the child, so this
        # runs from an empty directory and asserts it stays empty. monkeypatch
        # restores the cwd even if the assertion below raises.
        #
        # Coverage is interpreter-dependent, deliberately: only MSIX/Store CPython
        # writes its packaging cache at process start, so only there does this fail
        # when SYSTEMDRIVE is dropped. On python.org CPython it passes either way
        # and guards nothing — test_child_can_expand_systemdrive is what holds the
        # line on those. Kept because it is the only test that observes the actual
        # out-of-sandbox write rather than the condition that enables it.
        monkeypatch.chdir(tmp_path)
        result = run_python_sandbox("import socket; socket.socket().close()", allow_network=True)

        assert result.success, result.stderr
        assert list(tmp_path.iterdir()) == []


class TestSandboxResultModel:
    def test_success_property_true_on_zero_exit(self) -> None:
        r = SandboxResult(
            code="",
            inputs={},
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=False,
        )
        assert r.success is True

    def test_success_property_false_on_nonzero_exit(self) -> None:
        r = SandboxResult(
            code="",
            inputs={},
            stdout="",
            stderr="",
            exit_code=1,
            timed_out=False,
        )
        assert r.success is False

    def test_success_property_false_when_timed_out(self) -> None:
        r = SandboxResult(
            code="",
            inputs={},
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=True,
        )
        assert r.success is False
