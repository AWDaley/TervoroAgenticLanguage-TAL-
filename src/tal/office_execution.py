"""Closed TAL office-execution authorization contract; no provider dispatch.

This is a trusted-server record, not a bearer token. A prepared authorization
becomes authoritative ONLY after a service atomically persists it alongside the
corresponding immutable outbox intent in its protected SQL transaction. The
experimental TAL SQLite journal remains an independently reconciled audit.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .core import Hash, Identifier, StrictModel, sha256
from .office import (
    OfficeAdmissionDecision,
    OfficeAdmissionStatus,
    OfficeRuntimeAction,
    _aware,
    admit_runtime_office_action,
)

OFFICE_EXECUTION_AUTHORIZATION_VERSION = "tal.office.execution_authorization.v1"
MAX_AUTHORIZATION_LIFETIME = timedelta(seconds=60)


class OfficeExecutionAuthorizationDenied(ValueError):
    """The office action has no eligible, exact, fresh execution authorization."""


class OfficeExecutionAuthorization(StrictModel):
    """Hash-sealed immutable metadata; NEVER put this in a model or public API.

    The hash is an integrity cross-check, not a signature. The trusted SQL
    repository must enforce uniqueness and bind this record to its own outbox.
    """

    schema_version: Literal["tal.office.execution_authorization.v1"] = (
        "tal.office.execution_authorization.v1"
    )
    organization_id: Identifier
    initiator_id: Identifier
    task_id: Identifier
    operation: Literal["calendar.create_event"] = "calendar.create_event"
    source_manifest_hash: Hash
    source_proposal_hash: Hash
    tal_grant_hash: Hash
    tal_proposal_hash: Hash
    outbox_action_hash: Hash
    calendar_ref: Identifier
    state_version: int = Field(ge=0)
    cycles_used: int = Field(ge=0)
    effects_used: int = Field(ge=0)
    max_cycles: int = Field(ge=1)
    max_external_effects: int = Field(ge=1)
    approval_hash: Hash | None = None
    approval_expires_at: datetime | None = None
    issued_at: datetime
    expires_at: datetime
    authorization_hash: Hash

    @field_validator("issued_at", "expires_at", "approval_expires_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @model_validator(mode="after")
    def valid_bounds(self) -> OfficeExecutionAuthorization:
        if (self.expires_at <= self.issued_at
                or self.expires_at > self.issued_at + MAX_AUTHORIZATION_LIFETIME
                or self.cycles_used >= self.max_cycles
                or self.effects_used >= self.max_external_effects
                or (self.approval_hash is None) != (self.approval_expires_at is None)):
            raise ValueError("invalid execution authorization bounds")
        if self.approval_expires_at is not None and self.expires_at > self.approval_expires_at:
            raise ValueError("execution authorization outlives its approval")
        return self


def _authorization_payload(
    action: OfficeRuntimeAction, *, now: datetime,
) -> dict[str, object]:
    grant, proposal, evidence = action.grant, action.proposal, action.evidence
    if evidence.phase != "EXECUTING" or action.outbox_action_hash is None:
        raise OfficeExecutionAuthorizationDenied("OFFICE_OUTBOX_NOT_EXECUTING")
    if evidence.cycles_used >= grant.max_cycles or evidence.effects_used >= grant.max_external_effects:
        raise OfficeExecutionAuthorizationDenied("OFFICE_EFFECT_BUDGET_EXHAUSTED")
    approval = evidence.approval
    if grant.requires_approval and (
        approval is None or not approval.approved or approval.approver_id == grant.initiator_id
    ):
        raise OfficeExecutionAuthorizationDenied("OFFICE_APPROVAL_REQUIRED")
    deadline = min(
        now + MAX_AUTHORIZATION_LIFETIME,
        grant.task_deadline,
        evidence.projection_expires_at,
    )
    if approval is not None:
        deadline = min(deadline, approval.expires_at)
    if deadline <= now:
        raise OfficeExecutionAuthorizationDenied("OFFICE_AUTHORIZATION_EXPIRED")
    return {
        "schema_version": OFFICE_EXECUTION_AUTHORIZATION_VERSION,
        "organization_id": grant.organization_id,
        "initiator_id": grant.initiator_id,
        "task_id": grant.task_id,
        "operation": grant.operation,
        "source_manifest_hash": grant.source_manifest_hash,
        "source_proposal_hash": proposal.source_proposal_hash,
        "tal_grant_hash": grant.grant_hash,
        "tal_proposal_hash": sha256(proposal),
        "outbox_action_hash": action.outbox_action_hash,
        "calendar_ref": grant.calendar_ref,
        "state_version": evidence.state_version,
        "cycles_used": evidence.cycles_used,
        "effects_used": evidence.effects_used,
        "max_cycles": grant.max_cycles,
        "max_external_effects": grant.max_external_effects,
        "approval_hash": sha256(approval) if approval is not None else None,
        "approval_expires_at": approval.expires_at if approval is not None else None,
        "issued_at": now,
        "expires_at": deadline,
    }


def prepare_office_execution_authorization(
    action: OfficeRuntimeAction,
    decision: OfficeAdmissionDecision,
    *,
    run_id: str,
    actor_id: str | None,
    now: datetime,
) -> OfficeExecutionAuthorization:
    """Prepare authority ONLY from freshly re-evaluated TAL office admission.

    The caller must reconstruct the action from a protected task/outbox store and
    persist the resulting record in the SAME SQL transaction as that outbox
    intent. This pure method neither stores a record nor authorizes a tool call.
    """
    now = _aware(now)
    fresh = admit_runtime_office_action(
        action, run_id=run_id, actor_id=actor_id, now=now,
    )
    if (fresh.status is not OfficeAdmissionStatus.ELIGIBLE
            or decision != fresh or fresh.authorizes_execution is not False):
        raise OfficeExecutionAuthorizationDenied("OFFICE_RUNTIME_ADMISSION_DENIED")
    payload = _authorization_payload(action, now=now)
    return OfficeExecutionAuthorization.model_validate({
        **payload, "authorization_hash": sha256(payload),
    })


def require_office_execution_authorization(
    authorization: OfficeExecutionAuthorization,
    action: OfficeRuntimeAction,
    *,
    run_id: str,
    actor_id: str | None,
    state_version: int,
    now: datetime,
) -> None:
    """Read-barrier check for a NEW effect; never use for past-effect readback.

    The worker must first load this record by action hash from protected SQL
    storage and check it against the same persisted outbox. Neither a detached
    object nor a TAL admission decision is sufficient execution authority.
    """
    now = _aware(now)
    if type(authorization) is not OfficeExecutionAuthorization:
        raise OfficeExecutionAuthorizationDenied("OFFICE_AUTHORIZATION_TYPE_INVALID")
    if (now < authorization.issued_at or now >= authorization.expires_at
            or state_version != authorization.state_version):
        raise OfficeExecutionAuthorizationDenied("OFFICE_AUTHORIZATION_STALE")
    fresh = admit_runtime_office_action(
        action, run_id=run_id, actor_id=actor_id, now=now,
    )
    if fresh.status is not OfficeAdmissionStatus.ELIGIBLE:
        raise OfficeExecutionAuthorizationDenied("OFFICE_RUNTIME_REVALIDATION_DENIED")
    issued = admit_runtime_office_action(
        action, run_id=run_id, actor_id=actor_id, now=authorization.issued_at,
    )
    if issued.status is not OfficeAdmissionStatus.ELIGIBLE:
        raise OfficeExecutionAuthorizationDenied("OFFICE_ISSUANCE_BINDING_INVALID")
    expected = prepare_office_execution_authorization(
        action, issued, run_id=run_id, actor_id=actor_id,
        now=authorization.issued_at,
    )
    if expected != authorization:
        raise OfficeExecutionAuthorizationDenied("OFFICE_AUTHORIZATION_BINDING_MISMATCH")
    payload = authorization.model_dump(mode="python", exclude={"authorization_hash"})
    if sha256(payload) != authorization.authorization_hash:
        raise OfficeExecutionAuthorizationDenied("OFFICE_AUTHORIZATION_SEAL_INVALID")
