"""Authoritative TAL run engine, capability policy, action gate, and replay."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256 as hashlib_sha256
from pathlib import Path
from typing import TYPE_CHECKING, overload

from .adapters import (
    TOOL_CONTRACTS,
    ApprovalProposal,
    CompletionProposal,
    EscalateProposal,
    FailureProposal,
    FakeToolRegistry,
    InferenceProposal,
    InformationProposal,
    ModelAdapter,
    ModelContext,
    PlanProposal,
    Proposal,
    ToolProposal,
)
from .coordination import (
    ActorStatusRecord,
    Claim,
    CompiledExecutionTopology,
    CoordinationPacket,
    PrivateHistoryRecord,
    ProtocolMode,
    Subgoal,
)
from .core import (
    ALLOWED_TRANSITIONS,
    TERMINAL_PHASES,
    ErrorCode,
    Rejection,
    RuntimePhase,
    TrustClass,
    canonical_json,
)
from .core import sha256 as tal_sha256
from .events import (
    ApprovalDecision,
    ApprovalGrant,
    ApprovalRequest,
    BudgetLimit,
    CapabilityDenial,
    CapabilityGrant,
    EffectRecord,
    Event,
    EventKind,
    EvidenceReceipt,
    InformationSubmission,
    Observation,
    RuntimeState,
    event_projects_state,
)
from .invoice import ActionId, ConstraintKind, TALManifest, TerminalOutcome, ToolId
from .office import (
    OfficeAdmissionDecision,
    OfficeAdmissionStatus,
    OfficeRuntimeAction,
    admit_runtime_office_action,
)
from .office_journal import (
    OfficeJournalIntegrityError,
    OfficeJournalKind,
    OfficeJournalProjection,
    OfficeVerifiedEvidence,
    SQLiteOfficeJournal,
)
from .storage import EventStore
from .telemetry import ModelCallRecord
from .validation import ValidationPipeline

if TYPE_CHECKING:
    from .patches import ManifestPatchRequest

Clock = Callable[[], datetime]
IdProvider = Callable[[str], str]


def default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass
class SequentialIds:
    counter: int = 0

    def __call__(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}:{self.counter:06d}"


def compute_capabilities(
    manifest: TALManifest,
) -> tuple[tuple[CapabilityGrant, ...], tuple[CapabilityDenial, ...]]:
    prohibited = {(item.action_id, item.target_ref) for item in manifest.prohibited_actions}
    grants: list[CapabilityGrant] = []
    denials: list[CapabilityDenial] = []
    for requested in manifest.requested_capabilities:
        if (
            requested.action_id is ActionId.PAYMENT_SUBMIT
            or (requested.action_id, requested.target_ref) in prohibited
        ):
            denials.append(
                CapabilityDenial(
                    action_id=requested.action_id.value,
                    target_ref=requested.target_ref,
                    reason_code=ErrorCode.ACTION_PROHIBITED.value,
                )
            )
            continue
        approval_required = (
            requested.action_id is ActionId.PAYMENT_PREPARE
            and requested.amount_limit is not None
            and requested.amount_limit.decimal > 500
        )
        grants.append(
            CapabilityGrant(
                action_id=requested.action_id.value,
                target_ref=requested.target_ref,
                amount_limit=requested.amount_limit.amount if requested.amount_limit else None,
                approval_required=approval_required,
            )
        )
    return tuple(sorted(grants, key=lambda item: (item.action_id, item.target_ref))), tuple(
        sorted(denials, key=lambda item: (item.action_id, item.target_ref))
    )


def manifest_budgets(manifest: TALManifest) -> tuple[BudgetLimit, ...]:
    return tuple(
        BudgetLimit(kind=item.kind.value, maximum=item.maximum) for item in manifest.constraints
    )


@dataclass
class TALRuntime:
    store: EventStore
    tools: FakeToolRegistry
    clock: Clock = default_clock
    ids: IdProvider = field(default_factory=SequentialIds)
    grammar_path: Path = Path(__file__).with_name("grammar_sheet.md")
    # Optional TAL-owned *audit*, never a tool dispatcher or executable permit.
    office_journal: SQLiteOfficeJournal | None = None

    def grammar_hash(self) -> str:
        return "sha256:" + hashlib_sha256(self.grammar_path.read_bytes()).hexdigest()

    def start(self, manifest: TALManifest) -> RuntimeState | Rejection:
        validation = ValidationPipeline().validate(manifest)
        if validation is not None:
            return validation
        grants, denials = compute_capabilities(manifest)
        state = RuntimeState(
            run_id=self.ids("run"),
            manifest=manifest,
            phase=RuntimePhase.CREATED,
            state_version=0,
            effective_capabilities=grants,
            denied_capabilities=denials,
            budgets=manifest_budgets(manifest),
        )
        self.store.create(state)
        state = self._append(
            state,
            EventKind.RUN_CREATED,
            "run genesis recorded",
            manifest=manifest,
            initial_manifest=manifest,
            initial_effective_capabilities=grants,
            initial_denied_capabilities=denials,
            initial_budgets=manifest_budgets(manifest),
        )
        state = self._transition(state, RuntimePhase.VALIDATING, "run validation started")
        state = self._append(
            state,
            EventKind.CAPABILITIES_COMPUTED,
            "effective capabilities computed",
            refs=tuple(item.action_id for item in grants),
            mutates=False,
        )
        for denial in denials:
            state = self._append(
                state,
                EventKind.CAPABILITY_DENIED,
                denial.reason_code,
                refs=(denial.action_id,),
                mutates=False,
            )
        return self._transition(state, RuntimePhase.READY, "run ready")

    def _event(
        self,
        state: RuntimeState,
        event_type: EventKind,
        message: str,
        *,
        refs: tuple[str, ...] = (),
        rejection: Rejection | None = None,
        observation: Observation | None = None,
        evidence_receipt: EvidenceReceipt | None = None,
        manifest: TALManifest | None = None,
        phase: RuntimePhase | None = None,
        budget_kind: str | None = None,
        budget_amount: int | None = None,
        idempotency_key: str | None = None,
        topology: CompiledExecutionTopology | None = None,
        protocol_mode: ProtocolMode | None = None,
        coordination_phase_ref: str | None = None,
        coordination_round: int | None = None,
        actor: str = "tal_harness",
        actor_status: ActorStatusRecord | None = None,
        packet: CoordinationPacket | None = None,
        claim: Claim | None = None,
        subgoal: Subgoal | None = None,
        model_call: ModelCallRecord | None = None,
        private_history: PrivateHistoryRecord | None = None,
        approval_request: ApprovalRequest | None = None,
        approval_decision: ApprovalDecision | None = None,
        approval_grant: ApprovalGrant | None = None,
        effect_record: EffectRecord | None = None,
        initial_manifest: TALManifest | None = None,
        initial_effective_capabilities: tuple[CapabilityGrant, ...] = (),
        initial_denied_capabilities: tuple[CapabilityDenial, ...] = (),
        initial_budgets: tuple[BudgetLimit, ...] = (),
        mutates: bool = True,
    ) -> Event:
        return Event(
            event_id=self.ids("event"),
            event_type=event_type,
            run_id=state.run_id,
            goal_id=state.manifest.goal.goal_id,
            manifest_hash=state.manifest.integrity.manifest_hash or "sha256:" + "0" * 64,
            manifest_version=state.manifest.integrity.manifest_version,
            ledger_sequence=state.ledger_sequence + 1,
            state_version_before=state.state_version,
            state_version_after=state.state_version + int(event_projects_state(event_type)),
            occurred_at=self.clock(),
            actor=actor,
            message=message,
            refs=refs,
            rejection=rejection,
            observation=observation,
            evidence_receipt=evidence_receipt,
            manifest=manifest,
            phase=phase,
            budget_kind=budget_kind,
            budget_amount=budget_amount,
            idempotency_key=idempotency_key,
            topology=topology,
            protocol_mode=protocol_mode,
            coordination_phase_ref=coordination_phase_ref,
            coordination_round=coordination_round,
            actor_id=actor if actor != "tal_harness" else None,
            actor_status=actor_status,
            packet=packet,
            claim=claim,
            subgoal=subgoal,
            model_call=model_call,
            private_history=private_history,
            approval_request=approval_request,
            approval_decision=approval_decision,
            approval_grant=approval_grant,
            effect_record=effect_record,
            initial_manifest=initial_manifest,
            initial_effective_capabilities=initial_effective_capabilities,
            initial_denied_capabilities=initial_denied_capabilities,
            initial_budgets=initial_budgets,
        )

    def _append(
        self,
        state: RuntimeState,
        event_type: EventKind,
        message: str,
        *,
        refs: tuple[str, ...] = (),
        rejection: Rejection | None = None,
        observation: Observation | None = None,
        evidence_receipt: EvidenceReceipt | None = None,
        manifest: TALManifest | None = None,
        phase: RuntimePhase | None = None,
        budget_kind: str | None = None,
        budget_amount: int | None = None,
        idempotency_key: str | None = None,
        topology: CompiledExecutionTopology | None = None,
        protocol_mode: ProtocolMode | None = None,
        coordination_phase_ref: str | None = None,
        coordination_round: int | None = None,
        actor: str = "tal_harness",
        actor_status: ActorStatusRecord | None = None,
        packet: CoordinationPacket | None = None,
        claim: Claim | None = None,
        subgoal: Subgoal | None = None,
        model_call: ModelCallRecord | None = None,
        private_history: PrivateHistoryRecord | None = None,
        approval_request: ApprovalRequest | None = None,
        approval_decision: ApprovalDecision | None = None,
        approval_grant: ApprovalGrant | None = None,
        effect_record: EffectRecord | None = None,
        initial_manifest: TALManifest | None = None,
        initial_effective_capabilities: tuple[CapabilityGrant, ...] = (),
        initial_denied_capabilities: tuple[CapabilityDenial, ...] = (),
        initial_budgets: tuple[BudgetLimit, ...] = (),
        mutates: bool = True,
    ) -> RuntimeState:
        event = self._event(
            state,
            event_type,
            message,
            refs=refs,
            rejection=rejection,
            observation=observation,
            evidence_receipt=evidence_receipt,
            manifest=manifest,
            phase=phase,
            budget_kind=budget_kind,
            budget_amount=budget_amount,
            idempotency_key=idempotency_key,
            topology=topology,
            protocol_mode=protocol_mode,
            coordination_phase_ref=coordination_phase_ref,
            coordination_round=coordination_round,
            actor=actor,
            actor_status=actor_status,
            packet=packet,
            claim=claim,
            subgoal=subgoal,
            model_call=model_call,
            private_history=private_history,
            approval_request=approval_request,
            approval_decision=approval_decision,
            approval_grant=approval_grant,
            effect_record=effect_record,
            initial_manifest=initial_manifest,
            initial_effective_capabilities=initial_effective_capabilities,
            initial_denied_capabilities=initial_denied_capabilities,
            initial_budgets=initial_budgets,
            mutates=mutates,
        )
        return self.store.append(
            state.run_id,
            state.state_version,
            state.ledger_sequence,
            (event,),
        )

    def _transition(self, state: RuntimeState, target: RuntimePhase, message: str) -> RuntimeState:
        if target not in ALLOWED_TRANSITIONS[state.phase]:
            raise ValueError(f"illegal transition {state.phase.value}->{target.value}")
        return self._append(state, EventKind.PHASE_CHANGED, message, phase=target)

    def _reject(
        self,
        state: RuntimeState,
        code: ErrorCode,
        message: str,
        *,
        path: str | None = None,
        retryable: bool = False,
    ) -> tuple[RuntimeState, Rejection]:
        rejection = Rejection(
            reason_code=code,
            message=message,
            path=path,
            manifest_hash=state.manifest.integrity.manifest_hash,
            manifest_version=state.manifest.integrity.manifest_version,
            state_version=state.state_version,
            retryable=retryable,
        )
        next_state = self._append(
            state, EventKind.PROPOSAL_REJECTED, message, rejection=rejection, mutates=False
        )
        return next_state, rejection

    def _budget_remaining(self, state: RuntimeState, kind: str) -> int | None:
        limits = {item.kind: item.maximum for item in state.budgets}
        if kind not in limits:
            return None
        used = {item.kind: item.maximum for item in state.budget_used}
        return limits[kind] - used.get(kind, 0)

    def _consume_budget(
        self, state: RuntimeState, kind: str, amount: int = 1
    ) -> RuntimeState | Rejection:
        remaining = self._budget_remaining(state, kind)
        if remaining is not None and remaining < amount:
            _, rejection = self._reject(
                state, ErrorCode.BUDGET_EXCEEDED, f"{kind} budget is exhausted"
            )
            return rejection
        return self._append(
            state,
            EventKind.BUDGET_CONSUMED,
            f"consumed {amount} {kind}",
            budget_kind=kind,
            budget_amount=amount,
        )

    def build_context(self, state: RuntimeState) -> ModelContext:
        grammar_hash = self.grammar_hash()
        usable_tools = tuple(
            sorted(
                contract.tool_id.value
                for contract in TOOL_CONTRACTS.values()
                if any(
                    grant.action_id == contract.required_capability
                    for grant in state.effective_capabilities
                )
            )
        )
        denied = tuple(item.action_id for item in state.denied_capabilities)
        budget = tuple(
            f"{item.kind}:{self._budget_remaining(state, item.kind)}" for item in state.budgets
        )
        return ModelContext(
            grammar_id="tal-core-0.3.0",
            grammar_hash=grammar_hash,
            manifest_hash=state.manifest.integrity.manifest_hash or "",
            state_version=state.state_version,
            phase=state.phase.value,
            effective_tools=usable_tools,
            denied_actions=denied,
            evidence_refs=tuple(item.id for item in state.observations),
            remaining_budgets=budget,
        )

    def step(self, run_id: str, adapter: ModelAdapter) -> RuntimeState | Rejection:
        state = self.store.load_state(run_id)
        if state.phase is not RuntimePhase.READY:
            return self._reject(
                state,
                ErrorCode.INVALID_STATE_TRANSITION,
                f"model may not act from {state.phase.value}",
            )[1]
        model_budget = self._consume_budget(state, ConstraintKind.MODEL_CALLS.value)
        if isinstance(model_budget, Rejection):
            return model_budget
        state = model_budget
        state = self._append(
            state,
            EventKind.CONTEXT_BUILT,
            "model context built",
            refs=(self.grammar_hash(),),
            mutates=False,
        )
        proposal = adapter.propose(self.build_context(state))
        state = self._append(
            state,
            EventKind.PROPOSAL_RECEIVED,
            proposal.kind.value,
            refs=(proposal.proposal_id,),
            mutates=False,
        )
        return self.apply_proposal(run_id, proposal)

    def _validate_proposal(self, state: RuntimeState, proposal: Proposal) -> Rejection | None:
        if proposal.goal_id != state.manifest.goal.goal_id:
            return Rejection(
                reason_code=ErrorCode.UNRESOLVED_REFERENCE,
                message="proposal goal does not match run",
                manifest_hash=state.manifest.integrity.manifest_hash,
                manifest_version=state.manifest.integrity.manifest_version,
                state_version=state.state_version,
                retryable=False,
            )
        if (
            proposal.manifest_hash != state.manifest.integrity.manifest_hash
            or proposal.manifest_version != state.manifest.integrity.manifest_version
        ):
            return Rejection(
                reason_code=ErrorCode.STALE_STATE_VERSION,
                message="proposal manifest is stale",
                manifest_hash=state.manifest.integrity.manifest_hash,
                manifest_version=state.manifest.integrity.manifest_version,
                state_version=state.state_version,
                retryable=True,
            )
        if proposal.state_version != state.state_version:
            return Rejection(
                reason_code=ErrorCode.STALE_STATE_VERSION,
                message="proposal state is stale",
                manifest_hash=state.manifest.integrity.manifest_hash,
                manifest_version=state.manifest.integrity.manifest_version,
                state_version=state.state_version,
                retryable=True,
            )
        return None

    @overload
    def apply_proposal(
        self, run_id: str, proposal: OfficeRuntimeAction, actor_id: str | None = None
    ) -> OfficeAdmissionDecision: ...

    @overload
    def apply_proposal(
        self, run_id: str, proposal: Proposal, actor_id: str | None = None
    ) -> RuntimeState | Rejection: ...

    def apply_proposal(
        self, run_id: str, proposal: Proposal | OfficeRuntimeAction, actor_id: str | None = None
    ) -> RuntimeState | Rejection | OfficeAdmissionDecision:
        """Single typed entrypoint. Office admission has no effect or execution grant.

        Trusted office evidence must come from the external durable task store.
        The existing invoice branch retains its authoritative event-ledger behavior.
        """
        if isinstance(proposal, OfficeRuntimeAction):
            now = self.clock()
            decision = admit_runtime_office_action(
                proposal, run_id=run_id, actor_id=actor_id, now=now
            )
            if (self.office_journal is not None
                    and proposal.outbox_action_hash is not None):
                try:
                    self.office_journal.record_admission(
                        proposal, decision, run_id=run_id,
                        actor_id=actor_id or "", now=now,
                    )
                except OfficeJournalIntegrityError:
                    # An inaccessible/changed journal can never authorize dispatch.
                    return OfficeAdmissionDecision(
                        status=OfficeAdmissionStatus.DENIED,
                        reason_code="OFFICE_JOURNAL_INTEGRITY_FAILURE",
                        grant_hash=proposal.grant.grant_hash,
                        proposal_hash=decision.proposal_hash,
                        state_version=decision.state_version,
                    )
            return decision
        state = self.store.load_state(run_id)
        if state.phase is not RuntimePhase.READY:
            return self._reject(
                state,
                ErrorCode.INVALID_STATE_TRANSITION,
                f"model may not act from {state.phase.value}",
            )[1]
        return self._action_gate(state, proposal, actor_id=actor_id)

    def record_office_outcome(
        self, *, action_hash: str, kind: OfficeJournalKind,
        reason_code: str, evidence: OfficeVerifiedEvidence | None = None,
        now: datetime | None = None,
    ) -> OfficeJournalProjection:
        """Trusted verifier callback; no tool calls and no AI-created receipt."""
        if self.office_journal is None:
            raise OfficeJournalIntegrityError("office journal is not configured")
        return self.office_journal.record_outcome(
            action_hash=action_hash, kind=kind, reason_code=reason_code,
            evidence=evidence, now=now or self.clock(),
        )

    def _action_gate(
        self, state: RuntimeState, proposal: Proposal, *, actor_id: str | None = None
    ) -> RuntimeState | Rejection:
        """Internal authority gate for a state loaded by the runtime itself."""
        if state.phase is not RuntimePhase.READY:
            return self._reject(
                state,
                ErrorCode.INVALID_STATE_TRANSITION,
                f"model may not act from {state.phase.value}",
            )[1]
        invalid = self._validate_proposal(state, proposal)
        if invalid is not None:
            return (
                self._append(
                    state,
                    EventKind.PROPOSAL_REJECTED,
                    invalid.message,
                    rejection=invalid,
                    mutates=False,
                )
                and invalid
            )
        if isinstance(proposal, PlanProposal):
            state = self._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                proposal.kind.value,
                refs=(proposal.proposal_id,),
                actor=actor_id or "tal_harness",
                mutates=False,
            )
            return state
        if isinstance(proposal, InformationProposal):
            state = self._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                proposal.kind.value,
                refs=(proposal.proposal_id,),
            )
            state = self._transition(state, RuntimePhase.ACTING, "information request accepted")
            return self._transition(
                state, RuntimePhase.WAITING_FOR_INFORMATION, "waiting for information"
            )
        if isinstance(proposal, ApprovalProposal):
            state = self._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                proposal.kind.value,
                refs=(proposal.proposal_id,),
            )
            state = self._transition(state, RuntimePhase.ACTING, "approval request accepted")
            request = ApprovalRequest(
                request_id=proposal.proposal_id,
                action_id=proposal.action_id,
                target_ref=proposal.target_ref,
                manifest_hash=state.manifest.integrity.manifest_hash or "sha256:" + "0" * 64,
                manifest_version=state.manifest.integrity.manifest_version,
                requested_by="model",
                approver_role=proposal.approver_role,
                evidence_refs=proposal.evidence_refs,
                requested_at=self.clock(),
            )
            state = self._append(
                state,
                EventKind.APPROVAL_REQUESTED,
                proposal.action_id,
                refs=(proposal.target_ref,),
                approval_request=request,
            )
            return self._transition(
                state, RuntimePhase.WAITING_FOR_APPROVAL, "waiting for approval"
            )
        if isinstance(proposal, InferenceProposal):
            known = {item.id for item in state.observations}
            if not set(proposal.evidence_refs) <= known:
                return self._reject(
                    state, ErrorCode.UNRESOLVED_REFERENCE, "inference cites unknown evidence"
                )[1]
            return self._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                proposal.kind.value,
                refs=(proposal.proposal_id,),
                mutates=False,
            )
        if isinstance(proposal, CompletionProposal):
            return self._complete(state, proposal)
        if isinstance(proposal, EscalateProposal):
            fallback = next(
                (item for item in state.manifest.fallbacks if item.id == proposal.fallback_ref),
                None,
            )
            if fallback is None:
                return self._reject(state, ErrorCode.UNRESOLVED_REFERENCE, "unknown fallback")[1]
            state = self._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                proposal.kind.value,
                refs=(proposal.proposal_id,),
            )
            return self._terminal(state, RuntimePhase.ESCALATED, fallback.terminal_outcome.value)
        if isinstance(proposal, FailureProposal):
            state = self._append(
                state,
                EventKind.PROPOSAL_ACCEPTED,
                proposal.kind.value,
                refs=(proposal.proposal_id,),
            )
            return self._terminal(state, RuntimePhase.FAILED, proposal.failure_code)
        if isinstance(proposal, ToolProposal):
            return self._execute_tool(state, proposal)
        return self._reject(state, ErrorCode.INVALID_TYPE, "unregistered proposal")[1]

    def _execute_tool(
        self, state: RuntimeState, proposal: ToolProposal
    ) -> RuntimeState | Rejection:
        contract = TOOL_CONTRACTS.get(proposal.tool_id)
        if contract is None:
            return self._reject(state, ErrorCode.TOOL_NOT_REGISTERED, "tool is not registered")[1]
        if proposal.tool_version != "1.0.0":
            return self._reject(
                state, ErrorCode.UNSUPPORTED_VERSION, "tool version is unsupported"
            )[1]
        grant = next(
            (
                item
                for item in state.effective_capabilities
                if item.action_id == contract.required_capability
                and item.target_ref
                == (
                    proposal.arguments.invoice_ref
                    or proposal.arguments.vendor_ref
                    or proposal.arguments.contract_ref
                )
            ),
            None,
        )
        if grant is None:
            return self._reject(
                state, ErrorCode.CAPABILITY_NOT_GRANTED, "effective capability is missing"
            )[1]
        if grant.approval_required and not any(
            item.action_id == contract.required_capability
            and item.target_ref == grant.target_ref
            and item.manifest_hash == state.manifest.integrity.manifest_hash
            and item.manifest_version == state.manifest.integrity.manifest_version
            for item in state.approval_grants
        ):
            return self._reject(
                state,
                ErrorCode.APPROVAL_REQUIRED,
                "current action requires approval",
                retryable=True,
            )[1]
        expected = contract.observation_type
        if proposal.expected_observation_type != expected:
            return self._reject(
                state,
                ErrorCode.TOOL_ARGUMENT_INVALID,
                "expected observation type does not match tool",
            )[1]
        argument_error = self._validate_tool_arguments(state, proposal)
        if argument_error is not None:
            return self._reject(state, ErrorCode.TOOL_ARGUMENT_INVALID, argument_error)[1]
        key: str | None = None
        if contract.idempotency_required:
            preimage = {
                "goal_id": state.manifest.goal.goal_id,
                "manifest_hash": state.manifest.integrity.manifest_hash,
                "semantic_action": contract.required_capability,
                "target": proposal.arguments.invoice_ref or proposal.arguments.vendor_ref,
                "arguments": proposal.arguments.model_dump(mode="json", exclude_none=True),
            }
            key = "sha256:" + hashlib_sha256(canonical_json(preimage)).hexdigest()
            existing = next(
                (item for item in state.effect_records if item.idempotency_key == key), None
            )
            if existing is not None and existing.status in {"succeeded", "reconciled"}:
                return self._append(
                    state,
                    EventKind.PROPOSAL_ACCEPTED,
                    "duplicate idempotent effect reused",
                    refs=(proposal.proposal_id, existing.idempotency_key),
                )
        budget = self._consume_budget(state, ConstraintKind.TOOL_CALLS.value)
        if isinstance(budget, Rejection):
            return budget
        state = budget
        if contract.effect_class.value != "read":
            external_budget = self._consume_budget(state, ConstraintKind.EXTERNAL_ACTIONS.value)
            if isinstance(external_budget, Rejection):
                return external_budget
            state = external_budget
        effect = (
            EffectRecord(
                idempotency_key=key,
                tool_id=contract.tool_id.value,
                target_ref=(
                    proposal.arguments.invoice_ref
                    or proposal.arguments.vendor_ref
                    or "invoice:unknown"
                ),
                arguments_hash=tal_sha256(proposal.arguments),
                status="intended",
            )
            if key is not None
            else None
        )
        state = self._append(
            state, EventKind.PROPOSAL_ACCEPTED, proposal.kind.value, refs=(proposal.proposal_id,)
        )
        state = self._transition(state, RuntimePhase.ACTING, "tool proposal accepted")
        state = self._append(
            state,
            EventKind.EFFECT_INTENT_RECORDED,
            contract.tool_id.value,
            refs=(proposal.proposal_id,),
            idempotency_key=key,
            effect_record=effect,
        )
        state = self._append(
            state,
            EventKind.TOOL_STARTED,
            contract.tool_id.value,
            refs=(proposal.proposal_id,),
            idempotency_key=key,
        )
        state = self._transition(state, RuntimePhase.WAITING_FOR_TOOL, "tool execution pending")
        result = self.tools.execute(contract.tool_id, proposal.arguments, key)
        if result.outcome == "timeout_after_acceptance":
            uncertain = effect.model_copy(update={"status": "uncertain"}) if effect else None
            state = self._append(
                state,
                EventKind.TOOL_TIMED_OUT,
                "provider outcome uncertain",
                idempotency_key=key,
                effect_record=uncertain,
            )
            return self.reconcile(
                state.run_id,
                contract.tool_id,
                key or "",
                proposal.arguments.invoice_ref
                or proposal.arguments.vendor_ref
                or "invoice:unknown",
            )
        if result.outcome == "timeout_before_effect":
            failed = effect.model_copy(update={"status": "failed"}) if effect else None
            state = self._append(
                state,
                EventKind.TOOL_TIMED_OUT,
                "tool did not start",
                idempotency_key=key,
                effect_record=failed,
            )
            return self._transition(state, RuntimePhase.READY, "retry may be proposed")
        if result.outcome != "success":
            failed = effect.model_copy(update={"status": "failed"}) if effect else None
            state = self._append(
                state,
                EventKind.TOOL_FAILED,
                result.detail or "tool failed",
                idempotency_key=key,
                effect_record=failed,
            )
            return self._terminal(state, RuntimePhase.FAILED, result.status)
        if result.observation_type != contract.observation_type:
            failed = effect.model_copy(update={"status": "failed"}) if effect else None
            state = self._append(
                state,
                EventKind.TOOL_FAILED,
                "tool returned an invalid observation type",
                idempotency_key=key,
                effect_record=failed,
            )
            return self._terminal(state, RuntimePhase.FAILED, ErrorCode.TOOL_OUTPUT_INVALID.value)
        observation = Observation(
            id=self.ids("obs"),
            observation_type=result.observation_type,
            subject_ref=result.subject_ref,
            status=result.status,
            trust=result.trust,
            detail=result.detail,
            observed_at=self.clock(),
        )
        state = self._append(
            state,
            EventKind.TOOL_SUCCEEDED,
            result.status,
            observation=observation,
            idempotency_key=key,
            effect_record=effect.model_copy(update={"status": "succeeded"}) if effect else None,
        )
        state = self._append(
            state,
            EventKind.OBSERVATION_RECORDED,
            result.status,
            observation=observation,
            idempotency_key=key,
        )
        return self._transition(state, RuntimePhase.READY, "tool result committed")

    def _validate_tool_arguments(self, state: RuntimeState, proposal: ToolProposal) -> str | None:
        """Closed domain argument checks performed before any effect is recorded."""
        arguments = proposal.arguments
        target = arguments.invoice_ref or arguments.vendor_ref or arguments.contract_ref
        if target is None:
            return "a registered target reference is required"
        declared = {item.ref for item in state.manifest.scope.entity_declarations}
        if target not in declared:
            return "tool target is outside the declared scope"
        if (
            arguments.invoice_ref is not None
            and arguments.invoice_ref != state.manifest.goal.target_ref
        ):
            return "tool invoice target must match the goal target"
        if (
            proposal.tool_id in {ToolId.MESSAGE_SEND, ToolId.MESSAGE_DRAFT}
            and not arguments.invoice_ref
        ):
            return "message tools require invoice_ref"
        return None

    def reconcile(
        self, run_id: str, tool_id: ToolId, idempotency_key: str, subject_ref: str
    ) -> RuntimeState:
        state = self.store.load_state(run_id)
        prior_effect = next(
            (item for item in state.effect_records if item.idempotency_key == idempotency_key),
            None,
        )
        if prior_effect is None:
            raise ValueError("cannot reconcile an unknown effect")
        observation = self.tools.reconcile(tool_id, idempotency_key, subject_ref, self.clock())
        state = self._append(
            state,
            EventKind.EFFECT_RECONCILED,
            "uncertain outcome reconciled",
            observation=observation,
            idempotency_key=idempotency_key,
            effect_record=prior_effect.model_copy(update={"status": "reconciled"}),
        )
        state = self._append(
            state,
            EventKind.OBSERVATION_RECORDED,
            observation.status,
            observation=observation,
            idempotency_key=idempotency_key,
        )
        return self._transition(state, RuntimePhase.READY, "reconciliation complete")

    def decide_approval(self, run_id: str, decision: ApprovalDecision) -> RuntimeState:
        """Commit a typed, authenticated approval response to one pending request."""
        state = self.store.load_state(run_id)
        if state.phase is not RuntimePhase.WAITING_FOR_APPROVAL:
            raise ValueError("no approval is pending")
        request = next(
            (
                item
                for item in state.pending_approval_requests
                if item.request_id == decision.request_id
            ),
            None,
        )
        if request is None:
            raise ValueError("approval decision does not match a pending request")
        if decision.approver_role != request.approver_role:
            raise ValueError("approval decision role does not match request")
        if decision.decision == "granted":
            grant = ApprovalGrant(
                grant_id=self.ids("approval_grant"),
                request_id=request.request_id,
                action_id=request.action_id,
                target_ref=request.target_ref,
                manifest_hash=request.manifest_hash,
                manifest_version=request.manifest_version,
                approver_id=decision.approver_id,
                granted_at=decision.decided_at,
            )
            state = self._append(
                state,
                EventKind.APPROVAL_GRANTED,
                "approval granted",
                refs=(decision.decision_id,),
                approval_decision=decision,
                approval_grant=grant,
            )
        else:
            state = self._append(
                state,
                EventKind.APPROVAL_DENIED,
                decision.reason or "approval denied",
                refs=(decision.decision_id,),
                approval_decision=decision,
            )
            state = self._append(
                state,
                EventKind.OBSERVATION_RECORDED,
                decision.reason or "approval denied",
                observation=Observation(
                    id=self.ids("obs"),
                    observation_type="approval_decision",
                    subject_ref=request.target_ref,
                    status="approval_denied",
                    trust=TrustClass.AUTHENTICATED_HUMAN,
                    detail=decision.reason,
                    evidence_refs=(decision.decision_id,),
                    observed_at=decision.decided_at,
                ),
            )
        return self._transition(state, RuntimePhase.READY, "approval resolved")

    def patch(self, run_id: str, request: "ManifestPatchRequest") -> RuntimeState | Rejection:
        """Apply an authorized typed patch without overwriting historical manifest state."""
        from .patches import apply_patch

        state = self.store.load_state(run_id)
        if state.phase in TERMINAL_PHASES:
            return self._reject(
                state, ErrorCode.INVALID_STATE_TRANSITION, "terminal run cannot be patched"
            )[1]
        patched = apply_patch(state.manifest, request)
        if isinstance(patched, Rejection):
            return (
                self._append(
                    state,
                    EventKind.PROPOSAL_REJECTED,
                    patched.message,
                    rejection=patched,
                    mutates=False,
                )
                and patched
            )
        validation = ValidationPipeline().validate(patched)
        if validation is not None:
            return (
                self._append(
                    state,
                    EventKind.PROPOSAL_REJECTED,
                    validation.message,
                    rejection=validation,
                    mutates=False,
                )
                and validation
            )
        return self._append(
            state,
            EventKind.MANIFEST_PATCHED,
            "manifest patch accepted; approvals invalidated",
            refs=(patched.integrity.manifest_hash or "",),
            manifest=patched,
        )

    def submit_information(self, run_id: str, submission: InformationSubmission) -> RuntimeState:
        """Record a typed source submission without accepting caller-supplied trust."""
        state = self.store.load_state(run_id)
        if state.phase is not RuntimePhase.WAITING_FOR_INFORMATION:
            raise ValueError("information is not pending")
        observation = Observation(
            id=self.ids("obs"),
            observation_type=submission.observation_type,
            subject_ref=submission.subject_ref,
            status=submission.status,
            trust=TrustClass.REPORTED_SOURCE,
            detail=submission.detail,
            evidence_refs=(submission.receipt.receipt_id,),
            observed_at=submission.receipt.received_at,
        )
        state = self._append(
            state,
            EventKind.OBSERVATION_RECORDED,
            observation.status,
            observation=observation,
            evidence_receipt=submission.receipt,
        )
        return self._transition(state, RuntimePhase.READY, "information resolved")

    def _complete(
        self, state: RuntimeState, proposal: CompletionProposal
    ) -> RuntimeState | Rejection:
        condition = next(
            (
                item
                for item in state.manifest.success_conditions
                if item.id == proposal.success_condition_ref
            ),
            None,
        )
        if condition is None:
            return self._reject(
                state, ErrorCode.UNRESOLVED_REFERENCE, "success condition does not exist"
            )[1]
        evidence = {item.id: item for item in state.observations}
        if not set(proposal.evidence_refs) <= set(evidence):
            return self._reject(
                state,
                ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT,
                "completion cites missing evidence",
                retryable=True,
            )[1]
        selected = tuple(evidence[item_id] for item_id in proposal.evidence_refs)
        evidence_types = {item.observation_type for item in selected}
        if not set(condition.required_evidence_types) <= evidence_types:
            return self._reject(
                state,
                ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT,
                "completion lacks declared evidence types",
                retryable=True,
            )[1]
        if any(item.observed_at < self.clock() - timedelta(days=1) for item in selected):
            return self._reject(
                state,
                ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT,
                "completion evidence is stale",
                retryable=True,
            )[1]
        if condition.verification_mode.value in {"trusted_tool", "external_event"} and any(
            item.trust not in {TrustClass.TRUSTED_REGISTRY, TrustClass.AUTHORITATIVE_EXTERNAL}
            for item in selected
        ):
            return self._reject(
                state,
                ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT,
                "completion evidence lacks required trust",
                retryable=True,
            )[1]
        if (
            condition.verification_mode.value == "model_advisory"
            and condition.terminal_outcome is TerminalOutcome.PAYMENT_SETTLED
        ):
            return self._reject(
                state,
                ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT,
                "model advisory cannot settle payment",
            )[1]
        statuses = {item.status for item in selected}
        if (
            condition.terminal_outcome is TerminalOutcome.APPROVAL_PACKAGE_DELIVERED
            and "delivered" not in statuses
        ):
            return self._reject(
                state,
                ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT,
                "delivery evidence is required",
                retryable=True,
            )[1]
        if (
            condition.terminal_outcome is TerminalOutcome.INVOICE_REJECTED
            and not statuses.intersection(
                {
                    "unknown_vendor",
                    "inactive",
                    "duplicate",
                    "approval_denied",
                    "rejected",
                    "contract_mismatch",
                    "bank_conflict",
                    "missing_purchase_order",
                }
            )
        ):
            return self._reject(
                state,
                ErrorCode.COMPLETION_EVIDENCE_INSUFFICIENT,
                "recorded rejection reason is required",
                retryable=True,
            )[1]
        state = self._append(
            state, EventKind.PROPOSAL_ACCEPTED, proposal.kind.value, refs=(proposal.proposal_id,)
        )
        state = self._transition(state, RuntimePhase.VERIFYING, "completion verification started")
        state = self._append(
            state, EventKind.COMPLETION_VERIFIED, condition.id, refs=proposal.evidence_refs
        )
        return self._terminal(state, RuntimePhase.COMPLETED, condition.terminal_outcome.value)

    def _terminal(self, state: RuntimeState, phase: RuntimePhase, message: str) -> RuntimeState:
        if phase not in ALLOWED_TRANSITIONS[state.phase]:
            raise ValueError(f"illegal terminal transition {state.phase.value}->{phase.value}")
        kind = {
            RuntimePhase.COMPLETED: EventKind.GOAL_COMPLETED,
            RuntimePhase.ESCALATED: EventKind.GOAL_ESCALATED,
            RuntimePhase.CANCELLED: EventKind.GOAL_CANCELLED,
            RuntimePhase.FAILED: EventKind.GOAL_FAILED,
        }[phase]
        return self._append(state, kind, message, phase=phase)

    def replay(self, run_id: str) -> RuntimeState:
        events = self.store.load_events(run_id)
        if not events or events[0].event_type is not EventKind.RUN_CREATED:
            raise AssertionError("ledger has no run genesis event")
        genesis = events[0]
        if genesis.initial_manifest is None:
            raise AssertionError("run genesis is missing its manifest")
        initial = RuntimeState(
            run_id=genesis.run_id,
            manifest=genesis.initial_manifest,
            phase=RuntimePhase.CREATED,
            state_version=0,
            effective_capabilities=genesis.initial_effective_capabilities,
            denied_capabilities=genesis.initial_denied_capabilities,
            budgets=genesis.initial_budgets,
        )
        from .events import reduce_event

        replayed = initial
        for event in events:
            replayed = reduce_event(replayed, event)
        state = self.store.load_state(run_id)
        if replayed.model_dump(mode="json") != state.model_dump(mode="json"):
            raise AssertionError("canonical replay state differs from stored state")
        return replayed
