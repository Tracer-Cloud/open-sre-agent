"""Deployment-facing runtime packages.

This top-level package contains code that runs at deployment boundaries:
external entrypoints, remote-hosted runtime operations, health polling, local
persisted EC2 outputs, and provider config validation for dry runs.

Import from ``deployment.entrypoints``, ``deployment.operations``, or
``deployment.remote``.
"""
