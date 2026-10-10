"""Deterministic manifest validation pipeline with stable TAL rejection codes."""

from __future__ import annotations

from dataclasses import dataclass

from .compiler import verify_manifest_hash
from .core import ErrorCode, Rejection, VerificationMode
from .invoice import TALManifest, TerminalOutcome


@dataclass(frozen=True)
class ValidationPipeline:
    """Validation order is deliberately explicit and independent of model execution."""

    def validate(self, manifest: TALManifest) -> Rejection | None:
        result = self._versions(manifest)
        if result is not None:
            return result
        result = self._references(manifest)
        if result is not None:
            return result
        result = self._semantics(manifest)
        if result is not None:
            return result
        result = self._consistency(manifest)
        if result is not None:
            return result
        return self._authority(manifest)

    @staticmethod
    def _rejection(
        manifest: TALManifest, code: ErrorCode, message: str, path: str | None = None
    ) -> Rejection:
        return Rejection(
            reason_code=code,
            message=message,
            path=path,
            manifest_hash=manifest.integrity.manifest_hash,
            manifest_version=manifest.integrity.manifest_version,
            state_version=0,
            retryable=False,
        )

    def _versions(self, manifest: TALManifest) -> Rejection | None:
        if not verify_manifest_hash(manifest):
            return self._rejection(
                manifest,
                ErrorCode.UNSUPPORTED_VERSION,
                "manifest integrity hash is invalid",
                "/integrity/manifest_hash",
            )
        return None

    def _references(self, manifest: TALManifest) -> Rejection | None:
        entity_refs = {item.ref for item in manifest.scope.entity_declarations}
        for index, target_ref in enumerate(manifest.scope.target_refs):
            if target_ref not in entity_refs:
                return self._rejection(
                    manifest,
                    ErrorCode.UNRESOLVED_REFERENCE,
                    "scope target is not declared",
                    f"/scope/target_refs/{index}",
                )
        for index, unknown in enumerate(manifest.unknowns):
            if unknown.subject_ref not in entity_refs:
                return self._rejection(
                    manifest,
                    ErrorCode.UNRESOLVED_REFERENCE,
                    "unknown subject is not declared",
                    f"/unknowns/{index}/subject_ref",
                )
        for index, requirement in enumerate(manifest.requirements):
            for target_index, target_ref in enumerate(requirement.target_refs):
                if target_ref not in entity_refs:
                    return self._rejection(
                        manifest,
                        ErrorCode.UNRESOLVED_REFERENCE,
                        "requirement target is not declared",
                        f"/requirements/{index}/target_refs/{target_index}",
                    )
        for index, fact in enumerate(manifest.reported_facts):
            if fact.subject_ref not in entity_refs:
                return self._rejection(
                    manifest,
                    ErrorCode.UNRESOLVED_REFERENCE,
                    "fact subject is not declared",
                    f"/reported_facts/{index}/subject_ref",
                )
        for index, capability in enumerate(manifest.requested_capabilities):
            if capability.target_ref not in entity_refs:
                return self._rejection(
                    manifest,
                    ErrorCode.UNRESOLVED_REFERENCE,
                    "capability target is not declared",
                    f"/requested_capabilities/{index}/target_ref",
                )
        for index, prohibition in enumerate(manifest.prohibited_actions):
            if prohibition.target_ref not in entity_refs:
                return self._rejection(
                    manifest,
                    ErrorCode.UNRESOLVED_REFERENCE,
                    "prohibition target is not declared",
                    f"/prohibited_actions/{index}/target_ref",
                )
        return None

    def _semantics(self, manifest: TALManifest) -> Rejection | None:
        for condition in manifest.success_conditions:
            if (
                condition.verification_mode
                in {
                    VerificationMode.TRUSTED_TOOL,
                    VerificationMode.HUMAN_APPROVAL,
                    VerificationMode.EXTERNAL_EVENT,
                }
                and not condition.required_evidence_types
            ):
                return self._rejection(
                    manifest,
                    ErrorCode.UNVERIFIABLE_SUCCESS_CONDITION,
                    "external completion conditions require declared evidence types",
                    "/success_conditions",
                )
            if (
                condition.verification_mode is VerificationMode.MODEL_ADVISORY
                and condition.terminal_outcome
                in {TerminalOutcome.APPROVAL_PACKAGE_DELIVERED, TerminalOutcome.PAYMENT_SETTLED}
            ):
                return self._rejection(
                    manifest,
                    ErrorCode.UNVERIFIABLE_SUCCESS_CONDITION,
                    "model advisory cannot independently complete a high-impact condition",
                    "/success_conditions",
                )
        return None

    def _consistency(self, manifest: TALManifest) -> Rejection | None:
        prohibited = {(item.action_id, item.target_ref) for item in manifest.prohibited_actions}
        requested = {(item.action_id, item.target_ref) for item in manifest.requested_capabilities}
        if not manifest.success_conditions:
            return self._rejection(
                manifest,
                ErrorCode.MISSING_SUCCESS_CONDITION,
                "manifest must declare a success condition",
                "/success_conditions",
            )
        # Requesting a forbidden capability is legitimate; it remains denied at runtime.
        _ = prohibited & requested
        requested_ids = [item.id for item in manifest.requested_capabilities]
        if len(requested_ids) != len(set(requested_ids)):
            return self._rejection(
                manifest,
                ErrorCode.DUPLICATE_IDENTIFIER,
                "capability identifiers must be unique",
                "/requested_capabilities",
            )
        constraint_kinds = [item.kind for item in manifest.constraints]
        if len(constraint_kinds) != len(set(constraint_kinds)):
            return self._rejection(
                manifest,
                ErrorCode.SEMANTIC_CONFLICT,
                "each constraint kind may be declared once",
                "/constraints",
            )
        if (
            manifest.goal.deadline is not None
            and manifest.goal.deadline < manifest.request.submitted_at
        ):
            return self._rejection(
                manifest,
                ErrorCode.SEMANTIC_CONFLICT,
                "deadline precedes submission",
                "/goal/deadline",
            )
        return None

    def _authority(self, manifest: TALManifest) -> Rejection | None:
        if not manifest.request.submitted_by.startswith("user:"):
            return self._rejection(
                manifest,
                ErrorCode.ACTOR_NOT_AUTHORIZED,
                "only authenticated users may submit a TAL request",
                "/request/submitted_by",
            )
        return None
