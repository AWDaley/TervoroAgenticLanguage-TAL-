from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tal.adapters import FakeToolRegistry, PlanProposal
from tal.compiler import compile_manifest
from tal.coordination import (
    ArchitectureId,
    CoordinationPacket,
    PacketKind,
    PacketPayload,
    ProtocolMode,
    Subgoal,
    TopologyCompiler,
)
from tal.core import sha256
from tal.invoice import InvoiceFormSubmission
from tal.profiles import compile_profile, get_profile, load_agent_scaling_reference
from tal.runtime import SequentialIds, TALRuntime
from tal.storage import InMemoryEventStore
from tal.team import (
    EpisodeDefinition,
    TeamModelContext,
    TeamModelOutput,
    TeamRuntime,
)


def fixture_manifest():
    form = InvoiceFormSubmission.model_validate_json(
        Path("src/tal/fixtures/valid_approval_package.json").read_text(encoding="utf-8")
    )
    return compile_manifest(form)


class ContextPlanAdapter:
    model_id = "context-plan"

    def __init__(self) -> None:
        self.contexts: list[TeamModelContext] = []

    async def respond(self, context: TeamModelContext) -> TeamModelOutput:
        self.contexts.append(context)
        return TeamModelOutput(
            proposal=PlanProposal(
                proposal_id=f"proposal:{context.actor_id}:{context.phase_ref}",
                goal_id=fixture_manifest().goal.goal_id,
                manifest_hash=fixture_manifest().integrity.manifest_hash or "",
                manifest_version=1,
                state_version=context.state_version,
                plan_id=f"plan:{context.actor_id}",
            ),
            complete_phase=True,
        )


class LeadAssignmentAdapter:
    model_id = "context-plan"

    def __init__(self, manifest) -> None:
        self.manifest = manifest

    async def respond(self, context: TeamModelContext) -> TeamModelOutput:
        packet = CoordinationPacket(
            packet_id="packet:assign_a",
            sender_actor_id=context.actor_id,
            recipient_actor_ids=("actor:worker_a",),
            goal_ref=self.manifest.goal.goal_id,
            phase_ref=context.phase_ref,
            round_index=context.round_index,
            state_version=context.state_version,
            kind=PacketKind.ASSIGN_SUBGOAL,
            payload=PacketPayload(
                subgoal=Subgoal(
                    subgoal_id="subgoal:verify",
                    parent_goal_ref=self.manifest.goal.goal_id,
                    assigned_actor_id="actor:worker_a",
                    desired_result="verify the vendor",
                )
            ),
        )
        return TeamModelOutput(packet=packet, complete_phase=True)


class PersistentContextPlanAdapter(ContextPlanAdapter):
    async def respond(self, context: TeamModelContext) -> TeamModelOutput:
        self.contexts.append(context)
        attempt = len(self.contexts)
        return TeamModelOutput(
            proposal=PlanProposal(
                proposal_id=f"proposal:{context.actor_id}:{attempt}",
                goal_id=fixture_manifest().goal.goal_id,
                manifest_hash=fixture_manifest().integrity.manifest_hash or "",
                manifest_version=1,
                state_version=context.state_version,
                plan_id=f"plan:{context.actor_id}:{attempt}",
            ),
            complete_phase=False,
        )


def test_frozen_reference_profiles_compile() -> None:
    bundle = load_agent_scaling_reference()
    assert bundle.source_revision == "v2.1.3"
    assert len(bundle.profiles) == 5
    for architecture in ArchitectureId:
        compiled = compile_profile(architecture)
        assert compiled.specification.architecture is architecture
        assert compiled.state.value == "frozen"
        assert compiled.topology_hash.startswith("sha256:")
    decentralized = get_profile(ArchitectureId.DECENTRALIZED)
    assert decentralized.consensus_policy.threshold_gates_selection is False


def test_single_team_phase_records_telemetry_and_replays() -> None:
    manifest = fixture_manifest()
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    team = TeamRuntime(runtime)
    state = team.start_episode(
        EpisodeDefinition(
            manifest=manifest,
            topology=compile_profile(ArchitectureId.SINGLE),
            protocol_mode=ProtocolMode.TAL,
            model_id="context-plan",
        )
    )
    adapter = ContextPlanAdapter()
    state = asyncio.run(team.run_next_phase(state.run_id, {"actor:single": adapter}))
    assert state.topology is not None
    assert len(state.model_calls) == 1
    assert state.model_calls[0].actor_or_stage_id == "actor:single"
    assert runtime.replay(state.run_id).model_dump(mode="json") == state.model_dump(mode="json")


def test_control_uses_the_same_scheduler_and_authority_gate() -> None:
    manifest = fixture_manifest()
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    team = TeamRuntime(runtime)
    state = team.start_episode(
        EpisodeDefinition(
            manifest=manifest,
            topology=compile_profile(ArchitectureId.SINGLE),
            protocol_mode=ProtocolMode.CONTROL,
            model_id="context-plan",
        )
    )
    state = asyncio.run(team.run_next_phase(state.run_id, {"actor:single": ContextPlanAdapter()}))
    assert state.protocol_mode is ProtocolMode.CONTROL
    assert len(state.model_calls) == 1
    assert runtime.replay(state.run_id).model_dump(mode="json") == state.model_dump(mode="json")


def test_packets_are_delivered_only_to_declared_recipient_context() -> None:
    manifest = fixture_manifest()
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    team = TeamRuntime(runtime)
    state = team.start_episode(
        EpisodeDefinition(
            manifest=manifest,
            topology=compile_profile(ArchitectureId.CENTRALIZED),
            protocol_mode=ProtocolMode.TAL,
            model_id="context-plan",
        )
    )
    state = asyncio.run(
        team.run_next_phase(state.run_id, {"actor:lead": LeadAssignmentAdapter(manifest)})
    )
    worker_a = ContextPlanAdapter()
    worker_b = ContextPlanAdapter()
    worker_c = ContextPlanAdapter()
    state = asyncio.run(
        team.run_next_phase(
            state.run_id,
            {"actor:worker_a": worker_a, "actor:worker_b": worker_b, "actor:worker_c": worker_c},
        )
    )
    assert state.subgoals[0].assigned_actor_id == "actor:worker_a"
    assert "packet:assign_a" in worker_a.contexts[0].rendered_text
    assert "packet:assign_a" not in worker_b.contexts[0].rendered_text
    assert "packet:assign_a" not in worker_c.contexts[0].rendered_text


def test_control_omits_tal_specific_context_segments() -> None:
    manifest = fixture_manifest()
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    team = TeamRuntime(runtime)
    state = team.start_episode(
        EpisodeDefinition(
            manifest=manifest,
            topology=compile_profile(ArchitectureId.SINGLE),
            protocol_mode=ProtocolMode.CONTROL,
            model_id="context-plan",
        )
    )
    adapter = ContextPlanAdapter()
    asyncio.run(team.run_next_phase(state.run_id, {"actor:single": adapter}))
    component_names = {segment.component.value for segment in adapter.contexts[0].segments}
    assert "input.tal_core_grammar" not in component_names
    assert "input.tal_multi_agent_grammar" not in component_names


def test_actor_phase_uses_distinct_bounded_coordination_rounds() -> None:
    manifest = fixture_manifest()
    profile = compile_profile(ArchitectureId.SINGLE)
    first_phase = profile.specification.phases[0].model_copy(
        update={"max_activations_per_entity": 1, "max_rounds": 2}
    )
    specification = profile.specification.model_copy(
        update={"phases": (first_phase, *profile.specification.phases[1:])}
    )
    topology = TopologyCompiler().compile(specification)
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    team = TeamRuntime(runtime)
    state = team.start_episode(
        EpisodeDefinition(
            manifest=manifest,
            topology=topology,
            protocol_mode=ProtocolMode.TAL,
            model_id="context-plan",
        )
    )
    adapter = PersistentContextPlanAdapter()
    state = asyncio.run(team.run_next_phase(state.run_id, {"actor:single": adapter}))
    assert [context.round_index for context in adapter.contexts] == [1, 2]
    assert len(state.model_calls) == 2
    assert state.coordination_round == 2


def test_team_episode_rejects_a_tampered_topology_before_starting() -> None:
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    topology = compile_profile(ArchitectureId.SINGLE).model_copy(
        update={"topology_hash": sha256({"tampered": True})}
    )
    with pytest.raises(ValueError, match="topology hash"):
        TeamRuntime(runtime).start_episode(
            EpisodeDefinition(
                manifest=fixture_manifest(),
                topology=topology,
                protocol_mode=ProtocolMode.TAL,
                model_id="fixture",
            )
        )


def test_team_rejects_an_actor_using_a_different_model() -> None:
    manifest = fixture_manifest()
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry(), ids=SequentialIds())
    team = TeamRuntime(runtime)
    state = team.start_episode(
        EpisodeDefinition(
            manifest=manifest,
            topology=compile_profile(ArchitectureId.SINGLE),
            protocol_mode=ProtocolMode.TAL,
            model_id="frozen-model",
        )
    )
    result = asyncio.run(team.run_next_phase(state.run_id, {"actor:single": ContextPlanAdapter()}))
    assert result.reason_code.value == "TOPOLOGY_INVALID"
