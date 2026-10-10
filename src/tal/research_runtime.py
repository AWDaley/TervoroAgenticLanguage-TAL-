"""Typed TAL execution for benchmark-neutral research episodes.

This module deliberately does not reuse the invoice proposal union.  It retains the
same closed-world principles while allowing benchmark tools and native state to be
represented without pretending they are invoice entities.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from .coordination import (
    ActorDefinition,
    CompiledExecutionTopology,
    CoordinationPacket,
    PhaseDefinition,
    ProtocolMode,
    ResourceKind,
    TopologyCompiler,
)
from .core import Hash, Identifier, StrictModel, canonical_json, sha256
from .research import BenchmarkScore, EpisodeMetrics, ResearchTaskManifest
from .research_environment import (
    BenchmarkEnvironment,
    BenchmarkObservation,
    BenchmarkToolCall,
    EnvironmentProtocolError,
)


class ResearchRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ResearchProposalBase(StrictModel):
    proposal_id: Identifier
    kind: str
    manifest_hash: Hash
    state_version: int = Field(ge=0)


class ResearchToolProposal(ResearchProposalBase):
    kind: Literal["request_tool"] = "request_tool"
    tool_id: Identifier
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ResearchAnswerProposal(ResearchProposalBase):
    kind: Literal["propose_answer"] = "propose_answer"
    answer: str = Field(min_length=1, max_length=100_000)


class ResearchEscalationProposal(ResearchProposalBase):
    kind: Literal["escalate"] = "escalate"
    reason: str = Field(min_length=1, max_length=10_000)


class ResearchFailureProposal(ResearchProposalBase):
    kind: Literal["report_failure"] = "report_failure"
    reason: str = Field(min_length=1, max_length=10_000)


ResearchProposal: TypeAlias = Annotated[
    ResearchToolProposal
    | ResearchAnswerProposal
    | ResearchEscalationProposal
    | ResearchFailureProposal,
    Field(discriminator="kind"),
]
_RESEARCH_PROPOSAL_ADAPTER: TypeAdapter[ResearchProposal] = TypeAdapter(ResearchProposal)


class ResearchModelOutput(StrictModel):
    proposal: ResearchProposal | None = None
    packet: CoordinationPacket | None = None
    private_history_entry: str = Field(default="", max_length=10_000)
    complete_phase: bool = False

    @model_validator(mode="after")
    def contains_exactly_one_operation(self) -> "ResearchModelOutput":
        if (self.proposal is None) == (self.packet is None):
            raise ValueError("research model output must contain exactly one proposal or packet")
        return self


class ResearchModelContext(StrictModel):
    actor_id: Identifier
    role: str
    phase_ref: Identifier
    round_index: int = Field(ge=1)
    state_version: int = Field(ge=0)
    protocol_mode: ProtocolMode
    segments: dict[str, str]

    @property
    def rendered_text(self) -> str:
        return "\n\n".join(f"[{name}]\n{content}" for name, content in self.segments.items())


class AsyncResearchModelAdapter(Protocol):
    model_id: str

    def respond(self, context: ResearchModelContext) -> Awaitable[ResearchModelOutput]: ...


class ResearchEvent(StrictModel):
    event_id: Identifier
    event_type: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    state_version: int = Field(ge=0)
    actor_id: Identifier | None = None
    detail: str = Field(default="", max_length=100_000)
    payload_hash: Hash
    previous_hash: Hash | None = None
    event_hash: Hash
    created_at: datetime


class ResearchRuntimeState(StrictModel):
    manifest: ResearchTaskManifest
    topology: CompiledExecutionTopology
    protocol_mode: ProtocolMode
    environment: BenchmarkObservation
    state_version: int = Field(default=0, ge=0)
    events: tuple[ResearchEvent, ...] = ()
    packets: tuple[CoordinationPacket, ...] = ()
    private_histories: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    actor_answers: dict[str, str] = Field(default_factory=dict)
    final_answer: str = ""
    terminal_reason: str | None = None


@dataclass(frozen=True)
class BudgetReservation:
    kind: ResourceKind
    amount: int


@dataclass
class BudgetController:
    """Atomic in-process admission for all per-episode resource limits."""

    limits: Mapping[ResourceKind, int]
    used: dict[ResourceKind, int] = field(default_factory=dict)
    reserved: dict[ResourceKind, int] = field(default_factory=dict)

    @classmethod
    def from_manifest(cls, manifest: ResearchTaskManifest) -> "BudgetController":
        return cls({item.kind: item.maximum for item in manifest.resource_budgets})

    def reserve(self, kind: ResourceKind, amount: int) -> BudgetReservation:
        if amount < 0:
            raise ValueError("budget amount may not be negative")
        maximum = self.limits.get(kind)
        current = self.used.get(kind, 0) + self.reserved.get(kind, 0)
        if maximum is not None and current + amount > maximum:
            raise ResearchRuntimeError("BUDGET_EXCEEDED", f"{kind.value} budget exhausted")
        self.reserved[kind] = self.reserved.get(kind, 0) + amount
        return BudgetReservation(kind, amount)

    def commit(self, reservation: BudgetReservation, actual: int | None = None) -> None:
        amount = reservation.amount if actual is None else actual
        if amount < 0 or amount > reservation.amount:
            raise ValueError("actual budget amount must be within its reservation")
        self.reserved[reservation.kind] = (
            self.reserved.get(reservation.kind, 0) - reservation.amount
        )
        self.used[reservation.kind] = self.used.get(reservation.kind, 0) + amount

    def release(self, reservation: BudgetReservation) -> None:
        self.reserved[reservation.kind] = (
            self.reserved.get(reservation.kind, 0) - reservation.amount
        )

    def consume(self, kind: ResourceKind, amount: int = 1) -> None:
        reservation = self.reserve(kind, amount)
        self.commit(reservation)


@dataclass
class ResearchCounters:
    invalid_tool_requests: int = 0
    rejected_proposals: int = 0
    rejected_messages: int = 0
    unsupported_completion_attempts: int = 0
    state_conflicts: int = 0
    coordination_packets: int = 0
    coordination_rounds: int = 0
    duplicate_tool_calls: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    peak_context_occupancy: int = 0
    tal_specific_input_tokens: int = 0
    coordination_input_tokens: int = 0
    wall_clock_milliseconds: int = 0

    def metrics(self) -> EpisodeMetrics:
        return EpisodeMetrics(
            invalid_tool_requests=self.invalid_tool_requests,
            rejected_proposals=self.rejected_proposals,
            rejected_messages=self.rejected_messages,
            unsupported_completion_attempts=self.unsupported_completion_attempts,
            state_conflicts=self.state_conflicts,
            coordination_packets=self.coordination_packets,
            coordination_rounds=self.coordination_rounds,
            duplicate_tool_calls=self.duplicate_tool_calls,
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            peak_context_occupancy=self.peak_context_occupancy,
            tal_specific_input_tokens=self.tal_specific_input_tokens,
            coordination_input_tokens=self.coordination_input_tokens,
            wall_clock_milliseconds=self.wall_clock_milliseconds,
        )


class ResearchActionGate:
    def __init__(
        self,
        environment: BenchmarkEnvironment,
        budget: BudgetController,
        counters: ResearchCounters,
    ) -> None:
        self.environment = environment
        self.budget = budget
        self.counters = counters

    async def execute(
        self,
        state: ResearchRuntimeState,
        actor: ActorDefinition,
        phase: PhaseDefinition,
        proposal: ResearchProposal,
    ) -> ResearchRuntimeState:
        if proposal.manifest_hash != state.manifest.manifest_hash:
            raise ResearchRuntimeError(
                "STALE_MANIFEST", "proposal manifest hash differs from episode manifest"
            )
        if proposal.state_version != state.state_version:
            self.counters.state_conflicts += 1
            raise ResearchRuntimeError("STALE_STATE_VERSION", "proposal state version is stale")
        if isinstance(proposal, ResearchToolProposal):
            return await self._tool(state, actor, phase, proposal)
        if isinstance(proposal, ResearchAnswerProposal):
            return _append_event(
                state.model_copy(
                    update={
                        "actor_answers": {**state.actor_answers, actor.actor_id: proposal.answer},
                        "final_answer": proposal.answer,
                    }
                ),
                "answer_proposed",
                actor.actor_id,
                proposal.answer,
            )
        if isinstance(proposal, ResearchEscalationProposal):
            return _append_event(
                state.model_copy(update={"terminal_reason": "escalated"}),
                "episode_escalated",
                actor.actor_id,
                proposal.reason,
            )
        assert isinstance(proposal, ResearchFailureProposal)
        return _append_event(
            state.model_copy(update={"terminal_reason": "failed"}),
            "episode_failed",
            actor.actor_id,
            proposal.reason,
        )

    async def _tool(
        self,
        state: ResearchRuntimeState,
        actor: ActorDefinition,
        phase: PhaseDefinition,
        proposal: ResearchToolProposal,
    ) -> ResearchRuntimeState:
        manifest_tools = {item.tool_id: item for item in state.manifest.tools}
        environment_tools = {item.tool_id: item for item in self.environment.tool_definitions()}
        tool = manifest_tools.get(proposal.tool_id)
        if (
            tool is None
            or proposal.tool_id not in environment_tools
            or proposal.tool_id not in actor.tool_capabilities
            or phase.tool_mode.value == "none"
        ):
            self.counters.invalid_tool_requests += 1
            raise ResearchRuntimeError(
                "TOOL_NOT_AUTHORIZED", "tool is not declared for actor and phase"
            )
        _validate_schema(proposal.arguments, tool.arguments_schema)
        self.budget.consume(ResourceKind.TOOL_CALLS)
        if tool.external_effect:
            self.budget.consume(ResourceKind.EXTERNAL_EFFECTS)
        call = BenchmarkToolCall(
            call_id="toolcall:"
            + sha256({"proposal": proposal.proposal_id, "state": state.state_version}).removeprefix(
                "sha256:"
            )[:24],
            tool_id=proposal.tool_id,
            arguments=proposal.arguments,
            expected_state_version=state.environment.state_version,
        )
        try:
            result = await self.environment.execute_tool(call)
        except EnvironmentProtocolError as error:
            raise ResearchRuntimeError("TOOL_FAILURE", str(error)) from error
        self.counters.tool_calls += 1
        if result.duplicate:
            self.counters.duplicate_tool_calls += 1
        if tool.result_schema:
            _validate_schema(result.output, tool.result_schema)
        next_state = state.model_copy(update={"environment": result.observation})
        return _append_event(
            next_state,
            "tool_result",
            actor.actor_id,
            canonical_json(result).decode("utf-8"),
        )


class ResearchTopologyScheduler:
    """Execute frozen topology mechanics with a benchmark action gate."""

    def __init__(self, environment: BenchmarkEnvironment) -> None:
        self.environment = environment

    async def run(
        self,
        *,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
        protocol_mode: ProtocolMode,
        adapters: Mapping[str, AsyncResearchModelAdapter],
    ) -> tuple[ResearchRuntimeState, BenchmarkScore, ResearchCounters, BudgetController]:
        if not TopologyCompiler().verify(topology):
            raise ValueError("research topology hash is invalid")
        if manifest.topology_hash != topology.topology_hash:
            raise ValueError("task manifest topology does not match episode topology")
        expected_model = (
            manifest.model_configuration.resolved_model or manifest.model_configuration.model
        )
        for actor in topology.specification.actors:
            adapter = adapters.get(actor.actor_id)
            if adapter is None:
                raise ValueError(f"missing adapter for {actor.actor_id}")
            if adapter.model_id != expected_model:
                raise ValueError("actor adapter model differs from frozen model")
        initial = await self.environment.reset(manifest)
        state = ResearchRuntimeState(
            manifest=manifest,
            topology=topology,
            protocol_mode=protocol_mode,
            environment=initial,
        )
        state = _append_event(state, "episode_started", None, "frozen benchmark episode started")
        counters = ResearchCounters()
        budget = BudgetController.from_manifest(manifest)
        gate = ResearchActionGate(self.environment, budget, counters)
        actors = {actor.actor_id: actor for actor in topology.specification.actors}
        for phase in topology.specification.phases:
            if state.terminal_reason is not None or state.environment.terminal:
                break
            if all(entity.startswith("stage:") for entity in phase.eligible_entity_ids):
                state = self._run_stage(state, phase)
                continue
            active = {actor_id for actor_id in phase.eligible_entity_ids if actor_id in actors}
            for round_index in range(1, phase.max_rounds + 1):
                if not active:
                    break
                budget.consume(ResourceKind.COORDINATION_ROUNDS)
                counters.coordination_rounds += 1
                for _activation in range(phase.max_activations_per_entity):
                    if (
                        not active
                        or state.terminal_reason is not None
                        or state.environment.terminal
                    ):
                        break
                    contexts = {
                        actor_id: self._context(state, actors[actor_id], phase, round_index)
                        for actor_id in active
                    }
                    for context in contexts.values():
                        occupancy = _estimate_tokens(context.rendered_text)
                        counters.peak_context_occupancy = max(
                            counters.peak_context_occupancy, occupancy
                        )
                        if occupancy > manifest.model_configuration.context_capacity:
                            raise ResearchRuntimeError(
                                "CONTEXT_BUDGET_EXCEEDED", "actor context exceeds frozen capacity"
                            )
                    for _actor_id in active:
                        budget.consume(ResourceKind.MODEL_CALLS)
                        counters.model_calls += 1
                    outputs = await asyncio.gather(
                        *(adapters[actor_id].respond(contexts[actor_id]) for actor_id in active),
                        return_exceptions=True,
                    )
                    by_actor = dict(zip(active, outputs, strict=True))
                    for actor_id in topology.entity_order:
                        if actor_id not in by_actor:
                            continue
                        output = by_actor[actor_id]
                        if isinstance(output, BaseException):
                            state = _append_event(state, "actor_failure", actor_id, str(output))
                            state = state.model_copy(
                                update={"terminal_reason": "infrastructure_failure"}
                            )
                            break
                        assert isinstance(output, ResearchModelOutput)
                        try:
                            state = await self._apply_output(
                                state, actors[actor_id], phase, output, gate
                            )
                        except ResearchRuntimeError as error:
                            counters.rejected_proposals += 1
                            state = _append_event(
                                state, "proposal_rejected", actor_id, error.code + ": " + str(error)
                            )
                            if output.complete_phase:
                                active.discard(actor_id)
                            continue
                        if output.private_history_entry:
                            histories = dict(state.private_histories)
                            histories[actor_id] = (
                                *histories.get(actor_id, ()),
                                output.private_history_entry,
                            )
                            state = state.model_copy(update={"private_histories": histories})
                        if output.complete_phase:
                            active.discard(actor_id)
                if not active:
                    break
        if not state.final_answer and state.actor_answers:
            state = state.model_copy(
                update={"final_answer": tuple(state.actor_answers.values())[-1]}
            )
        score = await self.environment.score(state.final_answer)
        state = _append_event(state, "episode_scored", None, canonical_json(score).decode("utf-8"))
        await self.environment.close()
        return state, score, counters, budget

    async def _apply_output(
        self,
        state: ResearchRuntimeState,
        actor: ActorDefinition,
        phase: PhaseDefinition,
        output: ResearchModelOutput,
        gate: ResearchActionGate,
    ) -> ResearchRuntimeState:
        if output.packet is not None:
            return self._packet(state, actor, phase, output.packet, gate.counters, gate.budget)
        assert output.proposal is not None
        return await gate.execute(state, actor, phase, output.proposal)

    def _packet(
        self,
        state: ResearchRuntimeState,
        actor: ActorDefinition,
        phase: PhaseDefinition,
        packet: CoordinationPacket,
        counters: ResearchCounters,
        budget: BudgetController,
    ) -> ResearchRuntimeState:
        if packet.sender_actor_id != actor.actor_id or packet.phase_ref != phase.phase_id:
            counters.rejected_messages += 1
            raise ResearchRuntimeError("PACKET_INVALID", "packet sender or phase is invalid")
        if packet.state_version != state.state_version:
            counters.state_conflicts += 1
            raise ResearchRuntimeError("STALE_STATE_VERSION", "packet state version is stale")
        valid = all(
            any(
                edge.sender_id == actor.actor_id
                and edge.recipient_id == recipient_id
                and packet.kind in edge.permitted_packet_kinds
                and phase.phase_id in edge.phase_refs
                for edge in state.topology.specification.communication_edges
            )
            for recipient_id in packet.recipient_actor_ids
        )
        if not valid or not set(packet.recipient_actor_ids).issubset(set(actor.allowed_recipients)):
            counters.rejected_messages += 1
            raise ResearchRuntimeError(
                "PACKET_NOT_AUTHORIZED", "packet violates the frozen communication graph"
            )
        budget.consume(ResourceKind.COORDINATION_PACKETS)
        counters.coordination_packets += 1
        return _append_event(
            state.model_copy(update={"packets": (*state.packets, packet)}),
            "packet_delivered",
            actor.actor_id,
            canonical_json(packet).decode("utf-8"),
        )

    def _run_stage(
        self, state: ResearchRuntimeState, phase: PhaseDefinition
    ) -> ResearchRuntimeState:
        del phase
        if state.actor_answers:
            answer = "\n".join(
                state.actor_answers[actor_id]
                for actor_id in state.topology.entity_order
                if actor_id in state.actor_answers
            )
            return _append_event(
                state.model_copy(update={"final_answer": answer}),
                "deterministic_aggregation",
                None,
                answer,
            )
        return _append_event(state, "deterministic_aggregation", None, "no actor answer")

    @staticmethod
    def _context(
        state: ResearchRuntimeState,
        actor: ActorDefinition,
        phase: PhaseDefinition,
        round_index: int,
    ) -> ResearchModelContext:
        received = tuple(
            packet for packet in state.packets if actor.actor_id in packet.recipient_actor_ids
        )
        segments: dict[str, str] = {
            "goal_manifest": canonical_json(state.manifest).decode("utf-8"),
            "role": canonical_json(actor).decode("utf-8"),
            "authoritative_state": canonical_json(
                {
                    "environment": state.environment,
                    "state_version": state.state_version,
                    "phase": phase.phase_id,
                }
            ).decode("utf-8"),
        }
        if received:
            segments["received_packets"] = canonical_json(received).decode("utf-8")
        if state.private_histories.get(actor.actor_id):
            segments["private_history"] = canonical_json(
                state.private_histories[actor.actor_id]
            ).decode("utf-8")
        if state.protocol_mode is ProtocolMode.TAL:
            segments["tal_multi_agent_grammar"] = (
                "Return one typed research operation. Authority belongs to TAL."
            )
            segments["response_schema"] = canonical_json(
                _RESEARCH_PROPOSAL_ADAPTER.json_schema()
            ).decode("utf-8")
            segments["topology"] = canonical_json(state.topology).decode("utf-8")
        return ResearchModelContext(
            actor_id=actor.actor_id,
            role=actor.role.value,
            phase_ref=phase.phase_id,
            round_index=round_index,
            state_version=state.state_version,
            protocol_mode=state.protocol_mode,
            segments=segments,
        )


def _append_event(
    state: ResearchRuntimeState,
    event_type: str,
    actor_id: str | None,
    detail: str,
) -> ResearchRuntimeState:
    sequence = len(state.events)
    payload_hash = sha256({"event_type": event_type, "actor_id": actor_id, "detail": detail})
    previous = state.events[-1].event_hash if state.events else None
    event_hash = sha256(
        {
            "sequence": sequence,
            "state_version": state.state_version + 1,
            "payload_hash": payload_hash,
            "previous_hash": previous,
        }
    )
    event = ResearchEvent(
        event_id=f"research-event:{sequence:08d}",
        event_type=event_type,
        sequence=sequence,
        state_version=state.state_version + 1,
        actor_id=actor_id,
        detail=detail,
        payload_hash=payload_hash,
        previous_hash=previous,
        event_hash=event_hash,
        created_at=datetime.now(UTC),
    )
    return state.model_copy(
        update={"state_version": state.state_version + 1, "events": (*state.events, event)}
    )


def verify_research_event_chain(events: tuple[ResearchEvent, ...]) -> bool:
    previous: Hash | None = None
    for sequence, event in enumerate(events):
        if event.sequence != sequence or event.previous_hash != previous:
            return False
        expected = sha256(
            {
                "sequence": event.sequence,
                "state_version": event.state_version,
                "payload_hash": event.payload_hash,
                "previous_hash": event.previous_hash,
            }
        )
        if event.event_hash != expected:
            return False
        previous = event.event_hash
    return True


def _estimate_tokens(value: str) -> int:
    return len(value.split())


def _validate_schema(value: object, schema: Mapping[str, JsonValue]) -> None:
    """Small deterministic JSON-schema subset sufficient for frozen tool contracts."""

    if not schema:
        return
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ResearchRuntimeError("TOOL_ARGUMENT_INVALID", "tool arguments must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise ResearchRuntimeError("TOOL_SCHEMA_INVALID", "tool required schema is invalid")
        for name in required:
            if not isinstance(name, str) or name not in value:
                raise ResearchRuntimeError(
                    "TOOL_ARGUMENT_INVALID", f"missing required tool argument {name}"
                )
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for name, item in value.items():
                child = properties.get(name)
                if child is None:
                    if schema.get("additionalProperties", True) is False:
                        raise ResearchRuntimeError(
                            "TOOL_ARGUMENT_INVALID", f"unknown tool argument {name}"
                        )
                    continue
                if isinstance(child, dict):
                    _validate_schema(item, child)
    elif expected == "array":
        if not isinstance(value, list):
            raise ResearchRuntimeError("TOOL_ARGUMENT_INVALID", "tool argument must be an array")
        items = schema.get("items")
        if isinstance(items, dict):
            for item in value:
                _validate_schema(item, items)
    elif expected == "string" and not isinstance(value, str):
        raise ResearchRuntimeError("TOOL_ARGUMENT_INVALID", "tool argument must be a string")
    elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ResearchRuntimeError("TOOL_ARGUMENT_INVALID", "tool argument must be an integer")
    elif expected == "number" and (not isinstance(value, int | float) or isinstance(value, bool)):
        raise ResearchRuntimeError("TOOL_ARGUMENT_INVALID", "tool argument must be a number")
    elif expected == "boolean" and not isinstance(value, bool):
        raise ResearchRuntimeError("TOOL_ARGUMENT_INVALID", "tool argument must be a boolean")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ResearchRuntimeError(
            "TOOL_ARGUMENT_INVALID", "tool argument is not a registered enum value"
        )
