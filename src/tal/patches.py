"""Closed, authorized manifest patches that always create a new manifest version."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .compiler import compile_manifest
from .core import ErrorCode, Rejection, StrictModel
from .invoice import InvoiceFormSubmission, ReportedFact, TALManifest


class ReplaceDeadline(StrictModel):
    op: Literal["replace_deadline"] = "replace_deadline"
    deadline: datetime


class AddReportedFact(StrictModel):
    op: Literal["add_reported_fact"] = "add_reported_fact"
    fact: ReportedFact


class RemoveReportedFact(StrictModel):
    op: Literal["remove_reported_fact"] = "remove_reported_fact"
    fact_id: str


PatchOperation = ReplaceDeadline | AddReportedFact | RemoveReportedFact


class ManifestPatchRequest(StrictModel):
    base_manifest_hash: str
    requested_by: str
    reason: str = Field(min_length=1, max_length=500)
    operations: tuple[PatchOperation, ...] = Field(min_length=1)


def apply_patch(manifest: TALManifest, request: ManifestPatchRequest) -> TALManifest | Rejection:
    if request.base_manifest_hash != manifest.integrity.manifest_hash:
        return Rejection(
            reason_code=ErrorCode.STALE_STATE_VERSION,
            message="patch base hash is stale",
            manifest_hash=manifest.integrity.manifest_hash,
            manifest_version=manifest.integrity.manifest_version,
            state_version=0,
            retryable=True,
        )
    if request.requested_by != manifest.request.submitted_by:
        return Rejection(
            reason_code=ErrorCode.ACTOR_NOT_AUTHORIZED,
            message="patch requester is not manifest owner",
            manifest_hash=manifest.integrity.manifest_hash,
            manifest_version=manifest.integrity.manifest_version,
            state_version=0,
            retryable=False,
        )
    goal = manifest.goal
    facts = list(manifest.reported_facts)
    for operation in request.operations:
        if isinstance(operation, ReplaceDeadline):
            goal = goal.model_copy(update={"deadline": operation.deadline})
        elif isinstance(operation, AddReportedFact):
            if any(item.id == operation.fact.id for item in facts):
                return Rejection(
                    reason_code=ErrorCode.DUPLICATE_IDENTIFIER,
                    message="patched fact ID already exists",
                    manifest_hash=manifest.integrity.manifest_hash,
                    manifest_version=manifest.integrity.manifest_version,
                    state_version=0,
                    retryable=False,
                )
            facts.append(operation.fact)
        elif isinstance(operation, RemoveReportedFact):
            facts = [item for item in facts if item.id != operation.fact_id]
    form = InvoiceFormSubmission(
        request=manifest.request,
        scope=manifest.scope,
        goal=goal,
        reported_facts=tuple(facts),
        unknowns=manifest.unknowns,
        requirements=manifest.requirements,
        constraints=manifest.constraints,
        preferences=manifest.preferences,
        requested_capabilities=manifest.requested_capabilities,
        prohibited_actions=manifest.prohibited_actions,
        success_conditions=manifest.success_conditions,
        fallbacks=manifest.fallbacks,
        output_contract=manifest.output_contract,
    )
    compiled = compile_manifest(form)
    integrity = compiled.integrity.model_copy(
        update={
            "manifest_version": manifest.integrity.manifest_version + 1,
            "parent_manifest_hash": manifest.integrity.manifest_hash,
            "manifest_hash": None,
        }
    )
    prehashed = compiled.model_copy(update={"integrity": integrity})
    # Compile through the canonical compiler once more with the new version-bearing integrity.
    preimage = prehashed.model_dump(mode="json", exclude_none=True)
    from .core import sha256

    digest = sha256(preimage)
    return prehashed.model_copy(
        update={"integrity": integrity.model_copy(update={"manifest_hash": digest})}
    )
