"""Packaged, frozen v0.3 coordination profiles."""

from __future__ import annotations

import json
from importlib import resources

from pydantic import Field

from ..coordination import (
    ArchitectureId,
    CompiledExecutionTopology,
    CoordinationSpec,
    RuntimeInventory,
    TopologyCompiler,
)
from ..core import StrictModel

REFERENCE_PROFILE_ID = "agent_scaling_reference"


class ReferenceProfileBundle(StrictModel):
    profile_set_id: str = REFERENCE_PROFILE_ID
    source_ref: str = Field(min_length=1, max_length=500)
    source_revision: str = Field(min_length=1, max_length=128)
    profiles: tuple[CoordinationSpec, ...] = Field(min_length=5)


def load_agent_scaling_reference() -> ReferenceProfileBundle:
    resource = resources.files(__package__).joinpath("agent_scaling_reference.json")
    return ReferenceProfileBundle.model_validate_json(resource.read_text(encoding="utf-8"))


def get_profile(architecture: ArchitectureId) -> CoordinationSpec:
    bundle = load_agent_scaling_reference()
    for profile in bundle.profiles:
        if profile.architecture is architecture:
            return profile
    raise KeyError(architecture.value)


def compile_profile(
    architecture: ArchitectureId,
    inventory: RuntimeInventory | None = None,
) -> CompiledExecutionTopology:
    bundle = load_agent_scaling_reference()
    profile = next(profile for profile in bundle.profiles if profile.architecture is architecture)
    return TopologyCompiler().compile(
        profile,
        inventory,
        source_ref=f"{bundle.source_ref}@{bundle.source_revision}",
    )


def export_agent_scaling_reference() -> bytes:
    """Return the packaged frozen source, useful for audit exports and tests."""
    resource = resources.files(__package__).joinpath("agent_scaling_reference.json")
    raw = json.loads(resource.read_text(encoding="utf-8"))
    text = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")
