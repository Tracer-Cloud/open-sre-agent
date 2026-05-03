"""CLI entry point for the incident resolution agent."""

from dotenv import load_dotenv

load_dotenv(override=False)

from app.cli import parse_args, write_json  # noqa: E402
from app.cli.investigation import run_investigation_cli  # noqa: E402
from app.cli.investigation.alert_templates import build_alert_template  # noqa: E402
from app.cli.investigation.payload import load_payload  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)
    if args.print_template:
        write_json(build_alert_template(args.print_template), args.output)
        return 0

    payload = load_payload(
        input_path=args.input,
        input_json=getattr(args, "input_json", None),
        interactive=getattr(args, "interactive", False),
    )

    if getattr(args, "benchmark", False):
        from app.entrypoints.benchmark import (
            BenchmarkInputs,
            format_benchmark_block,
            run_benchmark_case,
        )

        result = run_benchmark_case(
            BenchmarkInputs(
                case_name=getattr(args, "case_name", "") or "case",
                case_path=getattr(args, "case_path", "") or "/",
                fault_category=getattr(args, "fault_category", "") or "",
                model=getattr(args, "model", "") or "",
                prompt_strategy=getattr(args, "prompt_strategy", "base") or "base",
                raw_alert=payload,
            )
        )
        print(format_benchmark_block(result))
        return 0

    result = run_investigation_cli(
        raw_alert=payload,
        alert_name=getattr(args, "alert_name", None),
        pipeline_name=getattr(args, "pipeline_name", None),
        severity=getattr(args, "severity", None),
        opensre_evaluate=bool(getattr(args, "evaluate", False)),
    )
    write_json(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
