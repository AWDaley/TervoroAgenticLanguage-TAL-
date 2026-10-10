"""Typed append-only event ledger records and pure state projection."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import Field, field_validator

from .coordination import (
    ActorStatusRecord,
    Claim,
    CompiledExecutionTopology,
    CoordinationPacket,
    PrivateHistoryRecord,
    ProtocolMode,
    Subgoal,
)
from .core import Hash, Rejection, RuntimePhase, Source, StrictModel, TrustClass, sha256
from .invoice import TALManifest
from .telemetry import ModelCallRecord


class EventKind(str, Enum):
    RUN_CREATED = "run_created"
    MANIFEST_CREATED = "manifest_created"
    MANIFEST_VALIDATED = "manifest_validated"
    MANIFEST_PATCHED = "manifest_patched"
    CAPABILITIES_COMPUTED = "capabilities_computed"
    CAPABILITY_DENIED = "capability_denied"
    CONTEXT_BUILT = "context_built"
    PROPOSAL_RECEIVED = "proposal_received"
    PROPOSAL_ACCEPTED = "proposal_accepted"
    PROPOSAL_REJECTED = "proposal_rejected"
    EFFECT_INTENT_RECORDED = "effect_intent_recorded"
    TOOL_STARTED = "tool_started"
    TOOL_SUCCEEDED = "tool_succeeded"
    TOOL_FAILED = "tool_failed"
    TOOL_TIMED_OUT = "tool_timed_out"
    EFFECT_RECONCILED = "effect_reconciled"
    OBSERVATION_RECORDED = "observation_recorded"
    VERIFICATION_RECORDED = "verification_recorded"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_STALE = "approval_stale"
    BUDGET_CONSUMED = "budget_consumed"
    PHASE_CHANGED = "phase_changed"
    COMPLETION_VERIFIED = "completion_verified"
    GOAL_COMPLETED = "goal_completed"
    GOAL_ESCALATED = "goal_escalated"
    GOAL_CANCELLED = "goal_cancelled"
    GOAL_FAILED = "goal_failed"
    TOPOLOGY_COMPILED = "topology_compiled"
    COORDINATION_PHASE_STARTED = "coordination_phase_started"
    COORDINATION_BARRIER_CLOSED = "coordination_barrier_closed"
    ACTOR_ACTIVATED = "actor_activated"
    ACTOR_STATUS_CHANGED = "actor_status_changed"
    COORDINATION_PACKET_RECEIVED = "coordination_packet_received"
    COORDINATION_PACKET_DELIVERED = "coordination_packet_delivered"
    COORDINATION_PACKET_REJECTED = "coordination_packet_rejected"
    CLAIM_RECORDED = "claim_recorded"
    SUBGOAL_ASSIGNED = "subgoal_assigned"
    SUBGOAL_COMPLETED = "subgoal_completed"
    MODEL_CALL_RECORDED = "model_call_recorded"
    PRIVATE_HISTORY_RECORDED = "private_history_recorded"


class Observation(StrictModel):
    id: str
    observation_type: str
    subject_ref: str
    status: str
    trust: TrustClass
    detail: str = Field(default="", max_length=500)
    evidence_refs: tuple[str, ...] = ()
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)


class EvidenceReceipt(StrictModel):
    """Runtime receipt for externally supplied material; it is not an assertion of truth."""

    receipt_id: str
    source: Source
    submitted_by: str
    content_hash: Hash
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def received_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return value.astimezone(UTC)


class InformationSubmission(StrictModel):
    """Closed information input. The runtime, never the caller, assigns observation trust."""

    submission_id: str
    receipt: EvidenceReceipt
    subject_ref: str
    observation_type: str
    status: str
    detail: str = Field(default="", max_length=500)


class ApprovalRequest(StrictModel):
    """A runtime-issued request bound to one manifest, capability, and target."""

    request_id: str
    action_id: str
    target_ref: str
    manifest_hash: Hash
    manifest_version: int = Field(ge=1)
    requested_by: str
    approver_role: str
    evidence_refs: tuple[str, ...] = ()
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def requested_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requested_at must include a timezone")
        return value.astimezone(UTC)


class ApprovalDecision(StrictModel):
    """Authenticated response to one outstanding approval request."""

    decision_id: str
    request_id: str
    approver_id: str
    approver_role: str
    decision: str = Field(pattern="^(granted|denied)$")
    reason: str = Field(default="", max_length=500)
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def decided_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decided_at must include a timezone")
        return value.astimezone(UTC)


class ApprovalGrant(StrictModel):
    grant_id: str
    request_id: str
    action_id: str
    target_ref: str
    manifest_hash: Hash
    manifest_version: int = Field(ge=1)
    approver_id: str
    granted_at: datetime

    @field_validator("granted_at")
    @classmethod
    def granted_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("granted_at must include a timezone")
        return value.astimezone(UTC)


class EffectRecord(StrictModel):
    """Durable lifecycle record for an idempotent tool effect."""

    idempotency_key: Hash
    tool_id: str
    target_ref: str
    arguments_hash: Hash
    status: str = Field(pattern="^(intended|uncertain|succeeded|failed|reconciled)$")


class Event(StrictModel):
    schema_id: str = "tal-event-0.3.0"
    event_id: str
    event_type: EventKind
    run_id: str
    goal_id: str
    manifest_hash: Hash
    manifest_version: int = Field(ge=1)
    ledger_sequence: int = Field(ge=1)
    state_version_before: int = Field(ge=0)
    state_version_after: int = Field(ge=0)
    previous_event_hash: Hash | None = None
    event_hash: Hash | None = None
    occurred_at: datetime
    actor: str
    message: str = Field(default="", max_length=500)
    refs: tuple[str, ...] = ()
    rejection: Rejection | None = None
    observation: Observation | None = None
    evidence_receipt: EvidenceReceipt | None = None
    manifest: TALManifest | None = None
    phase: RuntimePhase | None = None
    budget_kind: str | None = None
    budget_amount: int | None = Field(default=None, ge=0)
    idempotency_key: Hash | None = None
    topology: CompiledExecutionTopology | None = None
    protocol_mode: ProtocolMode | None = None
    coordination_phase_ref: str | None = None
    coordination_round: int | None = Field(default=None, ge=0)
    actor_id: str | None = None
    actor_status: ActorStatusRecord | None = None
    packet: CoordinationPacket | None = None
    claim: Claim | None = None
    subgoal: Subgoal | None = None
    model_call: ModelCallRecord | None = None
    private_history: PrivateHistoryRecord | None = None
    approval_request: ApprovalRequest | None = None
    approval_decision: ApprovalDecision | None = None
    approval_grant: ApprovalGrant | None = None
    effect_record: EffectRecord | None = None
    initial_manifest: TALManifest | None = None
    initial_effective_capabilities: tuple["CapabilityGrant", ...] = ()
    initial_denied_capabilities: tuple["CapabilityDenial", ...] = ()
    initial_budgets: tuple["BudgetLimit", ...] = ()

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)


class CapabilityGrant(StrictModel):
    action_id: str
    target_ref: str
    amount_limit: str | None = None
    approval_required: bool = False


class CapabilityDenial(StrictModel):
    action_id: str
    target_ref: str
    reason_code: str


class BudgetLimit(StrictModel):
    kind: str
    maximum: int = Field(ge=0)


class RuntimeState(StrictModel):
    run_id: str
    manifest: TALManifest
    phase: RuntimePhase
    state_version: int = Field(ge=0)
    ledger_sequence: int = Field(default=0, ge=0)
    ledger_head_hash: Hash | None = None
    effective_capabilities: tuple[CapabilityGrant, ...] = ()
    denied_capabilities: tuple[CapabilityDenial, ...] = ()
    budgets: tuple[BudgetLimit, ...] = ()
    budget_used: tuple[BudgetLimit, ...] = ()
    observations: tuple[Observation, ...] = ()
    evidence_receipts: tuple[EvidenceReceipt, ...] = ()
    pending_approval_requests: tuple[ApprovalRequest, ...] = ()
    approval_grants: tuple[ApprovalGrant, ...] = ()
    pending_effect_key: Hash | None = None
    effect_records: tuple[EffectRecord, ...] = ()
    terminal_outcome: str | None = None
    topology: CompiledExecutionTopology | None = None
    protocol_mode: ProtocolMode | None = None
    coordination_phase_ref: str | None = None
    coordination_round: int = Field(default=0, ge=0)
    actor_statuses: tuple[ActorStatusRecord, ...] = ()
    coordination_packets: tuple[CoordinationPacket, ...] = ()
    claims: tuple[Claim, ...] = ()
    subgoals: tuple[Subgoal, ...] = ()
    model_calls: tuple[ModelCallRecord, ...] = ()
    private_histories: tuple[PrivateHistoryRecord, ...] = ()


def _replace_budget(
    items: tuple[BudgetLimit, ...], kind: str, amount: int
) -> tuple[BudgetLimit, ...]:
    current = {item.kind: item.maximum for item in items}
    current[kind] = amount
    return tuple(BudgetLimit(kind=name, maximum=value) for name, value in sorted(current.items()))


AUDIT_EVENT_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.RUN_CREATED,
        EventKind.MANIFEST_VALIDATED,
        EventKind.CAPABILITIES_COMPUTED,
        EventKind.CAPABILITY_DENIED,
        EventKind.CONTEXT_BUILT,
        EventKind.PROPOSAL_RECEIVED,
        EventKind.PROPOSAL_ACCEPTED,
        EventKind.PROPOSAL_REJECTED,
        EventKind.TOOL_STARTED,
        EventKind.COMPLETION_VERIFIED,
        EventKind.COORDINATION_BARRIER_CLOSED,
        EventKind.MODEL_CALL_RECORDED,
        EventKind.ACTOR_ACTIVATED,
    }
)


def event_projects_state(event_type: EventKind) -> bool:
    """Return whether an event changes the model-visible decision projection."""
    return event_type not in AUDIT_EVENT_KINDS


def event_hash(event: Event) -> Hash:
    """Hash an event envelope excluding its self-referential digest."""
    payload = event.model_dump(mode="json", exclude={"event_hash"}, exclude_none=True)
    return sha256(payload)


def seal_event(event: Event, previous_event_hash: Hash | None) -> Event:
    """Bind a draft event to its predecessor and canonical digest."""
    unsigned = event.model_copy(
        update={"previous_event_hash": previous_event_hash, "event_hash": None}
    )
    return unsigned.model_copy(update={"event_hash": event_hash(unsigned)})


def reduce_event(state: RuntimeState, event: Event) -> RuntimeState:
    """Pure, deterministic projection. Non-projecting events leave state unchanged."""
    if event.ledger_sequence != state.ledger_sequence + 1:
        raise ValueError("event ledger-sequence mismatch")
    if event.previous_event_hash != state.ledger_head_hash:
        raise ValueError("event hash-chain mismatch")
    if event.event_hash is None or event.event_hash != event_hash(event):
        raise ValueError("event digest is invalid")
    if event.run_id != state.run_id or event.goal_id != state.manifest.goal.goal_id:
        raise ValueError("event belongs to a different run or goal")
    if event.manifest_hash != state.manifest.integrity.manifest_hash:
        raise ValueError("event manifest hash does not match current state")
    if event.state_version_before != state.state_version:
        raise ValueError("event state-version mismatch")
    expected_after = state.state_version + int(event_projects_state(event.event_type))
    if event.state_version_after != expected_after:
        raise ValueError("event state-version delta does not match event type")
    next_state = state
    if event.event_type is EventKind.PHASE_CHANGED:
        if event.phase is None:
            raise ValueError("phase event requires phase")
        next_state = next_state.model_copy(update={"phase": event.phase})
    elif event.event_type is EventKind.TOPOLOGY_COMPILED:
        if event.topology is None or event.protocol_mode is None:
            raise ValueError("topology event requires topology and protocol mode")
        next_state = next_state.model_copy(
            update={"topology": event.topology, "protocol_mode": event.protocol_mode}
        )
    elif event.event_type is EventKind.COORDINATION_PHASE_STARTED:
        if event.coordination_phase_ref is None or event.coordination_round is None:
            raise ValueError("coordination phase event requires phase and round")
        next_state = next_state.model_copy(
            update={
                "coordination_phase_ref": event.coordination_phase_ref,
                "coordination_round": event.coordination_round,
            }
        )
    elif event.event_type is EventKind.ACTOR_STATUS_CHANGED:
        if event.actor_status is None:
            raise ValueError("actor status event requires actor status")
        statuses = {item.actor_id: item for item in next_state.actor_statuses}
        statuses[event.actor_status.actor_id] = event.actor_status
        next_state = next_state.model_copy(
            update={"actor_statuses": tuple(statuses[key] for key in sorted(statuses))}
        )
    elif event.event_type is EventKind.COORDINATION_PACKET_DELIVERED:
        if event.packet is None:
            raise ValueError("packet delivery event requires packet")
        if any(
            item.packet_id == event.packet.packet_id for item in next_state.coordination_packets
        ):
            raise ValueError("coordination packet may be delivered once")
        next_state = next_state.model_copy(
            update={"coordination_packets": (*next_state.coordination_packets, event.packet)}
        )
    elif event.event_type is EventKind.CLAIM_RECORDED:
        if event.claim is None:
            raise ValueError("claim event requires claim")
        if any(item.claim_id == event.claim.claim_id for item in next_state.claims):
            raise ValueError("claim may be recorded once")
        next_state = next_state.model_copy(update={"claims": (*next_state.claims, event.claim)})
    elif event.event_type in {EventKind.SUBGOAL_ASSIGNED, EventKind.SUBGOAL_COMPLETED}:
        if event.subgoal is None:
            raise ValueError("subgoal event requires subgoal")
        subgoals = {item.subgoal_id: item for item in next_state.subgoals}
        subgoals[event.subgoal.subgoal_id] = event.subgoal
        next_state = next_state.model_copy(
            update={"subgoals": tuple(subgoals[key] for key in sorted(subgoals))}
        )
    elif event.event_type is EventKind.MODEL_CALL_RECORDED:
        if event.model_call is None:
            raise ValueError("model call event requires model call accounting")
        if any(
            item.model_call_id == event.model_call.model_call_id for item in next_state.model_calls
        ):
            raise ValueError("model call may be recorded once")
        next_state = next_state.model_copy(
            update={"model_calls": (*next_state.model_calls, event.model_call)}
        )
    elif event.event_type is EventKind.PRIVATE_HISTORY_RECORDED:
        if event.private_history is None:
            raise ValueError("private history event requires a record")
        next_state = next_state.model_copy(
            update={"private_histories": (*next_state.private_histories, event.private_history)}
        )
    elif event.event_type is EventKind.BUDGET_CONSUMED:
        if event.budget_kind is None or event.budget_amount is None:
            raise ValueError("budget event requires kind and amount")
        used = {item.kind: item.maximum for item in next_state.budget_used}
        next_state = next_state.model_copy(
            update={
                "budget_used": _replace_budget(
                    next_state.budget_used,
                    event.budget_kind,
                    used.get(event.budget_kind, 0) + event.budget_amount,
                )
            }
        )
    elif event.event_type in {
        EventKind.EFFECT_INTENT_RECORDED,
        EventKind.TOOL_TIMED_OUT,
        EventKind.TOOL_SUCCEEDED,
        EventKind.TOOL_FAILED,
        EventKind.EFFECT_RECONCILED,
    }:
        if event.effect_record is None:
            if event.idempotency_key is None:
                return next_state.model_copy(
                    update={
                        "state_version": event.state_version_after,
                        "ledger_sequence": event.ledger_sequence,
                        "ledger_head_hash": event.event_hash,
                    }
                )
            raise ValueError("effect lifecycle event requires a typed effect record")
        records = {item.idempotency_key: item for item in next_state.effect_records}
        prior = records.get(event.effect_record.idempotency_key)
        if event.event_type is EventKind.EFFECT_INTENT_RECORDED and prior is not None:
            raise ValueError("effect intent may be recorded once")
        if event.event_type is not EventKind.EFFECT_INTENT_RECORDED and prior is None:
            raise ValueError("effect lifecycle transition requires an intent")
        records[event.effect_record.idempotency_key] = event.effect_record
        next_state = next_state.model_copy(
            update={
                "effect_records": tuple(records[key] for key in sorted(records)),
                "pending_effect_key": (
                    event.effect_record.idempotency_key
                    if event.effect_record.status in {"intended", "uncertain"}
                    else None
                ),
            }
        )
    elif event.event_type is EventKind.OBSERVATION_RECORDED:
        if event.observation is None:
            raise ValueError("observation event requires observation")
        receipts = next_state.evidence_receipts
        if event.evidence_receipt is not None:
            if any(item.receipt_id == event.evidence_receipt.receipt_id for item in receipts):
                raise ValueError("evidence receipt may be recorded once")
            receipts = (*receipts, event.evidence_receipt)
        next_state = next_state.model_copy(
            update={
                "observations": (*next_state.observations, event.observation),
                "evidence_receipts": receipts,
            }
        )
    elif event.event_type is EventKind.APPROVAL_REQUESTED:
        if event.approval_request is None:
            raise ValueError("approval request event requires typed request")
        if any(
            item.request_id == event.approval_request.request_id
            for item in next_state.pending_approval_requests
        ):
            raise ValueError("approval request may be recorded once")
        next_state = next_state.model_copy(
            update={
                "pending_approval_requests": (
                    *next_state.pending_approval_requests,
                    event.approval_request,
                )
            }
        )
    elif event.event_type in {EventKind.APPROVAL_GRANTED, EventKind.APPROVAL_DENIED}:
        if event.approval_decision is None:
            raise ValueError("approval decision event requires typed decision")
        pending = {
            item.request_id: item for item in next_state.pending_approval_requests
        }
        request = pending.pop(event.approval_decision.request_id, None)
        if request is None:
            raise ValueError("approval decision has no pending request")
        if event.approval_decision.approver_role != request.approver_role:
            raise ValueError("approval decision role does not match request")
        update: dict[str, object] = {
            "pending_approval_requests": tuple(pending[key] for key in sorted(pending))
        }
        if event.event_type is EventKind.APPROVAL_GRANTED:
            if event.approval_decision.decision != "granted" or event.approval_grant is None:
                raise ValueError("approval grant requires a granting decision and typed grant")
            grant = event.approval_grant
            if (
                grant.request_id != request.request_id
                or grant.action_id != request.action_id
                or grant.target_ref != request.target_ref
                or grant.manifest_hash != request.manifest_hash
                or grant.manifest_version != request.manifest_version
            ):
                raise ValueError("approval grant is not bound to its request")
            update["approval_grants"] = (*next_state.approval_grants, grant)
        elif event.approval_decision.decision != "denied":
            raise ValueError("approval denial requires a denying decision")
        next_state = next_state.model_copy(update=update)
    elif event.event_type is EventKind.MANIFEST_PATCHED:
        if event.manifest is None:
            raise ValueError("manifest patch event requires manifest")
        next_state = next_state.model_copy(
            update={
                "manifest": event.manifest,
                "approval_grants": (),
                "pending_approval_requests": (),
                "pending_effect_key": None,
            }
        )
    elif event.event_type in {
        EventKind.GOAL_COMPLETED,
        EventKind.GOAL_ESCALATED,
        EventKind.GOAL_CANCELLED,
        EventKind.GOAL_FAILED,
    }:
        if event.phase is None:
            raise ValueError("terminal event requires phase")
        next_state = next_state.model_copy(
            update={"phase": event.phase, "terminal_outcome": event.message}
        )
    return next_state.model_copy(
        update={
            "state_version": event.state_version_after,
            "ledger_sequence": event.ledger_sequence,
            "ledger_head_hash": event.event_hash,
        }
    )
