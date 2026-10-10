"""Closed invoice-resolution domain catalog and manifest/form models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .core import (
    EpistemicStatus,
    Hash,
    Identifier,
    Money,
    Source,
    StrictModel,
    VerificationMode,
)


class EntityType(str, Enum):
    INVOICE = "invoice"
    VENDOR = "vendor"
    CONTRACT = "contract"
    PURCHASE_ORDER = "purchase_order"
    PAYMENT = "payment"
    APPROVAL = "approval"
    MESSAGE = "message"
    USER = "user"


class InvoiceField(str, Enum):
    NUMBER = "invoice.number"
    VENDOR_NAME = "invoice.vendor_name"
    AMOUNT = "invoice.amount"
    CURRENCY = "invoice.currency"
    DUE_DATE = "invoice.due_date"
    PURCHASE_ORDER = "invoice.purchase_order"
    CONTRACT_ID = "invoice.contract_id"
    BANK_ACCOUNT_REFERENCE = "invoice.bank_account_reference"
    DUPLICATE_STATUS = "invoice.duplicate_status"
    CONTRACT_MATCH = "invoice.contract_match"
    VENDOR_ID = "vendor.id"
    VENDOR_NAME_VALUE = "vendor.name"
    VENDOR_STATUS = "vendor.status"
    VENDOR_BANK_ACCOUNT = "vendor.authorized_bank_account_reference"
    CONTRACT_ID_VALUE = "contract.id"
    PAYMENT_ID = "payment.id"
    PAYMENT_STATUS = "payment.status"
    APPROVAL_ID = "approval.id"
    APPROVAL_STATUS = "approval.status"


class ActionId(str, Enum):
    VENDOR_LOOKUP = "vendor.lookup"
    CONTRACT_LOOKUP = "contract.lookup"
    PAYMENT_HISTORY_SEARCH = "payment_history.search"
    INVOICE_VERIFY = "invoice.verify"
    APPROVAL_REQUEST = "approval.request"
    PAYMENT_PREPARE = "payment.prepare"
    PAYMENT_SUBMIT = "payment.submit"
    PAYMENT_STATUS = "payment.status"
    MESSAGE_DRAFT = "message.draft"
    MESSAGE_SEND = "message.send"


class ToolId(str, Enum):
    VENDOR_LOOKUP = "vendor_registry.lookup"
    CONTRACT_LOOKUP = "contract_registry.lookup"
    PAYMENT_HISTORY_SEARCH = "payment_history.search"
    INVOICE_VERIFY = "invoice_verifier.check"
    APPROVAL_REQUEST = "approval.request"
    PAYMENT_PREPARE = "payment.prepare"
    PAYMENT_SUBMIT = "payment.submit"
    PAYMENT_STATUS = "payment.status"
    MESSAGE_DRAFT = "message.draft"
    MESSAGE_SEND = "message.send"


TOOL_ACTION: dict[ToolId, ActionId] = {
    ToolId.VENDOR_LOOKUP: ActionId.VENDOR_LOOKUP,
    ToolId.CONTRACT_LOOKUP: ActionId.CONTRACT_LOOKUP,
    ToolId.PAYMENT_HISTORY_SEARCH: ActionId.PAYMENT_HISTORY_SEARCH,
    ToolId.INVOICE_VERIFY: ActionId.INVOICE_VERIFY,
    ToolId.APPROVAL_REQUEST: ActionId.APPROVAL_REQUEST,
    ToolId.PAYMENT_PREPARE: ActionId.PAYMENT_PREPARE,
    ToolId.PAYMENT_SUBMIT: ActionId.PAYMENT_SUBMIT,
    ToolId.PAYMENT_STATUS: ActionId.PAYMENT_STATUS,
    ToolId.MESSAGE_DRAFT: ActionId.MESSAGE_DRAFT,
    ToolId.MESSAGE_SEND: ActionId.MESSAGE_SEND,
}


class RequirementRule(str, Enum):
    VENDOR_ACTIVE = "invoice.vendor_must_be_active"
    NOT_DUPLICATE = "invoice.must_not_be_duplicate"
    CONTRACT_MATCH = "invoice.must_match_contract"
    BANK_MATCH = "invoice.bank_account_must_match"
    PURCHASE_ORDER = "invoice.purchase_order_required"


class ValidatorId(str, Enum):
    VENDOR_ACTIVE = "vendor_active"
    DUPLICATE_INVOICE = "duplicate_invoice"
    CONTRACT_MATCH = "contract_match"
    BANK_ACCOUNT_MATCH = "bank_account_match"
    APPROVAL_VALID = "approval_valid"
    PAYMENT_SETTLED = "payment_settled"
    APPROVAL_PACKAGE_DELIVERED = "approval_package_delivered"
    INVOICE_REJECTED_WITH_REASON = "invoice_rejected_with_reason"


class TerminalOutcome(str, Enum):
    APPROVAL_PACKAGE_DELIVERED = "approval_package_delivered"
    INVOICE_REJECTED = "invoice_rejected"
    PAYMENT_SETTLED = "payment_settled"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ConstraintKind(str, Enum):
    MODEL_CALLS = "model_calls"
    TOOL_CALLS = "tool_calls"
    EXTERNAL_ACTIONS = "external_actions"
    RETRIES = "retries"


class EntityDeclaration(StrictModel):
    ref: Identifier
    entity_type: EntityType

    @model_validator(mode="after")
    def type_matches_prefix(self) -> EntityDeclaration:
        if not self.ref.startswith(f"{self.entity_type.value}:"):
            raise ValueError("entity reference prefix must match entity_type")
        return self


class RequestMetadata(StrictModel):
    request_id: Identifier
    submitted_by: Identifier
    submitted_at: datetime
    submission_channel: Literal["form", "api", "cli", "fixture"] = "form"

    @field_validator("submitted_at")
    @classmethod
    def submitted_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("submitted_at must include a timezone")
        return value.astimezone(UTC)


class Scope(StrictModel):
    scope_id: Identifier
    organization_ref: Identifier
    target_refs: tuple[Identifier, ...] = Field(min_length=1)
    entity_declarations: tuple[EntityDeclaration, ...] = Field(min_length=1)
    timezone: str = Field(min_length=1, max_length=64)


class Goal(StrictModel):
    goal_id: Identifier
    actor_ref: Identifier
    owner_ref: Identifier
    outcome_id: Literal["invoice.resolve"] = "invoice.resolve"
    target_ref: Identifier
    deadline: datetime | None = None
    priority_order: tuple[Literal["correctness", "speed", "cost"], ...] = (
        "correctness",
        "speed",
        "cost",
    )

    @field_validator("deadline")
    @classmethod
    def deadline_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def priority_is_a_complete_order(self) -> "Goal":
        if len(self.priority_order) != len(set(self.priority_order)):
            raise ValueError("priority_order may not repeat a criterion")
        return self


FormValue = str | int | bool | Money


class ReportedFact(StrictModel):
    id: Identifier
    subject_ref: Identifier
    field_id: InvoiceField
    value: FormValue
    source: Source
    epistemic_status: Literal[EpistemicStatus.REPORTED] = EpistemicStatus.REPORTED

    @model_validator(mode="after")
    def field_value_match(self) -> ReportedFact:
        money_fields = {InvoiceField.AMOUNT}
        if self.field_id in money_fields and not isinstance(self.value, Money):
            raise ValueError("invoice.amount requires Money")
        if self.field_id is InvoiceField.CURRENCY and self.value not in {
            "USD",
            "CAD",
            "EUR",
            "GBP",
        }:
            raise ValueError("invoice.currency is not a registered currency")
        if self.field_id in {
            InvoiceField.DUPLICATE_STATUS,
            InvoiceField.CONTRACT_MATCH,
            InvoiceField.VENDOR_STATUS,
            InvoiceField.PAYMENT_STATUS,
            InvoiceField.APPROVAL_STATUS,
        } and not isinstance(self.value, str):
            raise ValueError("registered enum field requires a string enum")
        return self


class Unknown(StrictModel):
    id: Identifier
    subject_ref: Identifier
    field_id: InvoiceField
    blocking: bool = True
    requirement_refs: tuple[Identifier, ...] = ()


class Requirement(StrictModel):
    id: Identifier
    rule_id: RequirementRule
    verifier_id: ValidatorId
    target_refs: tuple[Identifier, ...] = Field(min_length=1)


class Constraint(StrictModel):
    id: Identifier
    kind: ConstraintKind
    maximum: int = Field(ge=0)


class Preference(StrictModel):
    id: Identifier
    criterion: Literal["minimize_external_messages", "minimize_cost", "resolve_earliest"]
    rank: int = Field(ge=1)


class RequestedCapability(StrictModel):
    id: Identifier
    action_id: ActionId
    target_ref: Identifier
    amount_limit: Money | None = None


class ProhibitedAction(StrictModel):
    id: Identifier
    action_id: ActionId
    target_ref: Identifier


class SuccessCondition(StrictModel):
    id: Identifier
    condition_id: ValidatorId
    terminal_outcome: TerminalOutcome
    verification_mode: VerificationMode
    required_evidence_types: tuple[str, ...] = ()


class Fallback(StrictModel):
    id: Identifier
    trigger: Literal[
        "unresolved_conflict",
        "budget_exhausted",
        "approval_denied",
        "information_timeout",
        "tool_failure",
    ]
    terminal_outcome: Literal[
        TerminalOutcome.ESCALATED, TerminalOutcome.FAILED, TerminalOutcome.CANCELLED
    ]
    target_ref: Identifier | None = None


class OutputContract(StrictModel):
    output_type: Literal["invoice_resolution_summary"] = "invoice_resolution_summary"
    required_fields: tuple[
        Literal["outcome", "evidence_summary", "remaining_unknowns", "audit_reference"], ...
    ] = (
        "outcome",
        "evidence_summary",
        "remaining_unknowns",
        "audit_reference",
    )
    format: Literal["json", "tal_english", "html"] = "json"
    destination: Literal["stdout", "local_ui", "api_response"] = "stdout"


class IntegrityMetadata(StrictModel):
    canonicalization: Literal["tal-json-c14n-0.2"] = "tal-json-c14n-0.2"
    compiler_version: Literal["0.3.0"] = "0.3.0"
    manifest_version: int = Field(default=1, ge=1)
    parent_manifest_hash: Hash | None = None
    manifest_hash: Hash | None = None


class InvoiceFormSubmission(StrictModel):
    request: RequestMetadata
    scope: Scope
    goal: Goal
    reported_facts: tuple[ReportedFact, ...] = ()
    unknowns: tuple[Unknown, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    preferences: tuple[Preference, ...] = ()
    requested_capabilities: tuple[RequestedCapability, ...] = ()
    prohibited_actions: tuple[ProhibitedAction, ...] = ()
    success_conditions: tuple[SuccessCondition, ...] = Field(min_length=1)
    fallbacks: tuple[Fallback, ...] = ()
    output_contract: OutputContract = OutputContract()


class TALManifest(StrictModel):
    tal_version: Literal["0.3.0"] = "0.3.0"
    grammar_id: Literal["tal-core-0.3.0"] = "tal-core-0.3.0"
    schema_id: Literal["tal-manifest-0.3.0"] = "tal-manifest-0.3.0"
    domain_pack: Literal["tal.business.invoice_resolution-0.3.0"] = (
        "tal.business.invoice_resolution-0.3.0"
    )
    request: RequestMetadata
    scope: Scope
    goal: Goal
    reported_facts: tuple[ReportedFact, ...]
    unknowns: tuple[Unknown, ...]
    requirements: tuple[Requirement, ...]
    constraints: tuple[Constraint, ...]
    preferences: tuple[Preference, ...]
    requested_capabilities: tuple[RequestedCapability, ...]
    prohibited_actions: tuple[ProhibitedAction, ...]
    success_conditions: tuple[SuccessCondition, ...]
    fallbacks: tuple[Fallback, ...]
    output_contract: OutputContract
    integrity: IntegrityMetadata

    @model_validator(mode="after")
    def manifest_invariants(self) -> TALManifest:
        entity_refs = {entity.ref for entity in self.scope.entity_declarations}
        if self.goal.target_ref not in entity_refs:
            raise ValueError("goal target must be declared in scope")
        ids: list[str] = []
        for records in (
            self.reported_facts,
            self.unknowns,
            self.requirements,
            self.constraints,
            self.preferences,
            self.requested_capabilities,
            self.prohibited_actions,
            self.success_conditions,
            self.fallbacks,
        ):
            ids.extend(record.id for record in records)
        if len(ids) != len(set(ids)):
            raise ValueError("record identifiers must be unique")
        if not any(
            condition.terminal_outcome
            in {TerminalOutcome.APPROVAL_PACKAGE_DELIVERED, TerminalOutcome.INVOICE_REJECTED}
            for condition in self.success_conditions
        ):
            raise ValueError("invoice manifests require a deliver or reject success condition")
        return self


def sort_submission(form: InvoiceFormSubmission) -> InvoiceFormSubmission:
    """Canonical order for set-like registered record categories."""
    return form.model_copy(
        update={
            "scope": form.scope.model_copy(
                update={
                    "target_refs": tuple(sorted(form.scope.target_refs)),
                    "entity_declarations": tuple(
                        sorted(form.scope.entity_declarations, key=lambda x: x.ref)
                    ),
                }
            ),
            "reported_facts": tuple(
                sorted(
                    form.reported_facts,
                    key=lambda x: (x.subject_ref, x.field_id.value, x.source.source_ref, x.id),
                )
            ),
            "unknowns": tuple(
                sorted(
                    form.unknowns,
                    key=lambda x: (not x.blocking, x.subject_ref, x.field_id.value, x.id),
                )
            ),
            "requirements": tuple(sorted(form.requirements, key=lambda x: (x.rule_id.value, x.id))),
            "constraints": tuple(sorted(form.constraints, key=lambda x: (x.kind.value, x.id))),
            "preferences": tuple(
                sorted(form.preferences, key=lambda x: (x.rank, x.criterion, x.id))
            ),
            "requested_capabilities": tuple(
                sorted(
                    form.requested_capabilities,
                    key=lambda x: (x.action_id.value, x.target_ref, x.id),
                )
            ),
            "prohibited_actions": tuple(
                sorted(
                    form.prohibited_actions, key=lambda x: (x.action_id.value, x.target_ref, x.id)
                )
            ),
            "success_conditions": tuple(
                sorted(form.success_conditions, key=lambda x: (x.terminal_outcome.value, x.id))
            ),
            "fallbacks": tuple(sorted(form.fallbacks, key=lambda x: (x.trigger, x.id))),
        }
    )
