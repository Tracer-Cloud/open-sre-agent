"""Alert-domain models, ingestion, routing, and planning rules.

- ``alert_source.py``  — resolve alert vendor, map to tool sources, relevance scoring
- ``fields.py``        — shared alert field precedence and payload-shape helpers
- ``extraction.py``    — deterministic field extraction for the extract_alert stage
- ``normalization.py`` — canonical OpenSRE alert payload shape
- ``inbox.py``         — in-process alert queue
- ``alert_listener.py`` — local HTTP listener for pushed alerts
- ``tool_planning.py`` — score and rank investigation tools for an alert
"""
