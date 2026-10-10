# TAL v0.2 Multi-Agent Architecture Requirements

**Version:** Draft v0.2 Requirements  
**Primary purpose:** Support the TAL follow-up experiment to *Towards a Science of Scaling Agent Systems*  
**Design priority:** Experimental validity, reproducibility, topology fidelity, and exact TAL-overhead measurement

---

# 0. Executive Requirement

TAL v0.2 extends TAL from a primarily single-agent harness into a **goal-centric runtime supporting multiple bounded reasoning actors under a precompiled coordination topology**.

The fundamental abstraction is:

```text
1 Governed Goal
1 TAL Runtime
N Bounded Reasoning Actors
1 Immutable Compiled Execution Topology
```

The architecture is selected **before model execution**, compiled into an immutable coordination program, and enforced deterministically throughout the episode.

The five required experimental structures are:

1. Single Agent.
2. Independent Multi-Agent.
3. Centralized Multi-Agent.
4. Decentralized Multi-Agent.
5. Hybrid Multi-Agent.

The purpose of v0.2 is **not** to create a general swarm framework.

Its purpose is to make these five structures faithfully reproducible under TAL so the research project can test:

> **Does applying the TAL harness to the same single-agent and multi-agent structures improve performance and reliability enough to compensate for TAL's additional context, token, communication, latency, and execution overhead?**

---

# 1. Relationship to TAL v0.1

TAL v0.2 preserves the v0.1 principle:

> The model proposes; the TAL harness authorizes, executes, records, and verifies.

TAL remains a controlled semantic protocol plus deterministic enforcement runtime. The model must not directly mutate state, grant authority, bypass tool validation, or declare consequential completion without verification.

The existing v0.1 runtime components remain authoritative:

- Grammar and behavior contract.
- Canonical manifest.
- Static validation.
- Policy and capability engine.
- Context builder.
- Model adapter.
- Action gate.
- Typed tool adapters.
- Authoritative state reducer.
- Immutable event ledger.
- Completion verifier.

TAL v0.2 adds bounded multi-agent reasoning and coordination **around this common core** rather than replacing it.

---

# 2. Core Design Update

TAL v0.2 uses a **compiled execution topology**.

The earlier graph-only concept:

```text
G = (V, E)
```

is insufficient because communication edges alone do not define an agent architecture.

The complete topology must instead encode:

```text
Topology =
    Actors
  + Roles
  + Communication Edges
  + Execution Phases
  + Activation Rules
  + Phase Barriers
  + Context Visibility
  + Aggregation Policy
  + Consensus Policy
  + Tool Authority
  + Resource Budgets
  + Termination Rules
```

Conceptually:

\[
\mathcal{T} =
(V,E,\Phi,\Omega,\mathcal{V},\mathcal{B},\mathcal{A},\mathcal{X})
\]

Where:

- \(V\) = reasoning actors and declared coordination stages.
- \(E\) = permitted communication paths.
- \(\Phi\) = execution phases, rounds, barriers, and activation rules.
- \(\Omega\) = orchestration, synthesis, debate, or consensus policy.
- \(\mathcal{V}\) = context and information visibility.
- \(\mathcal{B}\) = local and global resource budgets.
- \(\mathcal{A}\) = tool and action authority.
- \(\mathcal{X}\) = termination and failure behavior.

Execution is therefore:

```text
Execution = TAL(goal, compiled_execution_topology)
```

---

# 3. Fundamental Principle

## Topology Is a Compile-Time Contract

The architecture is selected once and compiled before the first model call.

After compilation:

- No model may modify the topology.
- No model may add or remove agents.
- No model may create new communication edges.
- No model may modify roles.
- No model may alter phase ordering.
- No model may increase round limits.
- No model may change aggregation policy.
- No model may change consensus policy.
- No model may grant itself or another agent capabilities.
- No model may modify team budgets.
- No model may change who can see which information.

Topology is not advisory.

It is executable runtime policy.

---

# 4. System Architecture

```text
Research Configuration
        │
        ↓
Named Architecture Profile
        │
        ↓
Coordination Specification
        │
        ↓
Topology Compiler
        │
        ↓
Immutable Compiled Execution Topology
        │
        ↓
Agent / Stage Instantiation
        │
        ↓
Phase-Aware Team Runtime
        │
        ↓
TAL Proposal + Coordination Protocol
        │
        ↓
Action Gate
        │
        ↓
Typed Tools
        │
        ↓
Authoritative State + Event Ledger
        │
        ↓
Completion Verifier
```

The topology compiler and scheduler must be deterministic software.

They must not use an LLM to decide how the team should be structured.

---

# 5. Named Experimental Profiles

The five experimental architectures should be expressed as **frozen named profiles** rather than separate implementations.

Required profiles:

```text
single
independent
centralized
decentralized
hybrid
```

Additionally, the repository should contain a frozen research profile:

```text
agent_scaling_reference
```

This profile must capture the exact architecture parameters used for the follow-up experiment, including:

- Number of reasoning actors.
- Coordination-stage count.
- Maximum reasoning iterations.
- Maximum orchestration rounds.
- Maximum debate rounds.
- Peer communication rounds.
- Phase ordering.
- Worker visibility.
- Synthesis rules.
- Consensus rules.
- Tool scheduling.
- Termination rules.

Before the research protocol is frozen, these values must be verified against the chosen reference implementation of the original agent-scaling study.

Do not rely on undocumented assumptions.

---

# 6. Coordination Specification

All architecture profiles compile from one common `CoordinationSpec`.

Conceptually:

```yaml
coordination_spec:
  architecture: centralized

  actors: []
  coordination_stages: []

  communication_edges: []

  phases: []
  activation_rules: []
  barriers: []

  visibility_policy: {}

  aggregation_policy: {}
  consensus_policy: {}

  tool_policy: {}
  action_policy: {}

  local_budgets: {}
  global_budget: {}

  failure_policy: {}
  termination_policy: {}
```

Exact field names may follow existing TAL schema conventions.

The important requirement is that all architecture-defining behavior is represented explicitly and versioned.

---

# 7. Compilation Requirements

## 7.1 Validate Architecture

The compiler must verify:

- Architecture identifier is registered.
- Actor count matches the selected profile.
- Required coordination stages exist.
- Roles are valid.
- Communication edges are valid.
- Phase definitions are complete.
- Aggregation policy matches the architecture.
- Consensus policy matches the architecture.
- Resource limits are valid.
- Required tools exist.
- Agent capabilities are valid.
- No undeclared actor or coordination stage exists.

---

## 7.2 Prevent Authority Escalation

Do **not** reject cyclic communication graphs merely because they contain cycles.

Decentralized systems intentionally require cyclic peer communication.

Instead, compile-time validation must ensure:

> No communication path, delegation path, role relationship, or topology feature can grant an agent authority beyond its statically assigned effective capability set.

Conceptually:

```text
verify_no_authority_escalation_paths()
```

Information may travel through cycles.

Authority may not grow through cycles.

---

## 7.3 Construct Actors

Every reasoning actor receives:

```yaml
actor:
  actor_id: unique
  team_id: fixed
  role: fixed

  allowed_recipients: fixed
  visible_context_classes: fixed

  tool_capabilities: fixed
  action_capabilities: fixed

  subgoal_authority: fixed

  local_budget: fixed
```

---

## 7.4 Construct Execution Phases

The compiler must explicitly define temporal behavior.

Examples of phase types:

```text
initial_reasoning
orchestrator_planning
worker_execution
worker_reporting
peer_exchange
debate
revision
synthesis
consensus
finalization
completion_proposal
```

A phase definition should specify:

- Eligible actors.
- Maximum activations.
- Permitted message kinds.
- Tool permissions.
- State visibility.
- Required inputs.
- Produced outputs.
- Barrier behavior.
- Next phase.
- Exit condition.

---

## 7.5 Freeze Topology

After successful compilation:

```text
compiled_topology.state = FROZEN
```

The serialized compiled topology must:

- Be canonicalized.
- Be hashed.
- Include its version.
- Be stored with the episode.
- Be included in experiment manifests.
- Be available during replay.

---

# 8. Context Model

Complete agent contexts must **not** be required to be mutually disjoint.

Instead:

\[
C_i =
C_{shared}
\cup
C_{role_i}
\cup
C_{private_i}
\cup
C_{received_i}
\]

Where:

### Shared context

May include:

- TAL core grammar.
- TAL multi-agent grammar addendum.
- Global goal.
- Tool documentation.
- Global requirements.
- Global constraints.
- Global prohibitions.
- Permitted authoritative state.
- Global budget state.

### Role context

May include:

- Role instructions.
- Assigned responsibility.
- Architecture-specific responsibilities.
- Subgoal information.

### Private context

Includes:

- Actor's previous model interactions.
- Actor-specific reasoning history.
- Actor-specific observations not shared through topology.
- Actor-local execution history.

### Received context

Includes:

- Messages delivered through legal topology edges.
- Shared reports.
- Peer challenges.
- Orchestrator assignments.
- Consensus information.

The required isolation invariant is:

\[
C_{private_i}\cap C_{private_j}=\varnothing
\]

unless the topology explicitly exposes part of that information.

No actor automatically receives another actor's full private history.

---

# 9. Authoritative State

There is exactly one authoritative environment state per episode.

```text
Reasoning Actors
       │
       ↓
TAL Proposals
       │
       ↓
Action Gate
       │
       ↓
Typed Tool
       │
       ↓
Validated Observation
       │
       ↓
Authoritative State Reducer
       │
       ↓
Immutable Event Ledger
```

Actor beliefs are not state.

Agent reports are not state.

Consensus is not state.

Tool output becomes authoritative only through the normal TAL validation and event-commit pathway.

TAL v0.1 already defines authoritative state as a deterministic projection of accepted events.

---

# 10. Epistemic Status Across Agents

Inter-agent communication must preserve TAL provenance.

If Agent A reports:

```text
The vendor is active.
```

Agent B must not automatically receive:

```text
vendor.status = verified
```

It should instead receive a source-attributed claim equivalent to:

```yaml
claim:
  claim_id: claim_42
  source_actor: worker_a
  epistemic_status: inferred
  evidence_refs:
    - observation_17
  value:
    vendor_status: active
```

Repeated agreement among models does not automatically promote a claim to authoritative fact.

TAL's existing distinction among reported, observed, inferred, unknown, conflict, and verified information continues to apply across agent boundaries.

---

# 11. Multi-Agent Coordination Packets

TAL v0.2 requires registered internal message kinds.

At minimum:

```text
assign_subgoal
submit_report
request_information
challenge_claim
request_peer_check
recommend_action
submit_vote
propose_subgoal_completion
```

Every packet must contain sufficient metadata to reconstruct its role in the execution trace.

Required fields should include semantic equivalents of:

```yaml
packet:
  packet_id:
  sender_actor_id:
  recipient_actor_ids:
  goal_ref:
  subgoal_ref:
  phase_ref:
  round_index:
  state_version:
  kind:
  evidence_refs:
  claim_refs:
  unknown_refs:
  conflict_refs:
  payload:
```

Free text may exist in registered explanation fields.

Free text remains non-operative.

---

# 12. Communication Enforcement

Messages are delivered only through compiled communication edges.

```text
if topology.permits(sender, recipient, phase, packet_kind):
    deliver(packet)
else:
    reject(packet)
```

A rejected packet:

- Is not delivered.
- Is not visible to the intended recipient.
- Does not mutate authoritative state.
- Produces a typed rejection event.
- Consumes whatever resource cost was already incurred.
- Is included in experimental telemetry.

---

# 13. Phase-Aware Communication

Communication permission depends on more than the existence of an edge.

The runtime must evaluate:

```text
sender
recipient
packet kind
current phase
current round
architecture
```

An edge that is valid during a peer-discussion phase may be invalid during independent initial reasoning.

This prevents architecture leakage.

---

# 14. Phase Barriers

Research mode should support deterministic phase barriers.

Conceptually:

```text
Phase N
 ├── Actor A
 ├── Actor B
 └── Actor C
       │
       ↓
    BARRIER
       │
       ↓
Canonicalize phase outputs
       │
       ↓
Build Phase N+1 contexts
```

When the architecture permits parallel work:

1. Eligible actors may execute concurrently.
2. All outputs are recorded.
3. The phase closes.
4. Results are canonically ordered.
5. The next phase's contexts are constructed.
6. No actor gains early access solely because its provider request completed faster.

This prevents network latency from silently changing logical information flow.

If the reference architecture deliberately depends on asynchronous information delivery, that behavior may be implemented instead, but it must be explicit in the frozen profile.

---

# 15. Scheduling

The scheduler must be deterministic in logical behavior.

Record:

- Activation order.
- Phase number.
- Round number.
- Concurrent branch start.
- Concurrent branch completion.
- Message delivery order.
- State version visible to each actor.
- Tool execution order.
- State-change commit order.

The scheduler may coordinate execution.

It may not reason about the task.

There is no scheduler LLM.

---

# 16. Global Budget Model

All reasoning actors and coordination stages consume a single authoritative episode budget.

Required counters:

```text
total_input_tokens
total_output_tokens
total_reasoning_tokens
total_model_calls
total_tool_calls
total_coordination_packets
total_coordination_rounds
total_external_effects
total_wall_clock_time
total_provider_cost
```

Individual actors may have local limits, but:

\[
\sum LocalUsage_i \leq GlobalBudget
\]

Adding agents must not implicitly create additional experimental compute.

---

# 17. First-Class TAL Overhead Accounting

Exact context accounting is a mandatory v0.2 feature because TAL overhead is one of the experiment's primary variables.

Every model request must attempt to classify input into:

```text
input.total

input.tal_core_grammar
input.tal_multi_agent_grammar
input.response_schema
input.topology_rules

input.goal_manifest
input.role_context
input.subgoal_context

input.authoritative_state
input.capability_context
input.tool_definitions

input.evidence
input.agent_messages
input.previous_private_history

input.other
```

Also record where available:

```text
output.visible
output.reasoning

cache.cached_input
cache.uncached_input

context.current_occupancy
context.maximum_capacity

cost.input
cost.output
cost.reasoning
cost.total
```

These fields must be exportable:

- Per model call.
- Per actor.
- Per coordination stage.
- Per phase.
- Per episode.
- Per architecture.
- Per protocol mode.

---

# 18. TAL Context Share

The experiment must be able to calculate:

\[
TALContextShare =
\frac{
T_{coreGrammar}
+
T_{multiAgentGrammar}
+
T_{responseSchema}
+
T_{topologyRules}
+
T_{TALManifest}
}{
T_{totalInput}
}
\]

And paired overhead:

\[
TALOverhead =
T_{TALCondition}
-
T_{ControlCondition}
\]

The system should also separately report context occupancy because cached tokens may become financially cheaper while still consuming model context.

---

# 19. CONTROL and TAL Protocol Modes

The same compiled architecture should support two research protocol modes whenever technically feasible:

```text
CONTROL
TAL
```

The topology remains identical.

```text
                 Compiled Topology
                        │
            ┌───────────┴───────────┐
            │                       │
         CONTROL                   TAL
            │                       │
  ordinary agent protocol      TAL protocol
            │                       │
            └───────────┬───────────┘
                        ↓
                   Same Benchmark
```

The following should remain identical across paired CONTROL and TAL runs:

- Actor count.
- Actor roles.
- Communication graph.
- Phase structure.
- Round limits.
- Information visibility.
- Aggregation behavior.
- Consensus behavior.
- Model.
- Generation parameters.
- Tool availability.
- Initial environment.
- Benchmark scorer.
- Experimental resource limits.

Differences should be limited to those intentionally caused by TAL governance and representation.

This creates the cleanest possible paired experimental treatment.

---

# 20. Single-Agent Architecture

## 20.1 Actors

```text
1 × reasoning actor
```

No additional reasoning agent exists.

## 20.2 Structure

```text
Goal
 ↓
Agent
 ↓
TAL / CONTROL Protocol
 ↓
Tools and Environment
```

## 20.3 Phases

Conceptually:

```text
reason
→ act or continue
→ observe
→ reason
→ ...
→ finalize
```

Exact iteration limits are defined by the frozen reference profile.

## 20.4 Communication

No inter-agent communication occurs.

## 20.5 Aggregation

```text
aggregation = self
```

## 20.6 Acceptance Criteria

- Exactly one reasoning actor.
- No synthesis model.
- No peer model.
- No hidden verifier model.
- No agent-message layer.
- All LLM usage attributable to the single actor.

---

# 21. Independent Multi-Agent Architecture

## 21.1 Reasoning Actors

```text
3 × independent reasoning workers
```

The reference profile may define another fixed number if required by the source experiment.

## 21.2 Coordination Stage

```text
1 × synthesis stage
```

The synthesis stage must be accounted for separately from the reasoning-worker count.

Instrumentation should distinguish:

```text
reasoning_actor_count
coordination_stage_count
total_model_calling_entities
```

if synthesis itself uses the foundation model.

## 21.3 Structure

```text
          ┌→ Worker A ─┐
          │            │
Goal ─────┼→ Worker B ─┼→ Synthesis → Final Output
          │            │
          └→ Worker C ─┘
```

## 21.4 Isolation

Workers must not receive:

- Other worker outputs.
- Other worker messages.
- Other worker tool histories.
- Other worker private histories.

Each worker explores independently.

## 21.5 Synthesis Policy

Independent requires an explicit frozen policy:

```yaml
aggregation_policy:
  type: synthesis_only
  feedback_to_workers: false
  worker_cross_validation: false
  peer_debate: false
  followup_worker_rounds: 0
```

The synthesizer may combine worker outputs according to the frozen reference policy.

It must not silently convert Independent into a verification or debate architecture.

## 21.6 Communication Graph

Allowed:

```text
worker_a → synthesis
worker_b → synthesis
worker_c → synthesis
```

Forbidden:

```text
worker ↔ worker
synthesis → worker
```

unless the verified source implementation requires otherwise.

## 21.7 Scientific Purpose

Independent isolates:

> parallel exploration without active inter-worker coordination.

TAL may improve report structure and provenance, but it must not introduce coordination mechanisms absent from the baseline architecture.

---

# 22. Centralized Multi-Agent Architecture

## 22.1 Actors

```text
1 × orchestrator
N × workers
```

For the reference experiment, worker count and round limits are frozen in `agent_scaling_reference`.

## 22.2 Structure

```text
                 Orchestrator
                /     |      \
               ↓      ↓       ↓
          Worker A Worker B Worker C
               \      |      /
                ↑     ↑     ↑
                 Orchestrator
                      │
                      ↓
                Final Output
```

## 22.3 Communication

Allowed:

```text
orchestrator ↔ worker
```

Forbidden:

```text
worker ↔ worker
```

## 22.4 Orchestrator Authority

The orchestrator may:

- Decompose the task.
- Assign predefined team members subgoals.
- Select permitted context for workers.
- Request follow-up work within round limits.
- Synthesize reports.
- Revise the plan.
- Recommend final action.
- Propose global completion.

The orchestrator may not:

- Create new agents.
- Grant capabilities.
- Alter topology.
- Increase budgets.
- Change phase rules.

## 22.5 Worker Authority

Workers may:

- Reason over assigned subgoals.
- Request permitted tools.
- Produce evidence-backed reports.
- Record unknowns and conflicts.
- Propose subgoal completion.

Workers may not:

- Communicate directly with one another.
- Change the global coordination plan.
- Grant authority.
- Declare global completion unless the profile explicitly assigns that capability.

## 22.6 Phase Pattern

Conceptually:

```text
orchestrator_plan
        ↓
worker_execution
        ↓
BARRIER
        ↓
worker_reports
        ↓
orchestrator_review
        ↓
continue | act | finalize
```

The reference profile determines exact rounds and iteration limits.

---

# 23. Decentralized Multi-Agent Architecture

## 23.1 Actors

```text
N × equivalent peer reasoning actors
```

No orchestrator exists.

## 23.2 Structure

```text
        Peer A
       ↙     ↘
   Peer B ↔ Peer C
```

## 23.3 Initial Independence

Peers must perform their initial reasoning phase without seeing peer conclusions.

This preserves reasoning diversity.

## 23.4 Debate / Peer Phases

After initial reasoning, peers participate in bounded communication phases.

Permitted packet kinds may include:

- `submit_report`
- `challenge_claim`
- `request_peer_check`
- `recommend_action`
- `submit_vote`

## 23.5 No Hidden Hierarchy

The runtime may:

- Schedule actors.
- Deliver legal packets.
- Count votes.
- Evaluate deterministic thresholds.

The runtime may not:

- Decide which peer is smarter.
- Assign peer work based on semantic judgment.
- Create a lead reasoning actor.
- Interpret debate through an undeclared LLM.

## 23.6 Finalization

The profile must define the exact finalization policy:

```text
consensus
vote
final peer synthesis
other verified reference mechanism
```

The policy must be frozen before the experiment.

## 23.7 Phase Pattern

Conceptually:

```text
initial_independent_reasoning
           ↓
        BARRIER
           ↓
peer_exchange_round_1
           ↓
        BARRIER
           ↓
revision
           ↓
peer_exchange_round_2
           ↓
          ...
           ↓
consensus / finalization
```

---

# 24. Hybrid Multi-Agent Architecture

## 24.1 Actors

```text
1 × orchestrator
N × workers
```

Workers additionally have selected peer communication permissions.

## 24.2 Structure

```text
                  Orchestrator
                 /     |      \
                ↓      ↓       ↓
           Worker A Worker B Worker C
                ↔       ↔       ↔
                 \      |      /
                  Orchestrator
                       │
                       ↓
                 Final Output
```

## 24.3 Important Requirement

Hybrid must **not** be defined merely as:

```text
centralized edges + some peer edges
```

Its exact temporal coordination pattern must be compiled.

The profile must define:

- When the orchestrator assigns work.
- When workers reason independently.
- When peer communication becomes visible.
- How many peer rounds occur.
- Whether workers revise after peer exchange.
- When reports return to the orchestrator.
- Whether peer communication repeats during later orchestration rounds.
- When final synthesis occurs.

## 24.4 Conceptual Phase Pattern

The actual reference implementation determines the final version, but the compiler must be capable of representing:

```text
orchestrator_plan
        ↓
worker_execution
        ↓
BARRIER
        ↓
peer_exchange
        ↓
BARRIER
        ↓
worker_revision
        ↓
BARRIER
        ↓
worker_reports
        ↓
orchestrator_synthesis
        ↓
continue | finalize
```

## 24.5 Scientific Purpose

Hybrid is particularly important because it may:

- Benefit the most from structured TAL communication.

while simultaneously:

- Paying the largest repeated TAL context cost.
- Producing the most coordination packets.
- Requiring the most context rebuilding.
- Incurring the greatest protocol overhead.

TAL v0.2 must expose enough telemetry to measure both effects.

---

# 25. Architecture Comparison Matrix

| Property | Single | Independent | Centralized | Decentralized | Hybrid |
|---|---|---|---|---|---|
| Multiple reasoning actors | No | Yes | Yes | Yes | Yes |
| Orchestrator | No | No | Yes | No | Yes |
| Worker isolation | — | Full | Between workers | No | Partial |
| Peer communication | No | No | No | Yes | Yes |
| Hierarchical assignment | No | No | Yes | No | Yes |
| Active debate | No | No | No | Yes | Limited |
| Aggregation | Self | Synthesis-only | Orchestrator | Consensus/reference policy | Orchestrator |
| Explicit phases | Yes | Yes | Yes | Yes | Yes |
| Shared authoritative state | Yes | Yes | Yes | Yes | Yes |
| Global action gate | Yes | Yes | Yes | Yes | Yes |
| Global budget | Yes | Yes | Yes | Yes | Yes |
| Verified completion | Yes | Yes | Yes | Yes | Yes |

---

# 26. Subgoals

TAL v0.2 must support bounded subgoals where required by the topology.

A subgoal may contain:

```text
subgoal_id
parent_goal_ref
assigned_actor
desired_result
required_evidence
permitted_tools
dependencies
local_budget
status
completion_requirements
```

A subgoal may not override:

- Global prohibitions.
- Global requirements.
- Global policy.
- Effective capabilities.
- Global budget.
- Global terminal conditions.

---

# 27. Subgoal Completion vs Global Completion

The runtime must distinguish:

```text
subgoal completion
```

from:

```text
global goal completion
```

A worker may legitimately establish:

```text
vendor verification complete
```

without being able to establish:

```text
global invoice-resolution task complete
```

Global completion remains subject to TAL's normal independent completion verification. TAL v0.1 already requires declared terminal conditions to be re-evaluated against authoritative evidence.

---

# 28. Parallel Reasoning

TAL v0.2 may execute independent model calls concurrently when the topology permits it.

Parallel reasoning must not imply:

- Shared private histories.
- Unrestricted communication.
- Shared write authority.
- Nondeterministic logical message ordering.

Physical concurrency and logical architecture are separate concepts.

---

# 29. Parallel Reads

TAL v0.1 defaults to one proposal and at most one external action per model cycle, while explicitly leaving broader read batching for later extension.

For v0.2 research mode, bounded parallel read-only operations may be supported when necessary to preserve the reference multi-agent architecture.

Requirements:

- Reads are non-mutating.
- Reads use registered typed tools.
- Reads occur against an explicitly recorded state version where relevant.
- Every read consumes budget.
- Every result receives provenance.
- All results enter the event ledger.
- Visibility is controlled by topology.
- CONTROL and TAL conditions use the same scheduler policy.

State-changing operations remain globally serialized unless a later version introduces formally defined concurrent effects.

---

# 30. External State-Changing Actions

Multiple reasoning actors do not imply multiple independent execution authorities.

All state-changing external actions pass through the common TAL action gate.

The action gate remains the sole authority for:

- Permission checks.
- Policy checks.
- State-transition checks.
- Budget checks.
- Tool-argument checks.
- Idempotency.
- Effect authorization.

TAL therefore permits:

```text
multiple reasoning actors
```

while maintaining:

```text
one governed external state transition pathway
```

---

# 31. No Dynamic Agent Creation

TAL v0.2 does not permit model-directed creation of reasoning actors.

Forbidden:

```text
spawn_agent
create_worker
hire_agent
create_subteam
delegate_to_unknown_agent
```

All model-calling actors must exist in the compiled topology.

---

# 32. No Model-Granted Authority

A model may assign work where the topology permits.

It may not grant permissions.

This:

```text
Orchestrator:
"Worker B, investigate the contract."
```

may be valid.

This:

```text
Orchestrator:
"Worker B, you now have permission to submit payment."
```

does not alter effective authority.

Permissions remain computed and enforced by the runtime.

---

# 33. No Hidden Agents

Every model call must be attributable to exactly one declared element of the compiled execution topology.

Required attribution:

```text
episode
topology
phase
round
actor_or_stage
role
model_call
```

No unregistered:

- Judge.
- Critic.
- Verifier.
- Router.
- Supervisor.
- Summarizer.
- Safety model.
- Consensus model.

may generate model output.

If a synthesis stage uses an LLM, it must be explicitly declared and budgeted.

Deterministic validators and schedulers are not model agents.

---

# 34. Failure Semantics

Each reasoning actor may enter bounded local states such as:

```text
ready
active
waiting
subgoal_completed
failed
budget_exhausted
```

Actor failure does not automatically change the topology.

Recovery behavior must come from the compiled profile.

Examples:

- Centralized may permit reassignment to an existing worker.
- Independent may proceed with fewer successful worker reports if the reference profile allows it.
- Decentralized may continue consensus if quorum rules permit.
- Hybrid may use its predefined failure route.

No failure may cause:

- Dynamic worker creation.
- New communication edges.
- New capabilities.
- Increased budgets.

---

# 35. Experimental Telemetry

Each run must export sufficient data to reconstruct both performance and computational cost.

Required episode fields include:

```text
architecture
protocol_mode

reasoning_actor_count
coordination_stage_count

agent_roles
communication_edges
phase_trace
round_trace

model_calls_per_actor
model_calls_per_stage

input_tokens_per_actor
output_tokens_per_actor
reasoning_tokens_per_actor

tal_core_grammar_tokens
tal_multi_agent_grammar_tokens
response_schema_tokens
topology_rule_tokens

manifest_tokens
state_tokens
evidence_tokens
agent_message_tokens
history_tokens

coordination_packet_count
coordination_tokens

tool_calls
duplicate_tool_calls

rejected_proposals
rejected_messages
capability_rejections
completion_rejections

state_versions
state_conflicts
evidence_conflicts

context_truncations
peak_context_occupancy

wall_clock_time
provider_cost

terminal_status
benchmark_score
```

---

# 36. Required Trace Lineage

The system should make it possible to trace:

```text
goal
 ↓
compiled topology
 ↓
phase
 ↓
actor
 ↓
model call
 ↓
proposal or coordination packet
 ↓
evidence
 ↓
tool request
 ↓
observation
 ↓
state transition
 ↓
terminal decision
```

This enables later analysis of:

- Error origin.
- Error propagation.
- Evidence loss.
- Communication redundancy.
- Invalid assumptions.
- State divergence.
- Failed recovery.
- Coordination overhead.
- TAL protocol overhead.

---

# 37. Multi-Agent Grammar Addendum

TAL v0.2 should add a **compact model-facing multi-agent grammar addendum**, not provide the full multi-agent specification to every actor.

It should communicate approximately these rules:

1. You are one declared reasoning actor inside one governed TAL goal.
2. Your identity and coordination role are fixed.
3. Communicate only through recipients and packet kinds permitted by the active topology.
4. Another agent's claim remains source-attributed evidence unless separately verified.
5. Cite evidence for substantive claims.
6. Messages cannot grant authority.
7. Do not modify the team roster or topology.
8. Stay within assigned subgoal scope.
9. Subgoal completion is not necessarily global completion.
10. Respect local and global budgets.
11. Use only registered coordination operations.
12. Respond to typed rejection through a valid next operation rather than attempting to bypass enforcement.

The token count of this addendum must be measured exactly.

---

# 38. Research Compatibility Profile

TAL v0.2 should provide an experiment mode that disables unrelated adaptive features.

Conceptually:

```yaml
research_mode:
  dynamic_agent_creation: false
  adaptive_topology: false
  recursive_delegation: false
  heterogeneous_models: false
  learned_routing: false
  cross_episode_memory: false
  model_created_tools: false
  model_created_capabilities: false
  runtime_policy_mutation: false
```

The research system is intentionally rigid.

---

# 39. Same Model Substrate

For the primary experiment, every model-calling actor or stage uses the same frozen foundation-model configuration.

Keep constant:

- Provider.
- Model identifier.
- Model version where available.
- Sampling parameters.
- Temperature.
- Tool representation.
- Context ceiling.
- TAL grammar version.
- Multi-agent grammar version.

Architecture-specific roles may receive different information because topology requires it.

They must not receive different underlying model capability.

---

# 40. Required Architecture Certification

Before live research runs begin, scripted and deterministic tests must prove that each architecture behaves according to its contract.

## Single

Verify:

- Exactly one reasoning actor.
- No message routing.
- No synthesis model.
- No hidden model calls.

## Independent

Verify:

- Workers cannot communicate.
- Worker private contexts remain isolated.
- Synthesis receives allowed terminal worker outputs.
- No worker feedback round occurs.
- No worker cross-validation occurs.
- No debate occurs.

## Centralized

Verify:

- Workers communicate only with orchestrator.
- Orchestrator may assign bounded subgoals.
- Workers cannot create direct peer edges.
- Round limits are enforced.
- Orchestrator cannot grant capabilities.

## Decentralized

Verify:

- No orchestrator exists.
- Peers begin from independent initial reasoning where required.
- Peer communication occurs only in permitted phases.
- Debate rounds are bounded.
- Finalization follows the frozen consensus policy.
- No hidden reasoning authority exists above the peers.

## Hybrid

Verify:

- Orchestrator-worker routing works.
- Permitted peer communication works.
- Peer messages outside permitted phases are rejected.
- Peer-round and orchestration-round limits are distinct.
- Final synthesis follows the frozen profile.
- Workers cannot bypass global authority.

---

# 41. Common Certification Invariants

All five architectures must prove:

- Rejected messages do not alter state.
- Rejected tool requests do not alter state.
- Invalid topology operations are impossible.
- Agent provenance survives every transfer.
- Global budgets are enforced.
- No model-created actor appears.
- No model-created communication edge appears.
- No authority escalation occurs through communication.
- Replay reconstructs the same committed state.
- Global completion remains verifier-controlled.
- Every model call is attributed.
- All TAL context overhead is measured.
- CONTROL and TAL modes share the same topology when used in paired experiments.

TAL v0.1 already requires state to remain unchanged after rejected proposals and requires replayable, versioned execution; v0.2 must preserve those guarantees across multiple actors.

---

# 42. Research Freeze

Before the main experiment begins, freeze:

```text
TAL core version
TAL multi-agent grammar version

coordination schema
compiled-topology schema

named architecture profiles
agent_scaling_reference profile

actor counts
role definitions

communication graphs
phase schedules
phase barriers

round limits
iteration limits

context visibility rules

synthesis policy
consensus policy

parallel-read policy
state-write serialization policy

global budget rules
local budget rules

CONTROL protocol
TAL protocol

context construction
token accounting rules

model configuration
tool definitions
benchmark adapters
```

Generate cryptographic hashes for the frozen artifacts.

---

# 43. Post-Freeze Rules

After the research freeze:

Do not:

- Increase an architecture's rounds because it performs badly.
- Reduce TAL grammar because TAL performs badly.
- Give Independent a verification stage.
- Give Decentralized an orchestrator.
- Remove expensive Hybrid communication.
- Change worker visibility.
- Change synthesis behavior.
- Change budget accounting.
- Change context construction.

Engineering bugs may be fixed.

Any bug fix affecting experimental behavior requires rerunning all affected conditions according to a documented invalidation policy.

---

# 44. Non-Goals

TAL v0.2 is not intended to implement:

- Dynamic swarms.
- Runtime agent spawning.
- Recursive delegation.
- Agent-created roles.
- Agent-created permissions.
- Learned coordination policies.
- Adaptive topology selection.
- Agent markets.
- Autonomous organizational design.
- Distributed leader election.
- Distributed consensus infrastructure.
- Cross-host worker leasing.
- Autonomous model selection.
- Multi-agent reinforcement learning.
- Self-modifying agent organizations.

These features introduce variables unrelated to the immediate experiment.

---

# 45. Distributed Execution Is Not Required

Multi-agent reasoning does not require multi-machine infrastructure.

TAL v0.2 may run all actors:

```text
inside one process
```

or:

```text
on one host with asynchronous model calls
```

while still implementing genuine independent, centralized, decentralized, and hybrid reasoning structures.

The research target is:

> coordination topology

not:

> distributed-systems engineering.

TAL v0.1 currently treats distributed execution details such as event ordering, leases, idempotency, and replay across workers and services as deferred design questions. v0.2 should not expand into that problem unless required for the experiment.

---

# 46. Recommended Implementation Order

## M0 — Reference Architecture Audit

Before coding the topology profiles:

- Inspect the original experiment implementation.
- Record exact actor counts.
- Record orchestration rules.
- Record debate rules.
- Record synthesis behavior.
- Record Hybrid phase behavior.
- Record iteration and round limits.
- Record information visibility.

Produce:

```text
docs/reference_architecture_audit.md
```

No profile should be finalized from memory alone.

---

## M1 — Coordination Schema

Implement:

- `CoordinationSpec`.
- Actor definitions.
- Coordination-stage definitions.
- Edges.
- Phases.
- Barriers.
- Visibility policies.
- Aggregation policies.
- Consensus policies.
- Budget definitions.

---

## M2 — Topology Compiler

Implement deterministic compilation:

```text
Named Profile
   ↓
CoordinationSpec
   ↓
Validation
   ↓
CompiledExecutionTopology
   ↓
Canonicalization + Hash
```

---

## M3 — Context Isolation

Implement explicit:

```text
shared context
role context
private context
received context
```

with runtime enforcement and tests.

---

## M4 — Coordination Packets

Implement:

- Typed packets.
- Source provenance.
- Delivery rules.
- Rejection rules.
- Phase restrictions.
- State-version references.

---

## M5 — Phase Runtime

Implement:

- Activation rules.
- Round counters.
- Barriers.
- Concurrent actor execution.
- Canonical delivery ordering.
- Deterministic logical scheduling.

---

## M6 — Architecture Profiles

Implement and test:

```text
single
independent
centralized
decentralized
hybrid
```

from the common compiler.

Do not fork the runtime five times.

---

## M7 — CONTROL/TAL Dual Protocol

Allow one compiled topology to execute through:

```text
CONTROL
```

or:

```text
TAL
```

while preserving topology semantics.

---

## M8 — Global Budgeting

Implement:

- Per-actor counters.
- Coordination-stage counters.
- Global counters.
- Hard resource ceilings.
- Budget-exhaustion events.

---

## M9 — TAL Overhead Telemetry

Implement component-level context accounting and cost reporting.

This is a release-critical feature for the research study.

---

## M10 — Parallel Read Support

Implement bounded parallel read-only operation support if required by the reference architectures.

Keep state-changing effects serialized.

---

## M11 — Architecture Certification

Create scripted tests proving topology fidelity.

---

## M12 — Research Freeze

Produce a certified release containing:

- TAL version.
- Multi-agent grammar.
- Architecture profiles.
- Reference profile.
- Tests.
- Hashes.
- Benchmark adapters.
- Telemetry schema.
- Reproduction instructions.

---

# 47. Final Acceptance Standard

TAL v0.2 is ready for the experiment when the same benchmark task can execute under:

```text
CONTROL-SINGLE
TAL-SINGLE

CONTROL-INDEPENDENT
TAL-INDEPENDENT

CONTROL-CENTRALIZED
TAL-CENTRALIZED

CONTROL-DECENTRALIZED
TAL-DECENTRALIZED

CONTROL-HYBRID
TAL-HYBRID
```

while preserving, within every paired architecture:

```text
same actor structure
same phase structure
same communication topology
same information visibility
same aggregation behavior
same coordination limits
same model
same tools
same benchmark
same environment
same resource policy
```

and changing only the intended TAL treatment.

---

# 48. Scientific Success Criterion

The runtime must permit the research project to independently measure:

### Performance

```text
Does TAL improve task success?
```

### Reliability

```text
Does TAL reduce invalid actions, unsupported completion,
state inconsistency, and coordination failures?
```

### Context overhead

```text
How much additional context does TAL require?
```

### Coordination overhead

```text
How much communication and additional execution does TAL introduce?
```

### Efficiency

```text
Does increased reliability compensate for additional
tokens, latency, model calls, and monetary cost?
```

### Architecture interaction

```text
Does TAL help some coordination structures more than others?
```

---

# 49. Final Design Principle

TAL v0.2 should not be conceptualized as:

```text
a swarm framework
```

or merely as:

```text
a collection of communication graphs
```

It should be conceptualized as:

> **A deterministic runtime for executing a governed goal through one or more bounded reasoning actors under a precompiled, phase-aware coordination contract.**

The architecture defines:

```text
who reasons
who communicates
when communication occurs
what information becomes visible
who aggregates results
how consensus is reached
what resources may be consumed
who may request external actions
how the episode terminates
```

TAL continues to define:

```text
what information means
what constitutes evidence
what authority exists
what operations are valid
what state is authoritative
what external effects may occur
what constitutes verified completion
```

Keeping these concerns separate is the central design requirement.

---

# 50. Research Objective

With this implementation, the five architectures become controlled computational treatments rather than emergent behaviors.

The resulting experiment can test:

> **When the same single-agent and multi-agent coordination structures are executed under TAL, do TAL's structured semantics, evidence provenance, scoped authority, controlled communication, authoritative state, and deterministic enforcement improve performance and reliability enough to offset the additional grammar, schema, context, communication, latency, and runtime overhead TAL introduces?**

Everything implemented in TAL v0.2 should either:

1. Be necessary to faithfully represent the five reference architectures.
2. Preserve an existing TAL safety or reproducibility invariant.
3. Measure a variable required by the experiment.

Features outside those categories should be deferred until after the experimental release.