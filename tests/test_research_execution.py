from __future__ import annotations

from datetime import UTC, datetime

from tal.coordination import ArchitectureId, ProtocolMode
from tal.core import sha256
from tal.research import (
    BenchmarkId,
    BenchmarkScore,
    EpisodeMetrics,
    EpisodeResult,
    TerminalStatus,
    compile_episode_result,
)
from tal.research_execution import (
    ControlledTrace,
    EpisodeTrace,
    PersistedEpisode,
    ResearchRunner,
    ResearchRunStore,
    compile_episode_trace,
    public_release_payload,
)
from tal.research_study import ScheduledEpisode, StudySchedule, StudySplit


def schedule() -> StudySchedule:
    return StudySchedule(
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


def record(episode: ScheduledEpisode) -> PersistedEpisode:
    result = compile_episode_result(
        EpisodeResult(
            episode_id=episode.episode_id,
            freeze_hash="sha256:" + "1" * 64,
            benchmark_id=episode.benchmark_id,
            task_id=episode.task_id,
            architecture=episode.architecture,
            protocol_mode=episode.protocol_mode,
            terminal_status=TerminalStatus.COMPLETED,
            score=BenchmarkScore(
                success=True,
                native_score_millionths=1_000_000,
                scorer_id="fixture",
                scorer_hash=sha256({}),
            ),
            metrics=EpisodeMetrics(),
            completed_at=datetime.now(UTC),
        )
    )
    return PersistedEpisode(
        result=result,
        trace=compile_episode_trace(
            EpisodeTrace(
                episode_id=episode.episode_id,
                task_manifest_hash=sha256({"task": episode.task_id}),
                output_hash=sha256({"output": "redacted"}),
            )
        ),
    )


def test_runner_persists_sealed_results_and_resume_skips_completed_episodes(tmp_path) -> None:
    store = ResearchRunStore(tmp_path / "run", trace_key=b"x" * 32)
    store.initialize(schedule())
    runner = ResearchRunner(store, rerun_budget_microcents=10)
    written = runner.run(
        lambda episode_id: record(
            next(item for item in schedule().episodes if item.episode_id == episode_id)
        ),
        limit=1,
    )
    assert len(written) == 1
    assert runner.pending_episode_ids() == ("episode:control",)
    release = public_release_payload(schedule(), store.load_records())
    assert (
        release["controlled_trace_policy"]
        == "AES-GCM encrypted raw traces excluded from public release"
    )
    assert "redacted" not in str(release)


def test_store_rejects_a_different_trace_key(tmp_path) -> None:
    root = tmp_path / "run"
    store = ResearchRunStore(root, trace_key=b"x" * 32)
    store.initialize(schedule())

    wrong_key_store = ResearchRunStore(root, trace_key=b"y" * 32)
    try:
        wrong_key_store.initialize(schedule())
    except ValueError as error:
        assert "different trace key" in str(error)
    else:
        raise AssertionError("a different trace key must not open the run")


def test_store_encrypts_controlled_trace_at_rest(tmp_path) -> None:
    store = ResearchRunStore(tmp_path / "run", trace_key=b"x" * 32)
    store.initialize(schedule())
    sealed = record(schedule().episodes[0]).model_copy(
        update={
            "controlled_trace": ControlledTrace(model_outputs=("sensitive benchmark answer",)),
        }
    )
    sealed = sealed.model_copy(
        update={
            "trace": compile_episode_trace(
                sealed.trace.model_copy(
                    update={
                        "controlled_payload_hash": sha256(
                            sealed.controlled_trace.model_dump(mode="json")
                        )
                    }
                )
            )
        }
    )
    store.append(sealed, rerun_budget_microcents=10)
    assert store.load_records()[0].controlled_trace.model_outputs == ("sensitive benchmark answer",)
    assert b"sensitive benchmark answer" not in store.path.read_bytes()
