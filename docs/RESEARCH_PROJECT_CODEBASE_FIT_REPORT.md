# Research-Project Fit Assessment

## TAL v0.3 and the proposed Finance-Agent / PlanCraft agent-scaling study

**Assessment date:** August 19, 2026  
**Code assessed:** workspace snapshot of `tervoro-agentic-language` v0.3.0  
**Research brief assessed:** “TAL Agent-Scaling Follow-Up Study: Testing Tervoro Agentic Language on Finance-Agent and PlanCraft”  
**Scope:** technical readiness, experimental validity, measurement coverage, reproducibility, and undergraduate-project feasibility. This is a codebase assessment, not a claim about TAL's benchmark performance; no Finance-Agent or PlanCraft episodes have been run from this repository.

## Executive conclusion

The codebase is a strong deterministic TAL enforcement prototype, but it is not yet an executable research system for the proposed study.

It already provides several difficult foundations: closed and immutable data models, a deterministic manifest compiler, a hash-chained event ledger, replay, an action gate, frozen topology descriptions for all five architectures, context isolation, parallel actor invocation, CONTROL/TAL context modes, and component-labeled telemetry records. These capabilities make the proposed study technically plausible.

However, the repository cannot currently run either primary benchmark, call a live foundation model, produce native benchmark scores, enforce a shared experimental compute budget, record real provider token and cost usage, execute the 1,000-episode paired matrix, or export an analysis-ready dataset. In addition, the current CONTROL condition still uses the TAL scheduler and authority gate. It therefore isolates TAL's model-facing representation more than it isolates the complete TAL governance treatment described in the research question.

**As-is verdict:** no-go for paid pilot or main-study runs.  
**Development verdict:** go for a focused undergraduate implementation-and-evaluation project, provided the missing experiment layer is completed and certified before the research freeze.  
**Heuristic end-to-end readiness:** approximately **39/100**. This score is a requirements-based engineering judgment, not a performance measurement.

| Area | Weight | Readiness | Assessment |
| --- | ---: | ---: | --- |
| Deterministic TAL core and reliability controls | 15 | 13 | Strong prototype foundation |
| Five-architecture topology support | 15 | 9 | Defined and compiled; execution parity is not certified |
| CONTROL/TAL treatment validity | 15 | 5 | Shared topology exists; treatment boundary is scientifically ambiguous |
| Finance-Agent and PlanCraft execution/scoring | 20 | 0 | Absent |
| Telemetry, resource equality, and cost control | 15 | 5 | Schemas exist; live measurements and enforcement are absent |
| Reproducibility and research freeze | 10 | 3 | Some hashes exist; experiment-wide freeze does not |
| Certification and analysis pipeline | 10 | 4 | Core tests pass; required ten-condition and statistical pipeline absent |
| **Total** | **100** | **39** | **Research prototype, not study-ready** |

## What the codebase already does well

### 1. Deterministic authority and auditability

All TAL Pydantic objects are configured as strict, immutable, and closed to unknown fields (`src/tal/core.py`, lines 35–38). Model proposals are checked against goal, manifest, and state versions before passing through a single action boundary (`src/tal/runtime.py`, lines 449–485). This is directly relevant to the study's reliability outcomes: invalid requests, stale proposals, unsupported completion, and state inconsistency.

The in-memory and SQLite stores use monotonic state and ledger versions, seal every event into a predecessor hash chain, and support deterministic replay (`src/tal/storage.py`; `src/tal/events.py`). The existing tests cover rejection without mutation, approval binding, replay, tamper detection, idempotency, uncertain provider outcomes, and unsupported completion.

### 2. All five named architecture descriptions exist

`src/tal/profiles/agent_scaling_reference.json` contains Single, Independent, Centralized, Decentralized, and Hybrid profiles tied to the documented upstream release `v2.1.3`. The compiler validates declared actors, edges, phases, inventory, architecture shape, and subgoal authority before producing a canonical entity order, phase order, and topology hash (`src/tal/coordination.py`, lines 424–470).

The profiles preserve several important reference distinctions:

- Single has one actor and no communication.
- Independent has three isolated workers followed by a deterministic synthesis stage.
- Centralized has one lead and three workers without worker-to-worker edges.
- Decentralized has three peers, peer edges, and a deterministic consensus stage.
- Hybrid combines a lead/worker hierarchy with lateral worker communication.

The repository also documents known upstream quirks, including decentralized threshold behavior and Hybrid's late peer-sharing behavior, in `docs/reference_architecture_audit.md`.

### 3. Useful multi-agent runtime mechanisms exist

The team runtime invokes declared actors only, rejects packets outside compiled phase-scoped edges, isolates private histories, injects packets only into declared recipients, and commits concurrent actor outputs in canonical topology order (`src/tal/team.py`). Actor calls in one activation are physically concurrent while their results are applied deterministically.

CONTROL and TAL use the same stored topology and scheduler. TAL mode adds core grammar, multi-agent grammar, topology rules, and the response schema; CONTROL omits those segments (`src/tal/team.py`, lines 365–455). This is a useful foundation for paired context-overhead measurement.

### 4. Telemetry has the right basic vocabulary

`src/tal/telemetry.py` defines separate fields for input context components, visible and reasoning output, cached input, context occupancy, latency, and component/provider cost. `tal_context_share()` implements the specification's grammar + schema + topology + manifest numerator. The CLI can export call records and a basic summary.

This schema is well aligned with the proposal, even though the current runtime does not populate most live-provider fields.

### 5. The offline core is verifiably functional

Local verification produced these results:

- 22 of 22 tests passed.
- All 15 registered offline invoice scenarios reached terminal outcomes.
- Ruff lint checks passed.
- Strict mypy checks passed for all 18 source files.
- Source and wheel distributions built successfully with the installed build environment.
- Ruff format checking failed on two files (`src/tal/events.py` and `src/tal/team.py`), so the repository's documented quality gate is not completely green.

These results demonstrate a working offline harness; they do not demonstrate benchmark or experimental readiness.

## Requirement-by-requirement fit

| Research requirement | Status | Evidence and implication |
| --- | --- | --- |
| Five fixed architectures | Partial | Five profiles compile and hash. Full behavioral parity is not tested. |
| Same topology in CONTROL and TAL | Partial | Same compiled topology is stored, but actual model/config equality is not enforced. |
| Ordinary non-TAL control protocol | Not established | CONTROL uses the same TAL manifest, proposal types, authoritative state, scheduler, and action gate. |
| Finance-Agent adapter and scorer | Missing | No Finance-Agent code, task loader, tools, or scorer is present. |
| PlanCraft environment adapter and scorer | Missing | No inventory/crafting state, actions, task loader, or native scorer is present. |
| Live foundation-model adapter | Missing | Only scripted model adapters exist; the README explicitly states there is no real model provider. |
| Native benchmark success outcome | Missing | Runtime state has no benchmark identifier, task-instance identifier, native score, or benchmark output record. |
| Component-level input accounting | Partial | Context segments are labeled, but the default counter counts characters rather than provider tokens. |
| Output/reasoning/cache token usage | Missing in execution | Fields exist but `_record_model_call()` leaves them at zero. |
| Provider cost | Missing in execution | Cost fields exist but no provider usage/pricing adapter populates them. |
| Latency and context occupancy | Partial | Approximate call-batch latency and rendered-context size are recorded; no provider end-to-end reconciliation exists. |
| Reliability events | Partial-to-strong | Rejections, action outcomes, replay, and state versions are recorded; no episode-level research exporter derives all requested rates. |
| Coordination measurements | Partial | Packets and calls are recorded; duplicate work, coordination-token totals, and evidence loss across handoffs are not computed. |
| Equal aggregate reasoning budget | Missing | Reference profiles contain no global or local resource limits; only model-call and packet reservations are consumed by the team runtime. |
| 1,000-episode matrix runner | Missing | No task sampler, paired-run orchestrator, resume/checkpoint policy, or result table exists. |
| Research freeze | Partial | Manifests and topologies are hashed; prompts, model config, task IDs, tool schemas, scoring code, analysis rules, and full experiment config are not frozen together. |
| Architecture certification | Partial | Profiles compile; only five multi-agent tests exist, and only Single plus the first part of Centralized are actually executed. |
| Statistical analysis | Missing | No paired analysis, confidence interval, hypothesis-test, table, or plot pipeline exists. |

## Critical scientific-validity risks

### 1. The current control does not isolate the full TAL treatment

`TeamRuntime._apply_output()` explicitly sends both CONTROL and TAL proposals through `TALRuntime.apply_proposal()` and the same authority gate (`src/tal/team.py`, lines 334–362). Both modes also receive the TAL goal manifest and authoritative state. The tested difference is primarily the extra TAL grammar/schema/topology context.

That is valid if the study asks, “What is the effect of TAL's model-facing representation when deterministic enforcement is held constant?” It is not a clean control for the stated question, “Does TAL governance and representation outperform ordinary execution enough to pay for its overhead?”

Before implementation continues, choose and preregister one of two designs:

1. **Full-harness treatment:** CONTROL reproduces the upstream ordinary architecture and TAL adds TAL representation plus deterministic governance. This matches the current proposal but introduces more implementation differences to audit.
2. **Representation-only treatment:** both modes use the deterministic gate and scheduler, while only TAL receives TAL representation. This is easier to pair cleanly, but the research question and hypotheses must be narrowed accordingly.

Do not describe the second design as measuring the complete TAL harness.

### 2. The runtime is bound to invoice resolution

The only registered domain is `tal.business.invoice_resolution-0.3.0` (`src/tal/core.py`, line 25; `src/tal/invoice.py`, lines 327–347). The manifest, capability computation, tool registry, observations, and completion checks are domain-specific. The packaged team profiles grant no tool capabilities. As a result, a Finance-Agent or PlanCraft tool proposal cannot be represented and completed by the current runtime without new domain/benchmark adapters.

This is the largest implementation gap. The architecture layer is reusable, but the actual task and scoring layer is not.

### 3. Phase and round behavior needs a parity correction

The actor scheduler loops over `max_activations_per_entity * max_rounds` while `coordination_round` is incremented only once when the phase begins (`src/tal/team.py`, lines 204–332). For the Centralized work phase, the profile specifies 25 activations and 10 rounds; the implementation therefore permits up to 250 activation cycles per worker inside one recorded coordination round. The Decentralized debate phase similarly permits 25 × 9 cycles while retaining a single round index.

This does not directly express the audit's “one independent round plus nine peer-debate rounds,” and it risks multiplying compute beyond the source ceiling. It also prevents faithful round-level telemetry. The intended relationship among model calls, agent iterations, architecture rounds, and phase barriers must be made explicit and tested against upstream traces before any paid run.

Related declared policies are not fully enforced: quorum/barrier semantics are not checked before `_close_barrier()`, failure/reassignment policy is not executed, and episode start does not verify the supplied topology hash against its content.

### 4. Same-model and same-budget guarantees are descriptive, not enforced

`EpisodeDefinition` contains one `model_id`, but the runtime records each adapter's own `model_id` and does not compare it to the episode value. Thus actors can silently use heterogeneous models despite `research_mode.heterogeneous_models=false`.

Reference profiles contain no global or local limits. The team runtime reserves one unit for a model call and one for a packet, but it does not consume actual input tokens, output tokens, reasoning tokens, wall time, provider cost, tool calls, coordination rounds, or external effects against topology budgets. Consequently, adding agents can add compute, and paired resource equality is not guaranteed.

### 5. Current telemetry is a schema, not experiment-grade accounting

The default `CharacterTokenCounter` returns the number of characters and explicitly says it is not for billing (`src/tal/telemetry.py`, lines 86–92). `_record_model_call()` populates input segment sizes, total rendered input, occupancy, approximate elapsed time, and completion order, but it does not populate visible output, reasoning tokens, cache usage, or any cost field (`src/tal/team.py`, lines 637–683).

The adapter contract returns only a proposal/packet and has no provider-usage envelope. Accurate accounting therefore requires a provider response object containing raw provider usage, model/version, request ID, cache fields, reasoning usage when available, finish status, and priced cost. Provider-reported totals should be retained separately from locally classified input segments so discrepancies can be audited.

### 6. The required experimental artifact chain is absent

There is no episode result schema, benchmark task table, paired condition identifier, seed/sampling record, 1,000-run orchestrator, retry/invalidation policy, result export, or statistical analysis script. This workspace snapshot also has no `.git` metadata, so an immutable source commit cannot currently be included in the freeze from this copy.

The code hashes individual manifests and topologies, but the study needs one experiment manifest binding all consequential artifacts: source commit, environment lock, TAL grammars, topology profiles, control/TAL prompt constructors, provider/model settings, benchmark task IDs, tool schemas, scorers, budgets, exclusion rules, and analysis version.

## Testing and certification gap

The 22 passing tests are valuable but concentrated on the invoice core. There are only five multi-agent tests:

- all five profiles compile;
- one TAL-Single phase records telemetry and replays;
- one CONTROL-Single phase uses the shared gate;
- a Centralized assignment reaches only its declared worker context; and
- CONTROL-Single omits TAL-specific context segments.

Independent, Decentralized, and Hybrid are never executed end to end. Centralized is not run through reporting, synthesis, benchmark action, and completion. No test executes all five architectures in both modes, validates model identity, enforces global token/cost budgets, checks round traces, rejects a tampered topology at episode start, tests actor failure policy, or compares outcomes against the upstream implementation.

At minimum, certification must include a 2 benchmarks × 5 architectures × 2 protocols smoke matrix on excluded fixture tasks, plus property/adversarial tests for communication, budgets, topology immutability, barriers, stale concurrent outputs, replay, and scorer determinism.

## Recommended implementation sequence

### Gate 1 — Resolve the treatment and architecture semantics

1. Freeze the scientific meaning of CONTROL versus TAL.
2. Re-audit upstream v2.1.3 using an exact commit, executable traces, resolved prompts, and model settings.
3. Correct the phase/round/iteration model and create parity fixtures for every architecture.
4. Verify topology hashes and runtime inventory automatically at episode start.
5. Enforce the episode's frozen model configuration across every model-calling actor/stage.

**Exit criterion:** excluded deterministic tasks produce architecture traces within preregistered parity tolerances for all five profiles.

### Gate 2 — Add a benchmark-neutral experiment interface

Define versioned contracts for:

- task instance loading and stable task IDs;
- initial environment creation;
- benchmark-visible tools/actions;
- authoritative benchmark state transitions;
- task output extraction;
- native scoring and terminal status; and
- benchmark-specific evidence/completion verification.

Implement separate Finance-Agent and PlanCraft adapters against this interface. Do not force either benchmark into invoice entities or invoice completion rules.

**Exit criterion:** one excluded task from each benchmark completes and receives the same deterministic native score when replayed from its event ledger.

### Gate 3 — Add live model and exact usage accounting

Implement one provider adapter first. Freeze the exact provider, model snapshot, sampling parameters, retry policy, context limit, and pricing table. Capture provider usage and populate visible output, reasoning, cache, latency, and cost fields. Add event-derived episode summaries for every required reliability, coordination, and efficiency metric.

Implement real resource consumption and fail-closed ceilings for input/output/reasoning tokens, calls, packets, rounds, tools, effects, latency, and provider cost. The same aggregate limits must be compiled into paired CONTROL/TAL episodes.

**Exit criterion:** local classified context totals reconcile to provider totals under a documented rule, cost summaries reproduce provider billing within an allowed tolerance, and intentional budget overruns terminate predictably.

### Gate 4 — Build the experiment and freeze pipeline

Create:

- a canonical experiment manifest and content hash;
- a paired condition/run ID scheme;
- excluded pilot/main task lists;
- resumable execution without silent duplicate episodes;
- one immutable row per episode plus call/event child tables;
- a documented run invalidation policy;
- environment and dependency locks;
- analysis scripts that consume only the frozen result schema; and
- an automatic provenance appendix for the final report.

**Exit criterion:** the complete excluded smoke matrix can be reproduced from a clean environment and yields byte-stable configuration/provenance artifacts.

### Gate 5 — Pilot cost, then freeze

Run a small excluded pilot across both benchmarks and all ten conditions. Measure actual provider cost, failure rate, context occupancy, and wall time. With a $500 ceiling and a 20% contingency reserve, the 1,000 main episodes must average no more than about **$0.40 per episode** if the main matrix is allotted $400; pilot and rerun costs must fit in the remaining reserve. Use an upper confidence or conservative percentile cost estimate, not only the mean, before launch.

Only after the pilot should the model, budgets, prompts, grammars, profiles, tasks, adapters, scorers, exclusions, and analysis be frozen and hashed. Pilot tasks must remain excluded from the main analysis.

## Analysis recommendations for the final research report

The brief labels \(P_{TAL,A}/P_{CONTROL,A}\) an “absolute performance effect,” but that expression is a ratio. Make the paired success-rate difference the primary estimand:

\[
\Delta_A = P(\text{success}\mid TAL,A) - P(\text{success}\mid CONTROL,A)
\]

Report the ratio only as a secondary effect because it is unstable when control success is low. For each benchmark and architecture, report paired outcome counts, the success-rate difference with a confidence interval, and a paired binary test such as McNemar's test. For the architecture-by-protocol question, use a preregistered interaction model or a paired/clustered bootstrap over task instances. With only 50 paired instances per architecture/benchmark, emphasize confidence intervals and exploratory interaction estimates rather than binary significance claims.

Efficiency analysis should report total cost, cost per successful task, success per model call/token, TAL context share, peak occupancy, and the paired TAL-minus-CONTROL difference. Provider failures, invalid outputs, budget termination, and exhausted retries must remain in the dataset under preregistered outcome rules rather than being silently dropped.

## Minimum go/no-go checklist for the main study

Do not begin paid main-study episodes until all items are true:

- [ ] The same excluded Finance-Agent task runs and scores under all ten conditions.
- [ ] The same excluded PlanCraft task runs and scores under all ten conditions.
- [ ] CONTROL and TAL have identical paired topology, task, model, tool, visibility, and resource hashes except for the preregistered treatment.
- [ ] The control path matches the scientific claim being made about it.
- [ ] All five architecture traces pass upstream parity tests.
- [ ] Topology hash, runtime inventory, and model identity are enforced at runtime.
- [ ] Provider token, reasoning, cache, latency, and cost fields are populated and reconciled.
- [ ] Aggregate token/call/tool/packet/round/effect/cost limits are actually enforced.
- [ ] Episode summaries contain benchmark score plus all required reliability, coordination, and efficiency outcomes.
- [ ] The full excluded 20-condition smoke matrix passes and replays.
- [ ] Format, lint, type, test, build, and certification gates are all green.
- [ ] A source commit and locked environment are included in the experiment manifest.
- [ ] Pilot cost supports 1,000 episodes within $500 including contingency.
- [ ] The analysis plan, exclusions, and rerun/invalidation rules are frozen before main results are inspected.

## Final assessment

This repository already solves much of the hardest TAL-specific systems work: deterministic authority, immutable typed contracts, replayable state, fixed coordination graphs, controlled message routing, and a usable telemetry vocabulary. That makes it a credible base for the study and a meaningful undergraduate research artifact in its own right.

It does not yet fill the needs of the proposed empirical project because the missing pieces are exactly those that convert a harness into an experiment: benchmark adapters, live model execution, native scoring, real cost accounting, compute-equivalent paired conditions, architecture-parity certification, research-wide freezing, and statistical analysis.

The best characterization is therefore:

> **TAL v0.3 is a promising and comparatively mature offline enforcement core, but only an early-stage foundation for the Finance-Agent/PlanCraft study. Complete the experiment layer and resolve the CONTROL treatment before making any claim about whether TAL pays for its overhead.**
