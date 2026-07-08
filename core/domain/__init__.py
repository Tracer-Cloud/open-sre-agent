"""Pure investigation domain rules and entities.

- ``alerts/``       — alert parsing, source routing, inbox, tool planning
- ``correlation/``  — upstream candidate scoring and confidence math
- ``diagnosis/``    — diagnosis result model, category normalization, alignment
- ``feedback/``     — miss triage taxonomy, store, and benchmark export
- ``types/``        — shared typed contracts (evidence, retrieval, taxonomy, window)

Callers import from subpackages directly; this module is the package map only.
"""
