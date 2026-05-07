"""CLI entry point for the incident resolution agent."""

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    # Load .env before importing app modules — they read environment variables
    # at import time (e.g. Sentry DSN, API keys), so deferring these imports
    # until after load_dotenv() is required for correct configuration.
    load_dotenv(override=False)

    from app.cli import parse_args, write_json
    from app.cli.investigation import run_investigation_cli
    from app.cli.investigation.alert_templates import build_alert_template
    from app.cli.investigation.payload import load_payload
    from app.utils.sentry_sdk import init_sentry

    init_sentry(entrypoint="cli")
    args = parse_args(argv)
    if args.print_template:
        write_json(build_alert_template(args.print_template), args.output)
        return 0

    payload = load_payload(
        input_path=args.input,
        input_json=getattr(args, "input_json", None),
        interactive=getattr(args, "interactive", False),
    )
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
