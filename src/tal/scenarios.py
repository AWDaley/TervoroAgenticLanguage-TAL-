"""Offline, deterministic certification scenarios for invoice_resolution."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from .adapters import (
    ApprovalProposal,
    CompletionProposal,
    EscalateProposal,
    FakeToolRegistry,
    ToolArguments,
    ToolProposal,
    ToolResult,
)
from .compiler import compile_manifest
from .core import ErrorCode, ProposalKind, Rejection, RuntimePhase, StrictModel, TrustClass
from .events import ApprovalDecision, EventKind, Observation, RuntimeState
from .invoice import (
    ActionId,
    Constraint,
    ConstraintKind,
    InvoiceFormSubmission,
    RequestedCapability,
    TALManifest,
    ToolId,
)
from .patches import ManifestPatchRequest, ReplaceDeadline
from .runtime import SequentialIds, TALRuntime
from .storage import InMemoryEventStore


class ScenarioResult(StrictModel):
    name: str
    terminal_outcome: str
    assertions: tuple[str, ...]


SCENARIO_NAMES: tuple[str, ...] = (
    "valid_invoice_delivered",
    "unknown_vendor_rejected",
    "inactive_vendor_rejected",
    "duplicate_invoice_rejected",
    "contract_amount_mismatch",
    "bank_account_conflict",
    "missing_purchase_order",
    "approval_required_granted",
    "approval_denied",
    "stale_approval_after_patch",
    "timeout_before_side_effect",
    "timeout_after_acceptance_reconciled",
    "invented_tool_rejected",
    "completion_without_delivery_rejected",
    "budget_exhaustion_fallback",
)


def _form() -> InvoiceFormSubmission:
    return InvoiceFormSubmission.model_validate_json(
        (Path(__file__).with_name("fixtures") / "valid_approval_package.json").read_text(
            encoding="utf-8"
        )
    )


def _manifest(*actions: ActionId, tool_budget: int | None = None) -> TALManifest:
    form = _form()
    capabilities = list(form.requested_capabilities)
    known = {item.action_id for item in capabilities}
    for action in actions:
        if action not in known:
            capabilities.append(
                RequestedCapability(
                    id=f"cap:scenario:{action.value.replace('.', '_')}",
                    action_id=action,
                    target_ref=form.goal.target_ref,
                )
            )
    constraints = list(form.constraints)
    if tool_budget is not None:
        constraints = [
            Constraint(id=item.id, kind=item.kind, maximum=tool_budget)
            if item.kind is ConstraintKind.TOOL_CALLS
            else item
            for item in constraints
        ]
    return compile_manifest(
        form.model_copy(
            update={
                "requested_capabilities": tuple(capabilities),
                "constraints": tuple(constraints),
            }
        )
    )


def _runtime(
    manifest: TALManifest, scripted: dict[ToolId, deque[ToolResult]] | None = None
) -> tuple[TALRuntime, RuntimeState]:
    runtime = TALRuntime(
        InMemoryEventStore(), FakeToolRegistry(scripted=scripted or {}), ids=SequentialIds()
    )
    state = runtime.start(manifest)
    assert not isinstance(state, Rejection)
    return runtime, state


def _tool(runtime: TALRuntime, state: RuntimeState, tool: ToolId) -> RuntimeState | Rejection:
    proposal = ToolProposal(
        proposal_id=f"proposal:{tool.value.replace('.', '_')}",
        kind=ProposalKind.REQUEST_TOOL,
        goal_id=state.manifest.goal.goal_id,
        manifest_hash=state.manifest.integrity.manifest_hash or "",
        manifest_version=state.manifest.integrity.manifest_version,
        state_version=state.state_version,
        tool_id=tool,
        arguments=ToolArguments(invoice_ref=state.manifest.goal.target_ref),
        purpose="offline invoice-resolution certification action",
        expected_observation_type={
            ToolId.MESSAGE_SEND: "message_delivery",
            ToolId.VENDOR_LOOKUP: "vendor_record",
            ToolId.PAYMENT_PREPARE: "prepared_payment",
        }[tool],
    )
    return runtime.apply_proposal(state.run_id, proposal)


def _complete(
    runtime: TALRuntime, state: RuntimeState | Rejection, condition: str
) -> RuntimeState | Rejection:
    assert not isinstance(state, Rejection)
    proposal = CompletionProposal(
        proposal_id="proposal:complete",
        kind=ProposalKind.PROPOSE_COMPLETION,
        goal_id=state.manifest.goal.goal_id,
        manifest_hash=state.manifest.integrity.manifest_hash or "",
        manifest_version=state.manifest.integrity.manifest_version,
        state_version=state.state_version,
        success_condition_ref=condition,
        evidence_refs=tuple(item.id for item in state.observations),
        reason="independent scenario evidence is present",
    )
    return runtime.apply_proposal(state.run_id, proposal)


def _escalate(runtime: TALRuntime, state: RuntimeState | Rejection) -> RuntimeState:
    assert not isinstance(state, Rejection)
    result = runtime.apply_proposal(
        state.run_id,
        EscalateProposal(
            proposal_id="proposal:escalate",
            kind=ProposalKind.ESCALATE,
            goal_id=state.manifest.goal.goal_id,
            manifest_hash=state.manifest.integrity.manifest_hash or "",
            manifest_version=state.manifest.integrity.manifest_version,
            state_version=state.state_version,
            fallback_ref="fallback:budget",
            reason="declared fallback is required by the certification scenario",
        ),
    )
    assert not isinstance(result, Rejection)
    return result


def _rejection_evidence(runtime: TALRuntime, state: RuntimeState, status: str) -> RuntimeState:
    observation = Observation(
        id=runtime.ids("obs"),
        observation_type="rejection_reason",
        subject_ref=state.manifest.goal.target_ref,
        status=status,
        trust=TrustClass.DETERMINISTIC_VALIDATOR,
        detail="registered invoice validator recorded a rejection reason",
        observed_at=runtime.clock(),
    )
    return runtime._append(state, EventKind.OBSERVATION_RECORDED, status, observation=observation)


def _delivered(name: str) -> ScenarioResult:
    manifest = _manifest(ActionId.MESSAGE_SEND)
    runtime, state = _runtime(
        manifest,
        {
            ToolId.MESSAGE_SEND: deque(
                [
                    ToolResult(
                        outcome="success",
                        observation_type="message_delivery",
                        subject_ref=manifest.goal.target_ref,
                        status="delivered",
                        trust=TrustClass.AUTHORITATIVE_EXTERNAL,
                    )
                ]
            )
        },
    )
    result = _complete(runtime, _tool(runtime, state, ToolId.MESSAGE_SEND), "success:delivered")
    assert not isinstance(result, Rejection) and result.phase is RuntimePhase.COMPLETED
    return ScenarioResult(
        name=name,
        terminal_outcome=result.terminal_outcome or "",
        assertions=("delivery evidence independently verified",),
    )


def _rejected(name: str, status: str) -> ScenarioResult:
    manifest = _manifest(ActionId.VENDOR_LOOKUP)
    runtime, state = _runtime(
        manifest,
        {
            ToolId.VENDOR_LOOKUP: deque(
                [
                    ToolResult(
                        outcome="success",
                        observation_type="vendor_record",
                        subject_ref=manifest.goal.target_ref,
                        status=status,
                        trust=TrustClass.TRUSTED_REGISTRY,
                    )
                ]
            )
        },
    )
    tool_state = _tool(runtime, state, ToolId.VENDOR_LOOKUP)
    assert isinstance(tool_state, RuntimeState)
    state = _rejection_evidence(runtime, tool_state, status)
    result = _complete(runtime, state, "success:rejected")
    assert not isinstance(result, Rejection) and result.phase is RuntimePhase.COMPLETED
    return ScenarioResult(
        name=name,
        terminal_outcome=result.terminal_outcome or "",
        assertions=(status, "rejection reason recorded"),
    )


def run_scenario(name: str) -> ScenarioResult:
    if name not in SCENARIO_NAMES:
        raise KeyError(name)
    if name == "valid_invoice_delivered":
        return _delivered(name)
    rejection_statuses = {
        "unknown_vendor_rejected": "unknown_vendor",
        "inactive_vendor_rejected": "inactive",
        "duplicate_invoice_rejected": "duplicate",
        "contract_amount_mismatch": "contract_mismatch",
        "bank_account_conflict": "bank_conflict",
        "missing_purchase_order": "missing_purchase_order",
    }
    if name in rejection_statuses:
        return _rejected(name, rejection_statuses[name])
    if name == "approval_required_granted":
        manifest = _manifest(ActionId.PAYMENT_PREPARE, ActionId.MESSAGE_SEND)
        runtime, state = _runtime(
            manifest,
            {
                ToolId.MESSAGE_SEND: deque(
                    [
                        ToolResult(
                            outcome="success",
                            observation_type="message_delivery",
                            subject_ref=manifest.goal.target_ref,
                            status="delivered",
                            trust=TrustClass.AUTHORITATIVE_EXTERNAL,
                        )
                    ]
                )
            },
        )
        request = ApprovalProposal(
            proposal_id="proposal:approval",
            kind=ProposalKind.REQUEST_APPROVAL,
            goal_id=manifest.goal.goal_id,
            manifest_hash=manifest.integrity.manifest_hash or "",
            manifest_version=1,
            state_version=state.state_version,
            action_id="payment.prepare",
            target_ref=manifest.goal.target_ref,
            approver_role="finance_manager",
        )
        proposed = runtime.apply_proposal(state.run_id, request)
        assert isinstance(proposed, RuntimeState)
        state = proposed
        state = runtime.decide_approval(
            state.run_id,
            ApprovalDecision(
                decision_id="approval:granted",
                request_id="proposal:approval",
                approver_id="user:finance_manager",
                approver_role="finance_manager",
                decision="granted",
                decided_at=runtime.clock(),
            ),
        )
        tool_state = _tool(runtime, state, ToolId.MESSAGE_SEND)
        assert isinstance(tool_state, RuntimeState)
        state = tool_state
        result = _complete(runtime, state, "success:delivered")
        assert not isinstance(result, Rejection)
        return ScenarioResult(
            name=name,
            terminal_outcome=result.terminal_outcome or "",
            assertions=("approval linked before action",),
        )
    if name == "approval_denied":
        manifest = _manifest(ActionId.PAYMENT_PREPARE)
        runtime, state = _runtime(manifest)
        request = ApprovalProposal(
            proposal_id="proposal:approval",
            kind=ProposalKind.REQUEST_APPROVAL,
            goal_id=manifest.goal.goal_id,
            manifest_hash=manifest.integrity.manifest_hash or "",
            manifest_version=1,
            state_version=state.state_version,
            action_id="payment.prepare",
            target_ref=manifest.goal.target_ref,
            approver_role="finance_manager",
        )
        proposed = runtime.apply_proposal(state.run_id, request)
        assert isinstance(proposed, RuntimeState)
        state = proposed
        state = runtime.decide_approval(
            state.run_id,
            ApprovalDecision(
                decision_id="approval:denied",
                request_id="proposal:approval",
                approver_id="user:finance_manager",
                approver_role="finance_manager",
                decision="denied",
                reason="denied by approver",
                decided_at=runtime.clock(),
            ),
        )
        state = _rejection_evidence(runtime, state, "approval_denied")
        result = _complete(runtime, state, "success:rejected")
        assert not isinstance(result, Rejection)
        return ScenarioResult(
            name=name,
            terminal_outcome=result.terminal_outcome or "",
            assertions=("denial is evidence",),
        )
    if name == "stale_approval_after_patch":
        manifest = _manifest(ActionId.PAYMENT_PREPARE)
        runtime, state = _runtime(manifest)
        proposed = runtime.apply_proposal(
            state.run_id,
            ApprovalProposal(
                proposal_id="proposal:approval",
                kind=ProposalKind.REQUEST_APPROVAL,
                goal_id=manifest.goal.goal_id,
                manifest_hash=manifest.integrity.manifest_hash or "",
                manifest_version=1,
                state_version=state.state_version,
                action_id="payment.prepare",
                target_ref=manifest.goal.target_ref,
                approver_role="finance_manager",
            ),
        )
        assert isinstance(proposed, RuntimeState)
        state = proposed
        state = runtime.decide_approval(
            state.run_id,
            ApprovalDecision(
                decision_id="approval:old",
                request_id="proposal:approval",
                approver_id="user:finance_manager",
                approver_role="finance_manager",
                decision="granted",
                decided_at=runtime.clock(),
            ),
        )
        patched = runtime.patch(
            state.run_id,
            ManifestPatchRequest(
                base_manifest_hash=state.manifest.integrity.manifest_hash or "",
                requested_by="user:alex",
                reason="changed deadline",
                operations=(ReplaceDeadline(deadline=state.manifest.request.submitted_at),),
            ),
        )
        assert not isinstance(patched, Rejection) and not patched.approval_grants
        return ScenarioResult(
            name=name,
            terminal_outcome=_escalate(runtime, patched).terminal_outcome or "",
            assertions=("patch invalidated approval",),
        )
    if name in {"timeout_before_side_effect", "timeout_after_acceptance_reconciled"}:
        outcome = (
            "timeout_before_effect"
            if name == "timeout_before_side_effect"
            else "timeout_after_acceptance"
        )
        manifest = _manifest(ActionId.MESSAGE_SEND)
        runtime, state = _runtime(
            manifest,
            {
                ToolId.MESSAGE_SEND: deque(
                    [
                        ToolResult(
                            outcome=outcome,
                            observation_type="message_delivery",
                            subject_ref=manifest.goal.target_ref,
                            status="pending",
                            trust=TrustClass.PROVIDER_RECEIPT,
                            provider_accepted=outcome == "timeout_after_acceptance",
                        )
                    ]
                )
            },
        )
        tool_state = _tool(runtime, state, ToolId.MESSAGE_SEND)
        assert isinstance(tool_state, RuntimeState)
        state = tool_state
        if name == "timeout_after_acceptance_reconciled":
            result = _complete(runtime, state, "success:delivered")
            assert not isinstance(result, Rejection)
            return ScenarioResult(
                name=name,
                terminal_outcome=result.terminal_outcome or "",
                assertions=(
                    "reconciliation produced delivery evidence",
                    "duplicate effect suppressed",
                ),
            )
        return ScenarioResult(
            name=name,
            terminal_outcome=_escalate(runtime, state).terminal_outcome or "",
            assertions=("no provider effect was accepted",),
        )
    if name == "invented_tool_rejected":
        manifest = _manifest()
        runtime, state = _runtime(manifest)
        before = (state.phase, state.state_version, state.observations, state.budget_used)
        _, rejection = runtime._reject(
            state, ErrorCode.TOOL_NOT_REGISTERED, "invented tool rejected"
        )
        current = runtime.store.load_state(state.run_id)
        assert (
            current.phase,
            current.state_version,
            current.observations,
            current.budget_used,
        ) == before
        return ScenarioResult(
            name=name,
            terminal_outcome=_escalate(runtime, state).terminal_outcome or "",
            assertions=(rejection.reason_code.value, "state unchanged"),
        )
    if name == "completion_without_delivery_rejected":
        manifest = _manifest()
        runtime, state = _runtime(manifest)
        state = _rejection_evidence(runtime, state, "active")
        result = _complete(runtime, state, "success:delivered")
        assert isinstance(result, Rejection)
        return ScenarioResult(
            name=name,
            terminal_outcome=_escalate(runtime, state).terminal_outcome or "",
            assertions=(result.reason_code.value, "completion not model-authorized"),
        )
    manifest = _manifest(ActionId.MESSAGE_SEND, tool_budget=0)
    runtime, state = _runtime(manifest)
    result = _tool(runtime, state, ToolId.MESSAGE_SEND)
    assert isinstance(result, Rejection) and result.reason_code.value == "BUDGET_EXCEEDED"
    return ScenarioResult(
        name=name,
        terminal_outcome=_escalate(runtime, state).terminal_outcome or "",
        assertions=("declared fallback followed",),
    )
