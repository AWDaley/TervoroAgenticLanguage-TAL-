# TAL v0.3 implementation notes

## Authority boundary

`TALRuntime.action_gate` is the sole path from a model proposal to a state-changing
operation. It verifies goal/manifest/state versions, scope, effective capability,
arguments, approval state, budgets, idempotency, and output shape before committing
the corresponding event. A rejection is recorded as a non-projecting ledger event;
the authoritative `RuntimeState` is unchanged.

## Persistence and replay

`EventStore` has in-memory and SQLite implementations with optimistic state-version
checks. The SQLite implementation appends events and updates the state projection in
one transaction. `TALRuntime.replay` reconstructs the projection from the event
ledger and compares canonical JSON to the stored projection.

## Manifest lifecycle

The compiler is deterministic and model-free. It normalizes record ordering and
timestamps, generates a SHA-256 manifest hash, and exports the schema in
`schemas/tal-manifest-0.3.0.schema.json`. Final manifests are immutable. Typed patch
requests have a fixed allowlist; accepted changes create a child version, preserve the
parent hash, and invalidate approvals.

## Certification command

```powershell
.\.venv\Scripts\ruff.exe format --check src tests
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m mypy src\tal
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\tal fixture run all --offline
.\.venv\Scripts\python.exe -m build
```
