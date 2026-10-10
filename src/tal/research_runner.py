"""Generic benchmark episode runner sharing frozen topology in TAL/control modes."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .coordination import CompiledExecutionTopology, ProtocolMode, TopologyCompiler
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
from .research_engine import CallableResearchModelAdapter, PackScoringEnvironment, TALEpisodeEngine
from .research_execution import EpisodeTrace, PersistedEpisode, compile_episode_trace
from .research_provider import ProviderBudgetExceeded
from .research_study import ScheduledEpisode

ProviderCall = Callable[[dict[str, object]], tuple[str, ProviderCallReceipt]]


class ResearchBudgetExceeded(RuntimeError):
    pass


class FrozenResearchEpisodeRunner:
    """Run one scheduled task with the same frozen topology in TAL or control mode.

    The runner has no semantic cache, mutable topology, implicit tools, or cross-run
    state. It records every provider receipt in the controlled trace.
    """

    def __init__(self, *, grammar_path: Path | None = None) -> None:
        self.grammar_path = grammar_path or Path(__file__).with_name("multi_agent_grammar.md")

    def run(
        self,
        scheduled: ScheduledEpisode,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
        pack: BenchmarkPack,
        provider_call: ProviderCall,
        freeze_hash: Hash,
    ) -> PersistedEpisode:
        self._verify_inputs(scheduled, manifest, topology, pack)
        if scheduled.protocol_mode is ProtocolMode.TAL:
            return TALEpisodeEngine(
                lambda task_manifest: PackScoringEnvironment(pack, task_manifest)
            ).run(
                scheduled,
                manifest,
                topology,
                {
                    actor.actor_id: CallableResearchModelAdapter(
                        model_id=(
                            manifest.model_configuration.resolved_model
                            or manifest.model_configuration.model
                        ),
                        manifest_hash=manifest.manifest_hash
                        or sha256(manifest.model_dump(mode="json")),
                        provider_call=provider_call,
                    )
                    for actor in topology.specification.actors
                },
                freeze_hash,
            )
        outputs: list[str] = []
        receipts: list[ProviderCallReceipt] = []
        coordination_rounds = 0
        model_call_limit = next(
            (
                budget.maximum
                for budget in manifest.resource_budgets
                if budget.kind.value == "total_model_calls"
            ),
            None,
        )
        try:
            for phase in topology.specification.phases:
                actor_ids = [
                    actor_id
                    for actor_id in phase.eligible_entity_ids
                    if actor_id.startswith("actor:")
                ]
                if not actor_ids:
                    continue
                active = set(actor_ids)
                for round_index in range(1, phase.max_rounds + 1):
                    coordination_rounds += 1
                    for activation in range(phase.max_activations_per_entity):
                        if not active:
                            break
                        for actor_id in topology.entity_order:
                            if actor_id not in active:
                                continue
                            if model_call_limit is not None and len(receipts) >= model_call_limit:
                                raise ResearchBudgetExceeded("research model-call budget exhausted")
                            request = self._request(
                                manifest,
                                topology,
                                scheduled.protocol_mode,
                                actor_id,
                                phase.phase_id,
                                round_index,
                                activation,
                                outputs,
                            )
                            raw_output, receipt = provider_call(request)
                            self._verify_receipt(manifest, receipt)
                            output, complete_phase = self._parse_output(raw_output)
                            outputs.append(output)
                            receipts.append(receipt)
                            if complete_phase:
                                active.discard(actor_id)
                    if not active:
                        break
        except (ProviderBudgetExceeded, ResearchBudgetExceeded):
            return self._record(
                scheduled,
                manifest,
                freeze_hash,
                TerminalStatus.BUDGET_EXHAUSTED,
                None,
                receipts,
                coordination_rounds,
                "",
            )
        except Exception:
            return self._record(
                scheduled,
                manifest,
                freeze_hash,
                TerminalStatus.INFRASTRUCTURE_FAILURE,
                None,
                receipts,
                coordination_rounds,
                "",
            )
        output = outputs[-1] if outputs else ""
        return self._record(
            scheduled,
            manifest,
            freeze_hash,
            TerminalStatus.COMPLETED,
            pack.score(manifest.task_id, output),
            receipts,
            coordination_rounds,
            output,
        )

    def _verify_inputs(
        self,
        scheduled: ScheduledEpisode,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
        pack: BenchmarkPack,
    ) -> None:
        if not TopologyCompiler().verify(topology):
            raise ValueError("research topology hash is invalid")
        if manifest.topology_hash != topology.topology_hash:
            raise ValueError("task manifest topology does not match episode topology")
        if (
            scheduled.benchmark_id is not manifest.benchmark_id
            or scheduled.task_id != manifest.task_id
        ):
            raise ValueError("scheduled task does not match task manifest")
        if pack.benchmark_id is not manifest.benchmark_id:
            raise ValueError("benchmark pack does not match task manifest")

    def _request(
        self,
        manifest: ResearchTaskManifest,
        topology: CompiledExecutionTopology,
        protocol_mode: ProtocolMode,
        actor_id: str,
        phase_id: str,
        round_index: int,
        activation: int,
        outputs: list[str],
    ) -> dict[str, object]:
        instructions = (
            "Respond with the best benchmark answer. Do not invoke undeclared tools."
            if protocol_mode is ProtocolMode.CONTROL
            else self.grammar_path.read_text(encoding="utf-8")
            + "\nRespond with the best benchmark answer using TAL constraints."
        )
        return {
            "model": manifest.model_configuration.resolved_model
            or manifest.model_configuration.model,
            "temperature_milli": manifest.model_configuration.temperature_milli,
            "max_output_tokens": manifest.model_configuration.max_output_tokens,
            "input": [
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": {
                        "goal": manifest.goal,
                        "task": manifest.task_payload,
                        "actor_id": actor_id,
                        "phase_id": phase_id,
                        "round_index": round_index,
                        "activation": activation,
                        "prior_outputs": outputs,
                        "topology_hash": topology.topology_hash,
                    },
                },
            ],
        }

    @staticmethod
    def _verify_receipt(manifest: ResearchTaskManifest, receipt: ProviderCallReceipt) -> None:
        expected = manifest.model_configuration.resolved_model or manifest.model_configuration.model
        if receipt.requested_model != expected or receipt.resolved_model != expected:
            raise ValueError("provider receipt model differs from frozen model")

    @staticmethod
    def _parse_output(raw_output: str) -> tuple[str, bool]:
        """Allow an explicit continuation envelope; ordinary benchmark text completes."""

        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            return raw_output, True
        if not isinstance(parsed, dict) or not isinstance(parsed.get("answer"), str):
            return raw_output, True
        complete = parsed.get("complete_phase", True)
        return parsed["answer"], bool(complete)

    @staticmethod
    def _record(
        scheduled: ScheduledEpisode,
        manifest: ResearchTaskManifest,
        freeze_hash: Hash,
        terminal_status: TerminalStatus,
        score: BenchmarkScore | None,
        receipts: list[ProviderCallReceipt],
        coordination_rounds: int,
        output: str,
    ) -> PersistedEpisode:
        total_input = sum(item.input_tokens for item in receipts)
        total_output = sum(item.output_tokens for item in receipts)
        total_reasoning = sum(item.reasoning_tokens for item in receipts)
        total_cost = sum(item.total_cost_microcents for item in receipts)
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
                metrics=EpisodeMetrics(
                    model_calls=len(receipts),
                    coordination_packets=0,
                    coordination_rounds=coordination_rounds,
                    total_input_tokens=total_input,
                    total_output_tokens=total_output,
                    total_reasoning_tokens=total_reasoning,
                    total_cost_microcents=total_cost,
                ),
                completed_at=datetime.now(UTC),
            )
        )
        return PersistedEpisode(
            result=result,
            trace=compile_episode_trace(
                EpisodeTrace(
                    episode_id=scheduled.episode_id,
                    attempt_index=0,
                    task_manifest_hash=manifest.manifest_hash
                    or sha256(manifest.model_dump(mode="json")),
                    output_hash=sha256({"output": output}),
                    receipt_hashes=tuple(sha256(item.model_dump(mode="json")) for item in receipts),
                )
            ),
            receipts=tuple(receipts),
        )
