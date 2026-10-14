"""A TAL authorization is neither legacy admission nor a bearer capability."""
from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from test_office_domain import NOW, make_inputs
from tal.adapters import FakeToolRegistry
from tal.core import sha256
from tal.office import OfficeApprovalEvidence, OfficeRuntimeAction
from tal.office_execution import (
    OfficeExecutionAuthorization,
    OfficeExecutionAuthorizationDenied,
    prepare_office_execution_authorization,
    require_office_execution_authorization,
)
from tal.runtime import TALRuntime
from tal.storage import InMemoryEventStore


ACTION_HASH = sha256({"action": "test_only"})


def command(*, approval: bool = False) -> OfficeRuntimeAction:
    grant, proposal, evidence = make_inputs(approval=approval)
    if approval:
        evidence = evidence.model_copy(update={
            "approval": OfficeApprovalEvidence(
                approved=True, approver_id="supervisor_test",
                manifest_hash=grant.source_manifest_hash,
                proposal_hash=proposal.source_proposal_hash,
                expires_at=NOW + timedelta(seconds=20),
            ),
        })
    evidence = evidence.model_copy(update={"phase": "EXECUTING"})
    return OfficeRuntimeAction(
        grant=grant, proposal=proposal, evidence=evidence,
        expected_state_version=evidence.state_version,
        outbox_action_hash=ACTION_HASH,
    )


def runtime(*, later: timedelta = timedelta()) -> TALRuntime:
    return TALRuntime(
        InMemoryEventStore(), FakeToolRegistry(), clock=lambda: NOW + later,
    )


def prepared(cmd: OfficeRuntimeAction) -> OfficeExecutionAuthorization:
    return runtime().prepare_office_execution_authorization(
        "task_test", cmd, actor_id="actor_test",
    )


def test_runtime_prepares_exact_non_bearer_record_and_leaves_invoice_tools_unchanged() -> None:
    rt = runtime()
    cmd = command()
    admission = rt.apply_proposal("task_test", cmd, actor_id="actor_test")
    assert admission.authorizes_execution is False
    authorization = rt.prepare_office_execution_authorization(
        "task_test", cmd, actor_id="actor_test",
    )
    assert authorization.schema_version == "tal.office.execution_authorization.v1"
    assert authorization.outbox_action_hash == ACTION_HASH
    assert authorization.source_manifest_hash == cmd.grant.source_manifest_hash
    assert authorization.source_proposal_hash == cmd.proposal.source_proposal_hash
    assert authorization.approval_hash is None
    assert authorization.expires_at == NOW + timedelta(seconds=60)
    rt.require_office_execution_authorization(
        authorization, cmd, actor_id="actor_test",
        state_version=cmd.evidence.state_version,
    )
    assert rt.tools.calls == []
    assert rt.store._states == {}


@pytest.mark.parametrize("mutated", ["organization_id", "outbox_action_hash", "state_version", "calendar_ref", "source_manifest_hash", "approval_hash"])
def test_tampered_authorization_never_reaches_a_write_barrier(mutated: str) -> None:
    cmd = command()
    good = prepared(cmd)
    changes = {
        "organization_id": "another_org",
        "outbox_action_hash": sha256("other_action"),
        "state_version": good.state_version + 1,
        "calendar_ref": "other_calendar",
        "source_manifest_hash": sha256("forged_manifest"),
        "approval_hash": sha256("forged_approval"),
    }
    invalid = good.model_copy(update={mutated: changes[mutated]})
    with pytest.raises((OfficeExecutionAuthorizationDenied, ValidationError)):
        require_office_execution_authorization(
            invalid, cmd, run_id="task_test", actor_id="actor_test",
            state_version=cmd.evidence.state_version, now=NOW,
        )


def test_runtime_rejects_wrong_identity_missing_action_stale_budget_and_phase() -> None:
    cmd = command()
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        runtime().prepare_office_execution_authorization(
            "task_test", cmd, actor_id="other_actor",
        )
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        runtime().prepare_office_execution_authorization(
            "other_task", cmd, actor_id="actor_test",
        )
    without_outbox = cmd.model_copy(update={"outbox_action_hash": None})
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        prepared(without_outbox)
    out_of_budget = cmd.model_copy(update={
        "evidence": cmd.evidence.model_copy(update={"effects_used": 1}),
    })
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        prepared(out_of_budget)
    simulated = cmd.model_copy(update={
        "evidence": cmd.evidence.model_copy(update={"phase": "SIMULATED"}),
    })
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        prepared(simulated)


def test_time_state_version_and_changed_action_expire_authority() -> None:
    cmd = command()
    record = prepared(cmd)
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        require_office_execution_authorization(
            record, cmd, run_id="task_test", actor_id="actor_test",
            state_version=record.state_version + 1, now=NOW,
        )
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        require_office_execution_authorization(
            record, cmd, run_id="task_test", actor_id="actor_test",
            state_version=record.state_version, now=record.expires_at,
        )
    other = cmd.model_copy(update={"outbox_action_hash": sha256("other_action")})
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        require_office_execution_authorization(
            record, other, run_id="task_test", actor_id="actor_test",
            state_version=record.state_version, now=NOW,
        )


def test_approval_hash_and_expiry_are_bound_at_creation_and_dispatch() -> None:
    cmd = command(approval=True)
    record = prepared(cmd)
    assert record.approval_hash == sha256(cmd.evidence.approval)
    assert record.expires_at == NOW + timedelta(seconds=20)
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        require_office_execution_authorization(
            record, cmd, run_id="task_test", actor_id="actor_test",
            state_version=record.state_version, now=NOW + timedelta(seconds=21),
        )
    changed = cmd.model_copy(update={
        "evidence": cmd.evidence.model_copy(update={
            "approval": cmd.evidence.approval.model_copy(update={
                "approver_id": "different_supervisor",
            }),
        }),
    })
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        require_office_execution_authorization(
            record, changed, run_id="task_test", actor_id="actor_test",
            state_version=record.state_version, now=NOW,
        )


def test_legacy_admission_does_not_substitute_for_authorization() -> None:
    cmd = command()
    admission = runtime().apply_proposal("task_test", cmd, actor_id="actor_test")
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        require_office_execution_authorization(
            admission, cmd, run_id="task_test", actor_id="actor_test",
            state_version=cmd.evidence.state_version, now=NOW,
        )
    with pytest.raises(ValidationError):
        OfficeExecutionAuthorization.model_validate({
            **prepared(cmd).model_dump(mode="json"),
            "new_permission": "execute_anything",
        })


def test_cannot_prepare_using_forged_admission_even_if_it_says_eligible() -> None:
    cmd = command()
    good = runtime().apply_proposal("task_test", cmd, actor_id="actor_test")
    forged = good.model_copy(update={"state_version": 123})
    with pytest.raises(OfficeExecutionAuthorizationDenied):
        prepare_office_execution_authorization(
            cmd, forged, run_id="task_test", actor_id="actor_test", now=NOW,
        )
