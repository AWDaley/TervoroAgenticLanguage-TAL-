"""Office runtime entrypoint conformance: never confers effect authority."""
from __future__ import annotations

from datetime import timedelta

from tal.office import (
    OfficeAdmissionStatus,
    OfficeRuntimeAction,
    admit_runtime_office_action,
)
from tests.test_office_domain import NOW, make_inputs


def action(*, requires_approval: bool = False) -> OfficeRuntimeAction:
    grant, proposal, evidence = make_inputs(approval=requires_approval)
    evidence = evidence.model_copy(update={"phase": "READY_TO_COMMIT"})
    return OfficeRuntimeAction(
        grant=grant,
        proposal=proposal,
        evidence=evidence,
        expected_state_version=evidence.state_version,
    )


def check(cmd: OfficeRuntimeAction, *, run_id: str = "task_test",
          actor_id: str | None = "actor_test", hours_later: int = 0):
    return admit_runtime_office_action(
        cmd, run_id=run_id, actor_id=actor_id,
        now=NOW + timedelta(hours=hours_later),
    )


def test_no_effect_authority_even_on_valid_runtime_request() -> None:
    result = check(action())
    assert result.status is OfficeAdmissionStatus.ELIGIBLE
    assert result.authorizes_execution is False


def test_runtime_rejects_untrusted_actor_and_run_id() -> None:
    cmd = action()
    assert check(cmd, run_id="other_task").status is OfficeAdmissionStatus.DENIED
    assert check(cmd, actor_id=None).reason_code == "RUNTIME_ACTOR_MISMATCH"
    assert check(cmd, actor_id="intruder").status is OfficeAdmissionStatus.DENIED


def test_stale_version_and_phase_denied() -> None:
    cmd = action()
    stale = cmd.model_copy(update={"expected_state_version": cmd.expected_state_version + 1})
    assert check(stale).reason_code == "RUNTIME_STALE_STATE_VERSION"
    simulated = cmd.model_copy(update={
        "evidence": cmd.evidence.model_copy(update={"phase": "SIMULATED"}),
    })
    assert check(simulated).reason_code == "RUNTIME_INVALID_EXECUTION_PHASE"


def test_runtime_requires_exact_approval_and_valid_freshness() -> None:
    assert check(action(requires_approval=True)).status is OfficeAdmissionStatus.DENIED
    assert check(action(), hours_later=1).reason_code == "PROJECTION_EXPIRED"


def test_invoice_runtime_routes_office_action_without_creating_effect() -> None:
    """Run in upstream TAL CI; stub-only local setup skips runtime import."""
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
    assert runtime.store._states == {}  # no invoice run started or changed
    assert runtime.apply_proposal(
        "wrong", cmd, actor_id="actor_test"
    ).status is OfficeAdmissionStatus.DENIED
