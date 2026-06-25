"""Deployment runtime operations.

The previous CLI-specific deployment methods have been removed. This package now
contains reusable operations around an already-defined hosted service: HTTP
health polling, local persisted EC2 outputs, and provider config validation for
dry runs.

Import from ``app.deployment.operations`` or its submodules (for example
``app.deployment.operations.health``).
"""
