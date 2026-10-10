"""Episode engines for the preregistered full-TAL-effect experiment."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import JsonValue

from .coordination import CompiledExecutionTopology, ProtocolMode
from .core import Hash, sha256
from .research import (
    AttemptKind,
    BenchmarkPack,
    BenchmarkScore,
    EpisodeMetrics,
    EpisodeResult,
    ProviderCallReceipt,
    ResearchTaskManifest,
    TerminalStatus,
    compile_episode_result,
)
from .research_environment import (
    BenchmarkEnvironment,
    BenchmarkObservation,
    BenchmarkToolCall,
    BenchmarkToolResult,
    EnvironmentProtocolError,
)
from .research_execution import (
    ControlledTrace,
    EpisodeTrace,
    PersistedEpisode,
    compile_episode_trace,
)
from .research_provider import ProviderGateway, ProviderMessage, ProviderRequest
from .research_runtime import (
    AsyncResearchModelAdapter,
    ResearchModelContext,
    ResearchModelOutput,
    ResearchRuntimeError,
    ResearchTopologyScheduler,
)
from .research_study import ScheduledEpisode

EnvironmentFactory = Callable[[ResearchTaskManifest], BenchmarkEnvironment]
RawProviderCall = Callable[[dict[str, object]], tuple[str, ProviderCallReceipt]]


@dataclass
class GatewayResearchModelAdapter:
    """Provider-backed TAL actor that accepts only closed research outputs."""

    model_id: str
    manifest_hash: Hash
    gateway: ProviderGateway
    receipts: list[ProviderCallReceipt] = field(default_factory=list)
    model_contexts: list[dict[str, JsonValue]] = field(default_factory=list)
    model_outputs: list[str] = field(default_factory=list)

    async def respond(self, context: ResearchModelContext) -> ResearchModelOutput:
        system = (
            "You are a frozen TAL research actor. Return one strict JSON research model output."
        )
        request = ProviderRequest(
            model=self.model_id,
            messages=(
                ProviderMessage(role="system", content=system),
                ProviderMessage(role="user", content=context.rendered_text),
            ),
            max_output_tokens=4_096,
            context_capacity=max(
                1, sum(len(value.split()) for value in context.segments.values()) + 4_096
            ),
            input_segments=context.segments,
        )
        self.model_contexts.append(dict(context.segments))
        result = await asyncio.to_thread(self.gateway.complete, request)
        self.receipts.append(result.receipt)
        self.model_outputs.append(result.text)
        return _parse_research_output(result.text, context, self.manifest_hash, len(self.receipts))


@dataclass
class CallableResearchModelAdapter:
    """Fixture/compatibility adapter retaining the same typed TAL output boundary."""

    model_id: str
    manifest_hash: Hash
    provider_call: RawProviderCall
    receipts: list[ProviderCallReceipt] = field(default_factory=list)
    model_contexts: list[dict[str, JsonValue]] = field(default_factory=list)
    model_outputs: list[str] = field(default_factory=list)

    async def respond(self, context: ResearchModelContext) -> ResearchModelOutput:
        request: dict[str, object] = {
            "model": self.model_id,
            "input": [{"role": "user", "content": context.rendered_text}],
            "temperature_milli": 0,
        }
        self.model_contexts.append(dict(context.segments))
        text, receipt = await asyncio.to_thread(self.provider_call, request)
        if receipt.requested_model != self.model_id or receipt.resolved_model != self.model_id:
            raise ValueError("provider receipt model differs from frozen model")
        self.receipts.append(receipt)
        self.model_outputs.append(text)
        return _parse_research_output(text, context, self.manifest_hash, len(self.receipts))


@dataclass
class PackScoringEnvironment:
    """Compatibility environment for legacy text-only fixture packs.

    It deliberately supports no tools.  Production benchmark runs must supply a
    native environment factory instead.
    """

    pack: BenchmarkPack
    manifest: ResearchTaskManifest
    environment_id: str = "environment:pack_scoring"
    _state_version: int = 0

    @property
    def benchmark_id(self):  # type: ignore[no-untyped-def]
        return self.pack.benchmark_id

    async def reset(self, manifest: ResearchTaskManifest) -> BenchmarkObservation:
        self._state_version = 0
        return BenchmarkObservation(
            environment_id=self.environment_id,
            state_version=self._state_version,
            text=manifest.goal,
            payload={"task_id": manifest.task_id},
        )

    def tool_definitions(self):  # type: ignore[no-untyped-def]
        return ()

    async def execute_tool(self, call: BenchmarkToolCall) -> BenchmarkToolResult:
        del call
        raise EnvironmentProtocolError("legacy scoring environment has no tools")

    async def score(self, final_answer: str) -> BenchmarkScore:
        return self.pack.score(self.manifest.task_id, final_answer)

    async def close(self) -> None:
        return None


class TALEpisodeEngine:
    """Run TAL-controlled benchmark episodes through typed scheduler and action gate."""

    def __init__(self, environment_factory: EnvironmentFactory) -> None:
        self.environment_factory = environment_factory

    async def run_async(
        self,
        scheduled: ScheduledEpisode,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
        adapters: Mapping[str, AsyncResearchModelAdapter],
        freeze_hash: Hash,
    ) -> PersistedEpisode:
        if scheduled.protocol_mode is not ProtocolMode.TAL:
            raise ValueError("TALEpisodeEngine accepts TAL scheduled episodes only")
        environment = self.environment_factory(manifest)
        receipts: list[ProviderCallReceipt] = []
        try:
            state, score, counters, _budget = await ResearchTopologyScheduler(environment).run(
                manifest=manifest,
                topology=topology,
                protocol_mode=scheduled.protocol_mode,
                adapters=adapters,
            )
            receipts = _receipts(adapters)
            metrics = _metrics(counters.metrics(), receipts)
            return _record(
                scheduled,
                manifest,
                freeze_hash,
                TerminalStatus.COMPLETED,
                score,
                metrics,
                state.final_answer,
                receipts,
                _controlled_trace(adapters),
            )
        except ResearchRuntimeError as error:
            receipts = _receipts(adapters)
            status = (
                TerminalStatus.BUDGET_EXHAUSTED
                if error.code in {"BUDGET_EXCEEDED", "CONTEXT_BUDGET_EXCEEDED"}
                else TerminalStatus.FAILED
            )
            return _record(
                scheduled,
                manifest,
                freeze_hash,
                status,
                None,
                _metrics(EpisodeMetrics(rejected_proposals=1), receipts),
                "",
                receipts,
                _controlled_trace(adapters),
            )
        except (EnvironmentProtocolError, OSError, TimeoutError):
            receipts = _receipts(adapters)
            return _record(
                scheduled,
                manifest,
                freeze_hash,
                TerminalStatus.INFRASTRUCTURE_FAILURE,
                None,
                _metrics(EpisodeMetrics(), receipts),
                "",
                receipts,
                _controlled_trace(adapters),
            )
        finally:
            await environment.close()

    def run(
        self,
        scheduled: ScheduledEpisode,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
        adapters: Mapping[str, AsyncResearchModelAdapter],
        freeze_hash: Hash,
    ) -> PersistedEpisode:
        return asyncio.run(self.run_async(scheduled, manifest, topology, adapters, freeze_hash))


class ControlEpisodeExecutor:
    """Protocol boundary for the pinned ordinary upstream harness."""

    async def run(
        self,
        scheduled: ScheduledEpisode,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
    ) -> tuple[str, BenchmarkScore, EpisodeMetrics, tuple[ProviderCallReceipt, ...]]:
        raise NotImplementedError


class ControlEpisodeEngine:
    """Records ordinary upstream-harness episodes without applying TAL authority."""

    def __init__(self, executor: ControlEpisodeExecutor) -> None:
        self.executor = executor

    async def run_async(
        self,
        scheduled: ScheduledEpisode,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
        freeze_hash: Hash,
    ) -> PersistedEpisode:
        if scheduled.protocol_mode is not ProtocolMode.CONTROL:
            raise ValueError("ControlEpisodeEngine accepts CONTROL scheduled episodes only")
        try:
            output, score, metrics, receipts = await self.executor.run(
                scheduled, manifest, topology
            )
            return _record(
                scheduled,
                manifest,
                freeze_hash,
                TerminalStatus.COMPLETED,
                score,
                _metrics(metrics, receipts),
                output,
                receipts,
            )
        except (EnvironmentProtocolError, OSError, TimeoutError):
            return _record(
                scheduled,
                manifest,
                freeze_hash,
                TerminalStatus.INFRASTRUCTURE_FAILURE,
                None,
                EpisodeMetrics(),
                "",
                (),
            )


def _receipts(adapters: Mapping[str, AsyncResearchModelAdapter]) -> list[ProviderCallReceipt]:
    receipts: list[ProviderCallReceipt] = []
    for adapter in adapters.values():
        if isinstance(adapter, GatewayResearchModelAdapter | CallableResearchModelAdapter):
            receipts.extend(adapter.receipts)
    return receipts


def _metrics(
    base: EpisodeMetrics, receipts: list[ProviderCallReceipt] | tuple[ProviderCallReceipt, ...]
) -> EpisodeMetrics:
    receipt_tuple = tuple(receipts)
    tal_tokens = sum(
        value
        for receipt in receipt_tuple
        for name, value in receipt.input_segments.items()
        if name.startswith("tal_") or name == "response_schema" or name == "topology"
    )
    coordination_tokens = sum(
        receipt.input_segments.get("received_packets", 0) for receipt in receipt_tuple
    )
    return base.model_copy(
        update={
            "model_calls": max(base.model_calls, len(receipt_tuple)),
            "total_input_tokens": sum(item.input_tokens for item in receipt_tuple),
            "total_output_tokens": sum(item.output_tokens for item in receipt_tuple),
            "total_reasoning_tokens": sum(item.reasoning_tokens for item in receipt_tuple),
            "total_cost_microcents": sum(item.total_cost_microcents for item in receipt_tuple),
            "tal_specific_input_tokens": tal_tokens,
            "coordination_input_tokens": coordination_tokens,
            "wall_clock_milliseconds": sum(item.latency_milliseconds for item in receipt_tuple),
        }
    )


def _record(
    scheduled: ScheduledEpisode,
    manifest: ResearchTaskManifest,
    freeze_hash: Hash,
    terminal_status: TerminalStatus,
    score: BenchmarkScore | None,
    metrics: EpisodeMetrics,
    output: str,
    receipts: tuple[ProviderCallReceipt, ...] | list[ProviderCallReceipt],
    controlled_trace: ControlledTrace | None = None,
) -> PersistedEpisode:
    controlled_trace = controlled_trace or ControlledTrace()
    result = compile_episode_result(
        EpisodeResult(
            episode_id=scheduled.episode_id,
            freeze_hash=freeze_hash,
            benchmark_id=scheduled.benchmark_id,
            task_id=scheduled.task_id,
            architecture=scheduled.architecture,
            protocol_mode=scheduled.protocol_mode,
            attempt_kind=AttemptKind.PRIMARY,
            terminal_status=terminal_status,
            score=score if terminal_status is TerminalStatus.COMPLETED else None,
            metrics=metrics,
            completed_at=datetime.now(UTC),
        )
    )
    receipt_tuple = tuple(receipts)
    trace = compile_episode_trace(
        EpisodeTrace(
            episode_id=scheduled.episode_id,
            attempt_index=0,
            task_manifest_hash=manifest.manifest_hash or sha256(manifest.model_dump(mode="json")),
            output_hash=sha256({"output": output}),
            receipt_hashes=tuple(sha256(item.model_dump(mode="json")) for item in receipt_tuple),
            controlled_payload_hash=sha256(controlled_trace.model_dump(mode="json")),
        )
    )
    return PersistedEpisode(
        result=result,
        trace=trace,
        receipts=receipt_tuple,
        controlled_trace=controlled_trace,
    )


def _controlled_trace(adapters: Mapping[str, AsyncResearchModelAdapter]) -> ControlledTrace:
    contexts: list[dict[str, JsonValue]] = []
    outputs: list[str] = []
    for adapter in adapters.values():
        if isinstance(adapter, GatewayResearchModelAdapter | CallableResearchModelAdapter):
            contexts.extend(adapter.model_contexts)
            outputs.extend(adapter.model_outputs)
    return ControlledTrace(model_contexts=tuple(contexts), model_outputs=tuple(outputs))


def _parse_research_output(
    text: str, context: ResearchModelContext, manifest_hash: Hash, call_number: int
) -> ResearchModelOutput:
    try:
        return ResearchModelOutput.model_validate_json(text)
    except ValueError:
        return ResearchModelOutput.model_validate(
            {
                "proposal": {
                    "proposal_id": "proposal:"
                    + sha256({"actor": context.actor_id, "call": call_number}).removeprefix(
                        "sha256:"
                    )[:24],
                    "kind": "propose_answer",
                    "manifest_hash": manifest_hash,
                    "state_version": context.state_version,
                    "answer": text,
                },
                "complete_phase": True,
            }
        )
