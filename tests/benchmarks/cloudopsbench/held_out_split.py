"""Held-out 20% test split for the Cloud-OpsBench corpus.

Implements integrity Mechanism 8 (generalization gate) from the framework's
Pillar 0: 20% of cases are eval-only, never inspected during BDIL
optimization cycles. The split is **derived deterministically** from the
sorted ``case_id`` list — same corpus + same seed → same split, forever.

Policy (mirrors the pre-registration YAML at
``tests/benchmarks/configs/preregistrations/cloudopsbench_v1.yml``):

  - 80% optimize-against / 20% eval-only
  - ``HELD_OUT_SEED = 42`` — distinct from ``config.seed`` (case-selection seed
    inside each run). Sharing seeds would couple "which 20% is held out" to
    "which N cases this run drew", which would silently invalidate the gate.
  - Selection is uniform random over the SORTED case_id list, not the order
    the loader returns. Sorting first guarantees stability across loader
    iteration-order changes.
  - The split is computed at adapter ``load_cases`` time and tagged on each
    ``BenchmarkCase.metadata["is_held_out"]``. Reports stratify on this.

Inspecting a held-out case's results DURING a BDIL cycle is a contamination
event — the cycle becomes invalid per the pre-registration's
``generalization_gate`` clause.
"""

from __future__ import annotations

import random
from collections.abc import Iterable

# Constants — change these only via committed PR + matching pre-registration update.
HELD_OUT_SEED: int = 42
HELD_OUT_FRAC: float = 0.20


def compute_held_out_set(case_ids: Iterable[str]) -> set[str]:
    """Return the deterministic set of held-out case_ids for the given corpus.

    Sorts the input first, so the result is stable regardless of the order
    in which the loader yielded cases. Uses ``HELD_OUT_SEED`` — passing a
    different seed is not supported; the seed is policy, not configuration.

    Args:
        case_ids: every case_id in the full corpus (not a filtered subset).
                  Passing a filtered subset would produce a split of the
                  subset, which is NOT what the generalization gate wants —
                  it wants the same 20% relative to the full corpus,
                  consistently across runs that filter differently.

    Returns:
        The held-out subset as a set. Membership-check is O(1) at the
        adapter's ``load_cases`` tagging site.
    """
    sorted_ids = sorted(set(case_ids))
    if not sorted_ids:
        return set()
    held_out_count = max(1, int(round(len(sorted_ids) * HELD_OUT_FRAC)))
    rng = random.Random(HELD_OUT_SEED)
    return set(rng.sample(sorted_ids, held_out_count))


def is_held_out(case_id: str, full_corpus_case_ids: Iterable[str]) -> bool:
    """Convenience: True if ``case_id`` belongs to the held-out set.

    The full corpus must be passed every call because the split is defined
    relative to the entire corpus, not relative to any subset. If you're
    calling this in a loop, prefer ``compute_held_out_set`` once and use
    set membership directly.
    """
    return case_id in compute_held_out_set(full_corpus_case_ids)
