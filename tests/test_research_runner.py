from __future__ import annotations

from tal.coordination import ArchitectureId, ProtocolMode, ResourceKind
from tal.core import sha256
from tal.profiles import compile_profile
from tal.research import ModelConfiguration, ProviderCallReceipt, ResearchResourceBudget
from tal.research_packs import PlanCraftNativeVerdict, PlanCraftPack, PlanCraftTaskRecord
from tal.research_runner import FrozenResearchEpisodeRunner
from tal.research_study import ScheduledEpisode, StudySplit


def test_frozen_runner_uses_the_same_topology_for_control_and_tal() -> None:
    topology = compile_profile(ArchitectureId.SINGLE)
    model = ModelConfiguration(
        provider="fixture",
        model="fixture-model",
        resolved_model="fixture-model",
        max_output_tokens=10,
        context_capacity=100,
    )
    task = PlanCraftTaskRecord(
        task_id="task:plan",
        prompt="return correct",
        recipe_depth=1,
        native_task_ref="native:plan",
        expected_output_hash=sha256({"output": "correct"}),
    )
    pack = PlanCraftPack(
        (task,),
        native_scorer=lambda _task, output: PlanCraftNativeVerdict(
            success=output == "correct", num_steps=1
        ),
    )
    manifest = pack.task_manifest(
        task.task_id,
        topology_hash=topology.topology_hash,
        model_configuration=model,
        resource_budgets=(ResearchResourceBudget(kind=ResourceKind.MODEL_CALLS, maximum=100),),
    )

    def call(request: dict[str, object]) -> tuple[str, ProviderCallReceipt]:
        return "correct", ProviderCallReceipt(
            call_id="provider-call:00000001",
            provider="fixture",
            requested_model=str(request["model"]),
            resolved_model="fixture-model",
            request_hash=sha256(request),
            response_hash=sha256({"output": "correct"}),
            input_tokens=1,
            latency_milliseconds=1,
        )

    runner = FrozenResearchEpisodeRunner()
    for mode in (ProtocolMode.TAL, ProtocolMode.CONTROL):
        scheduled = ScheduledEpisode(
            sequence=0,
            benchmark_id=pack.benchmark_id,
            task_id=task.task_id,
            architecture=ArchitectureId.SINGLE,
            protocol_mode=mode,
            split=StudySplit.MAIN,
            pair_id=f"pair:{mode.value}",
            episode_id=f"episode:{mode.value}",
        )
        record = runner.run(scheduled, manifest, topology, pack, call, "sha256:" + "1" * 64)
        assert record.result.score is not None and record.result.score.success
        assert record.result.metrics.model_calls == 1
        assert record.result.freeze_hash == "sha256:" + "1" * 64
