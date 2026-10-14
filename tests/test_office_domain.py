"""Office-domain tests intentionally make no external provider calls."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tal.core import sha256
from tal.office import (
    OfficeAdmissionStatus,
    OfficeApprovalEvidence,
    OfficeCalendarGrantDraft,
    OfficeCalendarProposal,
    TrustedOfficeCalendarEvidence,
    admit_office_calendar,
    compile_office_calendar_grant,
    verify_office_calendar_grant,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
START = NOW + timedelta(days=1)
END = START + timedelta(minutes=30)
MANIFEST_HASH = sha256({"test_manifest": "synthetic_only"})


def make_inputs(*, approval: bool = False):
    draft = OfficeCalendarGrantDraft(
        organization_id="org_test", task_id="task_test", initiator_id="actor_test",
        source_manifest_hash=MANIFEST_HASH, source_manifest_version=1,
        calendar_ref="cal_opaque", customer_ref="cust_opaque", template_id="followup",
        allowed_start=START, allowed_end=START + timedelta(hours=3),
        time_zone="America/New_York", max_duration_minutes=60,
        max_cycles=4, max_external_effects=1,
        requires_approval=approval, task_deadline=END + timedelta(days=1),
    )
    grant = compile_office_calendar_grant(draft)
    SOURCE_PROPOSAL_HASH = sha256({"source_proposal": "synthetic_proposal"})
    proposal = OfficeCalendarProposal(
        organization_id="org_test", task_id="task_test", initiator_id="actor_test",
        source_manifest_hash=MANIFEST_HASH, source_proposal_hash=SOURCE_PROPOSAL_HASH,
        calendar_ref="cal_opaque",
        customer_ref="cust_opaque", template_id="followup",
        start_at=START, end_at=END, time_zone="America/New_York",
        projection_rev="projection_1", simulation_ref="simulation_1",
    )
    evidence = TrustedOfficeCalendarEvidence(
        organization_id="org_test", task_id="task_test", initiator_id="actor_test",
        source_manifest_hash=MANIFEST_HASH, source_proposal_hash=SOURCE_PROPOSAL_HASH,
        projection_rev="projection_1",
        simulation_projection_rev="projection_1", simulation_ref="simulation_1",
        projection_expires_at=NOW + timedelta(minutes=10),
        projected_calendar_ref="cal_opaque", projected_customer_ref="cust_opaque",
        projected_template_id="followup", projected_start_at=START,
        projected_end_at=END, expected_postconditions=("event_created", "matching_time"),
        phase="SIMULATED", state_version=3, cycles_used=0, effects_used=0,
    )
    return grant, proposal, evidence


def test_clean_calendar_is_admission_not_effect_authorization():
    grant, proposal, evidence = make_inputs()
    decision = admit_office_calendar(grant, proposal, evidence, now=NOW)
    assert decision.status == OfficeAdmissionStatus.ELIGIBLE
    assert decision.authorizes_execution is False
    assert verify_office_calendar_grant(grant)
    assert compile_office_calendar_grant(OfficeCalendarGrantDraft.model_validate(
        grant.model_dump(exclude={"grant_hash"})
    )) == grant


@pytest.mark.parametrize("changes,code", [
    ({"source_manifest_hash": sha256("fake")}, "TASK_OR_MANIFEST_BINDING_INVALID"),
    ({"calendar_ref": "other_calendar"}, "TARGET_NOT_GRANTED"),
    ({"customer_ref": "other_customer"}, "TARGET_NOT_GRANTED"),
    ({"template_id": "other_template"}, "TARGET_NOT_GRANTED"),
    ({"start_at": START - timedelta(seconds=1)}, "APPOINTMENT_OUTSIDE_AUTHORITY"),
    ({"end_at": END + timedelta(hours=3)}, "APPOINTMENT_OUTSIDE_AUTHORITY"),
    ({"projection_rev": "bogus"}, "SIMULATION_UNVERIFIED"),
    ({"simulation_ref": "bogus"}, "SIMULATION_UNVERIFIED"),
])
def test_proposal_attack_denied(changes, code):
    g, p, e = make_inputs()
    assert admit_office_calendar(g, p.model_copy(update=changes), e, now=NOW).reason_code == code


@pytest.mark.parametrize("changes,code", [
    ({"organization_id": "foreign_org"}, "TASK_OR_MANIFEST_BINDING_INVALID"),
    ({"initiator_id": "foreign_actor"}, "TASK_OR_MANIFEST_BINDING_INVALID"),
    ({"projection_expires_at": NOW}, "PROJECTION_EXPIRED"),
    ({"simulation_conflicts": ("BOOKING_OVERLAP",)}, "SIMULATION_UNVERIFIED"),
    ({"simulation_projection_rev": "bogus"}, "SIMULATION_UNVERIFIED"),
    ({"projected_customer_ref": "different"}, "SIMULATION_CHANGE_MISMATCH"),
    ({"cycles_used": 4}, "BUDGET_EXHAUSTED"),
    ({"effects_used": 1}, "BUDGET_EXHAUSTED"),
])
def test_observation_attack_denied(changes, code):
    g, p, e = make_inputs()
    assert admit_office_calendar(g, p, e.model_copy(update=changes), now=NOW).reason_code == code


def test_grant_tamper_denied():
    g, p, e = make_inputs()
    changed = g.model_copy(update={"max_external_effects": 99})
    assert admit_office_calendar(changed, p, e, now=NOW).reason_code == "GRANT_HASH_INVALID"


def test_approval_requires_independent_exact_bound_receipt():
    g, p, e = make_inputs(approval=True)
    assert admit_office_calendar(g, p, e, now=NOW).status == OfficeAdmissionStatus.APPROVAL_REQUIRED
    e = e.model_copy(update={"phase": "READY_TO_COMMIT"})
    assert admit_office_calendar(g, p, e, now=NOW).status == OfficeAdmissionStatus.DENIED
    receipt = OfficeApprovalEvidence(
        approved=True, approver_id="independent_manager", manifest_hash=MANIFEST_HASH,
        proposal_hash=p.source_proposal_hash, expires_at=NOW + timedelta(minutes=5),
    )
    approved = e.model_copy(update={"approval": receipt})
    assert admit_office_calendar(g, p, approved, now=NOW).status == OfficeAdmissionStatus.ELIGIBLE
    for changed in ({"approver_id": "actor_test"}, {"proposal_hash": sha256("other")},
                    {"expires_at": NOW}):
        forged = approved.model_copy(update={"approval": receipt.model_copy(update=changed)})
        assert admit_office_calendar(g, p, forged, now=NOW).status == OfficeAdmissionStatus.DENIED


def test_closed_contracts_reject_unknown_fields_and_naive_times():
    g, p, e = make_inputs()
    with pytest.raises(ValidationError):
        OfficeCalendarProposal.model_validate({**p.model_dump(), "unsupported": "execute_shell"})
    with pytest.raises(ValidationError):
        OfficeCalendarProposal.model_validate(
            {**p.model_dump(), "start_at": datetime(2026, 10, 14)}
        )
    with pytest.raises(ValidationError):
        OfficeCalendarGrantDraft.model_validate(
            {**g.model_dump(exclude={"grant_hash"}), "operation": "email.send"}
        )


def test_expired_task_and_zero_budget_denied():
    g, p, e = make_inputs()
    result = admit_office_calendar(g, p, e, now=g.task_deadline)
    assert result.status == OfficeAdmissionStatus.DENIED
    altered = g.model_copy(update={"max_external_effects": 0})
    resealed = compile_office_calendar_grant(OfficeCalendarGrantDraft.model_validate(
        altered.model_dump(exclude={"grant_hash"})
    ))
    assert admit_office_calendar(resealed, p, e, now=NOW).status == OfficeAdmissionStatus.DENIED
