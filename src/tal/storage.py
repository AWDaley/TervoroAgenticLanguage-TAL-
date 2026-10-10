"""Append-only memory and SQLite storage under one optimistic-concurrency protocol."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Protocol

from .events import Event, RuntimeState, reduce_event, seal_event


class ConcurrencyError(RuntimeError):
    pass


class EventStore(Protocol):
    def load_state(self, run_id: str) -> RuntimeState: ...
    def load_events(self, run_id: str) -> tuple[Event, ...]: ...
    def append(
        self,
        run_id: str,
        expected_state_version: int,
        expected_ledger_sequence: int,
        events: tuple[Event, ...],
    ) -> RuntimeState: ...
    def create(self, state: RuntimeState) -> None: ...


@dataclass
class InMemoryEventStore:
    _states: dict[str, RuntimeState] = field(default_factory=dict)
    _events: dict[str, list[Event]] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock)

    def create(self, state: RuntimeState) -> None:
        with self._lock:
            if state.run_id in self._states:
                raise ConcurrencyError("run already exists")
            self._states[state.run_id] = state
            self._events[state.run_id] = []

    def load_state(self, run_id: str) -> RuntimeState:
        with self._lock:
            return self._states[run_id]

    def load_events(self, run_id: str) -> tuple[Event, ...]:
        with self._lock:
            return tuple(self._events[run_id])

    def append(
        self,
        run_id: str,
        expected_state_version: int,
        expected_ledger_sequence: int,
        events: tuple[Event, ...],
    ) -> RuntimeState:
        with self._lock:
            state = self._states[run_id]
            if state.state_version != expected_state_version:
                raise ConcurrencyError("stale state version")
            if state.ledger_sequence != expected_ledger_sequence:
                raise ConcurrencyError("stale ledger sequence")
            candidate = state
            next_sequence = len(self._events[run_id]) + 1
            for event in events:
                if event.ledger_sequence != next_sequence:
                    raise ValueError("event sequence mismatch")
                sealed = seal_event(event, candidate.ledger_head_hash)
                candidate = reduce_event(candidate, sealed)
                event = sealed
                next_sequence += 1
                self._events[run_id].append(event)
            self._states[run_id] = candidate
            return candidate


class SQLiteEventStore:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS tal_v3_runs ("
            "run_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, state_version INTEGER NOT NULL, "
            "ledger_sequence INTEGER NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS tal_v3_events (run_id TEXT NOT NULL, "
            "ledger_sequence INTEGER NOT NULL, "
            "event_json TEXT NOT NULL, PRIMARY KEY (run_id, ledger_sequence), "
            "FOREIGN KEY(run_id) REFERENCES tal_v3_runs(run_id))"
        )

    def create(self, state: RuntimeState) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO tal_v3_runs(run_id, state_json, state_version, ledger_sequence) "
                "VALUES(?,?,?,?)",
                (state.run_id, state.model_dump_json(), state.state_version, state.ledger_sequence),
            )

    def load_state(self, run_id: str) -> RuntimeState:
        row = self._connection.execute(
            "SELECT state_json FROM tal_v3_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RuntimeState.model_validate_json(row[0])

    def load_events(self, run_id: str) -> tuple[Event, ...]:
        rows = self._connection.execute(
            "SELECT event_json FROM tal_v3_events WHERE run_id=? ORDER BY ledger_sequence",
            (run_id,),
        ).fetchall()
        return tuple(Event.model_validate_json(row[0]) for row in rows)

    def append(
        self,
        run_id: str,
        expected_state_version: int,
        expected_ledger_sequence: int,
        events: tuple[Event, ...],
    ) -> RuntimeState:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            state = self.load_state(run_id)
            if state.state_version != expected_state_version:
                raise ConcurrencyError("stale state version")
            if state.ledger_sequence != expected_ledger_sequence:
                raise ConcurrencyError("stale ledger sequence")
            candidate = state
            row = self._connection.execute(
                "SELECT COALESCE(MAX(ledger_sequence), 0) FROM tal_v3_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            next_sequence = int(row[0]) + 1
            for event in events:
                if event.ledger_sequence != next_sequence:
                    raise ValueError("event sequence mismatch")
                event = seal_event(event, candidate.ledger_head_hash)
                candidate = reduce_event(candidate, event)
                self._connection.execute(
                    "INSERT INTO tal_v3_events(run_id, ledger_sequence, event_json) VALUES(?,?,?)",
                    (run_id, event.ledger_sequence, event.model_dump_json()),
                )
                next_sequence += 1
            result = self._connection.execute(
                "UPDATE tal_v3_runs SET state_json=?, state_version=?, ledger_sequence=? "
                "WHERE run_id=? AND state_version=? AND ledger_sequence=?",
                (
                    candidate.model_dump_json(),
                    candidate.state_version,
                    candidate.ledger_sequence,
                    run_id,
                    expected_state_version,
                    expected_ledger_sequence,
                ),
            )
            if result.rowcount != 1:
                raise ConcurrencyError("stale state version")
            self._connection.execute("COMMIT")
            return candidate
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._connection.close()
