"""Durable office admission auditing; no provider effects or credential handling."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_office_domain import NOW, make_inputs

from tal.core import sha256
from tal.office import (
    OfficeAdmissionStatus,
    OfficeRuntimeAction,
    admit_runtime_office_action,
)
from tal.office_journal import (
    OfficeJournalIntegrityError,
    OfficeJournalKind,
    OfficeVerifiedEvidence,
    SQLiteOfficeJournal,
    replay_office_events,
)


def _action() -> OfficeRuntimeAction:
    grant, proposal, evidence = make_inputs()
    evidence = evidence.model_copy(update={"phase": "EXECUTING"})
    return OfficeRuntimeAction(
        grant=grant, proposal=proposal, evidence=evidence,
        expected_state_version=evidence.state_version,
        outbox_action_hash="sha256:" + "a" * 64,
    )


def _decision(action: OfficeRuntimeAction):
    return admit_runtime_office_action(
        action, run_id=action.grant.task_id,
        actor_id=action.grant.initiator_id, now=NOW,
    )


def _admit(journal: SQLiteOfficeJournal, action: OfficeRuntimeAction):
    return journal.record_admission(
        action, _decision(action), run_id=action.grant.task_id,
        actor_id=action.grant.initiator_id, now=NOW,
    )


def _evidence(action: OfficeRuntimeAction):
    assert action.outbox_action_hash is not None
    return OfficeVerifiedEvidence(
        action_hash=action.outbox_action_hash,
        task_id=action.grant.task_id,
        source_manifest_hash=action.grant.source_manifest_hash,
        source_proposal_hash=action.proposal.source_proposal_hash,
        observation_hash="sha256:" + "f" * 64,
        observed_at=NOW + timedelta(seconds=5),
    )


def test_durable_admission_idempotency_and_replay(tmp_path) -> None:
    path = tmp_path / "office.sqlite"
    cmd = _action()
    j = SQLiteOfficeJournal(path)
    initial = _admit(j, cmd)
    assert initial.state is OfficeJournalKind.ADMISSION
    assert initial.sequence == 1
    assert _admit(j, cmd) == initial  # Same action only recorded once.
    assert len(j.events(cmd.outbox_action_hash)) == 1
    j.close()
    again = SQLiteOfficeJournal(path)
    assert again.replay(cmd.outbox_action_hash) == initial
    again.close()


def test_unknown_then_verified_after_independent_receipt(tmp_path) -> None:
    cmd = _action()
    j = SQLiteOfficeJournal(tmp_path / "office.sqlite")
    _admit(j, cmd)
    unknown = j.record_outcome(
        action_hash=cmd.outbox_action_hash, kind=OfficeJournalKind.OUTCOME_UNKNOWN,
        reason_code="WRITE_RESULT_LOST", now=NOW + timedelta(seconds=1),
    )
    assert unknown.state is OfficeJournalKind.OUTCOME_UNKNOWN
    assert unknown.last_evidence_hash is None
    verified = j.record_outcome(
        action_hash=cmd.outbox_action_hash, kind=OfficeJournalKind.VERIFIED,
        evidence=_evidence(cmd), reason_code="SYNTHETIC_READBACK_MATCHED",
        now=NOW + timedelta(seconds=6),
    )
    assert verified.state is OfficeJournalKind.VERIFIED
    assert verified.last_evidence_hash == sha256(_evidence(cmd))
    assert j.record_outcome(
        action_hash=cmd.outbox_action_hash, kind=OfficeJournalKind.VERIFIED,
        evidence=_evidence(cmd), reason_code="SYNTHETIC_READBACK_MATCHED",
        now=NOW + timedelta(seconds=7),
    ) == verified
    with pytest.raises(OfficeJournalIntegrityError):
        j.record_outcome(action_hash=cmd.outbox_action_hash,
                         kind=OfficeJournalKind.NEEDS_REVIEW,
                         reason_code="CANNOT_CHANGE_VERIFIED", now=NOW + timedelta(seconds=8))


def test_denied_office_admission_cannot_become_verified(tmp_path) -> None:
    cmd = _action()
    altered = cmd.model_copy(update={"expected_state_version": cmd.expected_state_version + 1})
    decision = _decision(altered)
    assert decision.status is OfficeAdmissionStatus.DENIED
    journal = SQLiteOfficeJournal(tmp_path / "denied.sqlite")
    journal.record_admission(altered, decision, run_id=altered.grant.task_id,
                             actor_id=altered.grant.initiator_id, now=NOW)
    with pytest.raises(OfficeJournalIntegrityError, match="denied"):
        journal.record_outcome(action_hash=altered.outbox_action_hash,
                               kind=OfficeJournalKind.OUTCOME_UNKNOWN,
                               reason_code="UNRESOLVED", now=NOW + timedelta(seconds=1))
    with pytest.raises(OfficeJournalIntegrityError, match="denied"):
        journal.record_outcome(action_hash=altered.outbox_action_hash,
                               kind=OfficeJournalKind.VERIFIED,
                               evidence=_evidence(altered),
                               reason_code="SHOULD_NEVER_PASS", now=NOW + timedelta(seconds=6))


def test_no_evidence_means_no_verified_result(tmp_path) -> None:
    cmd = _action()
    journal = SQLiteOfficeJournal(tmp_path / "j.sqlite")
    _admit(journal, cmd)
    with pytest.raises(OfficeJournalIntegrityError):
        journal.record_outcome(action_hash=cmd.outbox_action_hash,
                               kind=OfficeJournalKind.VERIFIED,
                               reason_code="NO_RECEIPT", now=NOW)
    with pytest.raises(OfficeJournalIntegrityError):
        journal.record_outcome(action_hash=cmd.outbox_action_hash,
                               kind=OfficeJournalKind.OUTCOME_UNKNOWN,
                               evidence=_evidence(cmd),
                               reason_code="FALSE_RECEIPT", now=NOW)
    with pytest.raises(ValidationError):
        OfficeVerifiedEvidence.model_validate({**_evidence(cmd).model_dump(),
                                               "provider_kind": "google"})


def test_cross_task_and_reused_action_refused(tmp_path) -> None:
    j = SQLiteOfficeJournal(tmp_path / "office.sqlite")
    cmd = _action()
    _admit(j, cmd)
    different = cmd.model_copy(update={"expected_state_version": cmd.expected_state_version + 1})
    with pytest.raises(OfficeJournalIntegrityError, match="reuse"):
        _admit(j, different)
    wrong = _evidence(cmd).model_copy(update={"task_id": "foreign_task"})
    with pytest.raises(OfficeJournalIntegrityError):
        j.record_outcome(action_hash=cmd.outbox_action_hash, kind=OfficeJournalKind.VERIFIED,
                         evidence=wrong, reason_code="WRONG_TARGET",
                         now=NOW + timedelta(seconds=6))


def test_terminal_needs_review_has_no_transition(tmp_path) -> None:
    j = SQLiteOfficeJournal(tmp_path / "office.sqlite")
    cmd = _action()
    _admit(j, cmd)
    final = j.record_outcome(action_hash=cmd.outbox_action_hash,
                             kind=OfficeJournalKind.NEEDS_REVIEW,
                             reason_code="CANNOT_PROVE_NO_DUPLICATE", now=NOW)
    assert final.state is OfficeJournalKind.NEEDS_REVIEW
    with pytest.raises(OfficeJournalIntegrityError):
        j.record_outcome(action_hash=cmd.outbox_action_hash, kind=OfficeJournalKind.VERIFIED,
                         evidence=_evidence(cmd), reason_code="UNTRUSTED_LATER_RESULT",
                         now=NOW + timedelta(seconds=6))


def test_hash_chain_detects_sql_tampering(tmp_path) -> None:
    path = tmp_path / "audit.sqlite"
    cmd = _action()
    j = SQLiteOfficeJournal(path)
    _admit(j, cmd)
    j.close()
    with sqlite3.connect(path) as raw:
        s = raw.execute("SELECT event_json FROM tal_office_journal").fetchone()[0]
        forged = json.loads(s)
        forged["reason_code"] = "FORGED_SUCCESS"
        raw.execute("UPDATE tal_office_journal SET event_json=?", (json.dumps(forged),))
    j = SQLiteOfficeJournal(path)
    with pytest.raises(OfficeJournalIntegrityError, match="digest"):
        j.replay(cmd.outbox_action_hash)
    with pytest.raises(OfficeJournalIntegrityError):
        _admit(j, cmd)


def test_replay_detects_ordering_and_rewritten_authority(tmp_path) -> None:
    j = SQLiteOfficeJournal(tmp_path / "j.sqlite")
    cmd = _action()
    _admit(j, cmd)
    first = j.events(cmd.outbox_action_hash)[0]
    with pytest.raises(OfficeJournalIntegrityError):
        replay_office_events((first, first))


def test_record_rejects_forged_decision_without_altering_ledger(tmp_path) -> None:
    cmd = _action()
    j = SQLiteOfficeJournal(tmp_path / "j.sqlite")
    forged = _decision(cmd).model_copy(update={"reason_code": "MODEL_SAYS_APPROVED"})
    with pytest.raises(OfficeJournalIntegrityError, match="deterministic"):
        j.record_admission(cmd, forged, run_id=cmd.grant.task_id,
                           actor_id=cmd.grant.initiator_id, now=NOW)
    assert j.events(cmd.outbox_action_hash) == ()


def test_tal_runtime_office_branch_records_without_invoking_invoice_tool(tmp_path) -> None:
    """Hosted TAL integration test; local standalone module may omit invoice runtime."""
    runtime_mod = pytest.importorskip("tal.runtime")
    adapters = pytest.importorskip("tal.adapters")
    storage = pytest.importorskip("tal.storage")
    journal = SQLiteOfficeJournal(tmp_path / "runtime.sqlite")
    runtime = runtime_mod.TALRuntime(
        storage.InMemoryEventStore(), adapters.FakeToolRegistry(),
        clock=lambda: NOW, office_journal=journal,
    )
    cmd = _action()
    decision = runtime.apply_proposal(cmd.grant.task_id, cmd, actor_id=cmd.grant.initiator_id)
    assert decision.status is OfficeAdmissionStatus.ELIGIBLE
    assert journal.replay(cmd.outbox_action_hash).sequence == 1
    assert runtime.tools.calls == []
    assert runtime.store._states == {}
    assert runtime.apply_proposal(cmd.grant.task_id, cmd,
                                  actor_id=cmd.grant.initiator_id) == decision
    assert len(journal.events(cmd.outbox_action_hash)) == 1
    forged = cmd.model_copy(update={
        "expected_state_version": cmd.expected_state_version + 1,
    })
    denied = runtime.apply_proposal(cmd.grant.task_id, forged, actor_id=cmd.grant.initiator_id)
    assert denied.status is OfficeAdmissionStatus.DENIED
    assert denied.reason_code == "OFFICE_JOURNAL_INTEGRITY_FAILURE"
    assert len(journal.events(cmd.outbox_action_hash)) == 1
