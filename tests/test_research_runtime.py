from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import JsonValue

from tal.coordination import ArchitectureId, ProtocolMode
from tal.core import sha256
from tal.profiles import compile_profile
from tal.research import (
    BenchmarkId,
    BenchmarkScore,
    ModelConfiguration,
    ResearchTaskManifest,
    ToolDefinition,
    compile_research_manifest,
)
from tal.research_environment import ScriptedBenchmarkEnvironment
from tal.research_runtime import (
    ResearchAnswerProposal,
    ResearchModelContext,
    ResearchModelOutput,
    ResearchToolProposal,
    ResearchTopologyScheduler,
    verify_research_event_chain,
)


def manifest(topology_hash: str, tools: tuple[ToolDefinition, ...] = ()) -> ResearchTaskManifest:
    payload: dict[str, JsonValue] = {"prompt": "answer the task"}
    return compile_research_manifest(
        ResearchTaskManifest(
            domain_pack="tal.benchmark.fixture",
            benchmark_id=BenchmarkId.FINANCE_AGENT,
            task_id="task:fixture",
            split="fixture",
            task_payload=payload,
            task_payload_hash=sha256(payload),
            goal="answer the task",
            tools=tools,
            topology_hash=topology_hash,
            model_configuration=ModelConfiguration(
                provider="fixture",
                model="fixture-model",
                resolved_model="fixture-model",
                max_output_tokens=100,
                context_capacity=10_000,
            ),
        )
    )


@dataclass
class AnswerAdapter:
    manifest_hash: str
    model_id: str = "fixture-model"

    async def respond(self, context: ResearchModelContext) -> ResearchModelOutput:
        return ResearchModelOutput(
            proposal=ResearchAnswerProposal(
                proposal_id=f"proposal:{context.actor_id}:{context.round_index}",
                manifest_hash=self.manifest_hash,
                state_version=context.state_version,
                answer=f"answer from {context.actor_id}",
            ),
            complete_phase=True,
        )


def test_tal_scheduler_records_hash_chained_events_and_scores_native_environment() -> None:
    topology = compile_profile(ArchitectureId.SINGLE)
    task = manifest(topology.topology_hash)

    async def scorer(answer: str) -> BenchmarkScore:
        return BenchmarkScore(
            success=answer == "answer from actor:single",
            native_score_millionths=1_000_000 if answer == "answer from actor:single" else 0,
            scorer_id="fixture-native",
            scorer_hash=sha256({"fixture": True}),
        )

    environment = ScriptedBenchmarkEnvironment(
        benchmark_id=BenchmarkId.FINANCE_AGENT,
        environment_id="environment:fixture",
        tools=(),
        score_handler=scorer,
    )
    state, score, counters, _budget = asyncio.run(
        ResearchTopologyScheduler(environment).run(
            manifest=task,
            topology=topology,
            protocol_mode=ProtocolMode.TAL,
            adapters={"actor:single": AnswerAdapter(task.manifest_hash or "")},
        )
    )
    assert score.success
    assert counters.model_calls == 1
    assert verify_research_event_chain(state.events)


def test_tal_scheduler_rejects_undeclared_tool_without_environment_mutation() -> None:
    topology = compile_profile(ArchitectureId.SINGLE)
    task = manifest(topology.topology_hash)

    @dataclass
    class ToolAdapter:
        model_id: str = "fixture-model"

        async def respond(self, context: ResearchModelContext) -> ResearchModelOutput:
            return ResearchModelOutput(
                proposal=ResearchToolProposal(
                    proposal_id="proposal:bad_tool",
                    manifest_hash=task.manifest_hash or "",
                    state_version=context.state_version,
                    tool_id="tool:invented",
                ),
                complete_phase=True,
            )

    environment = ScriptedBenchmarkEnvironment(
        benchmark_id=BenchmarkId.FINANCE_AGENT,
        environment_id="environment:fixture",
        tools=(),
    )
    state, _score, counters, _budget = asyncio.run(
        ResearchTopologyScheduler(environment).run(
            manifest=task,
            topology=topology,
            protocol_mode=ProtocolMode.TAL,
            adapters={"actor:single": ToolAdapter()},
        )
    )
    assert counters.invalid_tool_requests == 1
    assert counters.rejected_proposals == 1
    assert state.environment.state_version == 0
