"""Frozen benchmark-pack adapters for the Finance-Agent and PlanCraft studies.

The adapters intentionally load task records from versioned local snapshots. They do
not scrape a moving benchmark at run time, and the Finance rubric is delegated to a
separate judge callable rather than the actor model.
"""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue

from .core import Hash, Identifier, StrictModel, sha256
from .research import (
    BenchmarkId,
    BenchmarkScore,
    ModelConfiguration,
    ResearchResourceBudget,
    ResearchTaskManifest,
    ToolDefinition,
    compile_research_manifest,
)

FINANCE_PACK_ID = "tal.benchmark.finance-agent-0.4.0-rc2"
PLANCRAFT_PACK_ID = "tal.benchmark.plancraft-0.4.0-rc2"
FINANCE_TASK_COUNT = 50
PLANCRAFT_SAMPLE_COUNT = 50
PLANCRAFT_SAMPLE_SEED = 42
FINANCE_JUDGE_PROMPT = (
    "You are an independent Finance-Agent evaluator. Grade only the submitted answer "
    "against the frozen task rubric. Return strict JSON with success and score_millionths."
)
FINANCE_JUDGE_PROMPT_HASH = sha256(FINANCE_JUDGE_PROMPT)


class FinanceTaskRecord(StrictModel):
    task_id: Identifier
    prompt: str = Field(min_length=1, max_length=20_000)
    split: str = Field(default="main", min_length=1, max_length=128)
    source_ref: str = Field(min_length=1, max_length=2_000)
    rubric_ref: str = Field(min_length=1, max_length=2_000)


class PlanCraftTaskRecord(StrictModel):
    task_id: Identifier
    prompt: str = Field(min_length=1, max_length=20_000)
    recipe_depth: int = Field(ge=0, le=1_000)
    split: str = Field(default="main", min_length=1, max_length=128)
    native_task_ref: str = Field(min_length=1, max_length=2_000)
    native_payload: dict[str, JsonValue] = Field(default_factory=dict)
    expected_output_hash: Hash


class PlanCraftNativeVerdict(StrictModel):
    success: bool
    num_steps: int = Field(ge=-1)


class FinanceJudgeVerdict(StrictModel):
    success: bool
    score_millionths: int = Field(ge=0, le=1_000_000)
    rationale: str = Field(default="", max_length=10_000)


class FinanceAgentPack:
    benchmark_id = BenchmarkId.FINANCE_AGENT
    domain_pack_id = FINANCE_PACK_ID

    def __init__(
        self,
        tasks: tuple[FinanceTaskRecord, ...],
        judge: Callable[[FinanceTaskRecord, str], FinanceJudgeVerdict],
    ) -> None:
        if len(tasks) != FINANCE_TASK_COUNT:
            raise ValueError("Finance-Agent main pack must contain exactly 50 frozen tasks")
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ValueError("Finance-Agent task identifiers must be unique")
        self._tasks = {task.task_id: task for task in tasks}
        self._judge = judge

    @property
    def task_ids(self) -> tuple[Identifier, ...]:
        return tuple(sorted(self._tasks))

    def task_manifest(
        self,
        task_id: str,
        *,
        topology_hash: Hash,
        model_configuration: ModelConfiguration,
        resource_budgets: tuple[ResearchResourceBudget, ...],
    ) -> ResearchTaskManifest:
        task = self._tasks[task_id]
        payload: dict[str, JsonValue] = {"prompt": task.prompt, "source_ref": task.source_ref}
        return compile_research_manifest(
            ResearchTaskManifest(
                domain_pack=self.domain_pack_id,
                benchmark_id=self.benchmark_id,
                task_id=task.task_id,
                split=task.split,
                task_payload=payload,
                task_payload_hash=sha256(payload),
                goal=task.prompt,
                tools=(
                    ToolDefinition(
                        tool_id="tool:tavily_search",
                        description="Pinned Tavily Basic web search; read-only.",
                        read_only=True,
                        arguments_schema_hash=sha256(
                            {
                                "type": "object",
                                "required": ["query"],
                                "properties": {"query": {"type": "string"}},
                            }
                        ),
                        arguments_schema={
                            "type": "object",
                            "required": ["query"],
                            "properties": {"query": {"type": "string"}},
                        },
                        result_schema={"type": "object"},
                        result_schema_hash=sha256({"type": "object"}),
                    ),
                    ToolDefinition(
                        tool_id="tool:python_repl",
                        description="Ephemeral sandboxed Python calculation tool.",
                        read_only=True,
                        arguments_schema_hash=sha256(
                            {
                                "type": "object",
                                "required": ["code"],
                                "properties": {"code": {"type": "string"}},
                            }
                        ),
                        arguments_schema={
                            "type": "object",
                            "required": ["code"],
                            "properties": {"code": {"type": "string"}},
                        },
                        result_schema={"type": "object"},
                        result_schema_hash=sha256({"type": "object"}),
                    ),
                    ToolDefinition(
                        tool_id="tool:submit",
                        description="Submit the final answer to the native benchmark.",
                        read_only=False,
                        external_effect=True,
                        arguments_schema_hash=sha256(
                            {
                                "type": "object",
                                "required": ["answer"],
                                "properties": {"answer": {"type": "string"}},
                            }
                        ),
                        arguments_schema={
                            "type": "object",
                            "required": ["answer"],
                            "properties": {"answer": {"type": "string"}},
                        },
                        result_schema={"type": "object"},
                        result_schema_hash=sha256({"type": "object"}),
                    ),
                ),
                resource_budgets=resource_budgets,
                topology_hash=topology_hash,
                model_configuration=model_configuration,
            )
        )

    def score(self, task_id: str, output: str) -> BenchmarkScore:
        verdict = self._judge(self._tasks[task_id], output)
        return BenchmarkScore(
            success=verdict.success,
            native_score_millionths=verdict.score_millionths,
            metrics={
                "judge_rationale": verdict.rationale,
                "judge_prompt_hash": FINANCE_JUDGE_PROMPT_HASH,
            },
            scorer_id="tal.finance-independent-judge-0.4.0-rc2",
            scorer_hash=sha256(
                {
                    "prompt": FINANCE_JUDGE_PROMPT,
                    "rubrics": [task.rubric_ref for task in self._tasks.values()],
                }
            ),
        )


PlanCraftNativeScorer = Callable[[PlanCraftTaskRecord, str], PlanCraftNativeVerdict]


class PlanCraftPack:
    benchmark_id = BenchmarkId.PLANCRAFT
    domain_pack_id = PLANCRAFT_PACK_ID

    def __init__(
        self,
        tasks: tuple[PlanCraftTaskRecord, ...],
        native_scorer: PlanCraftNativeScorer | None = None,
    ) -> None:
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ValueError("PlanCraft task identifiers must be unique")
        self._tasks = {task.task_id: task for task in tasks}
        self._native_scorer = native_scorer

    @property
    def task_ids(self) -> tuple[Identifier, ...]:
        return tuple(sorted(self._tasks))

    def task_manifest(
        self,
        task_id: str,
        *,
        topology_hash: Hash,
        model_configuration: ModelConfiguration,
        resource_budgets: tuple[ResearchResourceBudget, ...],
    ) -> ResearchTaskManifest:
        task = self._tasks[task_id]
        payload: dict[str, JsonValue] = {
            "prompt": task.prompt,
            "native_task_ref": task.native_task_ref,
            "recipe_depth": task.recipe_depth,
            "native_task": task.native_payload,
        }
        return compile_research_manifest(
            ResearchTaskManifest(
                domain_pack=self.domain_pack_id,
                benchmark_id=self.benchmark_id,
                task_id=task.task_id,
                split=task.split,
                task_payload=payload,
                task_payload_hash=sha256(payload),
                goal=task.prompt,
                tools=_plancraft_tools(),
                resource_budgets=resource_budgets,
                topology_hash=topology_hash,
                model_configuration=model_configuration,
            )
        )

    def score(self, task_id: str, output: str) -> BenchmarkScore:
        task = self._tasks[task_id]
        if self._native_scorer is None:
            raise RuntimeError("PlanCraft scoring requires the pinned native runtime")
        verdict = self._native_scorer(task, output)
        return BenchmarkScore(
            success=verdict.success,
            native_score_millionths=1_000_000 if verdict.success else 0,
            metrics={"recipe_depth": task.recipe_depth, "num_steps": verdict.num_steps},
            scorer_id="plancraft-native-environment-scorer",
            scorer_hash=sha256(
                {
                    "task_ref": task.native_task_ref,
                    "native_payload": task.native_payload,
                }
            ),
        )


def _plancraft_tools() -> tuple[ToolDefinition, ...]:
    action_schema: dict[str, JsonValue] = {
        "type": "object",
        "required": ["action"],
        "properties": {"action": {"type": "string"}},
    }
    return tuple(
        ToolDefinition(
            tool_id=f"tool:plancraft_{action}",
            description=f"Native PlanCraft {action} action.",
            read_only=False,
            arguments_schema_hash=sha256(action_schema),
            arguments_schema=action_schema,
            result_schema={"type": "object"},
            result_schema_hash=sha256({"type": "object"}),
        )
        for action in ("search", "move", "smelt", "impossible")
    )


def sample_plancraft_stratified(
    tasks: tuple[PlanCraftTaskRecord, ...],
    *,
    count: int = PLANCRAFT_SAMPLE_COUNT,
    seed: int = PLANCRAFT_SAMPLE_SEED,
) -> tuple[PlanCraftTaskRecord, ...]:
    """Sample near-equal recipe-depth quartiles using the prespecified seed."""

    if count <= 0 or count > len(tasks):
        raise ValueError("sample count must be positive and no greater than available tasks")
    ordered = sorted(tasks, key=lambda task: (task.recipe_depth, task.task_id))
    quartiles = [
        ordered[index * len(ordered) // 4 : (index + 1) * len(ordered) // 4] for index in range(4)
    ]
    requested = [count // 4 + (1 if index < count % 4 else 0) for index in range(4)]
    if any(len(group) < requested[index] for index, group in enumerate(quartiles)):
        raise ValueError("insufficient tasks in a PlanCraft recipe-depth quartile")
    selected: list[PlanCraftTaskRecord] = []
    for index, group in enumerate(quartiles):
        rng = random.Random(f"{seed}:quartile:{index}")
        selected.extend(rng.sample(group, requested[index]))
    return tuple(sorted(selected, key=lambda task: task.task_id))


def frozen_fixture_finance_judge(
    expected: Literal["pass", "fail"] = "pass",
) -> Callable[[FinanceTaskRecord, str], FinanceJudgeVerdict]:
    """A deterministic testing-only judge; production wiring must use a separate model."""

    def judge(_: FinanceTaskRecord, __: str) -> FinanceJudgeVerdict:
        return FinanceJudgeVerdict(
            success=expected == "pass", score_millionths=1_000_000 if expected == "pass" else 0
        )

    return judge


def load_finance_agent_public_csv(path: Path) -> tuple[FinanceTaskRecord, ...]:
    """Import the pinned public Finance-Agent CSV with a Windows-safe CSV limit."""

    _set_csv_field_limit()
    records: list[FinanceTaskRecord] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            question = (row.get("Question") or "").strip()
            if not question:
                continue
            records.append(
                FinanceTaskRecord(
                    task_id=f"task:finance_{index:02d}",
                    prompt=question,
                    source_ref=f"finance-agent/data/public.csv:{index}",
                    rubric_ref=f"finance-agent/data/public.csv:{index}:Rubric",
                )
            )
    if len(records) != FINANCE_TASK_COUNT:
        raise ValueError(
            f"expected {FINANCE_TASK_COUNT} Finance-Agent public tasks, found {len(records)}"
        )
    return tuple(records)


def load_plancraft_test_snapshot(path: Path) -> tuple[PlanCraftTaskRecord, ...]:
    """Normalize the upstream PlanCraft JSON without importing its runtime dependency."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    instances = payload.get("instances") if isinstance(payload, dict) else None
    if not isinstance(instances, list) or not instances:
        raise ValueError("PlanCraft snapshot must contain a non-empty instances list")
    records: list[PlanCraftTaskRecord] = []
    for index, instance in enumerate(instances):
        if not isinstance(instance, dict):
            raise ValueError("PlanCraft instance must be an object")
        target = instance.get("target")
        inventory = instance.get("inventory")
        identifier = instance.get("id", f"index-{index}")
        if (
            not isinstance(target, str)
            or not isinstance(inventory, dict)
            or not isinstance(identifier, str)
        ):
            raise ValueError("PlanCraft instance has invalid target, inventory, or id")
        path_length = instance.get("optimal_path_length")
        depth = (
            int(path_length)
            if isinstance(path_length, int)
            else int(instance.get("complexity_bin") or 0)
        )
        prompt = (
            "Craft the target item from the inventory using valid PlanCraft actions. "
            + json.dumps(
                {
                    "target": target,
                    "inventory": inventory,
                    "impossible": bool(instance.get("impossible", False)),
                },
                sort_keys=True,
            )
        )
        records.append(
            PlanCraftTaskRecord(
                task_id=f"task:plancraft_{identifier.lower()}",
                prompt=prompt,
                recipe_depth=depth,
                native_task_ref=f"plancraft-test:{identifier}",
                native_payload=instance,
                expected_output_hash=sha256({"target": target}),
            )
        )
    return tuple(records)


def _set_csv_field_limit() -> None:
    """Avoid CPython-on-Windows overflow from passing an oversized C long."""

    limit = 2**31 - 1
    while limit > 0:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10
    raise RuntimeError("could not set a usable CSV field-size limit")
