"""Alert-domain models, ingestion, routing, and planning rules.

- ``alert_source.py``  — resolve alert vendor, map to tool sources, relevance scoring
- ``extraction.py``    — deterministic field extraction for the extract_alert stage
- ``normalization.py`` — canonical OpenSRE alert payload shape
- ``inbox.py``         — in-process alert queue and local HTTP listener
- ``tool_planning.py`` — score and rank investigation tools for an alert
"""
