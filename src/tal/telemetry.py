"""Typed, component-level v0.3 accounting for model context and resource use."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from .coordination import ProtocolMode, ResourceKind
from .core import Hash, Identifier, StrictModel


class ContextComponent(str, Enum):
    TAL_CORE_GRAMMAR = "input.tal_core_grammar"
    TAL_MULTI_AGENT_GRAMMAR = "input.tal_multi_agent_grammar"
    RESPONSE_SCHEMA = "input.response_schema"
    TOPOLOGY_RULES = "input.topology_rules"
    GOAL_MANIFEST = "input.goal_manifest"
    ROLE_CONTEXT = "input.role_context"
    SUBGOAL_CONTEXT = "input.subgoal_context"
    AUTHORITATIVE_STATE = "input.authoritative_state"
    CAPABILITY_CONTEXT = "input.capability_context"
    TOOL_DEFINITIONS = "input.tool_definitions"
    EVIDENCE = "input.evidence"
    AGENT_MESSAGES = "input.agent_messages"
    PREVIOUS_PRIVATE_HISTORY = "input.previous_private_history"
    OTHER = "input.other"


class ContextSegment(StrictModel):
    component: ContextComponent
    token_count: int = Field(ge=0)
    content_hash: Hash


class ModelCallRecord(StrictModel):
    model_call_id: Identifier
    actor_or_stage_id: Identifier
    role: str = Field(min_length=1, max_length=64)
    phase_ref: Identifier
    round_index: int = Field(ge=0)
    protocol_mode: ProtocolMode
    model_id: str = Field(min_length=1, max_length=256)
    tokenization_method: str = Field(min_length=1, max_length=256)
    input_segments: tuple[ContextSegment, ...] = ()
    total_input_tokens: int = Field(ge=0)
    visible_output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    uncached_input_tokens: int = Field(default=0, ge=0)
    current_context_occupancy: int = Field(default=0, ge=0)
    maximum_context_capacity: int = Field(default=0, ge=0)
    input_cost_microcents: int = Field(default=0, ge=0)
    output_cost_microcents: int = Field(default=0, ge=0)
    reasoning_cost_microcents: int = Field(default=0, ge=0)
    total_cost_microcents: int = Field(default=0, ge=0)
    wall_clock_milliseconds: int = Field(default=0, ge=0)
    physical_completion_index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def accounting_reconciles(self) -> "ModelCallRecord":
        if sum(item.token_count for item in self.input_segments) > self.total_input_tokens:
            raise ValueError("context segment tokens may not exceed total input tokens")
        if self.cached_input_tokens + self.uncached_input_tokens > self.total_input_tokens:
            raise ValueError("cached and uncached input may not exceed total input")
        if self.current_context_occupancy > self.maximum_context_capacity > 0:
            raise ValueError("context occupancy may not exceed capacity")
        if (
            self.input_cost_microcents
            + self.output_cost_microcents
            + self.reasoning_cost_microcents
            != self.total_cost_microcents
        ):
            raise ValueError("component costs must equal total provider cost")
        return self


class TokenCounter(Protocol):
    identifier: str

    def count(self, text: str) -> int: ...


class CharacterTokenCounter:
    """Deterministic offline counter used only in fixtures, never for billing."""

    identifier = "characters-v1"

    def count(self, text: str) -> int:
        return len(text)


def aggregate_usage(records: Iterable[ModelCallRecord]) -> dict[ResourceKind, int]:
    totals = {kind: 0 for kind in ResourceKind}
    for record in records:
        totals[ResourceKind.INPUT_TOKENS] += record.total_input_tokens
        totals[ResourceKind.OUTPUT_TOKENS] += record.visible_output_tokens
        totals[ResourceKind.REASONING_TOKENS] += record.reasoning_tokens
        totals[ResourceKind.MODEL_CALLS] += 1
        totals[ResourceKind.WALL_CLOCK_MILLISECONDS] += record.wall_clock_milliseconds
        totals[ResourceKind.PROVIDER_COST_MICROCENTS] += record.total_cost_microcents
    return totals


def tal_context_share(record: ModelCallRecord) -> float:
    if record.total_input_tokens == 0:
        return 0.0
    tal_components = {
        ContextComponent.TAL_CORE_GRAMMAR,
        ContextComponent.TAL_MULTI_AGENT_GRAMMAR,
        ContextComponent.RESPONSE_SCHEMA,
        ContextComponent.TOPOLOGY_RULES,
        ContextComponent.GOAL_MANIFEST,
    }
    numerator = sum(
        segment.token_count
        for segment in record.input_segments
        if segment.component in tal_components
    )
    return numerator / record.total_input_tokens
