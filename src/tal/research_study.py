"""Deterministic study scheduling and budget admission for TAL v0.4 research."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from pydantic import Field, model_validator

from .coordination import ArchitectureId, ProtocolMode
from .core import Identifier, StrictModel
from .research import AttemptKind, BenchmarkId, EpisodeResult, ExperimentFreeze


class StudySplit(str, Enum):
    PILOT = "pilot"
    MAIN = "main"


class ScheduledEpisode(StrictModel):
    sequence: int = Field(ge=0)
    benchmark_id: BenchmarkId
    task_id: Identifier
    architecture: ArchitectureId
    protocol_mode: ProtocolMode
    split: StudySplit
    pair_id: Identifier
    episode_id: Identifier


class StudySchedule(StrictModel):
    """A pair-adjacent, randomized and fully enumerated execution schedule."""

    freeze_hash: str
    random_seed: int = Field(ge=0, le=2**63 - 1)
    episodes: tuple[ScheduledEpisode, ...] = Field(min_length=1)
    pilot_budget_microcents: int = Field(default=7_500_000_000, ge=0)
    main_study_budget_microcents: int = Field(default=40_000_000_000, ge=0)
    rerun_budget_microcents: int = Field(default=2_500_000_000, ge=0)

    @model_validator(mode="after")
    def pairs_are_adjacent_and_complete(self) -> "StudySchedule":
        if (
            self.pilot_budget_microcents,
            self.main_study_budget_microcents,
            self.rerun_budget_microcents,
        ) != (7_500_000_000, 40_000_000_000, 2_500_000_000):
            raise ValueError("schedule budgets must match the preregistered ceilings")
        sequences = [episode.sequence for episode in self.episodes]
        if sequences != list(range(len(self.episodes))):
            raise ValueError("schedule sequences must be contiguous and ordered")
        pairs: dict[str, list[ScheduledEpisode]] = {}
        for episode in self.episodes:
            pairs.setdefault(episode.pair_id, []).append(episode)
        for pair_id, members in pairs.items():
            if len(members) != 2:
                raise ValueError(f"pair {pair_id} must contain TAL and control episodes")
            if {item.protocol_mode for item in members} != {ProtocolMode.TAL, ProtocolMode.CONTROL}:
                raise ValueError(f"pair {pair_id} must contain one TAL and one control episode")
            if members[1].sequence != members[0].sequence + 1:
                raise ValueError(f"pair {pair_id} must be adjacent")
        return self


@dataclass(frozen=True)
class BudgetAdmission:
    admitted: bool
    reason: str | None = None


def build_paired_schedule(
    freeze: ExperimentFreeze,
    tasks: tuple[tuple[BenchmarkId, Identifier], ...],
    architectures: tuple[ArchitectureId, ...],
    *,
    split: StudySplit,
) -> StudySchedule:
    """Randomize pair order while keeping two modes immediately adjacent."""

    if freeze.freeze_hash is None:
        raise ValueError("experiment freeze must be sealed before scheduling")
    pairs = [
        (benchmark_id, task_id, architecture)
        for benchmark_id, task_id in tasks
        for architecture in architectures
    ]
    random.Random(freeze.random_seed).shuffle(pairs)
    episodes: list[ScheduledEpisode] = []
    for pair_number, (benchmark_id, task_id, architecture) in enumerate(pairs):
        pair_id = f"pair:{pair_number:06d}"
        modes = [ProtocolMode.TAL, ProtocolMode.CONTROL]
        # Randomize order only within a pair. Seed derivation is explicit and stable.
        random.Random(f"{freeze.random_seed}:{pair_number}").shuffle(modes)
        for mode in modes:
            episodes.append(
                ScheduledEpisode(
                    sequence=len(episodes),
                    benchmark_id=benchmark_id,
                    task_id=task_id,
                    architecture=architecture,
                    protocol_mode=mode,
                    split=split,
                    pair_id=pair_id,
                    episode_id=(
                        "episode:"
                        + freeze.freeze_hash.removeprefix("sha256:")[:12]
                        + f":{pair_number:06d}:{mode.value}"
                    ),
                )
            )
    return StudySchedule(
        freeze_hash=freeze.freeze_hash,
        random_seed=freeze.random_seed,
        episodes=tuple(episodes),
        pilot_budget_microcents=freeze.pilot_budget_microcents,
        main_study_budget_microcents=freeze.main_study_budget_microcents,
        rerun_budget_microcents=freeze.rerun_budget_microcents,
    )


def admit_result(
    result: EpisodeResult,
    schedule: StudySchedule,
    results: tuple[EpisodeResult, ...],
    *,
    rerun_budget_microcents: int,
) -> BudgetAdmission:
    """Allow one primary result and at most two infrastructure-only reruns."""

    scheduled = next(
        (item for item in schedule.episodes if item.episode_id == result.episode_id), None
    )
    if scheduled is None:
        return BudgetAdmission(False, "episode is not in the frozen schedule")
    if result.freeze_hash != schedule.freeze_hash:
        return BudgetAdmission(False, "result freeze hash does not match schedule")
    if (result.benchmark_id, result.task_id, result.architecture, result.protocol_mode) != (
        scheduled.benchmark_id,
        scheduled.task_id,
        scheduled.architecture,
        scheduled.protocol_mode,
    ):
        return BudgetAdmission(False, "result identity does not match frozen schedule")
    prior = tuple(item for item in results if item.episode_id == result.episode_id)
    if result.attempt_kind is AttemptKind.PRIMARY:
        if result.attempt_index != 0 or prior:
            return BudgetAdmission(False, "primary result may be recorded once at attempt zero")
        same_split_spend = sum(
            item.metrics.total_cost_microcents
            for item in results
            if item.attempt_kind is AttemptKind.PRIMARY
            and (
                prior_schedule := next(
                    (entry for entry in schedule.episodes if entry.episode_id == item.episode_id),
                    None,
                )
            )
            is not None
            and prior_schedule.split is scheduled.split
        )
        split_budget = (
            schedule.pilot_budget_microcents
            if scheduled.split is StudySplit.PILOT
            else schedule.main_study_budget_microcents
        )
        if same_split_spend + result.metrics.total_cost_microcents > split_budget:
            return BudgetAdmission(False, f"{scheduled.split.value} study budget would be exceeded")
        return BudgetAdmission(True)
    if result.attempt_index not in {1, 2}:
        return BudgetAdmission(False, "infrastructure reruns are limited to attempts one and two")
    if not prior or prior[-1].terminal_status.value != "infrastructure_failure":
        return BudgetAdmission(False, "reruns require a preceding infrastructure failure")
    spent = sum(
        item.metrics.total_cost_microcents
        for item in results
        if item.attempt_kind is AttemptKind.INFRASTRUCTURE_RERUN
    )
    if spent + result.metrics.total_cost_microcents > rerun_budget_microcents:
        return BudgetAdmission(False, "rerun budget would be exceeded")
    return BudgetAdmission(True)
