# TAL office action runtime branch (non-executable)

The existing `TALRuntime.apply_proposal()` now accepts a **closed** `OfficeRuntimeAction` in addition to the frozen invoice-domain `Proposal`. The runtime checks the task/run ID, initiating actor and pinned state version and requires `READY_TO_COMMIT` or an `EXECUTING` phase with a pinned durable outbox action hash before calling the existing `admit_office_calendar` evaluator.

The office branch **does not create or alter an invoice run**, mutate the TAL event store, invoke a tool, issue a signed execution capability, or authorize Google Calendar writes. It returns `OfficeAdmissionDecision(authorizes_execution=False)` for all outcomes. It must only be fed evidence rebuilt by a privileged server from its durable task ledger. An untrusted caller can construct a superficially valid request but this never grants any external effect.

This is a first-class, typed runtime **admission entrypoint**, not full domain-neutral TAL execution. SAFE-AGENT's M4 leased worker must still verify manifest, simulation, actor, approval, broker scopes, provider revisions and source integrity; any TAL denial blocks a new synthetic dispatch, but a previously committed provider effect can still be reconciled by its action key.

The invoice `TALRuntime.start/apply_proposal/replay` behavior and invoice test suite remain unchanged. Existing `tal.office.admit_office_calendar` remains available for simulated proposal previews.

## Tests

`pytest -q tests/test_office_domain.py tests/test_office_runtime.py`

Full TAL runtime/effect-store generalization, a shared authoritative audit ledger, and independent live-provider conformance tests remain **unimplemented** and are mandatory before activating any real Google effects.
