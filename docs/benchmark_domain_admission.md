# Synthetic benchmark domain admission

`tal.domain_admission` adds a pure admission protocol for `retail` and
`banking_knowledge`. Existing invoice and office-calendar contracts are unchanged.
Its only environment is `synthetic_benchmark`; it is not a live banking adapter.

## Authority boundary

The host compiles a `DomainGrantDraft` from protected episode configuration. Its
capability catalog fixes operations, effect classes, subject/authentication
requirements, customer confirmation requirements, and allowed dispatch targets.
It must never accept that catalog or `TrustedDomainEvidence` from model output.

`admit_domain_action(grant, proposal, evidence)` checks grant integrity, episode /
domain / policy bindings, state and observation versions, capability routes,
subject/authentication evidence, the call budget, unresolved effects, and any
required action-bound customer confirmation. Unsupported or invalid proposals
are denied. A dispatch wrapper cannot lower the effect or requirements of either
the wrapper or the effective inner operation.

An `ELIGIBLE` decision explicitly contains `authorizes_execution=False`. It is
not a token or bearer capability. The caller must still use its own trusted
coordinator to commit the exact call and a version-bound authorization in durable
storage before dispatch. The caller also owns deadlines, token/money budgets,
response privacy, result correlation, leases, and recovery. This API executes no
tool, mutates no ledger, and performs no network request.

## Transport and customer confirmation

Tool arguments use sorted, compact native JSON and permit finite binary floats
because benchmark APIs require JSON numbers. `domain_arguments_hash` hashes the
exact UTF-8 transport, not NFC-normalized strings. Duplicate keys, nonfinite
values, non-object arguments, noncanonical JSON, and oversized arguments are
rejected. The argument byte digest is included in proposal identity, so Unicode
normalization cannot change the dispatched text while preserving authorization.

`DomainCustomerConfirmation` binds the customer, episode, policy, latest actual
user turn, and exact effect hash. It is distinct from an office operator's
approval. The trusted host is responsible for showing the exact action details
and establishing that the customer's actual reply confirms them. A model's
assertion that a customer consented is insufficient. The grant declares which
operations require this confirmation; the API does not invent a blanket rule for
every domain write.

## Limits

`mechanical_checks_passed` and `scope_valid` attest to the host's declared,
deterministic checks. The API cannot infer whether natural-language customer
intent or a banking knowledge-base policy has been correctly interpreted.
Every decision therefore contains `semantic_policy_verified=False`.

The protocol accepts a host-built state record. It does not defend against a
hostile trusted coordinator or root process. A missing or ambiguous side-effect
response must be reconciled before another write/control effect; a local
idempotency key alone does not prove external exactly-once execution.

The authored tests in `tests/test_domain_admission.py` use no benchmark task,
golden trajectory, database, hosted model, or paid service. They establish
mechanical protocol behavior, not task-success performance.
