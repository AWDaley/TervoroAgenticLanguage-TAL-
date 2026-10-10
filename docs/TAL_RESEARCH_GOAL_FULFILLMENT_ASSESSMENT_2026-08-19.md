# TAL Research Goal-Fulfillment Assessment

**Assessment date:** 2026-08-19  
**Repository version:** `tervoro-agentic-language` 0.4.0rc1  
**Target project:** TAL Agent-Scaling Follow-Up Study on Finance-Agent and PlanCraft

## Executive verdict

**The codebase is a credible offline TAL certification and research-scaffolding project, but it is not currently capable of executing the proposed study or supporting a valid conclusion about whether TAL pays for its overhead.**

The strongest part of the repository is the deterministic TAL core: strict immutable models, frozen topology compilation, a hash-chained event ledger, replay, an action gate, bounded team scheduling, and offline certification scenarios. The research package also adds useful contracts for task manifests, experiment freezes, paired schedules, provider receipts, result persistence, and paired analysis.

The decisive weakness is that the actual research episode runner is not integrated with those TAL mechanisms. It does not use `TALRuntime`, `TeamRuntime`, typed proposals, coordination packets, tools, authoritative benchmark state, or the action gate. Instead, it iterates over actor IDs, sends ordinary text prompts, exposes all prior outputs to later actors, and scores the last text response. Consequently, its TAL/control comparison is primarily a prompt difference and does not test the governance system described in the proposal.

**Decision:** no-go for paid pilot or main-study episodes. The repository is suitable for continued engineering and offline certification, but not for empirical claims about H1-H5.

## What was assessed

The assessment compared the study brief and `docs/RESEARCH_PROTOCOL_V0.4.md` with:

- the TAL core runtime, ledger, validation, topology, and team scheduler;
- the research task, freeze, schedule, runner, provider, persistence, and analysis modules;
- the Finance-Agent and PlanCraft adapters and bundled upstream snapshots;
- the CLI and container path; and
- the executable test, lint, type-check, certification, build, and preflight evidence.

No live model calls or paid benchmark episodes were run.

## Readiness summary

| Area | Readiness | Assessment |
|---|---|---|
| Deterministic TAL core | Strong offline | The central authority and replay design are substantive and well tested. |
| Five topology definitions | Moderate | All five compile, but the research runner does not execute their communication and aggregation semantics. |
| Paired schedule and sealed contracts | Moderate | Useful foundations exist, but certification does not verify the complete 1,000-episode design or artifact contents. |
| Live provider integration | Early | OpenAI and Gemini transport helpers exist, but the research CLI does not run them and the Gemini request shape is not adapted to its API. |
| Finance-Agent integration | Not runnable | Tasks can be imported and a judge can be injected, but benchmark tools and the judge execution/cost path are absent. |
| PlanCraft integration | Blocked | No interactive environment is wired in, the native runtime is blocked, and the bundled fallback scoring path is invalid for imported tasks. |
| Required telemetry | Insufficient | Total provider usage can be recorded; TAL context share, reliability, tool, state, and coordination measures cannot be derived from research runs. |
| Confirmatory analysis | Partial and unsafe | McNemar/bootstrap helpers exist, but the CLI performs only pooled completed-pair analysis and silently excludes failed/budget-exhausted pairs. |
| End-to-end study | Not demonstrated | There is no sealed freeze, schedule, episode dataset, benchmark smoke matrix, pilot, or main-study result in the repository. |

## Evidence of genuine capability

### 1. The offline enforcement core is real

All TAL data models inherit a strict, frozen, `extra="forbid"` configuration (`src/tal/core.py:35-38`). Canonical serialization and SHA-256 hashing are centralized (`src/tal/core.py:213-223`). `TALRuntime.apply_proposal()` is the deterministic proposal boundary (`src/tal/runtime.py:482`), and replay reconstructs state from events (`src/tal/runtime.py:1082`).

The topology compiler validates and freezes execution structures (`src/tal/coordination.py:424-460`). `TeamRuntime` verifies a topology at episode start and has a separate phase scheduler and model-output application path (`src/tal/team.py:110-153`, `src/tal/team.py:361`). These are useful systems contributions independent of the benchmark study.

### 2. The research artifact model is thoughtfully structured

The v0.4 contracts cover provider receipts, episode metrics/results, benchmark scores, and experiment freezes (`src/tal/research.py:110-247`). The schedule builder produces deterministic, pair-adjacent TAL/control episodes (`src/tal/research_study.py:63-107`). The append-only result layer seals results and controlled traces and supports resumption (`src/tal/research_execution.py:53-160`).

The provider wrapper records request/response hashes, resolved models, token categories, and computed costs (`src/tal/research_provider.py:62-119`). The repository also contains importers for 50 public Finance-Agent tasks and deterministic PlanCraft depth-stratified sampling (`src/tal/research_packs.py:266-338`).

### 3. Local quality evidence is good for a prototype

The following checks were run against the current workspace:

- `pytest`: **39 passed**;
- coverage: **81% overall**;
- Ruff lint: **passed**;
- mypy strict: **passed** across 25 source files;
- offline certification: **15 scenarios certified** and **5 topologies compiled**;
- fixture certification: all 15 registered scenarios reached the expected terminal result; and
- clean package build: sdist and wheel **built successfully** in a fresh output directory.

The format gate is not fully green: `ruff format --check src tests` reports one formatting difference in `src/tal/events.py:494-495`.

## Study-blocking findings

### 1. The research runner bypasses TAL governance and topology semantics

This is the largest validity problem.

`FrozenResearchEpisodeRunner.run()` walks topology phases, extracts only IDs beginning with `actor:`, and calls a text provider (`src/tal/research_runner.py:57-101`). It never constructs a `TeamModelOutput`, admits a `CoordinationPacket`, applies a typed proposal, executes an action gate, records authoritative state transitions, or invokes `TALRuntime`/`TeamRuntime`.

The consequences are material:

- deterministic stages such as independent synthesis and decentralized voting are skipped because their IDs begin with `stage:`, not `actor:`;
- every request receives the same accumulated `prior_outputs` list (`src/tal/research_runner.py:157-193`), so independent workers are not isolated and actors can see outputs that topology edges do not authorize;
- ordinary text is treated as phase completion (`src/tal/research_runner.py:204-214`), so the configured activation and round semantics are not faithfully exercised;
- the final benchmark answer is simply the last collected actor output; and
- TAL mode adds the multi-agent grammar to a system prompt, while control receives a short ordinary instruction. Neither path executes the claimed TAL enforcement treatment.

This runner cannot test whether structured authority, evidence provenance, state integrity, rejection/recovery, or deterministic completion verification justify TAL's overhead.

### 2. Neither benchmark is actually executed

The research provider contract returns only `(text, receipt)` and exposes no tool-call loop. The runner never reads or invokes the tools declared in the task manifest.

For Finance-Agent, the manifest declares only `tool:tavily_search` (`src/tal/research_packs.py:91-128`), but the runner never calls it. The bundled Finance-Agent reference describes a broader multi-step tool surface, and the bundled agent-scaling adapter expects web search, Python, and submit. The independent judge is only an injected callable; no production judge transport, rubric loading, judge receipt, or judge-cost accounting is wired into the episode engine (`src/tal/research_packs.py:130-146`).

For PlanCraft, the manifest declares no tools (`src/tal/research_packs.py:170-199`). There is no environment state, search/move/smelt/impossible action loop, or native transition ledger. A native scorer may be injected, but the approved pinned runtime is documented as blocked. The repository's preflight was run against the included Finance CSV and expected PlanCraft snapshot location: it found all 50 Finance tasks but **zero PlanCraft tasks**, because the snapshot is absent.

The fallback scorer is also defective for records produced by the importer. It compares `sha256({"output": output})` (`src/tal/research_packs.py:214`) with a stored hash generated as `sha256({"target": target})` (`src/tal/research_packs.py:335`). The keys differ, so even an output equal to the target cannot match. This fallback must not be represented as native scoring.

### 3. Required reliability and overhead measurements are not produced

`EpisodeMetrics` names many appropriate outcomes, including rejected proposals/messages, unsupported completion, state conflicts, tool calls, context occupancy, and wall time (`src/tal/research.py:151-167`). The research runner populates only model-call count, coordination-round count, total token categories, and provider cost (`src/tal/research_runner.py:217-254`). Other values silently remain zero.

In particular:

- coordination packets are always recorded as zero;
- tool calls and duplicate calls are always zero because tools are not supported;
- rejections, invalid requests, completion failures, and state conflicts cannot occur through this runner;
- wall-clock time and peak context occupancy are not populated;
- provider receipt latency defaults to zero unless an external caller supplies it;
- TAL-specific context segments are not tokenized separately, so `TALContextShare` cannot be computed; and
- controlled storage retains output and request hashes but not the raw output, provider request, provider response, or model-visible prompt, preventing trace-level audit and redacted-example generation.

The richer `TeamRuntime` telemetry does segment TAL context, but the research runner does not use it.

### 4. Financial ceilings use the wrong unit

The CLI labels its amounts as microcents but encodes the intended $75 pilot, $400 main, and $25 rerun allocations as `7_500_000`, `40_000_000`, and `2_500_000` (`src/tal/cli.py:111`).

One dollar is 100,000,000 microcents. Correct amounts are therefore:

- $75 = 7,500,000,000 microcents;
- $400 = 40,000,000,000 microcents;
- $25 = 2,500,000,000 microcents; and
- $500 = 50,000,000,000 microcents.

The coded values are **1,000 times too small**. In addition, `MeteredProviderClient` calls the paid transport before checking whether the returned cost exceeds the ledger ceiling (`src/tal/research_provider.py:81-99`). A call that crosses the limit has already incurred cost and is then omitted from receipts when `reserve()` raises. This is not a hard pre-call budget guarantee.

Only the model-call resource limit is consumed by `FrozenResearchEpisodeRunner`; token, tool, round, wall-time, and provider-cost limits in a task manifest are not enforced there.

### 5. The experiment freeze and certification are too shallow

The freeze schema is a good hashable container, but artifact locators and hashes are not resolved or rechecked against files. `tal research certify` only verifies that the freeze is sealed and that its hash matches the schedule (`src/tal/cli.py:234-241`). It does not certify:

- exactly 50 Finance and 50 PlanCraft tasks;
- all five architectures and exactly 1,000 main episodes;
- content hashes for prompts, grammars, scorers, task payloads, tool schemas, pricing, or upstream sources;
- a valid source revision or container digest;
- architecture parity traces; or
- budget consistency with the stated dollar ceiling.

The current workspace has no Git metadata, and no study freeze, schedule, raw results, or public result artifact is present. The two upstream directories are ignored by `.gitignore`, while the root Dockerfile copies only `pyproject.toml`, `uv.lock`, `README.md`, and `src`. The production image therefore lacks the upstream benchmark code, data, and dependencies needed for this study (`Dockerfile:7-9`, `.gitignore:12-13`).

### 6. The analysis path cannot answer the stated hypotheses safely

`analyze_schedule()` implements a useful paired success difference, exact McNemar calculation, and paired cost bootstrap. However, it includes only primary episodes whose status is `COMPLETED` and whose score is present (`src/tal/research_analysis.py:39-56`). Budget failures, content failures, and other non-completions are silently removed along with their pair, creating attrition bias and conflicting with the protocol's requirement to report failures and deviations.

The CLI runs one pooled comparison (`src/tal/cli.py:222-231`). It does not emit the benchmark-by-architecture comparisons needed for H2-H4, does not invoke the implemented Holm correction, and does not run the stated secondary interaction/GEE analysis. It also does not verify result seals, freeze hashes, duplicate episode IDs, or schedule identity before analysis.

### 7. Test coverage does not establish architecture or benchmark fidelity

The 39 tests are valuable unit evidence, but only one test exercises `FrozenResearchEpisodeRunner`, and it covers a synthetic single-agent PlanCraft-like task in TAL and control (`tests/test_research_runner.py:12-63`). Multi-agent tests verify compilation, one single-agent phase, limited centralized routing, round bounds, topology tampering, and model identity (`tests/test_multi_agent.py:105-260`).

There is no end-to-end 2 benchmarks x 5 architectures x 2 protocols smoke matrix, no live or faithful fake benchmark environment, no Finance tool-use test, no PlanCraft transition/scoring test, no full independent/centralized/decentralized/hybrid research execution, and no upstream trace-parity test. The bundled upstream test suite also cannot collect in the root environment because its LangChain/Langfuse dependencies are not installed there.

## Ability to fulfill each research goal

| Proposed goal | Present ability | Verdict |
|---|---|---|
| Certified TAL v0.2/v0.4 multi-agent implementation | Strong offline core, incomplete benchmark treatment | Partially fulfilled |
| Five reproducible compiled topologies | Static definitions compile and hash | Partially fulfilled; execution parity unproven |
| 1,000 paired CONTROL/TAL episodes | Schedule scaffolding only; no executable CLI pipeline or results | Not fulfilled |
| Native Finance-Agent performance | Tasks import; tools/judge path not runnable | Not fulfilled |
| Native PlanCraft performance | Missing runtime/snapshot and no environment loop | Blocked |
| Reliability benefit measurement | Metric schema exists, actual signals not generated | Not fulfilled |
| TAL context/coordination overhead | Aggregate token receipts only; TAL context share unavailable | Not fulfilled |
| Cost-effectiveness under $500 | Cost wrapper exists; units and hard-ceiling behavior are wrong | Not fulfilled |
| Architecture-specific hypothesis tests | Pooled helper only | Not fulfilled |
| Reproducible report-ready dataset | No freeze, traces, results, or analysis artifact | Not fulfilled |

## Required remediation before any paid pilot

1. **Freeze the treatment definition.** Decide whether control is the upstream ordinary harness and TAL adds governance, or whether both share deterministic enforcement and the study measures TAL representation only. Encode the single permitted difference and test it.
2. **Use one real episode engine.** Integrate the research contracts with `TeamRuntime`/`TALRuntime`; execute typed proposals, packets, topology visibility, action gates, benchmark state, completion verification, ledger replay, and telemetry in both paired conditions.
3. **Implement benchmark environments.** Wire the agreed Finance tool surface and independent rubric judge, including judge usage/cost. Resolve the PlanCraft runtime decision and execute its native state transitions and scorer.
4. **Repair metering and budgets.** Correct the microcent constants, add pre-call reservations or conservative maximum-cost admission, enforce every frozen resource class, measure actual latency, and reconcile provider totals.
5. **Persist auditable controlled traces.** Store encrypted/access-controlled prompts, raw outputs, tool calls/results, judge inputs/outputs, receipts, topology events, and invalid/rejected attempts; generate public redactions separately.
6. **Strengthen certification.** Resolve every artifact hash, require a real source revision and image digest, validate exactly 100 tasks and 1,000 episodes, and fail if any topology/model/tool/budget/scorer parity invariant differs.
7. **Fix confirmatory analysis.** Apply preregistered failure coding, produce all ten benchmark-by-architecture comparisons, apply Holm correction, add the secondary interaction model, verify seals, and report missingness/deviations explicitly.
8. **Add fidelity tests.** Require an excluded 20-condition smoke matrix, all-five-architecture trace parity against the pinned upstream release, deterministic scorer replay, forced budget overruns, provider-schema contract tests, and crash-safe persistence tests.

## Go/no-go criteria

The paid pilot should begin only when all of the following are demonstrated from a clean, pinned environment:

- one excluded Finance task and one excluded PlanCraft task complete and score under all ten conditions each;
- every architecture's actor visibility, communication, aggregation, rounds, and tool access match the frozen reference within preregistered tolerances;
- TAL and control differ only by the frozen treatment;
- all benchmark actions pass through authoritative state and replay successfully;
- provider, judge, tool, reliability, coordination, context-segment, latency, and cost metrics reconcile;
- the PlanCraft native scorer/runtime preflight passes;
- the freeze certifier proves the exact 1,000-episode matrix and all artifact hashes;
- $500 is encoded as 50,000,000,000 microcents and intentional budget overruns terminate before unauthorized spend;
- failed and budget-exhausted primary episodes remain in the prespecified analysis; and
- format, lint, type, unit, fidelity, build, and offline certification gates are green.

## Bottom line

TAL has enough implemented substance to justify continuing the project. Its deterministic authority boundary, immutable contracts, replay model, and static topology machinery are meaningful research-engineering contributions.

What it does **not** yet have is the bridge from that machinery to a scientifically faithful Finance-Agent/PlanCraft experiment. Running the current `FrozenResearchEpisodeRunner` would produce data, but those data would not answer the proposal's central question because the runner does not execute the TAL treatment or the native benchmarks it claims to compare.

The defensible present claim is:

> TAL 0.4.0rc1 provides a strong offline governance core and partial reproducibility scaffolding, but the proposed agent-scaling follow-up study remains pre-pilot and scientifically blocked pending end-to-end benchmark, enforcement, telemetry, budget, and analysis integration.
