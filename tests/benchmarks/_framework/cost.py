"""Per-LLM cost accounting + hard-cap budget enforcement.

Separate input/output token pricing per model (Anthropic, OpenAI, DeepSeek,
Qwen) — important because most providers charge output tokens 3-5x what they
charge input tokens, so input/output-aggregate pricing under-counts.

The framework wires this in two places:
  1. Runner — after each model call, ``CostTracker.add(model, tokens_in, tokens_out)``
  2. Pre-flight — ``estimate_run_cost`` gives an upper-bound estimate the
     IntegrityGuard can check against ``cost_budget_usd``

If at runtime the cumulative cost exceeds the configured budget, the next
``add`` call raises ``CostBudgetExceeded`` — runner catches and halts the run,
publishes a partial-completion report (not silently overrunning the budget).

Pricing table is a frozen dict in this module. Unknown models raise
``UnknownModel`` rather than silently defaulting to a wrong number — opensre
should know what every model in the benchmark grid costs before running.

Prices below are Feb 2026 published rates; check provider pages before
running any large benchmark since rates change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

# --------------------------------------------------------------------------- #
# Pricing table                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TokenPricing:
    """Per-million-token cost in USD, separate for input vs output."""

    input_usd_per_mtok: float
    output_usd_per_mtok: float

    def cost_for(self, tokens_in: int, tokens_out: int) -> float:
        """USD cost of (tokens_in input + tokens_out output) tokens."""
        return (
            tokens_in / 1_000_000.0 * self.input_usd_per_mtok
            + tokens_out / 1_000_000.0 * self.output_usd_per_mtok
        )


# Published rates as of Feb 2026 per provider documentation. The benchmark's
# model_versions pin is what should appear here — exact snapshots, not family
# names — so the rate matches what the API actually charges.
#
# Anthropic: claude pricing pages
# OpenAI: platform.openai.com/docs/pricing
# DeepSeek: api-docs.deepseek.com/quick_start/pricing
# Qwen (via Together AI): together.ai/pricing
#
# IMPORTANT: verify current rates before any large run. The
# benchmark report should record the rates used.

PRICING_TABLE: dict[str, TokenPricing] = {
    # Anthropic Claude family
    "claude-sonnet-4-5-20250929": TokenPricing(3.0, 15.0),
    "claude-opus-4-7": TokenPricing(15.0, 75.0),
    "claude-3-5-haiku-20241022": TokenPricing(0.8, 4.0),
    # Claude Haiku 4.5 - used as the toolcall model for claude-4-sonnet and
    # claude-4-opus specs in llm_dispatch.py. Anthropic published pricing
    # at $1/MTok input, $5/MTok output. Verify before any large run.
    "claude-haiku-4-5-20251001": TokenPricing(1.0, 5.0),
    # OpenAI GPT family
    "gpt-4o-2024-11-20": TokenPricing(2.5, 10.0),
    "gpt-5-2025-08-07": TokenPricing(5.0, 20.0),  # approx — verify before run
    "gpt-4o-mini-2024-07-18": TokenPricing(0.15, 0.60),
    # DeepSeek
    "deepseek-chat-v3.2": TokenPricing(0.27, 1.10),
    # Qwen (via Together AI; approx, varies by host)
    "Qwen/Qwen3-235B": TokenPricing(0.90, 0.90),
    "Qwen/Qwen3-14B": TokenPricing(0.20, 0.20),
    "Qwen/Qwen3-8B": TokenPricing(0.20, 0.20),
}


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #


class UnknownModel(KeyError):
    """Raised when asked to price a model not in PRICING_TABLE.

    Honest-results discipline: don't silently default to a wrong number.
    Force the user to register pricing for the model they're running.
    """

    def __init__(self, model: str) -> None:
        super().__init__(
            f"No pricing for model {model!r}. Known models: "
            f"{sorted(PRICING_TABLE.keys())}. "
            f"Add a TokenPricing entry to PRICING_TABLE or call register_pricing()."
        )
        self.model = model


class CostBudgetExceeded(RuntimeError):
    """Raised when adding a call would exceed the configured budget.

    The CostTracker raises this BEFORE recording the call that would exceed
    — so the run can halt cleanly without partial-state confusion.
    """

    def __init__(self, current_usd: float, budget_usd: float, would_add_usd: float) -> None:
        super().__init__(
            f"Cost budget ${budget_usd:.2f} would be exceeded: "
            f"current ${current_usd:.2f} + ${would_add_usd:.2f} = "
            f"${current_usd + would_add_usd:.2f}. Halting run."
        )
        self.current_usd = current_usd
        self.budget_usd = budget_usd
        self.would_add_usd = would_add_usd


# --------------------------------------------------------------------------- #
# Lookup + registration                                                       #
# --------------------------------------------------------------------------- #


def lookup_pricing(model: str) -> TokenPricing:
    """Return pricing for ``model`` or raise UnknownModel."""
    pricing = PRICING_TABLE.get(model)
    if pricing is None:
        raise UnknownModel(model)
    return pricing


def register_pricing(model: str, pricing: TokenPricing) -> None:
    """Add or override pricing for a model.

    Use sparingly — committed PRICING_TABLE entries are the source of truth
    for benchmark reproducibility. Runtime registration is for one-off
    exploration; production runs should add the entry to the table and
    commit it.
    """
    PRICING_TABLE[model] = pricing


def compute_run_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Pure function: USD cost of (tokens_in + tokens_out) for the model."""
    return lookup_pricing(model).cost_for(tokens_in, tokens_out)


# --------------------------------------------------------------------------- #
# CostTracker — accumulates costs and enforces budget                          #
# --------------------------------------------------------------------------- #


@dataclass
class ModelUsage:
    """Per-model token + cost subtotals."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    call_count: int = 0


class CostTracker:
    """Aggregates costs across many model calls; enforces a hard budget cap.

    Thread-safe — callable from the framework runner's parallel worker pool.
    """

    def __init__(self, budget_usd: float) -> None:
        if budget_usd <= 0:
            raise ValueError(f"budget_usd must be positive, got {budget_usd}")
        self._budget_usd = budget_usd
        self._cost_usd = 0.0
        self._tokens_in = 0
        self._tokens_out = 0
        self._call_count = 0
        self._by_model: dict[str, ModelUsage] = {}
        self._lock = Lock()

    # ----------------------------------------------------------------------- #
    # Public API                                                              #
    # ----------------------------------------------------------------------- #

    def add(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Record one model call.

        Raises ``CostBudgetExceeded`` BEFORE recording if the new cost
        would exceed budget. Returns the cost of this call in USD.
        """
        if tokens_in < 0 or tokens_out < 0:
            raise ValueError(f"tokens must be non-negative; got in={tokens_in} out={tokens_out}")
        call_cost = compute_run_cost(model, tokens_in, tokens_out)
        with self._lock:
            if self._cost_usd + call_cost > self._budget_usd:
                raise CostBudgetExceeded(
                    current_usd=self._cost_usd,
                    budget_usd=self._budget_usd,
                    would_add_usd=call_cost,
                )
            usage = self._by_model.setdefault(model, ModelUsage())
            usage.tokens_in += tokens_in
            usage.tokens_out += tokens_out
            usage.cost_usd += call_cost
            usage.call_count += 1
            self._cost_usd += call_cost
            self._tokens_in += tokens_in
            self._tokens_out += tokens_out
            self._call_count += 1
        return call_cost

    def remaining_usd(self) -> float:
        """Headroom before budget is exhausted."""
        with self._lock:
            return self._budget_usd - self._cost_usd

    def total_cost_usd(self) -> float:
        with self._lock:
            return self._cost_usd

    def by_model(self) -> dict[str, ModelUsage]:
        """Snapshot of per-model usage. Returned dict is a copy."""
        with self._lock:
            return {model: ModelUsage(**vars(u)) for model, u in self._by_model.items()}

    def summary(self) -> dict[str, float | int | dict[str, dict[str, float | int]]]:
        """Machine-readable summary for reporting. Snapshot."""
        with self._lock:
            return {
                "budget_usd": self._budget_usd,
                "total_cost_usd": round(self._cost_usd, 4),
                "remaining_usd": round(self._budget_usd - self._cost_usd, 4),
                "total_tokens_in": self._tokens_in,
                "total_tokens_out": self._tokens_out,
                "total_calls": self._call_count,
                "by_model": {
                    model: {
                        "tokens_in": u.tokens_in,
                        "tokens_out": u.tokens_out,
                        "cost_usd": round(u.cost_usd, 4),
                        "call_count": u.call_count,
                    }
                    for model, u in self._by_model.items()
                },
            }


# --------------------------------------------------------------------------- #
# Pre-flight estimator                                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunSizeEstimate:
    """User-supplied estimate of one cell's token cost; multiplied by grid size."""

    estimated_tokens_in_per_run: int
    estimated_tokens_out_per_run: int
    cell_count: int
    runs_per_cell: int = 1

    @property
    def total_runs(self) -> int:
        return self.cell_count * self.runs_per_cell


@dataclass(frozen=True)
class RunCostEstimate:
    """What ``estimate_run_cost`` returns: enough detail to decide go/no-go."""

    upper_bound_usd: float
    per_model_upper_bound_usd: dict[str, float] = field(default_factory=dict)


def estimate_run_cost(
    models: list[str],
    size: RunSizeEstimate,
) -> RunCostEstimate:
    """Upper-bound cost estimate for a benchmark run.

    Assumes the worst case where EVERY model would handle EVERY run with
    the estimated tokens. Real runs split between models, so this is a
    safe upper bound for budget pre-flight.
    """
    per_model: dict[str, float] = {}
    for model in models:
        per_run_cost = compute_run_cost(
            model,
            size.estimated_tokens_in_per_run,
            size.estimated_tokens_out_per_run,
        )
        per_model[model] = per_run_cost * size.total_runs
    return RunCostEstimate(
        upper_bound_usd=sum(per_model.values()),
        per_model_upper_bound_usd=per_model,
    )
