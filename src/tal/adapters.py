"""Provider-neutral scripted model, fake approval provider, and deterministic fake tools."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter

from .core import EffectClass, ProposalKind, StrictModel, TrustClass
from .events import Observation
from .invoice import ToolId


class ToolArguments(StrictModel):
    invoice_ref: str | None = None
    vendor_ref: str | None = None
    contract_ref: str | None = None
    purchase_order_ref: str | None = None
    recipient_ref: str | None = None
    amount: str | None = None
    message_id: str | None = None


class ProposalBase(StrictModel):
    proposal_id: str
    kind: ProposalKind
    goal_id: str
    manifest_hash: str
    manifest_version: int = Field(ge=1)
    state_version: int = Field(ge=0)


class PlanProposal(ProposalBase):
    kind: Literal[ProposalKind.PROPOSE_PLAN] = ProposalKind.PROPOSE_PLAN
    plan_id: str
    step_ids: tuple[str, ...] = ()


class ToolProposal(ProposalBase):
    kind: Literal[ProposalKind.REQUEST_TOOL] = ProposalKind.REQUEST_TOOL
    tool_id: ToolId
    tool_version: str = "1.0.0"
    arguments: ToolArguments
    purpose: str = Field(min_length=1, max_length=500)
    requirement_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    expected_observation_type: str


class InformationProposal(ProposalBase):
    kind: Literal[ProposalKind.REQUEST_INFORMATION] = ProposalKind.REQUEST_INFORMATION
    unknown_ref: str
    requested_from: str
    requirement_refs: tuple[str, ...] = ()


class ApprovalProposal(ProposalBase):
    kind: Literal[ProposalKind.REQUEST_APPROVAL] = ProposalKind.REQUEST_APPROVAL
    action_id: str
    target_ref: str
    approver_role: str
    evidence_refs: tuple[str, ...] = ()


class InferenceProposal(ProposalBase):
    kind: Literal[ProposalKind.PROPOSE_INFERENCE] = ProposalKind.PROPOSE_INFERENCE
    subject_ref: str
    field_id: str
    conclusion: str
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class CompletionProposal(ProposalBase):
    kind: Literal[ProposalKind.PROPOSE_COMPLETION] = ProposalKind.PROPOSE_COMPLETION
    success_condition_ref: str
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class EscalateProposal(ProposalBase):
    kind: Literal[ProposalKind.ESCALATE] = ProposalKind.ESCALATE
    fallback_ref: str
    reason: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[str, ...] = ()


class FailureProposal(ProposalBase):
    kind: Literal[ProposalKind.REPORT_FAILURE] = ProposalKind.REPORT_FAILURE
    failure_code: str
    reason: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[str, ...] = ()


Proposal = Annotated[
    PlanProposal
    | ToolProposal
    | InformationProposal
    | ApprovalProposal
    | InferenceProposal
    | CompletionProposal
    | EscalateProposal
    | FailureProposal,
    Field(discriminator="kind"),
]


def parse_proposal_json(payload: str) -> Proposal:
    """Strictly parse one model output as a discriminated TAL proposal."""
    return TypeAdapter(Proposal).validate_json(payload)


class ModelContext(StrictModel):
    grammar_id: str
    grammar_hash: str
    manifest_hash: str
    state_version: int
    phase: str
    effective_tools: tuple[str, ...]
    denied_actions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    remaining_budgets: tuple[str, ...]
    proposal_schema_id: str = "tal-proposal-0.3.0"


class ModelAdapter(Protocol):
    def propose(self, context: ModelContext) -> Proposal: ...


@dataclass
class ScriptedModelAdapter:
    proposals: deque[Proposal] = field(default_factory=deque)

    def propose(self, context: ModelContext) -> Proposal:
        if not self.proposals:
            raise RuntimeError("scripted model has no proposal")
        return self.proposals.popleft()


class ToolResult(StrictModel):
    outcome: str
    observation_type: str
    subject_ref: str
    status: str
    trust: TrustClass
    detail: str = ""
    provider_accepted: bool = False
    retryable: bool = False


@dataclass(frozen=True)
class ToolContract:
    tool_id: ToolId
    effect_class: EffectClass
    required_capability: str
    idempotency_required: bool
    irreversible: bool
    observation_type: str
    version: str = "1.0.0"
    preconditions: tuple[str, ...] = ("effective_capability", "in_scope_target")
    budget_cost: int = 1
    timeout_seconds: int = 30
    retry_limit: int = 1
    reconciliation: Literal["none", "required"] = "none"
    compensation: Literal["not_required", "supported", "irreversible"] = "not_required"
    redaction_policy: Literal["none", "reference_only"] = "reference_only"


TOOL_CONTRACTS: dict[ToolId, ToolContract] = {
    ToolId.VENDOR_LOOKUP: ToolContract(
        ToolId.VENDOR_LOOKUP, EffectClass.READ, "vendor.lookup", False, False, "vendor_record"
    ),
    ToolId.CONTRACT_LOOKUP: ToolContract(
        ToolId.CONTRACT_LOOKUP, EffectClass.READ, "contract.lookup", False, False, "contract_record"
    ),
    ToolId.PAYMENT_HISTORY_SEARCH: ToolContract(
        ToolId.PAYMENT_HISTORY_SEARCH,
        EffectClass.READ,
        "payment_history.search",
        False,
        False,
        "payment_history",
    ),
    ToolId.INVOICE_VERIFY: ToolContract(
        ToolId.INVOICE_VERIFY, EffectClass.READ, "invoice.verify", False, False, "invoice_check"
    ),
    ToolId.APPROVAL_REQUEST: ToolContract(
        ToolId.APPROVAL_REQUEST,
        EffectClass.APPROVAL,
        "approval.request",
        True,
        False,
        "approval_receipt",
    ),
    ToolId.PAYMENT_PREPARE: ToolContract(
        ToolId.PAYMENT_PREPARE,
        EffectClass.INTERNAL_WRITE,
        "payment.prepare",
        True,
        False,
        "prepared_payment",
    ),
    ToolId.PAYMENT_SUBMIT: ToolContract(
        ToolId.PAYMENT_SUBMIT,
        EffectClass.FINANCIAL_WRITE,
        "payment.submit",
        True,
        True,
        "payment_receipt",
    ),
    ToolId.PAYMENT_STATUS: ToolContract(
        ToolId.PAYMENT_STATUS, EffectClass.READ, "payment.status", False, False, "payment_status"
    ),
    ToolId.MESSAGE_DRAFT: ToolContract(
        ToolId.MESSAGE_DRAFT,
        EffectClass.INTERNAL_WRITE,
        "message.draft",
        True,
        False,
        "message_draft",
    ),
    ToolId.MESSAGE_SEND: ToolContract(
        ToolId.MESSAGE_SEND,
        EffectClass.EXTERNAL_WRITE,
        "message.send",
        True,
        True,
        "message_delivery",
    ),
}


@dataclass
class FakeToolRegistry:
    scripted: dict[ToolId, deque[ToolResult]] = field(default_factory=dict)
    calls: list[tuple[ToolId, str | None]] = field(default_factory=list)
    accepted_effects: set[str] = field(default_factory=set)

    def execute(
        self, tool_id: ToolId, arguments: ToolArguments, idempotency_key: str | None
    ) -> ToolResult:
        contract = TOOL_CONTRACTS[tool_id]
        if (
            contract.idempotency_required
            and idempotency_key is not None
            and idempotency_key in self.accepted_effects
        ):
            return ToolResult(
                outcome="success",
                observation_type=contract.observation_type,
                subject_ref=arguments.invoice_ref or arguments.vendor_ref or "invoice:unknown",
                status="duplicate",
                trust=TrustClass.RUNTIME,
                detail="duplicate semantic effect suppressed",
            )
        self.calls.append((tool_id, idempotency_key))
        result = (
            self.scripted.get(tool_id, deque()).popleft()
            if self.scripted.get(tool_id)
            else ToolResult(
                outcome="success",
                observation_type=contract.observation_type,
                subject_ref=arguments.invoice_ref or arguments.vendor_ref or "invoice:unknown",
                status="ok",
                trust=TrustClass.TRUSTED_REGISTRY,
            )
        )
        if result.provider_accepted and idempotency_key is not None:
            self.accepted_effects.add(idempotency_key)
        if (
            result.outcome == "success"
            and contract.idempotency_required
            and idempotency_key is not None
        ):
            self.accepted_effects.add(idempotency_key)
        return result

    def reconcile(
        self, tool_id: ToolId, idempotency_key: str, subject_ref: str, now: datetime
    ) -> Observation:
        return Observation(
            id=f"obs:reconcile:{idempotency_key[-12:]}",
            observation_type=TOOL_CONTRACTS[tool_id].observation_type,
            subject_ref=subject_ref,
            status="delivered" if tool_id is ToolId.MESSAGE_SEND else "reconciled",
            trust=TrustClass.AUTHORITATIVE_EXTERNAL,
            detail="reconciled provider outcome",
            observed_at=now,
        )
