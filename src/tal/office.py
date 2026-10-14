"""Experimental TAL office/calendar *admission* domain (no tool execution).

This module belongs to the trusted TAL process, not a model runtime. Admission
never creates an execution token and never performs a provider effect. It can
be used as an additional fail-closed condition on the existing SAFE-AGENT
single-effect gate while the invoice-specific TALRuntime is generalized.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .core import Hash, Identifier, StrictModel, sha256


class OfficeAdmissionStatus(StrEnum):
    DENIED = "DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ELIGIBLE = "ELIGIBLE"  # Never an execution capability or signed bearer permit.


def _aware(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("office timestamps must include UTC offset")
    return value


class OfficeCalendarGrantDraft(StrictModel):
    """Trusted compiler input: one task, one operation, two opaque handles."""

    schema_version: Literal["tal.office.calendar_grant.v1"] = "tal.office.calendar_grant.v1"
    organization_id: Identifier
    task_id: Identifier
    initiator_id: Identifier
    source_manifest_hash: Hash
    source_manifest_version: int = Field(ge=1)
    calendar_ref: Identifier
    customer_ref: Identifier
    template_id: Identifier
    operation: Literal["calendar.create_event"] = "calendar.create_event"
    allowed_start: datetime
    allowed_end: datetime
    time_zone: str = Field(min_length=1, max_length=128)
    max_duration_minutes: int = Field(ge=5, le=60)
    max_cycles: int = Field(ge=1, le=100)
    max_external_effects: int = Field(ge=0, le=10)
    requires_approval: bool
    task_deadline: datetime

    @field_validator("allowed_start", "allowed_end", "task_deadline")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown IANA time zone") from exc
        return value

    @model_validator(mode="after")
    def valid_grant(self) -> OfficeCalendarGrantDraft:
        if self.allowed_start >= self.allowed_end:
            raise ValueError("invalid calendar grant window")
        return self


class SealedOfficeCalendarGrant(OfficeCalendarGrantDraft):
    """Canonical hash detects tampering; it is not a signature or bearer authorization."""

    grant_hash: Hash


def compile_office_calendar_grant(draft: OfficeCalendarGrantDraft) -> SealedOfficeCalendarGrant:
    """Deterministic and model-free. Only a trusted service may construct *draft*."""
    data = draft.model_dump(exclude_none=True)
    return SealedOfficeCalendarGrant.model_validate({**data, "grant_hash": sha256(draft)})


def verify_office_calendar_grant(grant: SealedOfficeCalendarGrant) -> bool:
    payload = grant.model_dump(mode="json", exclude={"grant_hash"})
    return grant.grant_hash == sha256(payload)


class OfficeCalendarProposal(StrictModel):
    """Exact typed operation; all refs are task-scoped opaque handles."""

    schema_version: Literal["tal.office.calendar_proposal.v1"] = "tal.office.calendar_proposal.v1"
    organization_id: Identifier
    task_id: Identifier
    initiator_id: Identifier
    source_manifest_hash: Hash
    source_proposal_hash: Hash
    calendar_ref: Identifier
    customer_ref: Identifier
    template_id: Identifier
    operation: Literal["calendar.create_event"] = "calendar.create_event"
    start_at: datetime
    end_at: datetime
    time_zone: str
    projection_rev: Identifier
    simulation_ref: Identifier

    @field_validator("start_at", "end_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def valid_interval(self) -> OfficeCalendarProposal:
        if self.start_at >= self.end_at:
            raise ValueError("invalid appointment interval")
        return self


class OfficeApprovalEvidence(StrictModel):
    approved: bool
    approver_id: Identifier
    manifest_hash: Hash
    proposal_hash: Hash
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _aware(value)


class TrustedOfficeCalendarEvidence(StrictModel):
    """Assembled *outside* the model from pinned ledger, broker and simulator.

    This record is trusted only insofar as its producer is trusted. `admit_*` must
    never be exposed directly to model-provided or public request bodies.
    """

    schema_version: Literal["tal.office.calendar_evidence.v1"] = "tal.office.calendar_evidence.v1"
    organization_id: Identifier
    task_id: Identifier
    initiator_id: Identifier
    source_manifest_hash: Hash
    source_proposal_hash: Hash
    projection_rev: Identifier
    projection_expires_at: datetime
    simulation_ref: Identifier
    simulation_projection_rev: Identifier
    simulation_conflicts: tuple[str, ...] = ()
    expected_postconditions: tuple[str, ...]
    projected_calendar_ref: Identifier
    projected_customer_ref: Identifier
    projected_template_id: Identifier
    projected_start_at: datetime
    projected_end_at: datetime
    phase: Literal["SIMULATED", "READY_TO_COMMIT", "EXECUTING"]
    state_version: int = Field(ge=0)
    cycles_used: int = Field(ge=0)
    effects_used: int = Field(ge=0)
    approval: OfficeApprovalEvidence | None = None

    @field_validator("projection_expires_at", "projected_start_at", "projected_end_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _aware(value)


class OfficeAdmissionDecision(StrictModel):
    schema_version: Literal["tal.office.admission_decision.v1"] = "tal.office.admission_decision.v1"
    status: OfficeAdmissionStatus
    reason_code: str
    grant_hash: Hash
    proposal_hash: Hash
    state_version: int = Field(ge=0)
    authorizes_execution: Literal[False] = False


def admit_office_calendar(
    grant: SealedOfficeCalendarGrant,
    proposal: OfficeCalendarProposal,
    evidence: TrustedOfficeCalendarEvidence,
    *,
    now: datetime,
) -> OfficeAdmissionDecision:
    """Check one calendar action, no IO, no approvals minted, no provider calls.

    The single privileged action gate must still do authoritative source
    reconciliation and handle effect dispatch, verification and recovery.
    """
    now = _aware(now)
    proposal_hash = sha256(proposal)

    def decision(status: OfficeAdmissionStatus, reason: str) -> OfficeAdmissionDecision:
        return OfficeAdmissionDecision(
            status=status, reason_code=reason,
            grant_hash=grant.grant_hash, proposal_hash=proposal_hash,
            state_version=evidence.state_version,
        )

    if not verify_office_calendar_grant(grant):
        return decision(OfficeAdmissionStatus.DENIED, "GRANT_HASH_INVALID")
    if any([
        grant.organization_id != proposal.organization_id,
        grant.organization_id != evidence.organization_id,
        grant.task_id != proposal.task_id,
        grant.task_id != evidence.task_id,
        grant.initiator_id != proposal.initiator_id,
        grant.initiator_id != evidence.initiator_id,
        grant.source_manifest_hash != proposal.source_manifest_hash,
        grant.source_manifest_hash != evidence.source_manifest_hash,
        proposal.source_proposal_hash != evidence.source_proposal_hash,
    ]):
        return decision(OfficeAdmissionStatus.DENIED, "TASK_OR_MANIFEST_BINDING_INVALID")
    if (grant.calendar_ref != proposal.calendar_ref
            or grant.customer_ref != proposal.customer_ref
            or grant.template_id != proposal.template_id
            or grant.time_zone != proposal.time_zone):
        return decision(OfficeAdmissionStatus.DENIED, "TARGET_NOT_GRANTED")
    if now >= grant.task_deadline:
        return decision(OfficeAdmissionStatus.DENIED, "TASK_EXPIRED")
    if (evidence.cycles_used >= grant.max_cycles
            or evidence.effects_used >= grant.max_external_effects):
        return decision(OfficeAdmissionStatus.DENIED, "BUDGET_EXHAUSTED")
    start, end = proposal.start_at.astimezone(UTC), proposal.end_at.astimezone(UTC)
    if (start < grant.allowed_start.astimezone(UTC)
            or end > grant.allowed_end.astimezone(UTC)
            or end - start > timedelta(minutes=grant.max_duration_minutes)):
        return decision(OfficeAdmissionStatus.DENIED, "APPOINTMENT_OUTSIDE_AUTHORITY")
    if now >= evidence.projection_expires_at:
        return decision(OfficeAdmissionStatus.DENIED, "PROJECTION_EXPIRED")
    if (proposal.projection_rev != evidence.projection_rev
            or proposal.simulation_ref != evidence.simulation_ref
            or evidence.simulation_projection_rev != evidence.projection_rev
            or evidence.simulation_conflicts
            or evidence.expected_postconditions != ("event_created", "matching_time")):
        return decision(OfficeAdmissionStatus.DENIED, "SIMULATION_UNVERIFIED")
    if (evidence.projected_calendar_ref != proposal.calendar_ref
            or evidence.projected_customer_ref != proposal.customer_ref
            or evidence.projected_template_id != proposal.template_id
            or evidence.projected_start_at != proposal.start_at
            or evidence.projected_end_at != proposal.end_at):
        return decision(OfficeAdmissionStatus.DENIED, "SIMULATION_CHANGE_MISMATCH")
    if grant.requires_approval:
        approval = evidence.approval
        if approval is None:
            return decision(
                OfficeAdmissionStatus.APPROVAL_REQUIRED if evidence.phase == "SIMULATED"
                else OfficeAdmissionStatus.DENIED,
                "APPROVAL_REQUIRED",
            )
        if (not approval.approved or approval.approver_id == grant.initiator_id
                or approval.manifest_hash != grant.source_manifest_hash
                or approval.proposal_hash != proposal.source_proposal_hash
                or now >= approval.expires_at):
            return decision(OfficeAdmissionStatus.DENIED, "APPROVAL_BINDING_INVALID")
    return decision(OfficeAdmissionStatus.ELIGIBLE, "TRUSTED_ADMISSION_ONLY")


class OfficeRuntimeAction(StrictModel):
    """Privileged runtime input for an office proposal already pinned to a task.

    This is *not* an AI-visible API, signed capability or executable permit.
    The authoritative caller must reconstruct its fields from trusted storage.
    """

    schema_version: Literal["tal.office.runtime_action.v1"] = "tal.office.runtime_action.v1"
    grant: SealedOfficeCalendarGrant
    proposal: OfficeCalendarProposal
    evidence: TrustedOfficeCalendarEvidence
    expected_state_version: int = Field(ge=0)
    outbox_action_hash: Hash | None = None


def admit_runtime_office_action(
    action: OfficeRuntimeAction, *, run_id: str, actor_id: str | None, now: datetime,
) -> OfficeAdmissionDecision:
    """The first-class TAL runtime office branch: deterministic, never dispatches.

    `TALRuntime.apply_proposal` invokes this only for a typed OfficeRuntimeAction.
    No part of this function touches invoice tools, a provider or the event store.
    SAFE-AGENT must still use its trusted SQL task/outbox ledger and verifier.
    """
    now = _aware(now)
    evidence = action.evidence
    proposal_hash = sha256(action.proposal)

    def deny(reason: str) -> OfficeAdmissionDecision:
        return OfficeAdmissionDecision(
            status=OfficeAdmissionStatus.DENIED, reason_code=reason,
            grant_hash=action.grant.grant_hash, proposal_hash=proposal_hash,
            state_version=evidence.state_version,
        )

    if run_id != action.grant.task_id or run_id != action.proposal.task_id:
        return deny("RUNTIME_TASK_ID_MISMATCH")
    if actor_id is None or actor_id != action.grant.initiator_id:
        return deny("RUNTIME_ACTOR_MISMATCH")
    if action.expected_state_version != evidence.state_version:
        return deny("RUNTIME_STALE_STATE_VERSION")
    if evidence.phase == "EXECUTING":
        if action.outbox_action_hash is None:
            return deny("RUNTIME_OUTBOX_BINDING_MISSING")
    elif evidence.phase != "READY_TO_COMMIT" or action.outbox_action_hash is not None:
        return deny("RUNTIME_INVALID_EXECUTION_PHASE")
    return admit_office_calendar(action.grant, action.proposal, evidence, now=now)
