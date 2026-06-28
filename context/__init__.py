"""Investigation evidence/state package for OpenSRE.

Top-level ``context`` does not re-export shell prompt builders, session helpers,
or agent runtime request objects. Import investigation state contracts from
``context.state`` and move surface/runtime concepts to their owning packages.
"""

__all__: list[str] = []
