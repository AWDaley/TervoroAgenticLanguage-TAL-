# Experimental TAL office-domain admission contract

Adds `tal.office` with a **closed, deterministic, model-free** office-calendar admission evaluator. It is a conservative building block for the TERVORO SAFE-AGENT `calendar.create_event` workflow.

**Status:** candidate domain package, **not** an extension of `TALRuntime.apply_proposal` and **not** production authorization or certification. The existing invoice-resolution runtime, hashes, and event/approval formats are unchanged. No Google API calls, OAuth scopes, provider credentials, network connections, or externally effective operations can be requested from this package.

The privileged integration compiles `OfficeCalendarGrantDraft` into a sealed grant using TAL's canonical `sha256`, then evaluates a **single** typed `OfficeCalendarProposal` against a `TrustedOfficeCalendarEvidence` record assembled only from server-side task/manifest, simulation, ledger, scoped-reference and approval stores. The `admit_office_calendar` response always includes `authorizes_execution=False` and must **never** be accepted as an execution token. Unknown fields, malformed timestamps, unsealed grant mutations, wrong identity/target, revoked scope, stale evidence, budget overflow, unsuitable time windows and invalid approval bindings must fail closed. Ref revocation and external provider state are still separately checked by the responsible trusted executor.

**Critical next integration gate:** replace SAFE-AGENT's provisional office policy with a tested TAL-owned runtime action gate (including the upstream event ledger and durable version/replay semantics), provide version-locked interop fixtures, and prove that worker dispatch cannot happen without that gate's authorization. This module alone cannot unlock real provider writes.

To run the focused tests, install TAL and run `pytest -q tests/test_office_domain.py`.
