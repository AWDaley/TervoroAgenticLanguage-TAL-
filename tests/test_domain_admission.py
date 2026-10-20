"""Pure synthetic domain grants are admission evidence, never bearer authority."""

import pytest
from pydantic import ValidationError

from tal.domain_admission import (
    DomainActionProposal,
    DomainAdmissionStatus,
    DomainCapability,
    DomainCustomerConfirmation,
    DomainGrantDraft,
    TrustedDomainEvidence,
    admit_domain_action,
    compile_domain_grant,
    domain_arguments_hash,
)

H = "sha256:" + "0" * 64


def fixture():
    grant = compile_domain_grant(
        DomainGrantDraft(
            episode_id="episode1",
            domain="retail",
            policy_hash=H,
            max_tool_calls=10,
            capabilities=(
                DomainCapability(operation="read", effect="read"),
                DomainCapability(
                    operation="write",
                    effect="write",
                    requires_subject=True,
                    requires_authentication=True,
                    requires_customer_confirmation=True,
                ),
            ),
        )
    )
    proposal = DomainActionProposal(
        episode_id="episode1",
        domain="retail",
        policy_hash=H,
        operation="write",
        effective_operation="write",
        arguments_json='{"amount":1.25}',
        arguments_hash=domain_arguments_hash('{"amount":1.25}'),
        subject_id="customer1",
    )
    evidence = TrustedDomainEvidence(
        episode_id="episode1",
        domain="retail",
        policy_hash=H,
        observation_hash=H,
        expected_observation_hash=H,
        state_version=1,
        expected_state_version=1,
        subject_id="customer1",
        authenticated=True,
        effective_operation="write",
        mechanical_checks_passed=True,
        scope_valid=True,
        tool_calls_used=0,
        latest_user_turn_id="user1",
        confirmation=DomainCustomerConfirmation(
            episode_id="episode1",
            subject_id="customer1",
            policy_hash=H,
            effect_hash=proposal.effect_hash,
            user_turn_id="user1",
        ),
    )
    return grant, proposal, evidence


def test_eligible_never_authorizes_execution_or_certifies_semantic_policy():
    result = admit_domain_action(*fixture())
    assert result.status == DomainAdmissionStatus.ELIGIBLE
    assert not result.authorizes_execution and not result.semantic_policy_verified


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"episode_id": "different"}, "DOMAIN_BINDING_INVALID"),
        ({"expected_state_version": 2}, "DOMAIN_OBSERVATION_STALE"),
        ({"expected_observation_hash": "sha256:" + "1" * 64}, "DOMAIN_OBSERVATION_STALE"),
        ({"subject_id": "foreign"}, "DOMAIN_SUBJECT_MISMATCH"),
        ({"authenticated": False}, "DOMAIN_AUTHENTICATION_REQUIRED"),
        ({"mechanical_checks_passed": False}, "DOMAIN_MECHANICAL_CHECK_FAILED"),
        ({"scope_valid": False}, "DOMAIN_MECHANICAL_CHECK_FAILED"),
        ({"tool_calls_used": 10}, "DOMAIN_TOOL_BUDGET_EXHAUSTED"),
        ({"unresolved_write": True}, "DOMAIN_RECONCILIATION_REQUIRED"),
        ({"latest_user_turn_id": "later"}, "DOMAIN_CUSTOMER_CONFIRMATION_STALE"),
    ],
)
def test_bound_evidence_rejects_stale_or_invalid(change, reason):
    grant, proposal, evidence = fixture()
    decision = admit_domain_action(grant, proposal, evidence.model_copy(update=change))
    assert decision.status == DomainAdmissionStatus.DENIED and decision.reason_code == reason


def test_missing_confirmation_returns_actionable_status_after_scope_checks():
    grant, proposal, evidence = fixture()
    decision = admit_domain_action(
        grant, proposal, evidence.model_copy(update={"confirmation": None})
    )
    assert decision.status == DomainAdmissionStatus.CONFIRMATION_REQUIRED
    evidence = evidence.model_copy(update={"confirmation": None, "authenticated": False})
    assert admit_domain_action(grant, proposal, evidence).status == DomainAdmissionStatus.DENIED


def test_model_copy_does_not_bypass_seal_or_argument_hash():
    grant, proposal, evidence = fixture()
    changed = grant.model_copy(update={"max_tool_calls": 100})
    assert (
        admit_domain_action(changed, proposal, evidence).reason_code == "DOMAIN_GRANT_HASH_INVALID"
    )
    changed_proposal = proposal.model_copy(update={"arguments_json": '{"amount":999}'})
    assert (
        admit_domain_action(grant, changed_proposal, evidence).reason_code
        == "DOMAIN_PROPOSAL_OR_EVIDENCE_INVALID"
    )


def test_dispatcher_cannot_launder_target_or_outer_effect():
    capabilities = (
        DomainCapability(operation="dispatch", effect="write", dispatches=("private_read",)),
        DomainCapability(operation="private_read", effect="read", directly_callable=False),
        DomainCapability(operation="not_granted_target", effect="write", directly_callable=False),
    )
    grant, proposal, evidence = fixture()
    grant = compile_domain_grant(
        DomainGrantDraft(
            episode_id="episode1",
            domain="retail",
            policy_hash=H,
            max_tool_calls=10,
            capabilities=capabilities,
        )
    )
    proposal = proposal.model_copy(
        update={"operation": "dispatch", "effective_operation": "private_read"}
    )
    evidence = evidence.model_copy(
        update={"effective_operation": "private_read", "confirmation": None}
    )
    assert admit_domain_action(grant, proposal, evidence).status == DomainAdmissionStatus.ELIGIBLE
    assert (
        admit_domain_action(
            grant, proposal, evidence.model_copy(update={"unresolved_write": True})
        ).reason_code
        == "DOMAIN_RECONCILIATION_REQUIRED"
    )
    changed = proposal.model_copy(update={"effective_operation": "not_granted_target"})
    assert (
        admit_domain_action(grant, changed, evidence).reason_code
        == "DOMAIN_DISPATCH_TARGET_NOT_GRANTED"
    )
    direct = proposal.model_copy(update={"operation": "private_read"})
    assert (
        admit_domain_action(grant, direct, evidence).reason_code == "DOMAIN_OPERATION_NOT_GRANTED"
    )


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', "[1]", '{ "a":1}'])
def test_strict_transport_hash(raw):
    with pytest.raises(ValueError):
        domain_arguments_hash(raw)


def test_exact_unicode_bytes_are_distinct_and_unknown_fields_rejected():
    assert domain_arguments_hash('{"v":"é"}') != domain_arguments_hash('{"v":"e\u0301"}')
    with pytest.raises(ValidationError):
        DomainCapability(operation="read", effect="read", execution_authorized=True)
