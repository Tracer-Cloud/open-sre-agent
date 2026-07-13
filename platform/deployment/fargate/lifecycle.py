"""Fargate lifecycle CLI — plan by default; apply gated and not yet implemented."""

from __future__ import annotations

import argparse
import os
import sys

from platform.deployment.fargate import config as cfg
from platform.deployment.fargate.stack import describe_plan, get_stack, resolve_env_name


def _confirm_enabled() -> bool:
    return os.getenv(cfg.FARGATE_CONFIRM_ENV, "").strip().lower() in {"1", "true", "yes"}


def plan(*, env: str | None = None) -> int:
    stack = get_stack(env=env)
    print("=" * 60)
    print(f"OpenSRE Fargate plan (ENV={stack.env}) — dry-run, no AWS calls")
    print("=" * 60)
    for line in describe_plan(stack):
        print(f"  - {line}")
    print()
    print("Live apply is not implemented yet.")
    print(f"When ready: {cfg.FARGATE_CONFIRM_ENV}=1 make deploy-fargate-apply ENV={stack.env}")
    return 0


def deploy(*, env: str | None = None) -> int:
    """Refuse accidental applies; AWS provisioning lands in a follow-up."""
    stack = get_stack(env=env)
    if not _confirm_enabled():
        print(
            f"Refusing deploy: set {cfg.FARGATE_CONFIRM_ENV}=1 to acknowledge live AWS changes.",
            file=sys.stderr,
        )
        plan(env=stack.env)
        return 2
    print(
        "Fargate AWS provision is not implemented yet "
        f"(ENV={stack.env}, region={stack.region}). "
        "Run `plan` for the intended resource list.",
        file=sys.stderr,
    )
    return 2


def destroy(*, env: str | None = None) -> int:
    stack = get_stack(env=env)
    if not _confirm_enabled():
        print(
            f"Refusing destroy: set {cfg.FARGATE_CONFIRM_ENV}=1 to acknowledge live AWS changes.",
            file=sys.stderr,
        )
        return 2
    print(
        f"Fargate destroy is not implemented yet (ENV={stack.env}).",
        file=sys.stderr,
    )
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenSRE Fargate deployment lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_env(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--env",
            default=None,
            help="staging|production (default: ENV env var or staging)",
        )

    plan_p = sub.add_parser("plan", help="Print intended resources (no AWS calls)")
    _add_env(plan_p)
    deploy_p = sub.add_parser("deploy", help="Provision stack (gated; not implemented yet)")
    _add_env(deploy_p)
    destroy_p = sub.add_parser("destroy", help="Tear down stack (gated; not implemented yet)")
    _add_env(destroy_p)

    args = parser.parse_args()
    env = resolve_env_name(args.env)
    if args.command == "plan":
        raise SystemExit(plan(env=env))
    if args.command == "deploy":
        raise SystemExit(deploy(env=env))
    raise SystemExit(destroy(env=env))


if __name__ == "__main__":
    main()
