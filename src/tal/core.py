"""Closed TAL core primitives, immutable records, canonicalization, and errors."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

TAL_VERSION = "0.3.0"
GRAMMAR_ID = "tal-core-0.3.0"
SCHEMA_ID = "tal-manifest-0.3.0"
CANONICALIZATION_ID = "tal-json-c14n-0.2"
DOMAIN_PACK_ID = "tal.business.invoice_resolution-0.3.0"
COORDINATION_SCHEMA_ID = "tal-coordination-spec-0.3.0"
COMPILED_TOPOLOGY_SCHEMA_ID = "tal-compiled-topology-0.3.0"
COORDINATION_PACKET_SCHEMA_ID = "tal-coordination-packet-0.3.0"
MULTI_AGENT_GRAMMAR_ID = "tal-multi-agent-0.3.0"

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")]
Hash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    """All TAL objects are closed, strict, and immutable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, validate_default=True)


class ErrorCode(str, Enum):
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_ENUM = "INVALID_ENUM"
    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"
    MISSING_SUCCESS_CONDITION = "MISSING_SUCCESS_CONDITION"
    UNVERIFIABLE_SUCCESS_CONDITION = "UNVERIFIABLE_SUCCESS_CONDITION"
    ACTOR_NOT_AUTHORIZED = "ACTOR_NOT_AUTHORIZED"
    CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_STALE = "APPROVAL_STALE"
    ACTION_PROHIBITED = "ACTION_PROHIBITED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TOOL_NOT_REGISTERED = "TOOL_NOT_REGISTERED"
    TOOL_ARGUMENT_INVALID = "TOOL_ARGUMENT_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    TOOL_OUTPUT_INVALID = "TOOL_OUTPUT_INVALID"
    COMPLETION_EVIDENCE_INSUFFICIENT = "COMPLETION_EVIDENCE_INSUFFICIENT"
    COMPLETION_CONFLICTED = "COMPLETION_CONFLICTED"
    STALE_STATE_VERSION = "STALE_STATE_VERSION"
    CONTEXT_BUDGET_EXCEEDED = "CONTEXT_BUDGET_EXCEEDED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    TOPOLOGY_INVALID = "TOPOLOGY_INVALID"
    TOPOLOGY_FROZEN = "TOPOLOGY_FROZEN"
    COMMUNICATION_NOT_PERMITTED = "COMMUNICATION_NOT_PERMITTED"
    PHASE_NOT_PERMITTED = "PHASE_NOT_PERMITTED"
    ACTOR_NOT_DECLARED = "ACTOR_NOT_DECLARED"
    AUTHORITY_ESCALATION = "AUTHORITY_ESCALATION"
    SUBGOAL_NOT_PERMITTED = "SUBGOAL_NOT_PERMITTED"


class RuntimePhase(str, Enum):
    CREATED = "created"
    VALIDATING = "validating"
    READY = "ready"
    ACTING = "acting"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_INFORMATION = "waiting_for_information"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EpistemicStatus(str, Enum):
    REPORTED = "reported"
    OBSERVED = "observed"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    VERIFIED = "verified"
    CONFLICTED = "conflicted"


class VerificationMode(str, Enum):
    RUNTIME = "runtime"
    DETERMINISTIC_VALIDATOR = "deterministic_validator"
    TRUSTED_TOOL = "trusted_tool"
    HUMAN_APPROVAL = "human_approval"
    EXTERNAL_EVENT = "external_event"
    MODEL_ADVISORY = "model_advisory"


class EffectClass(str, Enum):
    READ = "read"
    INTERNAL_WRITE = "internal_write"
    EXTERNAL_WRITE = "external_write"
    FINANCIAL_WRITE = "financial_write"
    APPROVAL = "approval"


class ProposalKind(str, Enum):
    PROPOSE_PLAN = "propose_plan"
    REQUEST_TOOL = "request_tool"
    REQUEST_INFORMATION = "request_information"
    REQUEST_APPROVAL = "request_approval"
    PROPOSE_INFERENCE = "propose_inference"
    PROPOSE_COMPLETION = "propose_completion"
    ESCALATE = "escalate"
    REPORT_FAILURE = "report_failure"


class TrustClass(str, Enum):
    REPORTED_SOURCE = "reported_source"
    MODEL_ADVISORY = "model_advisory"
    PROVIDER_RECEIPT = "provider_receipt"
    RUNTIME = "runtime"
    DETERMINISTIC_VALIDATOR = "deterministic_validator"
    TRUSTED_REGISTRY = "trusted_registry"
    AUTHENTICATED_HUMAN = "authenticated_human"
    AUTHORITATIVE_EXTERNAL = "authoritative_external"


class Money(StrictModel):
    amount: str
    currency: Literal["USD", "CAD", "EUR", "GBP"]

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: str) -> str:
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("money amount must be Decimal-compatible") from exc
        exponent = amount.as_tuple().exponent
        if not amount.is_finite() or amount < 0 or not isinstance(exponent, int) or exponent < -2:
            raise ValueError("money amount must be a non-negative two-decimal Decimal")
        return f"{amount.quantize(Decimal('0.01')):.2f}"

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.amount)


class Source(StrictModel):
    source_type: Literal["user_submission", "received_message", "document"]
    source_ref: Identifier


class Rejection(StrictModel):
    reason_code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    path: str | None = Field(default=None, max_length=300)
    manifest_hash: Hash | None = None
    manifest_version: int = Field(ge=0)
    state_version: int = Field(ge=0)
    retryable: bool
    state_mutated: Literal[False] = False


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_data(value: object) -> object:
    """Normalize only JSON-compatible, typed values for TAL canonical JSON."""
    if isinstance(value, BaseModel):
        return canonical_data(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetimes are forbidden in TAL canonical JSON")
        instant = value.astimezone(UTC)
        text = instant.isoformat().replace("+00:00", "Z")
        return text.replace(".000000Z", "Z")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal is not canonical JSON")
        text = format(value.normalize(), "f")
        return "0" if text in {"-0", ""} else text
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, tuple | list):
        return [canonical_data(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): canonical_data(item)
            for key, item in value.items()
        }
    if value is None or isinstance(value, bool | int | float):
        if isinstance(value, float):
            raise TypeError("binary float is forbidden in TAL canonical JSON")
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        canonical_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(value: object) -> Hash:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


class VersionedArtifact(StrictModel):
    tal_version: Literal["0.3.0"] = "0.3.0"
    grammar_id: Literal["tal-core-0.3.0"] = "tal-core-0.3.0"
    schema_id: Literal["tal-manifest-0.3.0"] = "tal-manifest-0.3.0"
    domain_pack: Literal["tal.business.invoice_resolution-0.3.0"] = (
        "tal.business.invoice_resolution-0.3.0"
    )
    canonicalization: Literal["tal-json-c14n-0.2"] = "tal-json-c14n-0.2"
    compiler_version: Literal["0.3.0"] = "0.3.0"


TERMINAL_PHASES: frozenset[RuntimePhase] = frozenset(
    {RuntimePhase.COMPLETED, RuntimePhase.ESCALATED, RuntimePhase.CANCELLED, RuntimePhase.FAILED}
)

ALLOWED_TRANSITIONS: dict[RuntimePhase, frozenset[RuntimePhase]] = {
    RuntimePhase.CREATED: frozenset({RuntimePhase.VALIDATING}),
    RuntimePhase.VALIDATING: frozenset({RuntimePhase.READY, RuntimePhase.FAILED}),
    RuntimePhase.READY: frozenset(
        {
            RuntimePhase.ACTING,
            RuntimePhase.VERIFYING,
            RuntimePhase.ESCALATED,
            RuntimePhase.CANCELLED,
            RuntimePhase.FAILED,
        }
    ),
    RuntimePhase.ACTING: frozenset(
        {
            RuntimePhase.READY,
            RuntimePhase.WAITING_FOR_TOOL,
            RuntimePhase.WAITING_FOR_INFORMATION,
            RuntimePhase.WAITING_FOR_APPROVAL,
            RuntimePhase.VERIFYING,
            RuntimePhase.ESCALATED,
            RuntimePhase.FAILED,
        }
    ),
    RuntimePhase.WAITING_FOR_TOOL: frozenset(
        {RuntimePhase.READY, RuntimePhase.VERIFYING, RuntimePhase.FAILED}
    ),
    RuntimePhase.WAITING_FOR_INFORMATION: frozenset(
        {RuntimePhase.READY, RuntimePhase.ESCALATED, RuntimePhase.CANCELLED, RuntimePhase.FAILED}
    ),
    RuntimePhase.WAITING_FOR_APPROVAL: frozenset(
        {RuntimePhase.READY, RuntimePhase.ESCALATED, RuntimePhase.FAILED}
    ),
    RuntimePhase.VERIFYING: frozenset(
        {
            RuntimePhase.READY,
            RuntimePhase.COMPLETED,
            RuntimePhase.ESCALATED,
            RuntimePhase.CANCELLED,
            RuntimePhase.FAILED,
        }
    ),
    RuntimePhase.COMPLETED: frozenset(),
    RuntimePhase.ESCALATED: frozenset(),
    RuntimePhase.CANCELLED: frozenset(),
    RuntimePhase.FAILED: frozenset(),
}
