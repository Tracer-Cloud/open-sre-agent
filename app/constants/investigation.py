"""Constants shared between pipeline routing and investigation stages.

Lives under ``app.constants`` (not ``app.pipeline`` / ``app.agent``) to avoid
partial-initialization cycles between orchestration and agent packages.
"""

from __future__ import annotations

MAX_INVESTIGATION_LOOPS = 20

# Maximum number of times ``adapt_window`` may replace ``state.incident_window``
# during a single investigation. Each replacement records the previous window
# in ``state.incident_window_history``; once the history reaches this length
# the rule layer no-ops. With ``MAX_INVESTIGATION_LOOPS = 20`` and
# ``MAX_EXPANSIONS = 4`` the worst case is four expansions inside the loop
# budget, which is enough to widen 120m → 240m → 480m before deferring to the
# diagnose narrative.
MAX_EXPANSIONS = 4
