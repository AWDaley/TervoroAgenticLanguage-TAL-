"""Versioned research contracts for reproducible TAL benchmark experiments.

This module is intentionally independent of the invoice domain pack.  It gives
benchmark integrations a closed, hashable representation without changing the
stable v0.3 invoice manifest or runtime schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, JsonValue, field_validator, model_validator

from .coordination import ArchitectureId, ProtocolMode, ResourceKind
from .core import Hash, Identifier, StrictModel, canonical_json, sha256

RESEARCH_VERSION = "0.4.0-rc2"
RESEARCH_MANIFEST_SCHEMA_ID = "tal-research-task-manifest-0.4.0-rc2"
EXPERIMENT_FREEZE_SCHEMA_ID = "tal-experiment-freeze-0.4.0-rc2"


class BenchmarkId(str, Enum):
    FINANCE_AGENT = "finance-agent"
    PLANCRAFT = "plancraft"


class AttemptKind(str, Enum):
    PRIMARY = "primary"
    INFRASTRUCTURE_RERUN = "infrastructure_rerun"


class TreatmentDefinition(str, Enum):
    """The preregistered scientific contrast represented by an episode."""

    FULL_TAL_EFFECT = "full_tal_effect"


class TerminalStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class ToolDefinition(StrictModel):
    tool_id: Identifier
    description: str = Field(min_length=1, max_length=2_000)
    read_only: bool = False
    arguments_schema_hash: Hash
    arguments_schema: dict[str, JsonValue] = Field(default_factory=dict)
    result_schema: dict[str, JsonValue] = Field(default_factory=dict)
    result_schema_hash: Hash | None = None
    external_effect: bool = False

    @model_validator(mode="after")
    def declared_schemas_match_their_hashes(self) -> "ToolDefinition":
        if self.arguments_schema and sha256(self.arguments_schema) != self.arguments_schema_hash:
            raise ValueError("tool arguments schema hash does not match arguments schema")
        if self.result_schema and self.result_schema_hash != sha256(self.result_schema):
            raise ValueError("tool result schema hash does not match result schema")
        if self.result_schema and self.result_schema_hash is None:
            raise ValueError("tool result schema requires a result schema hash")
        return self


class ResearchResourceBudget(StrictModel):
    """A hard per-episode ceiling. Amounts use ResourceKind units."""

    kind: ResourceKind
    maximum: int = Field(ge=0)


class ModelConfiguration(StrictModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    resolved_model: str | None = Field(default=None, min_length=1, max_length=256)
    temperature_milli: int = Field(default=0, ge=0, le=2_000)
    max_output_tokens: int = Field(ge=1, le=131_072)
    context_capacity: int = Field(ge=1, le=2_000_000)
    request_parameters: dict[str, JsonValue] = Field(default_factory=dict)


class ResearchTaskManifest(StrictModel):
    """Model-safe task description; scorer-only material is deliberately excluded."""

    schema_id: str = RESEARCH_MANIFEST_SCHEMA_ID
    tal_research_version: str = RESEARCH_VERSION
    domain_pack: str = Field(min_length=1, max_length=256)
    benchmark_id: BenchmarkId
    task_id: Identifier
    split: str = Field(min_length=1, max_length=128)
    task_payload: dict[str, JsonValue]
    task_payload_hash: Hash
    goal: str = Field(min_length=1, max_length=10_000)
    tools: tuple[ToolDefinition, ...] = ()
    resource_budgets: tuple[ResearchResourceBudget, ...] = ()
    topology_hash: Hash
    model_configuration: ModelConfiguration
    manifest_hash: Hash | None = None

    @model_validator(mode="after")
    def unique_budget_and_tool_ids(self) -> "ResearchTaskManifest":
        if len({item.kind for item in self.resource_budgets}) != len(self.resource_budgets):
            raise ValueError("research task manifest may not repeat a resource budget")
        if len({item.tool_id for item in self.tools}) != len(self.tools):
            raise ValueError("research task manifest may not repeat a tool")
        if sha256(self.task_payload) != self.task_payload_hash:
            raise ValueError("task payload hash does not match task payload")
        return self


def compile_research_manifest(manifest: ResearchTaskManifest) -> ResearchTaskManifest:
    """Seal a task manifest without permitting callers to choose its hash."""

    preimage = manifest.model_dump(mode="json", exclude_none=True)
    preimage.pop("manifest_hash", None)
    return manifest.model_copy(update={"manifest_hash": sha256(preimage)})


def verify_research_manifest(manifest: ResearchTaskManifest) -> bool:
    if manifest.manifest_hash is None:
        return False
    preimage = manifest.model_dump(mode="json", exclude_none=True)
    claimed = preimage.pop("manifest_hash", None)
    return bool(claimed == sha256(preimage))


class ProviderCallReceipt(StrictModel):
    call_id: Identifier
    provider: str = Field(min_length=1, max_length=64)
    requested_model: str = Field(min_length=1, max_length=256)
    resolved_model: str = Field(min_length=1, max_length=256)
    provider_request_id: str | None = Field(default=None, max_length=512)
    request_hash: Hash
    response_hash: Hash
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    latency_milliseconds: int = Field(ge=0)
    input_cost_microcents: int = Field(default=0, ge=0)
    output_cost_microcents: int = Field(default=0, ge=0)
    reasoning_cost_microcents: int = Field(default=0, ge=0)
    total_cost_microcents: int = Field(default=0, ge=0)
    retry_index: int = Field(default=0, ge=0, le=2)
    input_segments: dict[str, int] = Field(default_factory=dict)
    reservation_microcents: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def usage_and_cost_reconcile(self) -> "ProviderCallReceipt":
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens may not exceed input tokens")
        if (
            self.input_cost_microcents
            + self.output_cost_microcents
            + self.reasoning_cost_microcents
            != self.total_cost_microcents
        ):
            raise ValueError("provider component costs must equal total cost")
        if any(value < 0 for value in self.input_segments.values()):
            raise ValueError("provider input segments may not contain negative values")
        return self


class BenchmarkScore(StrictModel):
    success: bool
    native_score_millionths: int = Field(ge=0, le=1_000_000)
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    scorer_id: str = Field(min_length=1, max_length=256)
    scorer_hash: Hash


class EpisodeMetrics(StrictModel):
    invalid_tool_requests: int = Field(default=0, ge=0)
    rejected_proposals: int = Field(default=0, ge=0)
    rejected_messages: int = Field(default=0, ge=0)
    unsupported_completion_attempts: int = Field(default=0, ge=0)
    state_conflicts: int = Field(default=0, ge=0)
    coordination_packets: int = Field(default=0, ge=0)
    coordination_rounds: int = Field(default=0, ge=0)
    duplicate_tool_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    total_reasoning_tokens: int = Field(default=0, ge=0)
    total_cost_microcents: int = Field(default=0, ge=0)
    peak_context_occupancy: int = Field(default=0, ge=0)
    wall_clock_milliseconds: int = Field(default=0, ge=0)
    tal_specific_input_tokens: int = Field(default=0, ge=0)
    coordination_input_tokens: int = Field(default=0, ge=0)
    judge_input_tokens: int = Field(default=0, ge=0)
    judge_output_tokens: int = Field(default=0, ge=0)
    judge_cost_microcents: int = Field(default=0, ge=0)


class EpisodeResult(StrictModel):
    episode_id: Identifier
    freeze_hash: Hash
    benchmark_id: BenchmarkId
    task_id: Identifier
    architecture: ArchitectureId
    protocol_mode: ProtocolMode
    attempt_kind: AttemptKind = AttemptKind.PRIMARY
    attempt_index: int = Field(default=0, ge=0, le=2)
    terminal_status: TerminalStatus
    score: BenchmarkScore | None = None
    metrics: EpisodeMetrics
    result_hash: Hash | None = None
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def completed_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must include a timezone")
        return value.astimezone(UTC)


def compile_episode_result(result: EpisodeResult) -> EpisodeResult:
    preimage = result.model_dump(mode="json", exclude_none=True)
    preimage.pop("result_hash", None)
    return result.model_copy(update={"result_hash": sha256(preimage)})


def verify_episode_result(result: EpisodeResult) -> bool:
    """Verify that a persisted result has not been edited after execution."""

    if result.result_hash is None:
        return False
    preimage = result.model_dump(mode="json", exclude_none=True)
    claimed = preimage.pop("result_hash", None)
    return bool(claimed == sha256(preimage))


class FrozenArtifact(StrictModel):
    artifact_id: Identifier
    content_hash: Hash
    locator: str = Field(min_length=1, max_length=2_000)


class FrozenContainer(StrictModel):
    runtime_id: Identifier
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    purpose: str = Field(min_length=1, max_length=512)


class ExperimentFreeze(StrictModel):
    schema_id: str = EXPERIMENT_FREEZE_SCHEMA_ID
    research_version: str = RESEARCH_VERSION
    study_id: Identifier
    source_revision: str = Field(min_length=1, max_length=256)
    container_digest: str = Field(min_length=1, max_length=512)
    random_seed: int = Field(ge=0, le=2**63 - 1)
    actor_model: ModelConfiguration
    judge_model: ModelConfiguration
    artifacts: tuple[FrozenArtifact, ...] = Field(min_length=1)
    total_study_budget_microcents: int = Field(ge=0)
    main_study_budget_microcents: int = Field(ge=0)
    rerun_budget_microcents: int = Field(ge=0)
    pilot_budget_microcents: int = Field(default=7_500_000_000, ge=0)
    treatment_definition: TreatmentDefinition = TreatmentDefinition.FULL_TAL_EFFECT
    containers: tuple[FrozenContainer, ...] = ()
    provider_pricing_hash: Hash | None = None
    analysis_plan_hash: Hash | None = None
    controlled_trace_key_fingerprint: Hash | None = None
    freeze_hash: Hash | None = None

    @model_validator(mode="after")
    def freeze_budget_and_artifact_ids_are_valid(self) -> "ExperimentFreeze":
        if len({item.artifact_id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("experiment freeze may not repeat an artifact")
        if self.main_study_budget_microcents + self.rerun_budget_microcents > (
            self.total_study_budget_microcents
        ):
            raise ValueError("main and rerun budgets exceed total study budget")
        if self.total_study_budget_microcents != 50_000_000_000:
            raise ValueError("total study budget must be the preregistered $500 ceiling")
        if self.main_study_budget_microcents != 40_000_000_000:
            raise ValueError("main study budget must be the preregistered $400 allocation")
        if self.rerun_budget_microcents != 2_500_000_000:
            raise ValueError("rerun budget must be the preregistered $25 allocation")
        if self.pilot_budget_microcents != 7_500_000_000:
            raise ValueError("pilot budget must be the preregistered $75 allocation")
        if len({item.runtime_id for item in self.containers}) != len(self.containers):
            raise ValueError("experiment freeze may not repeat a container runtime")
        return self


def compile_experiment_freeze(freeze: ExperimentFreeze) -> ExperimentFreeze:
    preimage = freeze.model_dump(mode="json", exclude_none=True)
    preimage.pop("freeze_hash", None)
    return freeze.model_copy(update={"freeze_hash": sha256(preimage)})


def verify_experiment_freeze(freeze: ExperimentFreeze) -> bool:
    if freeze.freeze_hash is None:
        return False
    preimage = freeze.model_dump(mode="json", exclude_none=True)
    claimed = preimage.pop("freeze_hash", None)
    return bool(claimed == sha256(preimage))


def deterministic_episode_id(
    freeze_hash: Hash,
    benchmark_id: BenchmarkId,
    task_id: Identifier,
    architecture: ArchitectureId,
    protocol_mode: ProtocolMode,
    attempt_index: int = 0,
) -> str:
    """Derive a stable, valid TAL identifier for an episode/attempt."""

    digest = sha256(
        {
            "freeze_hash": freeze_hash,
            "benchmark_id": benchmark_id.value,
            "task_id": task_id,
            "architecture": architecture.value,
            "protocol_mode": protocol_mode.value,
            "attempt_index": attempt_index,
        }
    )
    return "episode:" + digest.removeprefix("sha256:")[:32]


class BenchmarkPack(Protocol):
    """Contract implemented by benchmark-specific environment integrations."""

    benchmark_id: BenchmarkId
    domain_pack_id: str

    def task_manifest(
        self,
        task_id: str,
        *,
        topology_hash: Hash,
        model_configuration: ModelConfiguration,
        resource_budgets: tuple[ResearchResourceBudget, ...],
    ) -> ResearchTaskManifest: ...

    def score(self, task_id: str, output: str) -> BenchmarkScore: ...


def canonical_research_json(value: object) -> bytes:
    """Public canonical encoder used by benchmark adapters and freeze exporters."""

    return canonical_json(value)
