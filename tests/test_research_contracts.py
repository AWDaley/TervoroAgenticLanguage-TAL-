from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tal.coordination import ArchitectureId, ProtocolMode, ResourceKind
from tal.core import sha256
from tal.research import (
    BenchmarkId,
    BenchmarkScore,
    EpisodeMetrics,
    EpisodeResult,
    ExperimentFreeze,
    FrozenArtifact,
    ModelConfiguration,
    ResearchResourceBudget,
    ResearchTaskManifest,
    TerminalStatus,
    ToolDefinition,
    compile_episode_result,
    compile_experiment_freeze,
    compile_research_manifest,
    deterministic_episode_id,
    verify_experiment_freeze,
    verify_research_manifest,
)


def model() -> ModelConfiguration:
    return ModelConfiguration(
        provider="fixture",
        model="fixture-model",
        resolved_model="fixture-model-001",
        max_output_tokens=100,
        context_capacity=1_000,
    )


def task_manifest() -> ResearchTaskManifest:
    payload = {"question": "fixture question"}
    return ResearchTaskManifest(
        domain_pack="tal.benchmark.fixture-0.4.0-rc1",
        benchmark_id=BenchmarkId.FINANCE_AGENT,
        task_id="task:fixture",
        split="excluded",
        task_payload=payload,
        task_payload_hash=sha256(payload),
        goal="answer fixture question",
        tools=(
            ToolDefinition(
                tool_id="tool:search",
                description="search fixture data",
                arguments_schema_hash=sha256({"type": "object"}),
            ),
        ),
        resource_budgets=(ResearchResourceBudget(kind=ResourceKind.MODEL_CALLS, maximum=3),),
        topology_hash=sha256({"topology": "fixture"}),
        model_configuration=model(),
    )


def test_research_manifest_is_hashable_and_detects_payload_tampering() -> None:
    manifest = compile_research_manifest(task_manifest())
    assert verify_research_manifest(manifest)
    tampered = task_manifest().model_dump()
    tampered["task_payload"] = {"question": "tampered"}
    with pytest.raises(ValueError, match="task payload hash"):
        ResearchTaskManifest.model_validate(tampered)


def test_experiment_freeze_is_hashable_and_budgets_are_bounded() -> None:
    freeze = compile_experiment_freeze(
        ExperimentFreeze(
            study_id="study:fixture",
            source_revision="fixture-revision",
            container_digest="sha256:container",
            random_seed=42,
            actor_model=model(),
            judge_model=model(),
            artifacts=(
                FrozenArtifact(
                    artifact_id="artifact:tasks",
                    content_hash=sha256({"tasks": ["task:fixture"]}),
                    locator="research/tasks.json",
                ),
            ),
            total_study_budget_microcents=50_000_000_000,
            main_study_budget_microcents=40_000_000_000,
            rerun_budget_microcents=2_500_000_000,
        )
    )
    assert verify_experiment_freeze(freeze)
    assert deterministic_episode_id(
        freeze.freeze_hash or "sha256:" + "0" * 64,
        BenchmarkId.FINANCE_AGENT,
        "task:fixture",
        ArchitectureId.SINGLE,
        ProtocolMode.TAL,
    ).startswith("episode:")


def test_result_hash_binds_protocol_and_score() -> None:
    result = compile_episode_result(
        EpisodeResult(
            episode_id="episode:fixture",
            freeze_hash=sha256({"freeze": "fixture"}),
            benchmark_id=BenchmarkId.PLANCRAFT,
            task_id="task:fixture",
            architecture=ArchitectureId.SINGLE,
            protocol_mode=ProtocolMode.CONTROL,
            terminal_status=TerminalStatus.COMPLETED,
            score=BenchmarkScore(
                success=True,
                native_score_millionths=1_000_000,
                scorer_id="fixture-scorer",
                scorer_hash=sha256({"scorer": "fixture"}),
            ),
            metrics=EpisodeMetrics(model_calls=1),
            completed_at=datetime.now(UTC),
        )
    )
    assert result.result_hash is not None
