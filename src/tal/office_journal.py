"""Durable, hash-linked office admission and reconciliation journal.

This is an additive TAL *audit* subsystem. It cannot execute provider tools,
produce an executable authorization or infer a verified effect from an LLM claim.
Admission originates exclusively from TAL's typed office admission function;
reconciliation evidence must originate from the trusted verifier. SQLite is
for local/research use; callers needing concurrent production DB transactions
must provide an independently reviewed storage implementation.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import Field, model_validator

from .core import Hash, Identifier, StrictModel, sha256
from .office import (
    OfficeAdmissionDecision,
    OfficeRuntimeAction,
    admit_runtime_office_action,
)


class OfficeJournalIntegrityError(ValueError):
    """A duplicate, out-of-order, cross-action or corrupted journal is never trusted."""


class OfficeJournalKind(StrEnum):
    ADMISSION = "ADMISSION"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class OfficeVerifiedEvidence(StrictModel):
    """Trusted verifier input (not an LLM output or an executable permit)."""

    schema_version: Literal["tal.office.verified_evidence.v1"] = (
        "tal.office.verified_evidence.v1"
    )
    action_hash: Hash
    task_id: Identifier
    source_manifest_hash: Hash
    source_proposal_hash: Hash
    observation_hash: Hash  # Digest of independent postcondition observation
    provider_kind: Literal["synthetic"] = "synthetic"
    observed_at: datetime

    @model_validator(mode="after")
    def check_observation(self) -> OfficeVerifiedEvidence:
        if self.observed_at.utcoffset() is None:
            raise ValueError("observation timestamp must be timezone-aware")
        return self


class OfficeJournalEvent(StrictModel):
    schema_version: Literal["tal.office.journal_event.v1"] = "tal.office.journal_event.v1"
    kind: OfficeJournalKind
    task_id: Identifier
    action_hash: Hash
    source_manifest_hash: Hash
    source_proposal_hash: Hash
    grant_hash: Hash
    admission_hash: Hash
    action_payload_hash: Hash
    evidence_hash: Hash | None = None
    reason_code: str = Field(min_length=1, max_length=90)
    sequence: int = Field(ge=1)
    previous_event_hash: Hash | None = None
    occurred_at: datetime
    event_hash: Hash

    @model_validator(mode="after")
    def aware(self) -> OfficeJournalEvent:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("journal timestamps must be timezone-aware")
        return self


class OfficeJournalProjection(StrictModel):
    schema_version: Literal["tal.office.journal_projection.v1"] = (
        "tal.office.journal_projection.v1"
    )
    task_id: Identifier
    action_hash: Hash
    source_manifest_hash: Hash
    source_proposal_hash: Hash
    grant_hash: Hash
    admission_hash: Hash
    action_payload_hash: Hash
    state: OfficeJournalKind
    reason_code: str
    last_evidence_hash: Hash | None = None
    sequence: int
    ledger_head_hash: Hash


def _as_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("naive journal timestamp")
    return value.astimezone(UTC)


def _seal(raw: dict[str, object]) -> OfficeJournalEvent:
    data = {**raw, "event_hash": sha256(raw)}
    return OfficeJournalEvent.model_validate(data)


def _verify_event(event: OfficeJournalEvent) -> None:
    unsealed = event.model_dump(mode="python", exclude={"event_hash"})
    if sha256(unsealed) != event.event_hash:
        raise OfficeJournalIntegrityError("office event digest mismatch")


def replay_office_events(
    events: tuple[OfficeJournalEvent, ...],
) -> OfficeJournalProjection:
    """Pure deterministic replay; refuses gaps, changed bindings and illegal transitions."""
    if not events or events[0].kind != OfficeJournalKind.ADMISSION:
        raise OfficeJournalIntegrityError("office journal has no admission genesis")
    projection: OfficeJournalProjection | None = None
    head: str | None = None
    for index, event in enumerate(events, 1):
        _verify_event(event)
        if event.sequence != index or event.previous_event_hash != head:
            raise OfficeJournalIntegrityError("office ledger predecessor/sequence mismatch")
        if projection is None:
            if event.evidence_hash is not None:
                raise OfficeJournalIntegrityError("admission cannot assert provider evidence")
            projection = OfficeJournalProjection(
                task_id=event.task_id, action_hash=event.action_hash,
                source_manifest_hash=event.source_manifest_hash,
                source_proposal_hash=event.source_proposal_hash,
                grant_hash=event.grant_hash, admission_hash=event.admission_hash,
                action_payload_hash=event.action_payload_hash,
                state=event.kind, reason_code=event.reason_code,
                sequence=event.sequence, ledger_head_hash=event.event_hash,
            )
        else:
            if any((
                projection.task_id != event.task_id,
                projection.action_hash != event.action_hash,
                projection.source_manifest_hash != event.source_manifest_hash,
                projection.source_proposal_hash != event.source_proposal_hash,
                projection.grant_hash != event.grant_hash,
                projection.admission_hash != event.admission_hash,
                projection.action_payload_hash != event.action_payload_hash,
            )):
                raise OfficeJournalIntegrityError("office event changed pinned authority")
            if projection.state in (OfficeJournalKind.VERIFIED, OfficeJournalKind.NEEDS_REVIEW):
                raise OfficeJournalIntegrityError("terminal office result cannot transition")
            if event.kind not in (
                OfficeJournalKind.OUTCOME_UNKNOWN,
                OfficeJournalKind.VERIFIED,
                OfficeJournalKind.NEEDS_REVIEW,
            ):
                raise OfficeJournalIntegrityError("unrecognized office phase transition")
            if event.kind == OfficeJournalKind.VERIFIED and event.evidence_hash is None:
                raise OfficeJournalIntegrityError("completion without independent evidence")
            if event.kind != OfficeJournalKind.VERIFIED and event.evidence_hash is not None:
                raise OfficeJournalIntegrityError("unverified event includes completion evidence")
            projection = projection.model_copy(update={
                "state": event.kind, "reason_code": event.reason_code,
                "last_evidence_hash": event.evidence_hash,
                "sequence": index, "ledger_head_hash": event.event_hash,
            })
        head = event.event_hash
    assert projection is not None
    return projection


class SQLiteOfficeJournal:
    """Durable shadow office ledger; cannot authorize or dispatch effects.

    A confirmed provider effect must be independently read and verified by the
    trusted worker *before* record_verified is called. This class does not
    access providers and does not attest caller authenticity by itself.
    """

    def __init__(self, path: str | Path) -> None:
        self._lock = RLock()
        self._connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS tal_office_journal (
                action_hash TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                PRIMARY KEY (action_hash, sequence)
            )
        """)

    def close(self) -> None:
        self._connection.close()

    def events(self, action_hash: str) -> tuple[OfficeJournalEvent, ...]:
        with self._lock:
            return self._load(action_hash)

    def replay(self, action_hash: str) -> OfficeJournalProjection:
        with self._lock:
            return replay_office_events(self._load(action_hash))

    def _load(self, action_hash: str) -> tuple[OfficeJournalEvent, ...]:
        try:
            rows = self._connection.execute(
                "SELECT event_json FROM tal_office_journal WHERE action_hash=? ORDER BY sequence",
                (action_hash,),
            ).fetchall()
            records = tuple(OfficeJournalEvent.model_validate_json(row[0]) for row in rows)
        except (ValueError, TypeError) as error:
            raise OfficeJournalIntegrityError("office ledger data is corrupt") from error
        if records:
            projection = replay_office_events(records)
            if projection.action_hash != action_hash:
                raise OfficeJournalIntegrityError("office action key and stored event mismatch")
        return records

    def _append(self, event: OfficeJournalEvent) -> OfficeJournalProjection:
        # Lock + SQLite immediate write lock serialize across workers/processes.
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._load(event.action_hash)
                if len(current) + 1 != event.sequence or (
                    current[-1].event_hash if current else None
                ) != event.previous_event_hash:
                    raise OfficeJournalIntegrityError("concurrent office journal transition")
                candidate = replay_office_events((*current, event))
                self._connection.execute(
                    "INSERT INTO tal_office_journal (action_hash, sequence, event_json) "
                    "VALUES (?, ?, ?)",
                    (event.action_hash, event.sequence, event.model_dump_json()),
                )
                self._connection.execute("COMMIT")
                return candidate
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def record_admission(
        self, action: OfficeRuntimeAction, decision: OfficeAdmissionDecision,
        *, run_id: str, actor_id: str, now: datetime,
    ) -> OfficeJournalProjection:
        """Seal the result of the *actual deterministic TAL* office policy."""
        if action.outbox_action_hash is None or action.evidence.phase != "EXECUTING":
            raise OfficeJournalIntegrityError("only pinned executing outbox actions are journaled")
        expected = admit_runtime_office_action(action, run_id=run_id, actor_id=actor_id, now=now)
        if expected != decision:
            raise OfficeJournalIntegrityError("office admission decision was not deterministic")
        payload_hash = sha256(action)
        existing = self.events(action.outbox_action_hash)
        if existing:
            first = existing[0]
            if (
                first.action_payload_hash != payload_hash
                or first.admission_hash != sha256(decision)
                or first.source_manifest_hash != action.grant.source_manifest_hash
                or first.source_proposal_hash != action.proposal.source_proposal_hash
            ):
                raise OfficeJournalIntegrityError(
                    "attempted reuse of action with changed authority"
                )
            return replay_office_events(existing)
        record = _seal({
            "schema_version": "tal.office.journal_event.v1",
            "kind": OfficeJournalKind.ADMISSION,
            "task_id": action.grant.task_id,
            "action_hash": action.outbox_action_hash,
            "source_manifest_hash": action.grant.source_manifest_hash,
            "source_proposal_hash": action.proposal.source_proposal_hash,
            "grant_hash": action.grant.grant_hash,
            "admission_hash": sha256(decision),
            "action_payload_hash": payload_hash,
            "reason_code": decision.reason_code,
            "sequence": 1,
            "previous_event_hash": None,
            "evidence_hash": None,
            "occurred_at": _as_utc(now),
        })
        return self._append(record)

    def record_outcome(
        self, *, action_hash: str, kind: OfficeJournalKind,
        evidence: OfficeVerifiedEvidence | None = None, reason_code: str,
        now: datetime,
    ) -> OfficeJournalProjection:
        """Record external evidence supplied by the trusted verifier, not a model."""
        if kind not in (
            OfficeJournalKind.OUTCOME_UNKNOWN, OfficeJournalKind.VERIFIED,
            OfficeJournalKind.NEEDS_REVIEW,
        ):
            raise OfficeJournalIntegrityError("invalid office outcome kind")
        if (kind is OfficeJournalKind.VERIFIED) != (evidence is not None):
            raise OfficeJournalIntegrityError("verified result requires exactly one receipt")
        current = self.replay(action_hash)
        if current.state in (OfficeJournalKind.VERIFIED, OfficeJournalKind.NEEDS_REVIEW):
            if kind is OfficeJournalKind.VERIFIED and current.state is kind and evidence:
                if current.last_evidence_hash == sha256(evidence):
                    return current  # Identical at-least-once verifier callback.
            raise OfficeJournalIntegrityError("office result already terminal")
        admitted = self.events(action_hash)[0]
        if admitted.reason_code != "TRUSTED_ADMISSION_ONLY":
            raise OfficeJournalIntegrityError("denied office action has no effect lifecycle")
        if evidence and (
            evidence.action_hash != current.action_hash
            or evidence.task_id != current.task_id
            or evidence.source_manifest_hash != current.source_manifest_hash
            or evidence.source_proposal_hash != current.source_proposal_hash
            or evidence.observed_at > _as_utc(now)
        ):
            raise OfficeJournalIntegrityError("independent evidence binding invalid")
        last = self.events(action_hash)[-1]
        event = _seal({
            "schema_version": "tal.office.journal_event.v1",
            "kind": kind,
            "task_id": current.task_id,
            "action_hash": current.action_hash,
            "source_manifest_hash": current.source_manifest_hash,
            "source_proposal_hash": current.source_proposal_hash,
            "grant_hash": current.grant_hash,
            "admission_hash": current.admission_hash,
            "action_payload_hash": current.action_payload_hash,
            "evidence_hash": sha256(evidence) if evidence else None,
            "reason_code": reason_code,
            "sequence": current.sequence + 1,
            "previous_event_hash": last.event_hash,
            "occurred_at": _as_utc(now),
        })
        return self._append(event)
