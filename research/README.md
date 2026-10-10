# TAL v0.4 research package

This directory holds only reproducible, non-secret study artifacts. Create the
prespecified plan with `tal research bootstrap`; seal a completed
`ExperimentFreeze` with `tal research freeze`; and create a pair-adjacent schedule
from the frozen Finance-Agent and PlanCraft task identifiers with `tal research
schedule`.

`task-ids.json` is the generated 100-task main-study list. Recreate it from the
pinned upstream snapshots with `tal research snapshot --finance-csv ...
--plancraft-json ...`; its current hash is recorded by the command output and should
be included as a `FrozenArtifact` before sealing the experiment freeze.

The confirmatory main study consists of 1,000 episodes: 50 frozen Finance-Agent
tasks and 50 PlanCraft tasks, each paired TAL/control across five frozen
architectures. PlanCraft selection is stratified by recipe-depth quartiles using seed
42. The pilot excludes 4 synthetic Finance tasks and 4 non-test PlanCraft tasks.

Financial ceilings are represented in microcents: $75 pilot, $400 main, and $25 for
at most two infrastructure-only reruns per episode. Provider keys, raw prompts,
responses, and full traces must remain outside version control under `research/raw`.

Use `uv sync --locked --all-groups` for the local research environment and `docker
build` for the production image. The resulting image digest—not its tag—belongs in
the sealed `ExperimentFreeze`.

Public release: code, frozen configuration, hashes, aggregate results, and redacted
examples. Controlled release: complete raw traces and any identifying benchmark
material.
