# ADR 0006 — TAL office execution authorization record (PR 1)

**Status:** Accepted as a trusted-only contract for offline testing; Google writes remain disabled.

The existing OfficeAdmissionDecision is intentionally non-executable and keeps
authorizes_execution=False. A new, strict tal.office.execution_authorization.v1
record is prepared only after TALRuntime re-evaluates an EXECUTING action tied to
an exact durable outbox action hash. It binds tenant, actor, task, calendar handle,
operation, manifest/proposal hashes, TAL grant/proposal hashes, approval digest and
expiry, state version, effect/cycle budgets, issue time and a bounded expiry.

The runtime preparation method does NOT grant a model a tool, write to a provider,
or autonomously persist authority. The caller must store the record in the SAME
protected SQL transaction as its committed outbox intent. Before a new attempt,
the trusted worker must load the persisted record by outbox identity and invoke
the TAL validation method using freshly reconstructed trusted evidence and
current time. Reconciliation/read-back of a POSSIBLY ALREADY committed effect is
separate and never uses an expired record to justify a new attempt.

A self-hash detects accidental mutation but is not an HMAC, signature or proof
against a database administrator rewriting all fields and hashes. DB row security,
immutable audit, authenticated principal, connector confinement, and provider
revalidation remain the responsibility of the privileged SAFE-AGENT backend.
The existing SQLite office journal is shadow audit only, not transactional
authority. TAL's invoice runtime behavior is unchanged.

**No production or isolated live provider access is enabled in PR 1.** At this
stage no Google-specific worker exists. Cross-service process isolation and
externally verifiable attestations remain future release gates.
