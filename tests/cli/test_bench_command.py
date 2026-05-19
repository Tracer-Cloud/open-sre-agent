from __future__ import annotations

from click.testing import CliRunner

from app.cli.__main__ import cli


def test_bench_list_includes_cloudopsbench_adapter() -> None:
    result = CliRunner().invoke(cli, ["bench", "list"])

    assert result.exit_code == 0
    assert "cloudopsbench" in result.output


def test_bench_validate_accepts_checked_in_cloudopsbench_config() -> None:
    config_path = "tests/benchmarks/configs/claude-vs-paper.yml"

    result = CliRunner().invoke(cli, ["bench", "validate", config_path])

    assert result.exit_code == 0
    assert f"OK: {config_path}" in result.output
