# TAL Office admission audit lifecycle — experimental v1

**Scope:** trusted offline/synthetic execution only. This feature is an append-only,
hash-chained record of the TAL calendar-admission decision and subsequent trusted
verifier observations. It cannot dispatch a tool, issue bearer capabilities,
authorize Google Calendar writes, or determine whether a provider actually
created an event.

## Lifecycle

`TALRuntime.apply_proposal` evaluates `OfficeRuntimeAction` through the existing
closed office admission function. When its optional `office_journal` is set, an
`EXECUTING` command with a pinned `outbox_action_hash` also records the exact
decision, source manifest/proposal hashes, and action digest in the TAL office
journal. Failed journal append or changed authority fails closed. An unconfigured
`TALRuntime` retains its existing invoice behavior; consumers **must explicitly
require journaling** before treating it as a durable audit of office admission.

A trusted reconciliation process may append `OUTCOME_UNKNOWN`, `VERIFIED`, or
`NEEDS_REVIEW`. `VERIFIED` requires a separate sealed
`OfficeVerifiedEvidence` binding to the action, manifest, proposal and a digest
of independently observed synthetic provider state. The model is never allowed
to create this input. A denied admission cannot transition to any effect state.
`VERIFIED`/`NEEDS_REVIEW` are terminal, with identical verified callbacks treated
idempotently. A prior `OUTCOME_UNKNOWN` may be resolved through read-back.

The journal uses one SQLite transaction per event, with immutable monotonically
numbered records and SHA-256 predecessor chaining. Replay reconstructs the
journal projection and verifies hashes, order, domain bindings, and transitions.
The journal contains **hashes, opaque action IDs, and safe reason codes only**;
never raw provider identifiers, credentials, customer names, or event descriptions.

## Boundaries and limitations

- SQLite is intended for isolated/local certification. For a real deployment,
  replace it with a reviewed store sharing SAFE-AGENT's transaction boundary or
  an explicitly reconciled cross-service design. Two separately committed
  ledgers do **not** provide distributed atomicity.
- SHA-256 chains are tamper-evident against accidental or unsophisticated edits;
  an attacker who can rewrite all records and recompute hashes is not prevented
  without external anchoring, signing or a protected audit service.
- The verifier evidence is only as trustworthy as its caller. The journal does
  not access providers and does not certify Google exactly-once semantics.
- Existing invoice domain remains independent. No `ToolId`, invoice manifest,
  or invoice event reducer is changed.

## Tests

`pytest tests/test_office_journal.py tests/test_office_runtime.py tests/test_harness.py`

Conformance tests include durable restarts, idempotent admission and read-back,
unknown outcomes, denied-action containment, hash-chain tampering, cross-task
bindings, immutable terminal states, and unchanged invoice effect behavior.
