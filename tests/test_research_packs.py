from __future__ import annotations

import json

from tal.coordination import ResourceKind
from tal.core import sha256
from tal.research import ModelConfiguration, ResearchResourceBudget
from tal.research_packs import (
    FINANCE_JUDGE_PROMPT_HASH,
    PLANCRAFT_SAMPLE_SEED,
    FinanceAgentPack,
    FinanceTaskRecord,
    PlanCraftNativeVerdict,
    PlanCraftPack,
    PlanCraftTaskRecord,
    frozen_fixture_finance_judge,
    load_finance_agent_public_csv,
    load_plancraft_test_snapshot,
    sample_plancraft_stratified,
)


def model() -> ModelConfiguration:
    return ModelConfiguration(
        provider="fixture", model="fixture", max_output_tokens=10, context_capacity=100
    )


def test_finance_pack_requires_the_full_frozen_main_list_and_uses_independent_judge() -> None:
    tasks = tuple(
        FinanceTaskRecord(
            task_id=f"task:finance_{index:02d}",
            prompt="question",
            source_ref="source",
            rubric_ref="rubric",
        )
        for index in range(50)
    )
    pack = FinanceAgentPack(tasks, frozen_fixture_finance_judge())
    manifest = pack.task_manifest(
        "task:finance_00",
        topology_hash=sha256({"topology": "fixture"}),
        model_configuration=model(),
        resource_budgets=(ResearchResourceBudget(kind=ResourceKind.MODEL_CALLS, maximum=3),),
    )
    assert manifest.tools[0].tool_id == "tool:tavily_search"
    assert (
        pack.score("task:finance_00", "answer").metrics["judge_prompt_hash"]
        == FINANCE_JUDGE_PROMPT_HASH
    )


def test_plancraft_stratified_sample_is_seeded_and_native_score_is_exact() -> None:
    tasks = tuple(
        PlanCraftTaskRecord(
            task_id=f"task:plan_{index:03d}",
            prompt="plan",
            recipe_depth=index,
            native_task_ref=f"native:{index}",
            expected_output_hash=sha256({"output": "correct"}),
        )
        for index in range(80)
    )
    sample = sample_plancraft_stratified(tasks, count=8, seed=PLANCRAFT_SAMPLE_SEED)
    assert [item.task_id for item in sample] == [
        item.task_id for item in sample_plancraft_stratified(tasks, count=8, seed=42)
    ]
    score = PlanCraftPack(
        sample,
        native_scorer=lambda _task, _output: PlanCraftNativeVerdict(success=True, num_steps=3),
    ).score(sample[0].task_id, "correct")
    assert score.success
    assert score.metrics["num_steps"] == 3


def test_upstream_snapshot_importers_preserve_the_finance_count_and_plancraft_ids(tmp_path) -> None:
    finance_csv = tmp_path / "public.csv"
    rows = "Question,Answer,Question Type,Expert time (mins),Rubric\n" + "\n".join(
        f"question {index},answer,type,1,[]" for index in range(50)
    )
    finance_csv.write_text(rows, encoding="utf-8")
    assert len(load_finance_agent_public_csv(finance_csv)) == 50
    plancraft_json = tmp_path / "plancraft.json"
    plancraft_json.write_text(
        json.dumps(
            {
                "instances": [
                    {"id": "TEST0000", "target": "stone", "inventory": {}, "complexity_bin": 2}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_plancraft_test_snapshot(plancraft_json)[0].task_id == "task:plancraft_test0000"
