# Agent Scaling v2.1.3 Reference Architecture Audit

## Source

- Repository: `https://github.com/ybkim95/agent-scaling`
- Frozen release: `v2.1.3`
- Scope: executable architecture code and `REPRODUCTION.md`, reviewed on 2026-08-17.

The TAL v0.3 `agent_scaling_reference` profile bundle is derived from this
release. The release's executable behavior is authoritative if its prose and
implementation disagree. Any behavioral correction must use a new named
profile rather than silently changing this reference profile.

## Frozen architecture facts

| Architecture | Actors/stages | Reference behavior |
|---|---:|---|
| Single | 1 actor | `max_steps=100`, scaled to `max(100, time_limit // 5)` when task time is supplied. |
| Independent | 3 workers + deterministic stage | Workers run independently with `min_iterations=3`, `max_iterations=25`; the stage concatenates outputs in insertion order without feedback, voting, or cross-validation. |
| Centralized | Lead + 3 workers | Lead creates a plan and coordinates up to 10 rounds. Worker calls are concurrent after lead guidance; workers use a 25-iteration ceiling. |
| Decentralized | 3 peers + deterministic stage | One independent round plus nine peer-debate rounds; all peers receive previous-round responses, then a deterministic majority answer is selected. |
| Hybrid | Lead + 3 workers | Centralized orchestration followed by one peer-enhanced worker round. |

## Material source behavior

- `n_base_agents=3`, `min_iterations_per_agent=3`, and
  `max_iterations_per_agent=25` apply to all multi-agent configurations.
- Centralized and decentralized configurations set `max_rounds=10`. Hybrid
  inherits the lead-agent fallback of 10 because its YAML does not override it.
- The decentralized profile configures a 70% consensus threshold. The released
  vote implementation logs threshold data but selects the most-common final
  answer even when the threshold is not met; ties resolve by declared agent
  order. `agent_scaling_reference` intentionally sets
  `threshold_gates_selection=false` to reproduce that behavior.
- Hybrid shares peer findings only after its centralized orchestration loop,
  truncating each raw peer finding to 400 characters; no additional lead
  synthesis occurs after that peer-enhanced round.

## Parity gate

Before any paid pilot, run the upstream release and TAL's instrumented
`CONTROL` adapter on an excluded fixed instance set. Freeze the upstream commit
hash, resolved prompts, task IDs, model settings, tool schemas, scheduler
policy, and output comparison tolerance with the research-run manifest.
