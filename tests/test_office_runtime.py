"""Deterministic first-class runtime office branch; NEVER an external execution permit."""
from __future__ import annotations

from datetime import timedelta

from tal.office import (
    OfficeRuntimeAction, OfficeAdmissionStatus, admit_runtime_office_action,
)
from tests.test_office_domain import NOW, make_inputs


def action(*, requires_approval: bool = False) -> OfficeRuntimeAction:
    grant, proposal, evidence = make_inputs(approval=requires_approval)
    evidence = evidence.model_copy(update={"phase": "READY_TO_COMMIT"})
    return OfficeRuntimeAction(grant=grant, proposal=proposal,
                               evidence=evidence, expected_state_version=evidence.state_version)


def test_no_effect_authority_even_on_valid_runtime_request() -> None:
    result = admit_runtime_office_action(action(),run_id="task_test",actor_id="actor_test",now=NOW)
    assert result.status is OfficeAdmissionStatus.ELIGIBLE
    assert result.authorizes_execution is False


def test_runtime_rejects_untrusted_actor_and_run_id() -> None:
    cmd = action()
    assert admit_runtime_office_action(cmd,run_id="other_task",actor_id="actor_test",now=NOW).status is OfficeAdmissionStatus.DENIED
    assert admit_runtime_office_action(cmd,run_id="task_test",actor_id=None,now=NOW).reason_code == "RUNTIME_ACTOR_MISMATCH"
    assert admit_runtime_office_action(cmd,run_id="task_test",actor_id="intruder",now=NOW).status is OfficeAdmissionStatus.DENIED


def test_stale_version_and_phase_denied() -> None:
    cmd=action()
    stale=cmd.model_copy(update={"expected_state_version":cmd.expected_state_version+1})
    assert admit_runtime_office_action(stale,run_id="task_test",actor_id="actor_test",now=NOW).reason_code == "RUNTIME_STALE_STATE_VERSION"
    simulated=cmd.model_copy(update={"evidence":cmd.evidence.model_copy(update={"phase":"SIMULATED"})})
    assert admit_runtime_office_action(simulated,run_id="task_test",actor_id="actor_test",now=NOW).reason_code == "RUNTIME_INVALID_EXECUTION_PHASE"


def test_runtime_requires_exact_approval_and_valid_freshness() -> None:
    cmd=action(requires_approval=True)
    assert admit_runtime_office_action(cmd,run_id="task_test",actor_id="actor_test",now=NOW).status is OfficeAdmissionStatus.DENIED
    late=action()
    assert admit_runtime_office_action(late,run_id="task_test",actor_id="actor_test",now=NOW+timedelta(hours=1)).reason_code == "PROJECTION_EXPIRED"


def test_invoice_runtime_routes_office_action_without_creating_effect() -> None:
    """Run in upstream TAL CI; stub-only local test setup deliberately skips."""
    from pytest import importorskip

    runtime_module = importorskip("tal.runtime")
    adapters = importorskip("tal.adapters")
    storage = importorskip("tal.storage")
    runtime = runtime_module.TALRuntime(
        storage.InMemoryEventStore(), adapters.FakeToolRegistry(), clock=lambda: NOW,
    )
    cmd = action()
    result = runtime.apply_proposal("task_test", cmd, actor_id="actor_test")
    assert result.status is OfficeAdmissionStatus.ELIGIBLE
    assert result.authorizes_execution is False
    assert runtime.tools.calls == []
    assert runtime.store._states == {}  # office admission does not create invoice runs
    assert runtime.apply_proposal("wrong", cmd, actor_id="actor_test").status is OfficeAdmissionStatus.DENIED
