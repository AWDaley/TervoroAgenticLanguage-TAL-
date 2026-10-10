"""Prespecified paired confirmatory analysis for sealed TAL experiments."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable

from pydantic import Field

from .core import StrictModel
from .research import AttemptKind, EpisodeResult, TerminalStatus
from .research_study import StudySchedule


class ComparisonSummary(StrictModel):
    comparison_id: str
    pairs: int = Field(ge=0)
    tal_successes: int = Field(ge=0)
    control_successes: int = Field(ge=0)
    tal_minus_control_success_rate: float
    mcnemar_p_value: float = Field(ge=0, le=1)
    holm_adjusted_p_value: float = Field(ge=0, le=1)
    bootstrap_mean_cost_difference_microcents: float
    bootstrap_cost_difference_ci_low: float
    bootstrap_cost_difference_ci_high: float
    h5_net_benefit_supported: bool


def analyze_schedule(
    schedule: StudySchedule,
    results: Iterable[EpisodeResult],
    *,
    comparison_id: str = "pooled-primary",
    bootstrap_samples: int = 2_000,
) -> ComparisonSummary:
    """Compare only completed primary TAL/control episode pairs from a frozen schedule."""

    result_by_episode = {
        result.episode_id: result
        for result in results
        if result.attempt_kind is AttemptKind.PRIMARY
        and result.terminal_status is TerminalStatus.COMPLETED
        and result.score is not None
    }
    grouped: dict[str, list[EpisodeResult]] = {}
    for scheduled in schedule.episodes:
        result = result_by_episode.get(scheduled.episode_id)
        if result is not None:
            grouped.setdefault(scheduled.pair_id, []).append(result)
    pairs = [
        members
        for members in grouped.values()
        if len(members) == 2
        and {item.protocol_mode.value for item in members} == {"tal", "control"}
    ]
    tal = [next(item for item in pair if item.protocol_mode.value == "tal") for pair in pairs]
    control = [
        next(item for item in pair if item.protocol_mode.value == "control") for pair in pairs
    ]
    tal_success = [bool(item.score and item.score.success) for item in tal]
    control_success = [bool(item.score and item.score.success) for item in control]
    discordant_tal = sum(t and not c for t, c in zip(tal_success, control_success, strict=True))
    discordant_control = sum(c and not t for t, c in zip(tal_success, control_success, strict=True))
    cost_differences = [
        t.metrics.total_cost_microcents - c.metrics.total_cost_microcents
        for t, c in zip(tal, control, strict=True)
    ]
    low, high = _bootstrap_mean_ci(
        cost_differences, seed=schedule.random_seed, samples=bootstrap_samples
    )
    tal_rate = sum(tal_success) / len(pairs) if pairs else 0.0
    control_rate = sum(control_success) / len(pairs) if pairs else 0.0
    tal_cost_per_success = _cost_per_success(tal)
    control_cost_per_success = _cost_per_success(control)
    return ComparisonSummary(
        comparison_id=comparison_id,
        pairs=len(pairs),
        tal_successes=sum(tal_success),
        control_successes=sum(control_success),
        tal_minus_control_success_rate=tal_rate - control_rate,
        mcnemar_p_value=_mcnemar_exact(discordant_tal, discordant_control),
        holm_adjusted_p_value=_mcnemar_exact(discordant_tal, discordant_control),
        bootstrap_mean_cost_difference_microcents=(
            sum(cost_differences) / len(cost_differences) if cost_differences else 0.0
        ),
        bootstrap_cost_difference_ci_low=low,
        bootstrap_cost_difference_ci_high=high,
        h5_net_benefit_supported=(
            tal_rate - control_rate >= -0.05 and tal_cost_per_success <= control_cost_per_success
        ),
    )


def apply_holm(comparisons: tuple[ComparisonSummary, ...]) -> tuple[ComparisonSummary, ...]:
    """Apply Holm correction across the prespecified (up to ten) comparisons."""

    if len(comparisons) > 10:
        raise ValueError("the confirmatory plan permits at most ten Holm comparisons")
    adjusted = [0.0] * len(comparisons)
    running = 0.0
    for rank, index in enumerate(
        sorted(range(len(comparisons)), key=lambda item: comparisons[item].mcnemar_p_value)
    ):
        candidate = min(1.0, comparisons[index].mcnemar_p_value * (len(comparisons) - rank))
        running = max(running, candidate)
        adjusted[index] = running
    return tuple(
        comparison.model_copy(update={"holm_adjusted_p_value": adjusted[index]})
        for index, comparison in enumerate(comparisons)
    )


def analyze_confirmatory(
    schedule: StudySchedule, results: Iterable[EpisodeResult]
) -> tuple[ComparisonSummary, ...]:
    """Run the preregistered pooled and benchmark-by-architecture comparisons."""

    result_tuple = tuple(results)
    keys = sorted(
        {(item.benchmark_id.value, item.architecture.value) for item in schedule.episodes}
    )
    comparisons = [analyze_schedule(schedule, result_tuple)]
    for benchmark_id, architecture in keys:
        episode_ids = {
            item.episode_id
            for item in schedule.episodes
            if item.benchmark_id.value == benchmark_id and item.architecture.value == architecture
        }
        comparisons.append(
            analyze_schedule(
                schedule,
                (result for result in result_tuple if result.episode_id in episode_ids),
                comparison_id=f"{benchmark_id}:{architecture}",
            )
        )
    # The pooled primary outcome is descriptive; Holm applies to the at-most-ten
    # benchmark-by-architecture confirmatory comparisons.
    adjusted = apply_holm(tuple(comparisons[1:]))
    return (comparisons[0], *adjusted)


def _mcnemar_exact(tal_only: int, control_only: int) -> float:
    discordant = tal_only + control_only
    if discordant == 0:
        return 1.0
    lower = min(tal_only, control_only)
    tail: float = float(sum(math.comb(discordant, value) for value in range(lower + 1))) / float(
        2**discordant
    )
    return 1.0 if 2 * tail > 1.0 else 2 * tail


def _bootstrap_mean_ci(values: list[int], *, seed: int, samples: int) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if samples < 100:
        raise ValueError("bootstrap samples must be at least 100")
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(samples))
    return means[int((samples - 1) * 0.025)], means[int((samples - 1) * 0.975)]


def _cost_per_success(results: list[EpisodeResult]) -> float:
    successes: int = sum(bool(result.score and result.score.success) for result in results)
    if successes == 0:
        return float("inf")
    total_cost: int = 0
    for result in results:
        total_cost += int(result.metrics.total_cost_microcents)
    return float(total_cost) / float(successes)
