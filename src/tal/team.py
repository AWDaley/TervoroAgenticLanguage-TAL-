"""Phase-aware v0.3 team runtime over the TAL action engine."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from pydantic import Field, model_validator

from .adapters import Proposal, ToolProposal
from .coordination import (
    ActorDefinition,
    ActorLifecycleStatus,
    ActorStatusRecord,
    CompiledExecutionTopology,
    CoordinationPacket,
    PacketKind,
    PhaseDefinition,
    PrivateHistoryRecord,
    ProtocolMode,
    ResourceKind,
    TopologyCompiler,
)
from .core import (
    ErrorCode,
    Rejection,
    RuntimePhase,
    StrictModel,
    canonical_json,
    sha256,
)
from .events import EventKind, RuntimeState
from .invoice import TALManifest
from .runtime import TALRuntime
from .telemetry import (
    CharacterTokenCounter,
    ContextComponent,
    ContextSegment,
    ModelCallRecord,
    TokenCounter,
)


class RenderedContextSegment(StrictModel):
    component: ContextComponent
    content: str


class TeamModelContext(StrictModel):
    actor_id: str
    role: str
    phase_ref: str
    round_index: int = Field(ge=0)
    state_version: int = Field(ge=0)
    protocol_mode: ProtocolMode
    segments: tuple[RenderedContextSegment, ...]

    @property
    def rendered_text(self) -> str:
        return "\n\n".join(
            f"[{segment.component.value}]\n{segment.content}" for segment in self.segments
        )


class TeamModelOutput(StrictModel):
    proposal: Proposal | None = None
    packet: CoordinationPacket | None = None
    private_history_entry: str = Field(default="", max_length=10_000)
    complete_phase: bool = False

    @model_validator(mode="after")
    def contains_one_operation(self) -> "TeamModelOutput":
        if (self.proposal is None) == (self.packet is None):
            raise ValueError("team model output must contain exactly one proposal or packet")
        return self


class AsyncTeamModelAdapter(Protocol):
    model_id: str

    def respond(self, context: TeamModelContext) -> Awaitable[TeamModelOutput]: ...


@dataclass
class ScriptedTeamModelAdapter:
    """Deterministic fixture adapter for the team scheduler."""

    model_id: str = "scripted-team-model"
    outputs: deque[TeamModelOutput] = field(default_factory=deque)

    async def respond(self, context: TeamModelContext) -> TeamModelOutput:
        del context
        if not self.outputs:
            raise RuntimeError("scripted team model has no output")
        return self.outputs.popleft()


class EpisodeDefinition(StrictModel):
    manifest: TALManifest
    topology: CompiledExecutionTopology
    protocol_mode: ProtocolMode
    model_id: str
    context_capacity: int = 128_000


class TeamRuntime:
    """Runs declared actors without allowing them to change their topology."""

    def __init__(
        self,
        runtime: TALRuntime,
        *,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.runtime = runtime
        self.token_counter = token_counter or CharacterTokenCounter()
        self._phase_snapshot_versions: dict[str, int] = {}
        self._packet_context_versions: dict[tuple[str, str, str, int], int] = {}
        self._context_capacities: dict[str, int] = {}
        self._expected_model_ids: dict[str, str] = {}
        self._completion_counter = 0

    def start_episode(self, definition: EpisodeDefinition) -> RuntimeState | Rejection:
        if not TopologyCompiler().verify(definition.topology):
            raise ValueError("episode topology hash is invalid")
        state = self.runtime.start(definition.manifest)
        if isinstance(state, Rejection):
            return state
        self._context_capacities[state.run_id] = definition.context_capacity
        self._expected_model_ids[state.run_id] = definition.model_id
        state = self.runtime._append(
            state,
            EventKind.TOPOLOGY_COMPILED,
            "compiled topology frozen for episode",
            refs=(definition.topology.topology_hash,),
            topology=definition.topology,
            protocol_mode=definition.protocol_mode,
        )
        for actor in definition.topology.specification.actors:
            state = self.runtime._append(
                state,
                EventKind.ACTOR_STATUS_CHANGED,
                "declared actor ready",
                actor=actor.actor_id,
                actor_status=ActorStatusRecord(actor_id=actor.actor_id),
            )
        return state

    async def run_next_phase(
        self,
        run_id: str,
        adapters: Mapping[str, AsyncTeamModelAdapter],
    ) -> RuntimeState | Rejection:
        state = self.runtime.store.load_state(run_id)
        if state.topology is None or state.protocol_mode is None:
            return self._reject(state, ErrorCode.TOPOLOGY_INVALID, "run has no compiled topology")
        if state.phase in {
            RuntimePhase.COMPLETED,
            RuntimePhase.ESCALATED,
            RuntimePhase.CANCELLED,
            RuntimePhase.FAILED,
        }:
            return self._reject(
                state, ErrorCode.INVALID_STATE_TRANSITION, "terminal run cannot advance"
            )
        phase = self._next_phase(state)
        if phase is None:
            return self._reject(
                state, ErrorCode.INVALID_STATE_TRANSITION, "topology has no next phase"
            )
        if all(entity.startswith("stage:") for entity in phase.eligible_entity_ids):
            round_index = state.coordination_round + 1
            state = self.runtime._append(
                state,
                EventKind.COORDINATION_PHASE_STARTED,
                phase.phase_type.value,
                coordination_phase_ref=phase.phase_id,
                coordination_round=round_index,
            )
            self._phase_snapshot_versions[run_id] = state.state_version
            state = self._run_deterministic_stage(state, phase)
            return self._close_barrier(state, phase)
        assert state.topology is not None
        actors = {item.actor_id: item for item in state.topology.specification.actors}
        active = {entity_id for entity_id in phase.eligible_entity_ids if entity_id in actors}
        expected_model = self._expected_model_ids.get(state.run_id)
        for actor_id in active:
            if actor_id not in adapters:
                return self._reject(
                    state, ErrorCode.ACTOR_NOT_DECLARED, f"missing adapter for {actor_id}"
                )
            if expected_model is None or adapters[actor_id].model_id != expected_model:
                return self._reject(
                    state,
                    ErrorCode.TOPOLOGY_INVALID,
                    f"actor {actor_id} does not use the episode's frozen model",
                )
        for _round in range(phase.max_rounds):
            if not active:
                break
            state = self.runtime._append(
                state,
                EventKind.COORDINATION_PHASE_STARTED,
                phase.phase_type.value,
                coordination_phase_ref=phase.phase_id,
                coordination_round=state.coordination_round + 1,
            )
            self._phase_snapshot_versions[run_id] = state.state_version
            state_or_rejection = await self._run_actor_phase(state, phase, adapters, active)
            if isinstance(state_or_rejection, Rejection):
                return state_or_rejection
            state, active = state_or_rejection
            state = self._close_barrier(state, phase)
        return state

    def _next_phase(self, state: RuntimeState) -> PhaseDefinition | None:
        assert state.topology is not None
        phases = state.topology.specification.phases
        if state.coordination_phase_ref is None:
            return phases[0]
        current_index = next(
            (
                index
                for index, phase in enumerate(phases)
                if phase.phase_id == state.coordination_phase_ref
            ),
            None,
        )
        if current_index is None or current_index + 1 >= len(phases):
            return None
        return phases[current_index + 1]

    async def _run_actor_phase(
        self,
        state: RuntimeState,
        phase: PhaseDefinition,
        adapters: Mapping[str, AsyncTeamModelAdapter],
        active: set[str],
    ) -> tuple[RuntimeState, set[str]] | Rejection:
        assert state.topology is not None and state.protocol_mode is not None
        actors = {item.actor_id: item for item in state.topology.specification.actors}
        topology = state.topology
        assert topology is not None
        # A coordination round provides the bounded activation opportunities;
        # run_next_phase owns the separate max_rounds bound.
        for _activation in range(phase.max_activations_per_entity):
            if not active:
                break
            for actor_id in topology.entity_order:
                if actor_id not in active:
                    continue
                reserved = self._consume_resource(state, ResourceKind.MODEL_CALLS, actor_id)
                if isinstance(reserved, Rejection):
                    return reserved
                state = self.runtime._append(
                    reserved,
                    EventKind.ACTOR_ACTIVATED,
                    "actor activation started",
                    actor=actor_id,
                    actor_status=ActorStatusRecord(
                        actor_id=actor_id, status=ActorLifecycleStatus.ACTIVE
                    ),
                    mutates=False,
                )
            contexts = {
                actor_id: self.build_context(state, actors[actor_id], phase) for actor_id in active
            }
            capacity = self._context_capacities.get(state.run_id, 128_000)
            if any(
                self.token_counter.count(context.rendered_text) > capacity
                for context in contexts.values()
            ):
                return self._reject(
                    state,
                    ErrorCode.CONTEXT_BUDGET_EXCEEDED,
                    "rendered actor context exceeds episode capacity",
                )
            for actor_id, context in contexts.items():
                self._packet_context_versions[
                    (state.run_id, actor_id, phase.phase_id, state.coordination_round)
                ] = context.state_version
            started_at = self.runtime.clock()

            async def invoke(
                actor_id: str,
                rendered_context: TeamModelContext,
            ) -> tuple[str, TeamModelOutput | BaseException]:
                try:
                    return actor_id, await adapters[actor_id].respond(rendered_context)
                except BaseException as error:
                    return actor_id, error

            completion_order: dict[str, int] = {}
            by_actor: dict[str, TeamModelOutput | BaseException] = {}
            tasks = [
                asyncio.create_task(invoke(actor_id, contexts[actor_id])) for actor_id in active
            ]
            for completed in asyncio.as_completed(tasks):
                actor_id, response = await completed
                self._completion_counter += 1
                completion_order[actor_id] = self._completion_counter
                by_actor[actor_id] = response
            completed_actor_ids: list[str] = []
            for actor_id in topology.entity_order:
                if actor_id not in by_actor:
                    continue
                response = by_actor[actor_id]
                if isinstance(response, BaseException):
                    state = self._actor_failed(state, actor_id, str(response))
                    active.discard(actor_id)
                    continue
                output = response
                applied = self._apply_output(state, actor_id, phase, output)
                if isinstance(applied, Rejection):
                    return applied
                state = applied
                if output.private_history_entry:
                    state = self.runtime._append(
                        state,
                        EventKind.PRIVATE_HISTORY_RECORDED,
                        "actor private history recorded",
                        actor=actor_id,
                        private_history=PrivateHistoryRecord(
                            actor_id=actor_id, entry=output.private_history_entry
                        ),
                        mutates=False,
                    )
                state = self._record_model_call(
                    state,
                    actor_id,
                    actors[actor_id],
                    phase,
                    contexts[actor_id],
                    adapters[actor_id].model_id,
                    output,
                    started_at,
                    completion_order[actor_id],
                )
                if output.complete_phase:
                    active.discard(actor_id)
                    completed_actor_ids.append(actor_id)
            # Every actor saw the same immutable activation snapshot. Commit lifecycle
            # state only after their outputs have been processed in topology order.
            for actor_id in completed_actor_ids:
                state = self.runtime._append(
                    state,
                    EventKind.ACTOR_STATUS_CHANGED,
                    "actor completed phase",
                    actor=actor_id,
                    actor_status=ActorStatusRecord(
                        actor_id=actor_id, status=ActorLifecycleStatus.WAITING
                    ),
                    mutates=False,
                )
        return state, active

    def _apply_output(
        self,
        state: RuntimeState,
        actor_id: str,
        phase: PhaseDefinition,
        output: TeamModelOutput,
    ) -> RuntimeState | Rejection:
        if output.packet is not None:
            return self.deliver_packet(state, output.packet, actor_id, phase)
        assert output.proposal is not None
        assert state.topology is not None
        actor = next(
            item for item in state.topology.specification.actors if item.actor_id == actor_id
        )
        if isinstance(output.proposal, ToolProposal) and (
            phase.tool_mode.value == "none"
            or output.proposal.tool_id not in actor.tool_capabilities
        ):
            return self._reject(
                state,
                ErrorCode.AUTHORITY_ESCALATION,
                "tool use is not authorized for actor/phase",
            )
        # CONTROL and TAL share the same execution mechanics. Their deliberate
        # difference is model-facing representation, not an authority bypass.
        result = self.runtime.apply_proposal(state.run_id, output.proposal, actor_id=actor_id)
        if isinstance(result, Rejection):
            return result
        return result

    def build_context(
        self, state: RuntimeState, actor: ActorDefinition, phase: PhaseDefinition
    ) -> TeamModelContext:
        assert state.topology is not None and state.protocol_mode is not None
        segments: list[RenderedContextSegment] = [
            RenderedContextSegment(
                component=ContextComponent.GOAL_MANIFEST,
                content=canonical_json(state.manifest).decode("utf-8"),
            ),
            RenderedContextSegment(
                component=ContextComponent.ROLE_CONTEXT,
                content=canonical_json(actor).decode("utf-8"),
            ),
            RenderedContextSegment(
                component=ContextComponent.AUTHORITATIVE_STATE,
                content=canonical_json(
                    {
                        "state_version": state.state_version,
                        "runtime_phase": state.phase.value,
                        "observations": state.observations,
                        "remaining_budgets": state.budget_used,
                    }
                ).decode("utf-8"),
            ),
        ]
        received = tuple(
            packet
            for packet in state.coordination_packets
            if actor.actor_id in packet.recipient_actor_ids
        )
        if received:
            segments.append(
                RenderedContextSegment(
                    component=ContextComponent.AGENT_MESSAGES,
                    content=canonical_json(received).decode("utf-8"),
                )
            )
        private = [
            item.entry for item in state.private_histories if item.actor_id == actor.actor_id
        ]
        if private:
            segments.append(
                RenderedContextSegment(
                    component=ContextComponent.PREVIOUS_PRIVATE_HISTORY,
                    content="\n".join(private),
                )
            )
        if state.protocol_mode is ProtocolMode.TAL:
            segments.insert(
                0,
                RenderedContextSegment(
                    component=ContextComponent.TAL_CORE_GRAMMAR,
                    content=self.runtime.grammar_path.read_text(encoding="utf-8"),
                ),
            )
            segments.insert(
                1,
                RenderedContextSegment(
                    component=ContextComponent.TAL_MULTI_AGENT_GRAMMAR,
                    content=(
                        self.runtime.grammar_path.with_name("multi_agent_grammar.md")
                    ).read_text(encoding="utf-8"),
                ),
            )
            segments.append(
                RenderedContextSegment(
                    component=ContextComponent.TOPOLOGY_RULES,
                    content=canonical_json(
                        {
                            "topology_hash": state.topology.topology_hash,
                            "phase": phase.phase_id,
                            "allowed_recipients": actor.allowed_recipients,
                        }
                    ).decode("utf-8"),
                )
            )
            segments.append(
                RenderedContextSegment(
                    component=ContextComponent.RESPONSE_SCHEMA,
                    content=TeamModelOutput.model_json_schema().__repr__(),
                )
            )
        return TeamModelContext(
            actor_id=actor.actor_id,
            role=actor.role.value,
            phase_ref=phase.phase_id,
            round_index=state.coordination_round,
            state_version=state.state_version,
            protocol_mode=state.protocol_mode,
            segments=tuple(segments),
        )

    def deliver_packet(
        self,
        state: RuntimeState,
        packet: CoordinationPacket,
        actor_id: str,
        phase: PhaseDefinition,
    ) -> RuntimeState | Rejection:
        assert state.topology is not None
        if packet.sender_actor_id != actor_id:
            return self._reject(
                state, ErrorCode.ACTOR_NOT_AUTHORIZED, "packet sender differs from active actor"
            )
        if (
            packet.phase_ref != phase.phase_id
            or packet.round_index != state.coordination_round
            or packet.state_version
            != self._packet_context_versions.get(
                (state.run_id, actor_id, phase.phase_id, state.coordination_round)
            )
        ):
            return self._reject(
                state, ErrorCode.PHASE_NOT_PERMITTED, "packet phase or round is invalid"
            )
        actor = next(
            item for item in state.topology.specification.actors if item.actor_id == actor_id
        )
        if not set(packet.recipient_actor_ids) <= set(actor.allowed_recipients):
            return self._reject(
                state, ErrorCode.COMMUNICATION_NOT_PERMITTED, "recipient is not allowed"
            )
        edge_map = {
            (edge.sender_id, edge.recipient_id): edge
            for edge in state.topology.specification.communication_edges
        }
        for recipient in packet.recipient_actor_ids:
            edge = edge_map.get((actor_id, recipient))
            if (
                edge is None
                or packet.kind not in edge.permitted_packet_kinds
                or phase.phase_id not in edge.phase_refs
            ):
                return self._reject(
                    state, ErrorCode.COMMUNICATION_NOT_PERMITTED, "compiled edge rejects packet"
                )
        if packet.kind not in phase.permitted_packet_kinds:
            return self._reject(state, ErrorCode.PHASE_NOT_PERMITTED, "phase rejects packet kind")
        if packet.kind is PacketKind.ASSIGN_SUBGOAL and not actor.subgoal_authority:
            return self._reject(
                state, ErrorCode.SUBGOAL_NOT_PERMITTED, "actor lacks subgoal authority"
            )
        consumed = self._consume_resource(state, ResourceKind.COORDINATION_PACKETS, actor_id)
        if isinstance(consumed, Rejection):
            return consumed
        state = consumed
        state = self.runtime._append(
            state,
            EventKind.COORDINATION_PACKET_DELIVERED,
            packet.kind.value,
            refs=(packet.packet_id,),
            actor=actor_id,
            packet=packet,
        )
        for claim in packet.payload.claims:
            state = self.runtime._append(
                state,
                EventKind.CLAIM_RECORDED,
                claim.claim_id,
                refs=(packet.packet_id,),
                actor=actor_id,
                claim=claim,
            )
        if packet.payload.subgoal is not None:
            state = self.runtime._append(
                state,
                EventKind.SUBGOAL_ASSIGNED,
                packet.payload.subgoal.subgoal_id,
                refs=(packet.packet_id,),
                actor=actor_id,
                subgoal=packet.payload.subgoal,
            )
        return state

    def _run_deterministic_stage(self, state: RuntimeState, phase: PhaseDefinition) -> RuntimeState:
        if phase.phase_type.value == "consensus":
            assert state.topology is not None
            votes = [
                packet.payload.vote
                for packet in state.coordination_packets
                if packet.payload.vote is not None and packet.payload.vote.approve
            ]
            ordered_candidates = [actor_id for actor_id in state.topology.entity_order]
            counts = {
                candidate: sum(vote.candidate_ref == candidate for vote in votes)
                for candidate in ordered_candidates
            }
            highest_count = max(counts.values(), default=0)
            winner = next(
                (
                    candidate
                    for candidate in ordered_candidates
                    if counts[candidate] == highest_count and highest_count > 0
                ),
                "no-approved-candidate",
            )
            return self.runtime._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                f"deterministic consensus stage completed: {winner}",
                refs=tuple(packet.packet_id for packet in state.coordination_packets),
            )
        if phase.phase_type.value == "synthesis":
            assert state.topology is not None
            ordered_reports = [
                packet.payload.explanation
                for actor_id in state.topology.entity_order
                for packet in state.coordination_packets
                if packet.sender_actor_id == actor_id and packet.kind is PacketKind.SUBMIT_REPORT
            ]
            return self.runtime._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                ("deterministic synthesis-only stage completed: " + "\n".join(ordered_reports))[
                    :500
                ],
                refs=tuple(packet.packet_id for packet in state.coordination_packets),
            )
        return state

    def _close_barrier(self, state: RuntimeState, phase: PhaseDefinition) -> RuntimeState:
        return self.runtime._append(
            state,
            EventKind.COORDINATION_BARRIER_CLOSED,
            phase.phase_id,
            coordination_phase_ref=phase.phase_id,
            coordination_round=state.coordination_round,
        )

    def _consume_resource(
        self, state: RuntimeState, kind: ResourceKind, actor_id: str
    ) -> RuntimeState | Rejection:
        assert state.topology is not None
        limits = {
            item.kind: item.maximum for item in state.topology.specification.global_budget.limits
        }
        used = {item.kind: item.maximum for item in state.budget_used}
        if kind in limits and used.get(kind.value, 0) >= limits[kind]:
            return self._reject(
                state, ErrorCode.BUDGET_EXCEEDED, f"{kind.value} budget is exhausted"
            )
        local = next(
            (
                item
                for item in state.topology.specification.local_budgets
                if item.actor_id == actor_id
            ),
            None,
        )
        if local is not None:
            local_limits = {item.kind: item.maximum for item in local.limits}
            if kind in local_limits:
                actor_used = sum(
                    event.budget_amount or 0
                    for event in self.runtime.store.load_events(state.run_id)
                    if event.actor == actor_id
                    and event.event_type is EventKind.BUDGET_CONSUMED
                    and event.budget_kind == kind.value
                )
                if actor_used >= local_limits[kind]:
                    return self._reject(
                        state, ErrorCode.BUDGET_EXCEEDED, f"{kind.value} local budget is exhausted"
                    )
        return self.runtime._append(
            state,
            EventKind.BUDGET_CONSUMED,
            f"consumed {kind.value}",
            budget_kind=kind.value,
            budget_amount=1,
            actor=actor_id,
        )

    def _record_model_call(
        self,
        state: RuntimeState,
        actor_id: str,
        actor: ActorDefinition,
        phase: PhaseDefinition,
        context: TeamModelContext,
        model_id: str,
        output: TeamModelOutput,
        started_at: datetime,
        completion_index: int,
    ) -> RuntimeState:
        text = context.rendered_text
        segments = tuple(
            ContextSegment(
                component=segment.component,
                token_count=self.token_counter.count(segment.content),
                content_hash=sha256(segment.content),
            )
            for segment in context.segments
        )
        elapsed = max(0, int((self.runtime.clock() - started_at).total_seconds() * 1_000))
        record = ModelCallRecord(
            model_call_id=self.runtime.ids("model"),
            actor_or_stage_id=actor_id,
            role=actor.role.value,
            phase_ref=phase.phase_id,
            round_index=context.round_index,
            protocol_mode=context.protocol_mode,
            model_id=model_id,
            tokenization_method=self.token_counter.identifier,
            input_segments=segments,
            total_input_tokens=self.token_counter.count(text),
            current_context_occupancy=self.token_counter.count(text),
            maximum_context_capacity=self._context_capacities.get(state.run_id, 128_000),
            wall_clock_milliseconds=elapsed,
            physical_completion_index=completion_index,
        )
        return self.runtime._append(
            state,
            EventKind.MODEL_CALL_RECORDED,
            "model call accounted",
            refs=(record.model_call_id,),
            actor=actor_id,
            model_call=record,
            mutates=False,
        )

    def _actor_failed(self, state: RuntimeState, actor_id: str, message: str) -> RuntimeState:
        return self.runtime._append(
            state,
            EventKind.ACTOR_STATUS_CHANGED,
            message[:500],
            actor=actor_id,
            actor_status=ActorStatusRecord(actor_id=actor_id, status=ActorLifecycleStatus.FAILED),
            mutates=False,
        )

    def _reject(self, state: RuntimeState, code: ErrorCode, message: str) -> Rejection:
        return self.runtime._reject(state, code, message)[1]
