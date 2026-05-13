"""Local OpenClaw infrastructure helpers — boot/teardown + fault injection.

Mirrors the layout of :mod:`tests.e2e.upstream_lambda.infrastructure_sdk`.
Splits "spin up a local OpenClaw instance" (``local.py``) from "make it
fail in a specific way" (``fault_injection.py``) so each fault scenario
test composes its boot helper with the right fault injector.
"""
