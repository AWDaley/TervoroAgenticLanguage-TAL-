from __future__ import annotations

from datetime import UTC, datetime

from tal.coordination import ArchitectureId, ProtocolMode
from tal.core import sha256
from tal.research import (
    AttemptKind,
    BenchmarkId,
    EpisodeMetrics,
    EpisodeResult,
    ExperimentFreeze,
    FrozenArtifact,
    ModelConfiguration,
    TerminalStatus,
    compile_experiment_freeze,
)
from tal.research_study import StudySplit, admit_result, build_paired_schedule


def freeze() -> ExperimentFreeze:
    model = ModelConfiguration(
        provider="fixture", model="fixture", max_output_tokens=10, context_capacity=10
    )
    return compile_experiment_freeze(
        ExperimentFreeze(
            study_id="study:fixture",
            source_revision="fixture",
            container_digest="sha256:fixture",
            random_seed=42,
            actor_model=model,
            judge_model=model,
            artifacts=(
                FrozenArtifact(
                    artifact_id="artifact:fixture", content_hash=sha256({}), locator="fixture"
                ),
            ),
            total_study_budget_microcents=50_000_000_000,
            main_study_budget_microcents=40_000_000_000,
            rerun_budget_microcents=2_500_000_000,
        )
    )


def test_schedule_keeps_tal_control_pairs_adjacent_and_randomized() -> None:
    schedule = build_paired_schedule(
        freeze(),
        ((BenchmarkId.FINANCE_AGENT, "task:one"), (BenchmarkId.PLANCRAFT, "task:two")),
        (ArchitectureId.SINGLE,),
        split=StudySplit.MAIN,
    )
    assert len(schedule.episodes) == 4
    assert {item.protocol_mode for item in schedule.episodes[:2]} == {
        ProtocolMode.TAL,
        ProtocolMode.CONTROL,
    }


def test_admission_rejects_duplicate_primary_result() -> None:
    study_freeze = freeze()
    schedule = build_paired_schedule(
        study_freeze,
        ((BenchmarkId.FINANCE_AGENT, "task:one"),),
        (ArchitectureId.SINGLE,),
        split=StudySplit.MAIN,
    )
    episode = schedule.episodes[0]
    result = EpisodeResult(
        episode_id=episode.episode_id,
        freeze_hash=study_freeze.freeze_hash or "",
        benchmark_id=episode.benchmark_id,
        task_id=episode.task_id,
        architecture=episode.architecture,
        protocol_mode=episode.protocol_mode,
        terminal_status=TerminalStatus.COMPLETED,
        metrics=EpisodeMetrics(),
        completed_at=datetime.now(UTC),
    )
    assert admit_result(result, schedule, (), rerun_budget_microcents=10).admitted
    assert not admit_result(result, schedule, (result,), rerun_budget_microcents=10).admitted
    assert result.attempt_kind is AttemptKind.PRIMARY


def test_admission_enforces_the_global_main_study_ceiling() -> None:
    study_freeze = freeze()
    schedule = build_paired_schedule(
        study_freeze,
        ((BenchmarkId.FINANCE_AGENT, "task:one"),),
        (ArchitectureId.SINGLE,),
        split=StudySplit.MAIN,
    )
    episode = schedule.episodes[0]
    result = EpisodeResult(
        episode_id=episode.episode_id,
        freeze_hash=study_freeze.freeze_hash or "",
        benchmark_id=episode.benchmark_id,
        task_id=episode.task_id,
        architecture=episode.architecture,
        protocol_mode=episode.protocol_mode,
        terminal_status=TerminalStatus.COMPLETED,
        metrics=EpisodeMetrics(total_cost_microcents=40_000_000_001),
        completed_at=datetime.now(UTC),
    )
    admission = admit_result(result, schedule, (), rerun_budget_microcents=10)
    assert not admission.admitted
    assert admission.reason == "main study budget would be exceeded"
