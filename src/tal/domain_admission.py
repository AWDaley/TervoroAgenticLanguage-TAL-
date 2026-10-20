"""Admission for simulated, multi-turn domains; never execution authority.

This additive protocol does not change invoice or office-calendar contracts.
The host builds grants/evidence from protected episode state. A caller can
construct these records, so an ELIGIBLE decision is not a bearer capability.
The host must still commit and dispatch the exact action through its outbox.
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .core import Hash, Identifier, StrictModel, sha256

DomainName = Literal["retail", "banking_knowledge"]
DomainEffect = Literal["read", "write", "control"]


def _finite_json(value: Any) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _finite_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _finite_json(item)
        return
    raise ValueError("arguments must be finite native JSON")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate argument key")
        result[key] = value
    return result


def domain_arguments_hash(value: str) -> str:
    """Hash exact native UTF-8 transport, without TAL string normalization."""
    if len(value.encode("utf-8")) > 65536:
        raise ValueError("arguments exceed transport bound")
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_pairs)
        if type(parsed) is not dict:
            raise ValueError("arguments must be an object")
        _finite_json(parsed)
        canonical = json.dumps(
            parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (RecursionError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid native JSON") from exc
    if canonical != value:
        raise ValueError("arguments must use canonical native JSON")
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class DomainCapability(StrictModel):
    """Trusted catalog entry. The model cannot classify its own operation."""

    operation: Identifier
    effect: DomainEffect
    requires_subject: bool = False
    requires_authentication: bool = False
    requires_customer_confirmation: bool = False
    directly_callable: bool = True
    dispatches: tuple[Identifier, ...] = ()


class DomainGrantDraft(StrictModel):
    schema_version: Literal["tal.benchmark.domain_grant.v1"] = "tal.benchmark.domain_grant.v1"
    environment: Literal["synthetic_benchmark"] = "synthetic_benchmark"
    episode_id: Identifier
    domain: DomainName
    policy_hash: Hash
    capabilities: tuple[DomainCapability, ...] = Field(min_length=1)
    max_tool_calls: int = Field(ge=1, le=1000000)

    @model_validator(mode="after")
    def unique_operations(self) -> DomainGrantDraft:
        names = [cap.operation for cap in self.capabilities]
        if len(names) != len(set(names)):
            raise ValueError("duplicate capability")
        return self


class SealedDomainGrant(DomainGrantDraft):
    grant_hash: Hash


def compile_domain_grant(draft: DomainGrantDraft) -> SealedDomainGrant:
    return SealedDomainGrant.model_validate({**draft.model_dump(), "grant_hash": sha256(draft)})


def verify_domain_grant(grant: SealedDomainGrant) -> bool:
    try:
        SealedDomainGrant.model_validate(grant.model_dump())
        return grant.grant_hash == sha256(grant.model_dump(exclude={"grant_hash"}))
    except (TypeError, ValueError):
        return False


class DomainActionProposal(StrictModel):
    schema_version: Literal["tal.benchmark.domain_proposal.v1"] = "tal.benchmark.domain_proposal.v1"
    episode_id: Identifier
    domain: DomainName
    policy_hash: Hash
    operation: Identifier
    effective_operation: Identifier
    arguments_json: str = Field(min_length=2, max_length=65536)
    arguments_hash: Hash
    subject_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def exact_arguments(self) -> DomainActionProposal:
        if domain_arguments_hash(self.arguments_json) != self.arguments_hash:
            raise ValueError("argument hash mismatch")
        return self

    @property
    def effect_hash(self) -> str:
        return sha256({"operation": self.operation, "arguments_hash": self.arguments_hash})


class DomainCustomerConfirmation(StrictModel):
    """Customer conversation evidence, not an office operator approval."""

    episode_id: Identifier
    subject_id: str = Field(min_length=1, max_length=256)
    policy_hash: Hash
    effect_hash: Hash
    user_turn_id: Identifier


class TrustedDomainEvidence(StrictModel):
    """Only the trusted host may assemble this from its observed episode state.

    mechanical_checks_passed covers the declared catalog/schema/subject checks,
    not complete semantic interpretation of natural-language business policy.
    """

    schema_version: Literal["tal.benchmark.domain_evidence.v1"] = "tal.benchmark.domain_evidence.v1"
    episode_id: Identifier
    domain: DomainName
    policy_hash: Hash
    observation_hash: Hash
    expected_observation_hash: Hash
    state_version: int = Field(ge=0)
    expected_state_version: int = Field(ge=0)
    subject_id: str | None = Field(default=None, min_length=1, max_length=256)
    authenticated: bool = False
    effective_operation: Identifier
    mechanical_checks_passed: bool
    scope_valid: bool
    tool_calls_used: int = Field(ge=0)
    unresolved_write: bool = False
    latest_user_turn_id: Identifier | None = None
    confirmation: DomainCustomerConfirmation | None = None


class DomainAdmissionStatus(StrEnum):
    DENIED = "DENIED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    ELIGIBLE = "ELIGIBLE"


class DomainAdmissionDecision(StrictModel):
    schema_version: Literal["tal.benchmark.domain_decision.v1"] = "tal.benchmark.domain_decision.v1"
    status: DomainAdmissionStatus
    reason_code: str
    grant_hash: Hash
    proposal_hash: Hash
    observation_hash: Hash
    state_version: int = Field(ge=0)
    authorizes_execution: Literal[False] = False
    semantic_policy_verified: Literal[False] = False


def admit_domain_action(
    grant: SealedDomainGrant,
    proposal: DomainActionProposal,
    evidence: TrustedDomainEvidence,
) -> DomainAdmissionDecision:
    """Pure admission: no tool execution, network, token, or ledger mutations."""

    def result(status: DomainAdmissionStatus, reason: str) -> DomainAdmissionDecision:
        return DomainAdmissionDecision(
            status=status,
            reason_code=reason,
            grant_hash=grant.grant_hash,
            proposal_hash=sha256(proposal),
            observation_hash=evidence.observation_hash,
            state_version=evidence.state_version,
        )

    deny = DomainAdmissionStatus.DENIED
    if not verify_domain_grant(grant):
        return result(deny, "DOMAIN_GRANT_HASH_INVALID")
    try:
        DomainActionProposal.model_validate(proposal.model_dump())
        TrustedDomainEvidence.model_validate(evidence.model_dump())
    except (TypeError, ValueError):
        return result(deny, "DOMAIN_PROPOSAL_OR_EVIDENCE_INVALID")
    if any(
        (
            grant.episode_id != proposal.episode_id,
            grant.episode_id != evidence.episode_id,
            grant.domain != proposal.domain,
            grant.domain != evidence.domain,
            grant.policy_hash != proposal.policy_hash,
            grant.policy_hash != evidence.policy_hash,
        )
    ):
        return result(deny, "DOMAIN_BINDING_INVALID")
    if (
        evidence.state_version != evidence.expected_state_version
        or evidence.observation_hash != evidence.expected_observation_hash
    ):
        return result(deny, "DOMAIN_OBSERVATION_STALE")
    capabilities = {cap.operation: cap for cap in grant.capabilities}
    cap = capabilities.get(proposal.effective_operation)
    outer = capabilities.get(proposal.operation)
    if cap is None or outer is None or not outer.directly_callable:
        return result(deny, "DOMAIN_OPERATION_NOT_GRANTED")
    if (
        proposal.operation != proposal.effective_operation
        and proposal.effective_operation not in outer.dispatches
    ):
        return result(deny, "DOMAIN_DISPATCH_TARGET_NOT_GRANTED")
    if evidence.effective_operation != proposal.effective_operation:
        return result(deny, "DOMAIN_EFFECTIVE_OPERATION_MISMATCH")
    if not evidence.mechanical_checks_passed or not evidence.scope_valid:
        return result(deny, "DOMAIN_MECHANICAL_CHECK_FAILED")
    if proposal.subject_id != evidence.subject_id:
        return result(deny, "DOMAIN_SUBJECT_MISMATCH")
    if (cap.requires_subject or outer.requires_subject) and evidence.subject_id is None:
        return result(deny, "DOMAIN_SUBJECT_REQUIRED")
    if (
        cap.requires_authentication or outer.requires_authentication
    ) and not evidence.authenticated:
        return result(deny, "DOMAIN_AUTHENTICATION_REQUIRED")
    if evidence.tool_calls_used >= grant.max_tool_calls:
        return result(deny, "DOMAIN_TOOL_BUDGET_EXHAUSTED")
    if evidence.unresolved_write and (cap.effect != "read" or outer.effect != "read"):
        return result(deny, "DOMAIN_RECONCILIATION_REQUIRED")
    if cap.requires_customer_confirmation or outer.requires_customer_confirmation:
        confirmation = evidence.confirmation
        if confirmation is None:
            return result(
                DomainAdmissionStatus.CONFIRMATION_REQUIRED, "DOMAIN_CUSTOMER_CONFIRMATION_REQUIRED"
            )
        if any(
            (
                confirmation.episode_id != grant.episode_id,
                confirmation.subject_id != evidence.subject_id,
                confirmation.policy_hash != grant.policy_hash,
                confirmation.effect_hash != proposal.effect_hash,
                confirmation.user_turn_id != evidence.latest_user_turn_id,
            )
        ):
            return result(deny, "DOMAIN_CUSTOMER_CONFIRMATION_STALE")
    return result(DomainAdmissionStatus.ELIGIBLE, "TRUSTED_DOMAIN_ADMISSION_ONLY")
