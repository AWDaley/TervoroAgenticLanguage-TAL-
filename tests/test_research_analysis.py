from __future__ import annotations

from datetime import UTC, datetime

from tal.coordination import ArchitectureId, ProtocolMode
from tal.core import sha256
from tal.research import BenchmarkId, BenchmarkScore, EpisodeMetrics, EpisodeResult, TerminalStatus
from tal.research_analysis import analyze_schedule, apply_holm
from tal.research_study import ScheduledEpisode, StudySchedule, StudySplit


def result(episode: ScheduledEpisode, success: bool, cost: int) -> EpisodeResult:
    return EpisodeResult(
        episode_id=episode.episode_id,
        freeze_hash="sha256:" + "1" * 64,
        benchmark_id=episode.benchmark_id,
        task_id=episode.task_id,
        architecture=episode.architecture,
        protocol_mode=episode.protocol_mode,
        terminal_status=TerminalStatus.COMPLETED,
        score=BenchmarkScore(
            success=success,
            native_score_millionths=1_000_000 if success else 0,
            scorer_id="fixture",
            scorer_hash=sha256({}),
        ),
        metrics=EpisodeMetrics(total_cost_microcents=cost),
        completed_at=datetime.now(UTC),
    )


def test_confirmatory_analysis_is_paired_and_h5_uses_cost_per_success() -> None:
    schedule = StudySchedule(
        freeze_hash="sha256:" + "1" * 64,
        random_seed=42,
        episodes=(
            ScheduledEpisode(
                sequence=0,
                benchmark_id=BenchmarkId.FINANCE_AGENT,
                task_id="task:one",
                architecture=ArchitectureId.SINGLE,
                protocol_mode=ProtocolMode.TAL,
                split=StudySplit.MAIN,
                pair_id="pair:0",
                episode_id="episode:tal",
            ),
            ScheduledEpisode(
                sequence=1,
                benchmark_id=BenchmarkId.FINANCE_AGENT,
                task_id="task:one",
                architecture=ArchitectureId.SINGLE,
                protocol_mode=ProtocolMode.CONTROL,
                split=StudySplit.MAIN,
                pair_id="pair:0",
                episode_id="episode:control",
            ),
        ),
    )
    summary = analyze_schedule(
        schedule,
        (result(schedule.episodes[0], True, 10), result(schedule.episodes[1], True, 20)),
        bootstrap_samples=100,
    )
    assert summary.pairs == 1
    assert summary.h5_net_benefit_supported
    assert apply_holm((summary,))[0].holm_adjusted_p_value == summary.mcnemar_p_value
