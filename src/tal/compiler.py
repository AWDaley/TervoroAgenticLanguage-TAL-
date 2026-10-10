"""Model-free deterministic form compiler and TAL English renderer."""

from __future__ import annotations

from datetime import UTC

from pydantic import ValidationError

from .core import ErrorCode, Rejection, canonical_json, sha256
from .invoice import IntegrityMetadata, InvoiceFormSubmission, TALManifest, sort_submission


def rejection_from_validation(error: ValidationError) -> Rejection:
    first = error.errors()[0]
    error_type = str(first["type"])
    code = ErrorCode.UNKNOWN_FIELD if error_type == "extra_forbidden" else ErrorCode.INVALID_TYPE
    if "enum" in error_type or "literal" in error_type:
        code = ErrorCode.INVALID_ENUM
    path = "/" + "/".join(str(item) for item in first["loc"])
    return Rejection(
        reason_code=code,
        message=str(first["msg"]),
        path=path,
        manifest_version=0,
        state_version=0,
        retryable=False,
    )


def compile_manifest(form: InvoiceFormSubmission) -> TALManifest:
    """Compile only typed, validated input; this function has no model dependency."""
    ordered = sort_submission(form)
    normalized_request = ordered.request.model_copy(
        update={"submitted_at": ordered.request.submitted_at.astimezone(UTC)}
    )
    unsigned = TALManifest(
        request=normalized_request,
        scope=ordered.scope,
        goal=ordered.goal,
        reported_facts=ordered.reported_facts,
        unknowns=ordered.unknowns,
        requirements=ordered.requirements,
        constraints=ordered.constraints,
        preferences=ordered.preferences,
        requested_capabilities=ordered.requested_capabilities,
        prohibited_actions=ordered.prohibited_actions,
        success_conditions=ordered.success_conditions,
        fallbacks=ordered.fallbacks,
        output_contract=ordered.output_contract,
        integrity=IntegrityMetadata(),
    )
    preimage = unsigned.model_dump(mode="json", exclude_none=True)
    preimage["integrity"].pop("manifest_hash", None)
    digest = sha256(preimage)
    return unsigned.model_copy(
        update={"integrity": unsigned.integrity.model_copy(update={"manifest_hash": digest})}
    )


def verify_manifest_hash(manifest: TALManifest) -> bool:
    preimage = manifest.model_dump(mode="json", exclude_none=True)
    claimed = preimage["integrity"].pop("manifest_hash", None)
    return isinstance(claimed, str) and claimed == sha256(preimage)


def render_tal_english(manifest: TALManifest) -> str:
    lines = [
        "goal:",
        f"  actor: {manifest.goal.actor_ref}",
        f"  outcome: {manifest.goal.outcome_id} for {manifest.goal.target_ref}",
        f"  priority: {', '.join(manifest.goal.priority_order)}",
        "reported facts:",
    ]
    lines.extend(
        f"  {fact.subject_ref} {fact.field_id.value}: {fact.value}"
        for fact in manifest.reported_facts
    )
    lines.append("requested capabilities:")
    lines.extend(
        f"  {capability.action_id.value} for {capability.target_ref}"
        for capability in manifest.requested_capabilities
    )
    lines.append("prohibited actions:")
    lines.extend(
        f"  {action.action_id.value} for {action.target_ref}"
        for action in manifest.prohibited_actions
    )
    lines.append("done when:")
    lines.extend(
        f"  {condition.terminal_outcome.value} via {condition.condition_id.value}"
        for condition in manifest.success_conditions
    )
    lines.append(f"manifest hash: {manifest.integrity.manifest_hash}")
    return "\n".join(lines)


def export_manifest_schema() -> bytes:
    return canonical_json(TALManifest.model_json_schema())
