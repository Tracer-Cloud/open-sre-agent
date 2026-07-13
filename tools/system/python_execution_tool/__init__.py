"""Agent-facing Python execution tool."""

from __future__ import annotations

from typing import Any

from core.tool_framework.base import BaseTool
from platform.observability.trace.spans import component_span
from tools.system.python_execution_tool.credentials import execution_env, github_extract_params
from tools.system.python_execution_tool.runner import run_python_execution


class PythonExecutionTool(BaseTool):
    """Run generated Python code with structured inputs and approved credentials."""

    name = "execute_python_code"
    display_name = "Python execution"
    source = "knowledge"
    side_effect_level = "read_only"
    surfaces = ("investigation", "chat")
    injected_params = ["github_token"]
    description = (
        "Execute generated Python code in a restricted subprocess, capture stdout, stderr, "
        "exceptions, and timeout state, and return the result to the agent. Network access is "
        "blocked by default; opt in only for approved API-backed analysis. Subprocess spawning "
        "is always blocked — for OpenSRE version/runtime facts use "
        "`inputs['opensre_runtime']` (injected automatically) or "
        "`importlib.metadata.version('opensre')`, never `opensre --version` or "
        "`subprocess`. When workflow guidance lists skills, read each skill description and "
        "follow the one that matches the user's request."
    )
    use_cases = [
        "Compute metrics or summaries from structured evidence already in context",
        "Run a small API-backed calculation with approved credentials",
        "Parse logs or JSON payloads when a direct tool result needs post-processing",
        "Read OpenSRE version via inputs['opensre_runtime'] or importlib.metadata",
    ]
    anti_examples = [
        "Changing local files or shelling out to other processes",
        "Calling opensre --version / subprocess / os.system (blocked by sandbox)",
        "Long-running jobs, crawlers, or broad external scans",
        "Accessing credentials not explicitly provided by configured integrations or env vars",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
            },
            "inputs": {
                "type": "object",
                "description": (
                    "Optional JSON-serializable values injected into the script as the "
                    "`inputs` global. OpenSRE always merges `opensre_runtime` "
                    "(version, runtime_env, feature_flags) unless you already set that key."
                ),
                "nullable": True,
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum execution time in seconds. Defaults to 30 and caps at 60.",
                "nullable": True,
            },
            "allow_network": {
                "type": "boolean",
                "description": (
                    "Allow outbound network calls for approved API-backed analysis. Defaults to "
                    "false; subprocess execution and filesystem write restrictions still apply."
                ),
                "nullable": True,
            },
            "github_token": {
                "type": "string",
                "description": "Injected GitHub token. Hidden from the model-facing schema.",
            },
        },
        "required": ["code"],
    }
    outputs = {
        "success": "True when execution completed with exit code 0 and did not time out",
        "stdout": "Captured standard output",
        "stderr": "Captured standard error",
        "exit_code": "Process exit code, or -1 for timeout/runner failures",
        "timed_out": "True when execution exceeded the timeout",
        "error": "Human-readable runner error when available",
        "credentials_available": "Credential labels made available to the subprocess",
    }

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """The sandbox itself is local and always available."""
        return True

    def extract_params(self, sources: dict[str, dict]) -> dict[str, Any]:
        """Inject approved credentials from resolved integration sources."""
        return github_extract_params(sources)

    def run(
        self,
        code: str,
        inputs: dict[str, Any] | None = None,
        timeout: int | None = None,
        allow_network: bool | None = None,
        github_token: str | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute ``code`` in the sandbox with runtime metadata injected.

        ``runtime_metadata`` — when the caller has a session with a pre-built
        ``runtime_metadata`` dict (e.g. ``SessionCore.runtime_metadata``),
        passing it here avoids re-running ``build_runtime_metadata()`` per tool
        call. When ``None`` (default) the merge helper builds a fresh dict —
        preserving the pre-#3946 behavior. Callers that thread session down
        (T-2c follow-up) can pass ``session.runtime_metadata`` directly.
        """
        from config.runtime_metadata import merge_runtime_into_inputs

        with component_span("runtime_metadata:sandbox"):
            env, credentials_available = execution_env(github_token=github_token)
            return run_python_execution(
                code=code,
                inputs=merge_runtime_into_inputs(inputs, metadata=runtime_metadata),
                timeout=timeout,
                env=env,
                allow_network=bool(allow_network),
                credentials_available=credentials_available,
            )


execute_python_code = PythonExecutionTool()


__all__ = ["PythonExecutionTool", "execute_python_code"]
