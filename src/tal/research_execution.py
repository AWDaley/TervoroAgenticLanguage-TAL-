"""Encrypted, transactional persistence for sealed TAL research episodes."""

from __future__ import annotations

import base64
import os
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field, JsonValue

from .core import Hash, Identifier, StrictModel, canonical_json, sha256
from .research import EpisodeResult, ProviderCallReceipt, verify_episode_result
from .research_study import StudySchedule, admit_result


class EpisodeTrace(StrictModel):
    """Controlled-access trace binding outputs and receipts to a sealed result."""

    episode_id: Identifier
    attempt_index: int = Field(default=0, ge=0, le=2)
    task_manifest_hash: Hash
    output_hash: Hash
    receipt_hashes: tuple[Hash, ...] = ()
    controlled_payload_hash: Hash | None = None
    trace_hash: Hash | None = None


def compile_episode_trace(trace: EpisodeTrace) -> EpisodeTrace:
    payload = trace.model_dump(mode="json", exclude_none=True)
    payload.pop("trace_hash", None)
    return trace.model_copy(update={"trace_hash": sha256(payload)})


def verify_episode_trace(trace: EpisodeTrace) -> bool:
    if trace.trace_hash is None:
        return False
    payload = trace.model_dump(mode="json", exclude_none=True)
    claimed = payload.pop("trace_hash", None)
    return bool(claimed == sha256(payload))


class ControlledTrace(StrictModel):
    """Sensitive payload retained only inside encrypted SQLite storage."""

    provider_requests: tuple[dict[str, JsonValue], ...] = ()
    provider_responses: tuple[dict[str, JsonValue], ...] = ()
    model_contexts: tuple[dict[str, JsonValue], ...] = ()
    model_outputs: tuple[str, ...] = ()
    tool_events: tuple[dict[str, JsonValue], ...] = ()
    judge_events: tuple[dict[str, JsonValue], ...] = ()


class PersistedEpisode(StrictModel):
    result: EpisodeResult
    trace: EpisodeTrace
    receipts: tuple[ProviderCallReceipt, ...] = ()
    controlled_trace: ControlledTrace = ControlledTrace()

    @property
    def episode_id(self) -> str:
        return self.result.episode_id


EpisodeExecutor = Callable[[str], PersistedEpisode]


def trace_key_from_environment(variable: str = "TAL_TRACE_KEY") -> bytes:
    encoded = os.environ.get(variable)
    if not encoded:
        raise ValueError(f"{variable} must contain a base64-encoded 32-byte trace key")
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError(f"{variable} must be valid base64") from error
    if len(key) != 32:
        raise ValueError(f"{variable} must decode to exactly 32 bytes")
    return key


def trace_key_fingerprint(key: bytes) -> Hash:
    if len(key) != 32:
        raise ValueError("trace key must contain exactly 32 bytes")
    return sha256({"trace_key": base64.b64encode(key).decode("ascii")})


class ResearchRunStore:
    """One SQLite transaction contains each result and its encrypted trace."""

    def __init__(self, root: Path, *, trace_key: bytes | None = None) -> None:
        self.root = root
        self.path = root / "research.sqlite"
        self.trace_key = trace_key if trace_key is not None else trace_key_from_environment()
        if len(self.trace_key) != 32:
            raise ValueError("trace key must contain exactly 32 bytes")
        self._cipher = AESGCM(self.trace_key)

    def initialize(self, schedule: StudySchedule) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        schedule_payload = canonical_json(schedule)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata "
                "(key TEXT PRIMARY KEY NOT NULL, value BLOB NOT NULL)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT NOT NULL,
                    attempt_index INTEGER NOT NULL,
                    result_json BLOB NOT NULL,
                    encrypted_trace BLOB NOT NULL,
                    nonce BLOB NOT NULL,
                    PRIMARY KEY (episode_id, attempt_index)
                )
                """
            )
            previous = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schedule'"
            ).fetchone()
            if previous is not None and bytes(previous[0]) != schedule_payload:
                raise ValueError("run directory already belongs to a different schedule")
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schedule', ?)",
                (schedule_payload,),
            )
            fingerprint = trace_key_fingerprint(self.trace_key).encode("utf-8")
            existing = connection.execute(
                "SELECT value FROM metadata WHERE key = 'trace_key_fingerprint'"
            ).fetchone()
            if existing is not None and bytes(existing[0]) != fingerprint:
                raise ValueError("run directory is encrypted with a different trace key")
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('trace_key_fingerprint', ?)",
                (fingerprint,),
            )

    def load_schedule(self) -> StudySchedule:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = 'schedule'").fetchone()
        if row is None:
            raise ValueError("research run store has not been initialized")
        return StudySchedule.model_validate_json(bytes(row[0]))

    def load_records(self) -> tuple[PersistedEpisode, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT episode_id, attempt_index, result_json, encrypted_trace, nonce "
                "FROM episodes ORDER BY rowid"
            ).fetchall()
        records: list[PersistedEpisode] = []
        for episode_id, attempt_index, result_json, encrypted, nonce in rows:
            try:
                payload = self._cipher.decrypt(
                    bytes(nonce), bytes(encrypted), _aad(str(episode_id), int(attempt_index))
                )
            except Exception as error:  # cryptography uses provider-specific exception types
                raise ValueError(f"cannot decrypt controlled trace for {episode_id}") from error
            record = PersistedEpisode.model_validate_json(payload)
            result = EpisodeResult.model_validate_json(bytes(result_json))
            if record.result.result_hash != result.result_hash:
                raise ValueError(f"result and controlled trace differ for {episode_id}")
            if not verify_episode_result(record.result) or not verify_episode_trace(record.trace):
                raise ValueError(f"unsealed persisted episode {episode_id}")
            records.append(record)
        return tuple(records)

    def append(self, record: PersistedEpisode, *, rerun_budget_microcents: int) -> None:
        self._validate_record(record)
        schedule = self.load_schedule()
        prior_records = self.load_records()
        admission = admit_result(
            record.result,
            schedule,
            tuple(item.result for item in prior_records),
            rerun_budget_microcents=rerun_budget_microcents,
        )
        if not admission.admitted:
            raise ValueError(admission.reason or "result is not admitted")
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(
            nonce,
            canonical_json(record),
            _aad(record.episode_id, record.result.attempt_index),
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO episodes(
                        episode_id, attempt_index, result_json, encrypted_trace, nonce
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.episode_id,
                        record.result.attempt_index,
                        canonical_json(record.result),
                        encrypted,
                        nonce,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _validate_record(self, record: PersistedEpisode) -> None:
        if not verify_episode_result(record.result) or not verify_episode_trace(record.trace):
            raise ValueError("result and trace must be sealed before persistence")
        if (
            record.trace.episode_id != record.result.episode_id
            or record.trace.attempt_index != record.result.attempt_index
        ):
            raise ValueError("trace must belong to its result episode")
        if record.trace.receipt_hashes != tuple(
            sha256(receipt.model_dump(mode="json")) for receipt in record.receipts
        ):
            raise ValueError("trace receipt hashes do not match receipts")
        controlled_hash = sha256(record.controlled_trace.model_dump(mode="json"))
        if record.trace.controlled_payload_hash not in {None, controlled_hash}:
            raise ValueError("trace controlled payload hash does not match payload")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class ResearchRunner:
    """Executes only frozen schedule entries and resumes from sealed records."""

    def __init__(self, store: ResearchRunStore, *, rerun_budget_microcents: int) -> None:
        self.store = store
        self.rerun_budget_microcents = rerun_budget_microcents

    def pending_episode_ids(self) -> tuple[str, ...]:
        schedule = self.store.load_schedule()
        completed = {record.episode_id for record in self.store.load_records()}
        return tuple(
            item.episode_id for item in schedule.episodes if item.episode_id not in completed
        )

    def run(
        self, executor: EpisodeExecutor, *, limit: int | None = None
    ) -> tuple[PersistedEpisode, ...]:
        pending = self.pending_episode_ids()
        if limit is not None:
            if limit < 1:
                raise ValueError("run limit must be positive")
            pending = pending[:limit]
        written: list[PersistedEpisode] = []
        for episode_id in pending:
            record = executor(episode_id)
            if record.episode_id != episode_id:
                raise ValueError("executor returned a result for a different episode")
            self.store.append(record, rerun_budget_microcents=self.rerun_budget_microcents)
            written.append(record)
        return tuple(written)


def public_release_payload(
    schedule: StudySchedule,
    records: Iterable[PersistedEpisode],
) -> dict[str, object]:
    """Return a publishable aggregate without output text, requests, or receipts."""

    results = tuple(record.result for record in records)
    return {
        "freeze_hash": schedule.freeze_hash,
        "schedule_hash": sha256(schedule.model_dump(mode="json")),
        "episodes_recorded": len(results),
        "results": [result.model_dump(mode="json") for result in results],
        "controlled_trace_policy": "AES-GCM encrypted raw traces excluded from public release",
    }


def write_public_release(
    root: Path, schedule: StudySchedule, records: Iterable[PersistedEpisode]
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "public-results.json"
    destination.write_bytes(canonical_json(public_release_payload(schedule, records)))
    return destination


def _aad(episode_id: str, attempt_index: int) -> bytes:
    return f"{episode_id}:{attempt_index}".encode("utf-8")
