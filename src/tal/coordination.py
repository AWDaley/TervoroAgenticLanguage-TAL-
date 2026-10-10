"""Strict v0.3 coordination contracts and deterministic topology compiler.

This module deliberately models coordination as data rather than behaviour.  A
compiled topology is an immutable, hashed contract consumed by the team runtime.
"""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .core import (
    COMPILED_TOPOLOGY_SCHEMA_ID,
    EpistemicStatus,
    Hash,
    Identifier,
    StrictModel,
    sha256,
)


class ArchitectureId(str, Enum):
    SINGLE = "single"
    INDEPENDENT = "independent"
    CENTRALIZED = "centralized"
    DECENTRALIZED = "decentralized"
    HYBRID = "hybrid"


class ProtocolMode(str, Enum):
    CONTROL = "control"
    TAL = "tal"


class ActorRole(str, Enum):
    SOLO = "solo"
    WORKER = "worker"
    ORCHESTRATOR = "orchestrator"
    PEER = "peer"
    SYNTHESIZER = "synthesizer"
    CONSENSUS = "consensus"


class PacketKind(str, Enum):
    ASSIGN_SUBGOAL = "assign_subgoal"
    SUBMIT_REPORT = "submit_report"
    REQUEST_INFORMATION = "request_information"
    CHALLENGE_CLAIM = "challenge_claim"
    REQUEST_PEER_CHECK = "request_peer_check"
    RECOMMEND_ACTION = "recommend_action"
    SUBMIT_VOTE = "submit_vote"
    PROPOSE_SUBGOAL_COMPLETION = "propose_subgoal_completion"


class PhaseType(str, Enum):
    INITIAL_REASONING = "initial_reasoning"
    ORCHESTRATOR_PLANNING = "orchestrator_planning"
    WORKER_EXECUTION = "worker_execution"
    WORKER_REPORTING = "worker_reporting"
    PEER_EXCHANGE = "peer_exchange"
    DEBATE = "debate"
    REVISION = "revision"
    SYNTHESIS = "synthesis"
    CONSENSUS = "consensus"
    FINALIZATION = "finalization"
    COMPLETION_PROPOSAL = "completion_proposal"


class ContextClass(str, Enum):
    SHARED = "shared"
    ROLE = "role"
    PRIVATE = "private"
    RECEIVED = "received"


class BarrierMode(str, Enum):
    NONE = "none"
    ALL_ELIGIBLE = "all_eligible"
    QUORUM = "quorum"


class ToolMode(str, Enum):
    NONE = "none"
    READ_ONLY = "read_only"
    DECLARED = "declared"


class AggregationType(str, Enum):
    SELF = "self"
    SYNTHESIS_ONLY = "synthesis_only"
    ORCHESTRATOR = "orchestrator"
    CONSENSUS = "consensus"


class ConsensusType(str, Enum):
    NONE = "none"
    MAJORITY = "majority"
    THRESHOLD = "threshold"


class FailureHandling(str, Enum):
    FAIL_EPISODE = "fail_episode"
    CONTINUE = "continue"
    QUORUM = "quorum"
    REASSIGN_EXISTING = "reassign_existing"


class ResourceKind(str, Enum):
    INPUT_TOKENS = "total_input_tokens"
    OUTPUT_TOKENS = "total_output_tokens"
    REASONING_TOKENS = "total_reasoning_tokens"
    MODEL_CALLS = "total_model_calls"
    TOOL_CALLS = "total_tool_calls"
    COORDINATION_PACKETS = "total_coordination_packets"
    COORDINATION_ROUNDS = "total_coordination_rounds"
    EXTERNAL_EFFECTS = "total_external_effects"
    WALL_CLOCK_MILLISECONDS = "total_wall_clock_milliseconds"
    PROVIDER_COST_MICROCENTS = "total_provider_cost_microcents"


class ResourceLimit(StrictModel):
    kind: ResourceKind
    maximum: int = Field(ge=0)


class ActorLifecycleStatus(str, Enum):
    READY = "ready"
    ACTIVE = "active"
    WAITING = "waiting"
    SUBGOAL_COMPLETED = "subgoal_completed"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ActorStatusRecord(StrictModel):
    actor_id: Identifier
    status: ActorLifecycleStatus = ActorLifecycleStatus.READY
    local_round: int = Field(default=0, ge=0)
    local_activations: int = Field(default=0, ge=0)


class PrivateHistoryRecord(StrictModel):
    """An actor-owned transcript fragment; never injected into another actor context."""

    actor_id: Identifier
    entry: str = Field(min_length=1, max_length=10_000)


class Claim(StrictModel):
    claim_id: Identifier
    source_actor_id: Identifier
    epistemic_status: EpistemicStatus
    evidence_refs: tuple[Identifier, ...] = ()
    subject_ref: Identifier
    field_id: Identifier
    value: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def packet_claims_cannot_be_authoritative(self) -> "Claim":
        if self.epistemic_status in {EpistemicStatus.OBSERVED, EpistemicStatus.VERIFIED}:
            raise ValueError("inter-agent claims may not assert observed or verified status")
        return self


class SubgoalStatus(str, Enum):
    ASSIGNED = "assigned"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class Subgoal(StrictModel):
    subgoal_id: Identifier
    parent_goal_ref: Identifier
    assigned_actor_id: Identifier
    desired_result: str = Field(min_length=1, max_length=1_000)
    required_evidence_refs: tuple[Identifier, ...] = ()
    permitted_tools: tuple[Identifier, ...] = ()
    dependencies: tuple[Identifier, ...] = ()
    local_budget: tuple[ResourceLimit, ...] = ()
    status: SubgoalStatus = SubgoalStatus.ASSIGNED
    completion_requirements: tuple[Identifier, ...] = ()


class Vote(StrictModel):
    candidate_ref: Identifier
    approve: bool


class PacketPayload(StrictModel):
    explanation: str = Field(default="", max_length=4_000)
    claims: tuple[Claim, ...] = ()
    subgoal: Subgoal | None = None
    vote: Vote | None = None
    recommended_operation: Identifier | None = None


class CoordinationPacket(StrictModel):
    schema_id: Literal["tal-coordination-packet-0.3.0"] = "tal-coordination-packet-0.3.0"
    packet_id: Identifier
    sender_actor_id: Identifier
    recipient_actor_ids: tuple[Identifier, ...] = Field(min_length=1)
    goal_ref: Identifier
    subgoal_ref: Identifier | None = None
    phase_ref: Identifier
    round_index: int = Field(ge=0)
    state_version: int = Field(ge=0)
    kind: PacketKind
    evidence_refs: tuple[Identifier, ...] = ()
    claim_refs: tuple[Identifier, ...] = ()
    unknown_refs: tuple[Identifier, ...] = ()
    conflict_refs: tuple[Identifier, ...] = ()
    payload: PacketPayload = PacketPayload()

    @model_validator(mode="after")
    def packet_kind_has_valid_operands(self) -> "CoordinationPacket":
        if self.kind is PacketKind.ASSIGN_SUBGOAL and self.payload.subgoal is None:
            raise ValueError("assign_subgoal packet requires a typed subgoal")
        if self.kind is PacketKind.SUBMIT_VOTE and self.payload.vote is None:
            raise ValueError("submit_vote packet requires a typed vote")
        if self.payload.subgoal is not None and self.payload.subgoal.assigned_actor_id not in (
            *self.recipient_actor_ids,
            self.sender_actor_id,
        ):
            raise ValueError("subgoal assignee must participate in packet delivery")
        return self


class ActorDefinition(StrictModel):
    actor_id: Identifier
    team_id: Identifier = "team:default"
    role: ActorRole
    allowed_recipients: tuple[Identifier, ...] = ()
    visible_context_classes: tuple[ContextClass, ...] = (
        ContextClass.SHARED,
        ContextClass.ROLE,
        ContextClass.PRIVATE,
        ContextClass.RECEIVED,
    )
    tool_capabilities: tuple[Identifier, ...] = ()
    action_capabilities: tuple[Identifier, ...] = ()
    subgoal_authority: bool = False


class CoordinationStageDefinition(StrictModel):
    stage_id: Identifier
    role: ActorRole
    model_calling: bool = False
    visible_context_classes: tuple[ContextClass, ...] = (
        ContextClass.SHARED,
        ContextClass.ROLE,
        ContextClass.RECEIVED,
    )


class CommunicationEdge(StrictModel):
    sender_id: Identifier
    recipient_id: Identifier
    permitted_packet_kinds: tuple[PacketKind, ...] = Field(min_length=1)
    phase_refs: tuple[Identifier, ...] = Field(min_length=1)


class PhaseDefinition(StrictModel):
    phase_id: Identifier
    phase_type: PhaseType
    eligible_entity_ids: tuple[Identifier, ...] = Field(min_length=1)
    max_activations_per_entity: int = Field(ge=0)
    max_rounds: int = Field(default=1, ge=1)
    permitted_packet_kinds: tuple[PacketKind, ...] = ()
    tool_mode: ToolMode = ToolMode.DECLARED
    barrier: BarrierMode = BarrierMode.ALL_ELIGIBLE
    quorum: int | None = Field(default=None, ge=1)
    next_phase_ref: Identifier | None = None

    @model_validator(mode="after")
    def quorum_requires_quorum_barrier(self) -> "PhaseDefinition":
        if self.barrier is BarrierMode.QUORUM and self.quorum is None:
            raise ValueError("quorum barrier requires quorum")
        if self.barrier is not BarrierMode.QUORUM and self.quorum is not None:
            raise ValueError("quorum is only valid for a quorum barrier")
        return self


class AggregationPolicy(StrictModel):
    aggregation_type: AggregationType
    feedback_to_workers: bool = False
    worker_cross_validation: bool = False
    peer_debate: bool = False
    followup_worker_rounds: int = Field(default=0, ge=0)
    model_calling: bool = False


class ConsensusPolicy(StrictModel):
    consensus_type: ConsensusType = ConsensusType.NONE
    threshold_basis_points: int = Field(default=0, ge=0, le=10_000)
    threshold_gates_selection: bool = True
    tie_break_entity_order: bool = True

    @model_validator(mode="after")
    def policy_is_coherent(self) -> "ConsensusPolicy":
        if self.consensus_type is ConsensusType.NONE and self.threshold_basis_points != 0:
            raise ValueError("no-consensus policy cannot set a threshold")
        if self.consensus_type is ConsensusType.THRESHOLD and self.threshold_basis_points == 0:
            raise ValueError("threshold consensus requires a positive threshold")
        return self


class ActorBudget(StrictModel):
    actor_id: Identifier
    limits: tuple[ResourceLimit, ...] = ()

    @model_validator(mode="after")
    def no_duplicate_resources(self) -> "ActorBudget":
        if len({item.kind for item in self.limits}) != len(self.limits):
            raise ValueError("actor budget may not repeat a resource kind")
        return self


class GlobalBudget(StrictModel):
    limits: tuple[ResourceLimit, ...] = ()
    permit_parallel_reads: bool = True

    @model_validator(mode="after")
    def no_duplicate_resources(self) -> "GlobalBudget":
        if len({item.kind for item in self.limits}) != len(self.limits):
            raise ValueError("global budget may not repeat a resource kind")
        return self


class VisibilityPolicy(StrictModel):
    shared_context_classes: tuple[ContextClass, ...] = (ContextClass.SHARED,)
    private_histories_isolated: Literal[True] = True
    received_packets_only: Literal[True] = True


class ToolPolicy(StrictModel):
    registered_tool_ids: tuple[Identifier, ...] = ()
    registered_action_ids: tuple[Identifier, ...] = ()


class ActionPolicy(StrictModel):
    state_changes_serialized: Literal[True] = True
    max_external_actions_per_activation: int = Field(default=1, ge=0, le=1)


class FailurePolicy(StrictModel):
    actor_failure_handling: FailureHandling = FailureHandling.FAIL_EPISODE
    allow_existing_actor_reassignment: bool = False


class TerminationPolicy(StrictModel):
    global_completion_verifier_required: Literal[True] = True
    allow_subgoal_completion: bool = True


class ResearchMode(StrictModel):
    dynamic_agent_creation: Literal[False] = False
    adaptive_topology: Literal[False] = False
    recursive_delegation: Literal[False] = False
    heterogeneous_models: Literal[False] = False
    learned_routing: Literal[False] = False
    cross_episode_memory: Literal[False] = False
    model_created_tools: Literal[False] = False
    model_created_capabilities: Literal[False] = False
    runtime_policy_mutation: Literal[False] = False


class CoordinationSpec(StrictModel):
    schema_id: Literal["tal-coordination-spec-0.3.0"] = "tal-coordination-spec-0.3.0"
    profile_id: Identifier
    architecture: ArchitectureId
    actors: tuple[ActorDefinition, ...] = Field(min_length=1)
    coordination_stages: tuple[CoordinationStageDefinition, ...] = ()
    communication_edges: tuple[CommunicationEdge, ...] = ()
    phases: tuple[PhaseDefinition, ...] = Field(min_length=1)
    visibility_policy: VisibilityPolicy = VisibilityPolicy()
    aggregation_policy: AggregationPolicy
    consensus_policy: ConsensusPolicy = ConsensusPolicy()
    tool_policy: ToolPolicy = ToolPolicy()
    action_policy: ActionPolicy = ActionPolicy()
    local_budgets: tuple[ActorBudget, ...] = ()
    global_budget: GlobalBudget = GlobalBudget()
    failure_policy: FailurePolicy = FailurePolicy()
    termination_policy: TerminationPolicy = TerminationPolicy()
    research_mode: ResearchMode = ResearchMode()

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "CoordinationSpec":
        entity_ids = [item.actor_id for item in self.actors] + [
            item.stage_id for item in self.coordination_stages
        ]
        phase_ids = [item.phase_id for item in self.phases]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("actor and coordination-stage identifiers must be unique")
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("phase identifiers must be unique")
        if len({item.actor_id for item in self.local_budgets}) != len(self.local_budgets):
            raise ValueError("local budget may be declared once per actor")
        return self


class RuntimeInventory(StrictModel):
    registered_tool_ids: tuple[Identifier, ...] = ()
    registered_action_ids: tuple[Identifier, ...] = ()


class CompiledTopologyState(str, Enum):
    FROZEN = "frozen"


class CompiledExecutionTopology(StrictModel):
    schema_id: Literal["tal-compiled-topology-0.3.0"] = "tal-compiled-topology-0.3.0"
    state: Literal[CompiledTopologyState.FROZEN] = CompiledTopologyState.FROZEN
    specification: CoordinationSpec
    topology_hash: Hash
    entity_order: tuple[Identifier, ...]
    phase_order: tuple[Identifier, ...]
    source_ref: str | None = Field(default=None, max_length=500)


class TopologyCompiler:
    """Pure compiler for a profile/specification and declared runtime inventory."""

    def compile(
        self,
        specification: CoordinationSpec,
        inventory: RuntimeInventory | None = None,
        *,
        source_ref: str | None = None,
    ) -> CompiledExecutionTopology:
        runtime_inventory = inventory or RuntimeInventory()
        self._validate_references(specification)
        self._validate_inventory(specification, runtime_inventory)
        self._validate_architecture(specification)
        self._validate_authority(specification)
        entity_order = tuple(
            [item.actor_id for item in specification.actors]
            + [item.stage_id for item in specification.coordination_stages]
        )
        phase_order = tuple(item.phase_id for item in specification.phases)
        preimage = {
            "schema_id": COMPILED_TOPOLOGY_SCHEMA_ID,
            "state": CompiledTopologyState.FROZEN.value,
            "specification": specification,
            "entity_order": entity_order,
            "phase_order": phase_order,
            "source_ref": source_ref,
        }
        return CompiledExecutionTopology(
            specification=specification,
            topology_hash=sha256(preimage),
            entity_order=entity_order,
            phase_order=phase_order,
            source_ref=source_ref,
        )

    def verify(self, topology: CompiledExecutionTopology) -> bool:
        """Recompile immutable data and prove the stored digest still binds it."""
        expected = self.compile(
            topology.specification,
            RuntimeInventory(
                registered_tool_ids=topology.specification.tool_policy.registered_tool_ids,
                registered_action_ids=topology.specification.tool_policy.registered_action_ids,
            ),
            source_ref=topology.source_ref,
        )
        return expected.topology_hash == topology.topology_hash

    def _validate_references(self, specification: CoordinationSpec) -> None:
        actor_ids = {item.actor_id for item in specification.actors}
        stage_ids = {item.stage_id for item in specification.coordination_stages}
        entity_ids = actor_ids | stage_ids
        phase_ids = {item.phase_id for item in specification.phases}
        if any(
            recipient not in entity_ids
            for actor in specification.actors
            for recipient in actor.allowed_recipients
        ):
            raise ValueError("actor recipient is not declared")
        for edge in specification.communication_edges:
            if edge.sender_id not in entity_ids or edge.recipient_id not in entity_ids:
                raise ValueError("communication edge references undeclared entity")
            if not set(edge.phase_refs) <= phase_ids:
                raise ValueError("communication edge references undeclared phase")
        for phase in specification.phases:
            if not set(phase.eligible_entity_ids) <= entity_ids:
                raise ValueError("phase references undeclared entity")
            if phase.next_phase_ref is not None and phase.next_phase_ref not in phase_ids:
                raise ValueError("phase next reference is undeclared")
        if specification.phases[-1].next_phase_ref is not None:
            raise ValueError("last phase must not point to a next phase")
        for index, phase in enumerate(specification.phases[:-1]):
            if phase.next_phase_ref is None:
                raise ValueError("non-terminal phase requires next phase")
            if phase.next_phase_ref != specification.phases[index + 1].phase_id:
                raise ValueError("phases must transition in declared canonical order")
        for budget in specification.local_budgets:
            if budget.actor_id not in actor_ids:
                raise ValueError("local budget references undeclared actor")

    def _validate_inventory(
        self, specification: CoordinationSpec, inventory: RuntimeInventory
    ) -> None:
        if specification.tool_policy.registered_tool_ids and not set(
            specification.tool_policy.registered_tool_ids
        ) <= set(inventory.registered_tool_ids):
            raise ValueError("topology requires unregistered tool")
        if specification.tool_policy.registered_action_ids and not set(
            specification.tool_policy.registered_action_ids
        ) <= set(inventory.registered_action_ids):
            raise ValueError("topology requires unregistered action")
        for actor in specification.actors:
            if actor.tool_capabilities and not set(actor.tool_capabilities) <= set(
                specification.tool_policy.registered_tool_ids
            ):
                raise ValueError("actor has undeclared tool capability")
            if actor.action_capabilities and not set(actor.action_capabilities) <= set(
                specification.tool_policy.registered_action_ids
            ):
                raise ValueError("actor has undeclared action capability")

    def _validate_architecture(self, specification: CoordinationSpec) -> None:
        actors = specification.actors
        roles = [actor.role for actor in actors]
        edges = specification.communication_edges
        worker_ids = {actor.actor_id for actor in actors if actor.role is ActorRole.WORKER}
        peer_ids = {actor.actor_id for actor in actors if actor.role is ActorRole.PEER}
        orchestrators = [actor for actor in actors if actor.role is ActorRole.ORCHESTRATOR]
        if specification.architecture is ArchitectureId.SINGLE:
            if len(actors) != 1 or specification.communication_edges:
                raise ValueError("single topology requires one actor and no communication")
            if specification.aggregation_policy.aggregation_type is not AggregationType.SELF:
                raise ValueError("single topology requires self aggregation")
        elif specification.architecture is ArchitectureId.INDEPENDENT:
            if len(worker_ids) < 2 or any(
                edge.sender_id in worker_ids and edge.recipient_id in worker_ids for edge in edges
            ):
                raise ValueError("independent topology requires isolated workers")
            if (
                specification.aggregation_policy.aggregation_type
                is not AggregationType.SYNTHESIS_ONLY
            ):
                raise ValueError("independent topology requires synthesis-only aggregation")
            if specification.aggregation_policy.feedback_to_workers:
                raise ValueError("independent topology cannot feed synthesis to workers")
        elif specification.architecture is ArchitectureId.CENTRALIZED:
            if len(orchestrators) != 1 or not worker_ids:
                raise ValueError("centralized topology requires one orchestrator and workers")
            if any(
                edge.sender_id in worker_ids and edge.recipient_id in worker_ids for edge in edges
            ):
                raise ValueError("centralized workers may not communicate directly")
            if (
                specification.aggregation_policy.aggregation_type
                is not AggregationType.ORCHESTRATOR
            ):
                raise ValueError("centralized topology requires orchestrator aggregation")
        elif specification.architecture is ArchitectureId.DECENTRALIZED:
            if orchestrators or len(peer_ids) < 2:
                raise ValueError("decentralized topology requires peers and no orchestrator")
            if specification.aggregation_policy.aggregation_type is not AggregationType.CONSENSUS:
                raise ValueError("decentralized topology requires consensus aggregation")
            if specification.consensus_policy.consensus_type is ConsensusType.NONE:
                raise ValueError("decentralized topology requires a consensus policy")
        elif specification.architecture is ArchitectureId.HYBRID:
            if len(orchestrators) != 1 or len(worker_ids) < 2:
                raise ValueError("hybrid topology requires one orchestrator and workers")
            if not any(
                edge.sender_id in worker_ids and edge.recipient_id in worker_ids for edge in edges
            ):
                raise ValueError("hybrid topology requires worker peer communication")
            if (
                specification.aggregation_policy.aggregation_type
                is not AggregationType.ORCHESTRATOR
            ):
                raise ValueError("hybrid topology requires orchestrator aggregation")
        if not roles:
            raise ValueError("topology requires actors")

    def _validate_authority(self, specification: CoordinationSpec) -> None:
        """Communication may form cycles, but never carries permission-grant semantics."""
        edge_recipients: dict[str, set[str]] = defaultdict(set)
        for edge in specification.communication_edges:
            edge_recipients[edge.sender_id].add(edge.recipient_id)
        for actor in specification.actors:
            if not set(actor.allowed_recipients) <= edge_recipients[actor.actor_id]:
                raise ValueError("actor recipient lacks a corresponding communication edge")
        permitted_kinds = {
            kind
            for edge in specification.communication_edges
            for kind in edge.permitted_packet_kinds
        }
        if PacketKind.ASSIGN_SUBGOAL in permitted_kinds and not any(
            actor.subgoal_authority for actor in specification.actors
        ):
            raise ValueError("subgoal packet requires a statically authorized actor")
