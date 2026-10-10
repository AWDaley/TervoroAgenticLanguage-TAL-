from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from tal.adapters import (
    ApprovalProposal,
    CompletionProposal,
    FakeToolRegistry,
    ToolArguments,
    ToolProposal,
    ToolResult,
    parse_proposal_json,
)
from tal.cli import app
from tal.compiler import compile_manifest, verify_manifest_hash
from tal.core import ErrorCode, ProposalKind, Rejection, RuntimePhase, TrustClass
from tal.events import ApprovalDecision, EventKind, Observation
from tal.invoice import InvoiceFormSubmission, ToolId
from tal.patches import ManifestPatchRequest, ReplaceDeadline
from tal.runtime import SequentialIds, TALRuntime
from tal.scenarios import SCENARIO_NAMES, run_scenario
from tal.storage import InMemoryEventStore, SQLiteEventStore


def fixture_manifest():
    form = InvoiceFormSubmission.model_validate_json(
        Path("src/tal/fixtures/valid_approval_package.json").read_text(encoding="utf-8")
    )
    return compile_manifest(form)


def delivered_runtime() -> tuple[TALRuntime, object]:
    manifest = fixture_manifest()
    tools = FakeToolRegistry(
        scripted={
            ToolId.MESSAGE_SEND: deque(
                [
                    ToolResult(
                        outcome="success",
                        observation_type="message_delivery",
                        subject_ref="invoice:I-452",
                        status="delivered",
                        trust=TrustClass.AUTHORITATIVE_EXTERNAL,
                    )
                ]
            )
        }
    )
    runtime = TALRuntime(InMemoryEventStore(), tools, ids=SequentialIds())
    state = runtime.start(manifest)
    assert hasattr(state, "run_id")
    return runtime, state


def tool_proposal(manifest, state, tool_id=ToolId.MESSAGE_SEND):
    return ToolProposal(
        proposal_id="proposal:tool",
        kind=ProposalKind.REQUEST_TOOL,
        goal_id=manifest.goal.goal_id,
        manifest_hash=manifest.integrity.manifest_hash or "",
        manifest_version=manifest.integrity.manifest_version,
        state_version=state.state_version,
        tool_id=tool_id,
        arguments=ToolArguments(invoice_ref=manifest.goal.target_ref),
        purpose="fixture action",
        expected_observation_type={
            ToolId.MESSAGE_SEND: "message_delivery",
            ToolId.VENDOR_LOOKUP: "vendor_record",
        }[tool_id],
    )


def test_unknown_fields_fail_closed() -> None:
    raw = Path("src/tal/fixtures/valid_approval_package.json").read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        InvoiceFormSubmission.model_validate_json(
            raw.replace('"request":', '"unknown": 1, "request":')
        )


def test_observations_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Observation(
            id="obs:naive",
            observation_type="fixture",
            subject_ref="invoice:I-452",
            status="ok",
            trust=TrustClass.TRUSTED_REGISTRY,
            observed_at=datetime(2026, 8, 17, 12, 0, 0),
        )


def test_canonical_manifest_is_stable() -> None:
    first = fixture_manifest()
    second = fixture_manifest()
    assert verify_manifest_hash(first)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.integrity.manifest_hash == second.integrity.manifest_hash


def test_requested_payment_submit_is_denied() -> None:
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    state = runtime.start(fixture_manifest())
    assert hasattr(state, "denied_capabilities")
    assert any(item.action_id == "payment.submit" for item in state.denied_capabilities)


def test_offline_certification_command_runs_all_registered_gates() -> None:
    result = CliRunner().invoke(app, ["certify", "all", "--offline"])
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["status"] == "certified"
    assert report["scenarios"] == len(SCENARIO_NAMES)
    assert report["topologies"] == 5


def test_rejected_proposal_does_not_mutate_or_call_tool() -> None:
    runtime, state = delivered_runtime()
    before = (state.phase, state.state_version, state.observations, state.budget_used)
    proposal = tool_proposal(state.manifest, state, ToolId.VENDOR_LOOKUP).model_copy(
        update={"state_version": state.state_version + 1}
    )
    result = runtime.apply_proposal(state.run_id, proposal)
    assert result.reason_code is ErrorCode.STALE_STATE_VERSION
    after = runtime.store.load_state(state.run_id)
    assert (after.phase, after.state_version, after.observations, after.budget_used) == before
    assert runtime.tools.calls == []


def test_audit_rejection_advances_only_the_ledger() -> None:
    runtime, state = delivered_runtime()
    proposal = tool_proposal(state.manifest, state).model_copy(
        update={"state_version": state.state_version + 1}
    )
    result = runtime.apply_proposal(state.run_id, proposal)
    assert isinstance(result, Rejection)
    after = runtime.store.load_state(state.run_id)
    assert after.state_version == state.state_version
    assert after.ledger_sequence == state.ledger_sequence + 1
    assert after.ledger_head_hash != state.ledger_head_hash


def test_typed_approval_is_bound_to_its_request_and_manifest() -> None:
    runtime, state = delivered_runtime()
    request = ApprovalProposal(
        proposal_id="proposal:approval-bound",
        kind=ProposalKind.REQUEST_APPROVAL,
        goal_id=state.manifest.goal.goal_id,
        manifest_hash=state.manifest.integrity.manifest_hash or "",
        manifest_version=state.manifest.integrity.manifest_version,
        state_version=state.state_version,
        action_id="message.send",
        target_ref=state.manifest.goal.target_ref,
        approver_role="finance_manager",
    )
    waiting = runtime.apply_proposal(state.run_id, request)
    assert not isinstance(waiting, Rejection)
    assert waiting.pending_approval_requests[0].request_id == request.proposal_id
    with pytest.raises(ValueError, match="pending request"):
        runtime.decide_approval(
            state.run_id,
            ApprovalDecision(
                decision_id="approval:wrong",
                request_id="proposal:other",
                approver_id="user:finance_manager",
                approver_role="finance_manager",
                decision="granted",
                decided_at=runtime.clock(),
            ),
        )
    approved = runtime.decide_approval(
        state.run_id,
        ApprovalDecision(
            decision_id="approval:bound",
            request_id=request.proposal_id,
            approver_id="user:finance_manager",
            approver_role="finance_manager",
            decision="granted",
            decided_at=runtime.clock(),
        ),
    )
    assert approved.approval_grants[0].target_ref == state.manifest.goal.target_ref
    assert runtime.replay(state.run_id).model_dump(mode="json") == approved.model_dump(mode="json")


def test_delivered_package_completes_and_replays() -> None:
    runtime, state = delivered_runtime()
    state = runtime.apply_proposal(state.run_id, tool_proposal(state.manifest, state))
    assert state.phase is RuntimePhase.READY
    completion = CompletionProposal(
        proposal_id="proposal:complete",
        kind=ProposalKind.PROPOSE_COMPLETION,
        goal_id=state.manifest.goal.goal_id,
        manifest_hash=state.manifest.integrity.manifest_hash or "",
        manifest_version=state.manifest.integrity.manifest_version,
        state_version=state.state_version,
        success_condition_ref="success:delivered",
        evidence_refs=(state.observations[-1].id,),
        reason="delivery was observed",
    )
    state = runtime.apply_proposal(state.run_id, completion)
    assert state.phase is RuntimePhase.COMPLETED
    assert runtime.replay(state.run_id).model_dump(mode="json") == state.model_dump(mode="json")


def test_completion_without_delivery_is_rejected_without_mutation() -> None:
    runtime, state = delivered_runtime()
    observation = Observation(
        id="obs:other",
        observation_type="vendor_record",
        subject_ref="invoice:I-452",
        status="active",
        trust=TrustClass.TRUSTED_REGISTRY,
        observed_at=runtime.clock(),
    )
    state = runtime._append(
        state, EventKind.OBSERVATION_RECORDED, "active", observation=observation
    )
    before = (state.phase, state.state_version, state.observations, state.budget_used)
    completion = CompletionProposal(
        proposal_id="proposal:bad-complete",
        kind=ProposalKind.PROPOSE_COMPLETION,
        goal_id=state.manifest.goal.goal_id,
        manifest_hash=state.manifest.integrity.manifest_hash or "",
        manifest_version=state.manifest.integrity.manifest_version,
        state_version=state.state_version,
        success_condition_ref="success:delivered",
        evidence_refs=(observation.id,),
        reason="unsupported",
    )
    result = runtime.apply_proposal(state.run_id, completion)
    assert result.reason_code is ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT
    current = runtime.store.load_state(state.run_id)
    assert (
        current.phase,
        current.state_version,
        current.observations,
        current.budget_used,
    ) == before


def test_patch_creates_new_version_and_invalidates_approval() -> None:
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    state = runtime.start(fixture_manifest())
    request = ManifestPatchRequest(
        base_manifest_hash=state.manifest.integrity.manifest_hash or "",
        requested_by="user:alex",
        reason="new deadline",
        operations=(ReplaceDeadline(deadline=state.manifest.request.submitted_at),),
    )
    state = runtime.patch(state.run_id, request)
    assert state.manifest.integrity.manifest_version == 2
    assert state.manifest.integrity.parent_manifest_hash
    assert state.approval_grants == ()


def test_sqlite_store_replays(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "tal.db")
    runtime = TALRuntime(store, FakeToolRegistry(), ids=SequentialIds())
    state = runtime.start(fixture_manifest())
    assert runtime.replay(state.run_id).model_dump(mode="json") == state.model_dump(mode="json")
    store.close()


def test_replay_rejects_a_tampered_ledger_event() -> None:
    runtime, state = delivered_runtime()
    store = runtime.store
    assert isinstance(store, InMemoryEventStore)
    original = store._events[state.run_id][0]
    store._events[state.run_id][0] = original.model_copy(update={"message": "tampered"})
    with pytest.raises(ValueError, match="digest"):
        runtime.replay(state.run_id)


def test_every_certification_scenario_reaches_a_terminal_outcome() -> None:
    outcomes = {run_scenario(name).terminal_outcome for name in SCENARIO_NAMES}
    assert outcomes >= {"approval_package_delivered", "invoice_rejected", "escalated"}


def test_model_output_is_discriminated_and_closed() -> None:
    manifest = fixture_manifest()
    payload = ToolProposal(
        proposal_id="proposal:parse",
        kind=ProposalKind.REQUEST_TOOL,
        goal_id=manifest.goal.goal_id,
        manifest_hash=manifest.integrity.manifest_hash or "",
        manifest_version=1,
        state_version=0,
        tool_id=ToolId.MESSAGE_SEND,
        arguments=ToolArguments(invoice_ref=manifest.goal.target_ref),
        purpose="strict parsing test",
        expected_observation_type="message_delivery",
    ).model_dump_json()
    assert parse_proposal_json(payload).kind is ProposalKind.REQUEST_TOOL
    with pytest.raises(ValidationError):
        parse_proposal_json(payload[:-1] + ',"invented":true}')


def test_timeout_after_acceptance_is_reconciled_without_second_provider_call() -> None:
    manifest = fixture_manifest()
    tools = FakeToolRegistry(
        scripted={
            ToolId.MESSAGE_SEND: deque(
                [
                    ToolResult(
                        outcome="timeout_after_acceptance",
                        observation_type="message_delivery",
                        subject_ref=manifest.goal.target_ref,
                        status="pending",
                        trust=TrustClass.PROVIDER_RECEIPT,
                        provider_accepted=True,
                    )
                ]
            )
        }
    )
    runtime = TALRuntime(InMemoryEventStore(), tools, ids=SequentialIds())
    state = runtime.start(manifest)
    assert not isinstance(state, Rejection)
    state = runtime.apply_proposal(state.run_id, tool_proposal(manifest, state))
    assert not isinstance(state, Rejection)
    assert state.observations[-1].status == "delivered"
    assert len(tools.calls) == 1


def test_completed_idempotent_effect_is_reused_without_a_second_provider_call() -> None:
    runtime, state = delivered_runtime()
    first = runtime.apply_proposal(state.run_id, tool_proposal(state.manifest, state))
    assert not isinstance(first, Rejection)
    repeat = tool_proposal(first.manifest, first).model_copy(
        update={"proposal_id": "proposal:retry"}
    )
    second = runtime.apply_proposal(first.run_id, repeat)
    assert not isinstance(second, Rejection)
    assert len(runtime.tools.calls) == 1
    assert second.budget_used == first.budget_used
    assert second.effect_records[0].status == "succeeded"
    assert runtime.replay(second.run_id).model_dump(mode="json") == second.model_dump(mode="json")
