# TAL v0.4 Research Protocol

## Confirmatory design

The experiment is a paired, randomized, fixed 1,000-episode study: the 50 frozen
Finance-Agent tasks and a 50-task PlanCraft recipe-depth-stratified sample are each
run under the five frozen `agent-scaling` architectures in both TAL and ordinary
control modes. Pair order and within-pair mode order are deterministic from seed 42;
members of a pair execute adjacent to avoid temporal confounding.

TAL and control share the pinned upstream topology, tools, environment, scorer, model
configuration, budget ceiling, pair worker, and observer-only telemetry. Control is
the ordinary pinned upstream `agent-scaling` harness; TAL adds only the typed
authority/action gate, state versioning, and audit trail. No semantic cache, dynamic
agents, topology mutation, heterogeneous actor models, or cross-episode memory is
allowed.

## Models, tools, and scoring

The pilot compares a dated OpenAI GPT-5 mini snapshot and Gemini 2.5 Flash subject to
preflight availability. The selected actor model is the lowest-cost eligible choice
within 10 percentage points of the best pilot success rate. Freeze the resolved model
identifier, provider pricing, request parameters, task snapshot, container digest,
topology, prompts, grammars, and scorer hashes before the main run.

Finance uses a pinned Tavily Basic search configuration and an independent frozen
judge, not the actor model. PlanCraft uses its native frozen scorer. Provider receipts
must include request/response hashes, resolved model, token usage, latency, and exact
microcent costs.

The currently pinned upstream PlanCraft package has a native-environment import
blocker; see [`UPSTREAM_COMPATIBILITY_BLOCKER.md`](UPSTREAM_COMPATIBILITY_BLOCKER.md).
The main-study freeze must name the approved repaired runtime or benchmark container
before any PlanCraft episode is run.

## Analysis and exceptions

The primary outcome is paired success, evaluated with exact McNemar tests and Holm
correction across at most ten prespecified comparisons. GEE is secondary. Efficiency
uses paired bootstrap intervals. H5 is supported only when TAL success is no worse
than control by 5 percentage points and TAL cost per success is no higher.

The hard ceiling is $500: $75 pilot, $400 main, and $25 for reruns, represented as
7,500,000,000, 40,000,000,000, and 2,500,000,000 microcents respectively. Provider
admission reserves the maximum cost before every call. Raw prompts, responses, model
contexts, tool records, and judge material are encrypted with AES-GCM in the local
SQLite run store; the public release contains only sealed result aggregates.

Only infrastructure failures may be rerun, with a maximum of two attempts and the
separate $25 rerun cap. Content failures, budget failures, and scoring failures are
not rerun. Report exclusions, failed episodes, and all deviations.
