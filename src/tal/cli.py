"""Offline Typer adapter; all semantic work remains in TAL core services."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import TypeAdapter, ValidationError

from .adapters import FakeToolRegistry, Proposal
from .compiler import (
    compile_manifest,
    export_manifest_schema,
    rejection_from_validation,
    render_tal_english,
)
from .coordination import ArchitectureId, CompiledExecutionTopology, TopologyCompiler
from .core import Rejection, canonical_json, sha256
from .invoice import InvoiceFormSubmission, TALManifest
from .profiles import compile_profile, load_agent_scaling_reference
from .research import (
    EpisodeResult,
    ExperimentFreeze,
    compile_experiment_freeze,
    verify_experiment_freeze,
)
from .research_analysis import analyze_confirmatory
from .research_execution import (
    PersistedEpisode,
    ResearchRunStore,
    trace_key_from_environment,
    write_public_release,
)
from .research_packs import (
    load_finance_agent_public_csv,
    load_plancraft_test_snapshot,
    sample_plancraft_stratified,
)
from .research_study import StudySchedule, StudySplit, build_paired_schedule
from .runtime import TALRuntime
from .scenarios import SCENARIO_NAMES, run_scenario
from .storage import SQLiteEventStore
from .telemetry import aggregate_usage, tal_context_share
from .validation import ValidationPipeline

app = typer.Typer(help="Tervoro Agentic Language offline certification harness")
manifest_app = typer.Typer(help="Compile, validate, and render TAL manifests")
run_app = typer.Typer(help="Start, inspect, step, and replay TAL runs")
fixture_app = typer.Typer(help="Run deterministic offline invoice fixtures")
events_app = typer.Typer(help="Export immutable TAL event ledgers")
topology_app = typer.Typer(help="Inspect and compile frozen multi-agent topologies")
telemetry_app = typer.Typer(help="Export v0.3 model-call accounting")
research_app = typer.Typer(help="v0.4 sealed benchmark-study workflow")
app.add_typer(manifest_app, name="manifest")
app.add_typer(run_app, name="run")
app.add_typer(fixture_app, name="fixture")
app.add_typer(events_app, name="events")
app.add_typer(topology_app, name="topology")
app.add_typer(telemetry_app, name="telemetry")
app.add_typer(research_app, name="research")


def _load_form(path: Path) -> InvoiceFormSubmission:
    return InvoiceFormSubmission.model_validate_json(path.read_text(encoding="utf-8"))


def _load_manifest(path: Path) -> TALManifest:
    return TALManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _load_topology(path: Path) -> CompiledExecutionTopology:
    return CompiledExecutionTopology.model_validate_json(path.read_text(encoding="utf-8"))


@app.command("schema")
def schema_export(
    output: Annotated[Path | None, typer.Option("--output")] = None,
    check: Annotated[bool, typer.Option("--check")] = False,
) -> None:
    data = export_manifest_schema()
    if output is None:
        typer.echo(data.decode("utf-8"))
        return
    if check:
        if not output.exists() or output.read_bytes() != data:
            raise typer.Exit(code=1)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)


@app.command("domain")
def domain_list() -> None:
    typer.echo(
        "tal.business.invoice_resolution-0.3.0\ntal.benchmark.finance-agent-0.4.0-rc2\ntal.benchmark.plancraft-0.4.0-rc2"
    )


@research_app.command("bootstrap")
def research_bootstrap(
    output: Annotated[Path, typer.Option("--output")] = Path("research/study-plan.json"),
) -> None:
    """Write the prespecified, non-secret v0.4 study-plan template."""
    payload = {
        "research_version": "0.4.0-rc2",
        "random_seed": 42,
        "main_tasks": {
            "finance_agent": 50,
            "plancraft": 50,
            "plancraft_selection": "recipe-depth quartiles, seed 42",
        },
        "pilot_exclusions": {"finance_agent": 4, "plancraft": 4},
        "budgets_microcents": {
            "pilot": 7_500_000_000,
            "main": 40_000_000_000,
            "reruns": 2_500_000_000,
            "total": 50_000_000_000,
        },
        "protocol": "adjacent randomized TAL/control pairs; one pair worker; no semantic cache",
        "release": "code/config/aggregates/redacted examples public; raw traces controlled",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json(payload))
    typer.echo(str(output))


@research_app.command("freeze")
def research_freeze(
    freeze_file: Path, output: Annotated[Path | None, typer.Option("--output")] = None
) -> None:
    """Validate and seal an experiment freeze after artifact hashes are populated."""
    freeze = compile_experiment_freeze(
        ExperimentFreeze.model_validate_json(freeze_file.read_text(encoding="utf-8"))
    )
    destination = output or freeze_file
    destination.write_bytes(canonical_json(freeze))
    typer.echo(freeze.model_dump_json())


@research_app.command("snapshot")
def research_snapshot(
    finance_csv: Annotated[Path, typer.Option("--finance-csv")],
    plancraft_json: Annotated[Path, typer.Option("--plancraft-json")],
    output: Annotated[Path, typer.Option("--output")] = Path("research/task-ids.json"),
) -> None:
    """Freeze the full 50-task Finance list and seeded 50-task PlanCraft selection."""
    from .research import BenchmarkId

    finance = load_finance_agent_public_csv(finance_csv)
    plancraft = sample_plancraft_stratified(load_plancraft_test_snapshot(plancraft_json))
    rows = [
        *[(BenchmarkId.FINANCE_AGENT.value, task.task_id) for task in finance],
        *[(BenchmarkId.PLANCRAFT.value, task.task_id) for task in plancraft],
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json(rows))
    typer.echo(
        json.dumps(
            {
                "finance_tasks": len(finance),
                "plancraft_tasks": len(plancraft),
                "task_list_hash": sha256(rows),
            },
            sort_keys=True,
        )
    )


@research_app.command("preflight")
def research_preflight(
    finance_csv: Annotated[Path, typer.Option("--finance-csv")],
    plancraft_json: Annotated[Path, typer.Option("--plancraft-json")],
    require_container_tools: Annotated[
        bool, typer.Option("--require-container-tools/--no-require-container-tools")
    ] = True,
) -> None:
    """Fail closed before any paid run if required task assets or runtime tools are missing."""
    issues: list[str] = []
    try:
        finance_count = len(load_finance_agent_public_csv(finance_csv))
    except (OSError, ValueError) as error:
        finance_count = 0
        issues.append(f"Finance-Agent snapshot invalid: {error}")
    try:
        plancraft_count = len(
            sample_plancraft_stratified(load_plancraft_test_snapshot(plancraft_json))
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        plancraft_count = 0
        issues.append(f"PlanCraft snapshot invalid: {error}")
    if require_container_tools:
        for executable in ("docker", "uv"):
            if shutil.which(executable) is None:
                issues.append(f"required executable not found: {executable}")
    payload = {"finance_tasks": finance_count, "plancraft_tasks": plancraft_count, "issues": issues}
    typer.echo(json.dumps(payload, sort_keys=True))
    if issues:
        raise typer.Exit(code=1)


@research_app.command("schedule")
def research_schedule(
    freeze_file: Path,
    tasks_file: Path,
    architecture: Annotated[list[ArchitectureId] | None, typer.Option("--architecture")] = None,
    split: Annotated[StudySplit, typer.Option("--split")] = StudySplit.MAIN,
    output: Annotated[Path, typer.Option("--output")] = Path("research/schedule.json"),
) -> None:
    """Create the fully enumerated paired schedule from sealed task identifiers."""
    freeze = ExperimentFreeze.model_validate_json(freeze_file.read_text(encoding="utf-8"))
    if not verify_experiment_freeze(freeze):
        raise typer.BadParameter("freeze file is not sealed")
    task_rows = TypeAdapter(list[tuple[str, str]]).validate_json(
        tasks_file.read_text(encoding="utf-8")
    )
    from .research import BenchmarkId

    schedule = build_paired_schedule(
        freeze,
        tuple((BenchmarkId(benchmark), task_id) for benchmark, task_id in task_rows),
        tuple(architecture) if architecture is not None else tuple(ArchitectureId),
        split=split,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json(schedule))
    typer.echo(schedule.model_dump_json())


@research_app.command("analyze")
def research_analyze(schedule_file: Path, results_file: Path) -> None:
    """Run the prespecified primary paired analysis over canonical result JSON lines."""
    schedule = StudySchedule.model_validate_json(schedule_file.read_text(encoding="utf-8"))
    results = tuple(
        EpisodeResult.model_validate_json(line)
        for line in results_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    typer.echo(
        json.dumps(
            [item.model_dump(mode="json") for item in analyze_confirmatory(schedule, results)],
            sort_keys=True,
        )
    )


@research_app.command("certify")
def research_certify(freeze_file: Path, schedule_file: Path) -> None:
    """Fail closed unless the freeze and its pair-adjacent schedule agree."""
    freeze = ExperimentFreeze.model_validate_json(freeze_file.read_text(encoding="utf-8"))
    schedule = StudySchedule.model_validate_json(schedule_file.read_text(encoding="utf-8"))
    if not verify_experiment_freeze(freeze) or freeze.freeze_hash != schedule.freeze_hash:
        raise typer.Exit(code=1)
    typer.echo('{"status":"research-freeze-certified"}')


def _research_store(schedule_file: Path, run_dir: Path) -> ResearchRunStore:
    schedule = StudySchedule.model_validate_json(schedule_file.read_text(encoding="utf-8"))
    store = ResearchRunStore(run_dir, trace_key=trace_key_from_environment())
    store.initialize(schedule)
    return store


@research_app.command("run")
def research_run(
    schedule_file: Path,
    run_dir: Annotated[Path, typer.Option("--run-dir")] = Path("research/raw/run"),
    record_file: Annotated[Path | None, typer.Option("--record-file")] = None,
    rerun_budget_microcents: Annotated[
        int, typer.Option("--rerun-budget-microcents")
    ] = 2_500_000_000,
) -> None:
    """Initialize a controlled run directory and append externally executed sealed records."""
    store = _research_store(schedule_file, run_dir)
    if record_file is not None:
        records = tuple(
            PersistedEpisode.model_validate_json(line)
            for line in record_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        for record in records:
            store.append(record, rerun_budget_microcents=rerun_budget_microcents)
    typer.echo(
        json.dumps(
            {
                "pending_episode_ids": [
                    item.episode_id
                    for item in store.load_schedule().episodes
                    if item.episode_id not in {record.episode_id for record in store.load_records()}
                ],
                "records_appended": 0 if record_file is None else len(records),
            },
            sort_keys=True,
        )
    )


@research_app.command("resume")
def research_resume(
    schedule_file: Path,
    run_dir: Annotated[Path, typer.Option("--run-dir")] = Path("research/raw/run"),
) -> None:
    """Report exactly which frozen schedule entries remain after an interrupted run."""
    store = _research_store(schedule_file, run_dir)
    complete = {record.episode_id for record in store.load_records()}
    typer.echo(
        json.dumps(
            {
                "pending_episode_ids": [
                    item.episode_id
                    for item in store.load_schedule().episodes
                    if item.episode_id not in complete
                ]
            },
            sort_keys=True,
        )
    )


@research_app.command("release")
def research_release(
    schedule_file: Path,
    run_dir: Annotated[Path, typer.Option("--run-dir")] = Path("research/raw/run"),
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path("research/release"),
) -> None:
    """Produce the publishable aggregate while retaining traces in the controlled run directory."""
    store = _research_store(schedule_file, run_dir)
    destination = write_public_release(output_dir, store.load_schedule(), store.load_records())
    typer.echo(str(destination))


@app.command("certify")
def certify(
    target: str = typer.Argument("all"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Run the closed, deterministic v0.3 certification suite."""
    if target != "all":
        raise typer.BadParameter("only the complete certification target is supported")
    if not offline:
        raise typer.BadParameter("certification is offline only; pass --offline")
    scenarios = tuple(run_scenario(name) for name in SCENARIO_NAMES)
    topologies = tuple(compile_profile(architecture) for architecture in ArchitectureId)
    typer.echo(
        json.dumps(
            {
                "status": "certified",
                "offline": True,
                "tal_version": "0.3.0",
                "scenarios": len(scenarios),
                "topologies": len(topologies),
                "scenario_outcomes": [item.terminal_outcome for item in scenarios],
            },
            sort_keys=True,
        )
    )


@manifest_app.command("compile")
def manifest_compile(form: Path, output: Path | None = None) -> None:
    try:
        manifest = compile_manifest(_load_form(form))
    except ValidationError as error:
        typer.echo(rejection_from_validation(error).model_dump_json(), err=True)
        raise typer.Exit(code=2) from error
    payload = canonical_json(manifest).decode("utf-8")
    if output is not None:
        output.write_text(payload, encoding="utf-8")
    else:
        typer.echo(payload)


@manifest_app.command("validate")
def manifest_validate(manifest_file: Path) -> None:
    try:
        rejection = ValidationPipeline().validate(_load_manifest(manifest_file))
    except ValidationError as error:
        typer.echo(rejection_from_validation(error).model_dump_json(), err=True)
        raise typer.Exit(code=2) from error
    if rejection is not None:
        typer.echo(rejection.model_dump_json(), err=True)
        raise typer.Exit(code=2)
    typer.echo('{"status":"valid"}')


@manifest_app.command("render")
def manifest_render(manifest_file: Path, format: str = "tal") -> None:
    manifest = _load_manifest(manifest_file)
    if format == "tal":
        typer.echo(render_tal_english(manifest))
    elif format == "json":
        typer.echo(canonical_json(manifest).decode("utf-8"))
    else:
        raise typer.BadParameter("format must be tal or json")


def _sqlite(uri: str) -> SQLiteEventStore:
    prefix = "sqlite:///"
    if not uri.startswith(prefix):
        raise typer.BadParameter("store must be sqlite:///PATH")
    return SQLiteEventStore(uri.removeprefix(prefix))


@run_app.command("start")
def run_start(manifest_file: Path, store: str = "sqlite:///tal.db") -> None:
    runtime = TALRuntime(_sqlite(store), FakeToolRegistry())
    result = runtime.start(_load_manifest(manifest_file))
    if isinstance(result, Rejection):
        typer.echo(result.model_dump_json(), err=True)
        raise typer.Exit(code=2)
    typer.echo(result.model_dump_json())


@run_app.command("inspect")
def run_inspect(run_id: str, store: str = "sqlite:///tal.db") -> None:
    typer.echo(_sqlite(store).load_state(run_id).model_dump_json())


@run_app.command("replay")
def run_replay(run_id: str, store: str = "sqlite:///tal.db") -> None:
    runtime = TALRuntime(_sqlite(store), FakeToolRegistry())
    typer.echo(runtime.replay(run_id).model_dump_json())


@run_app.command("events-export")
def run_events_export(run_id: str, store: str = "sqlite:///tal.db") -> None:
    """Export the append-only ledger as canonical JSON, with no provider secrets."""
    typer.echo(canonical_json(_sqlite(store).load_events(run_id)).decode("utf-8"))


@events_app.command("export")
def events_export(run_id: str, store: str = "sqlite:///tal.db") -> None:
    """Compatibility command for the canonical ledger export operation."""
    run_events_export(run_id, store)


@topology_app.command("list")
def topology_list() -> None:
    bundle = load_agent_scaling_reference()
    typer.echo(
        json.dumps(
            {
                "profile_set": bundle.profile_set_id,
                "source": f"{bundle.source_ref}@{bundle.source_revision}",
                "architectures": [item.architecture.value for item in bundle.profiles],
            }
        )
    )


@topology_app.command("compile")
def topology_compile(
    architecture: ArchitectureId,
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    topology = compile_profile(architecture)
    payload = canonical_json(topology).decode("utf-8")
    if output is None:
        typer.echo(payload)
    else:
        output.write_text(payload, encoding="utf-8")


@topology_app.command("inspect")
def topology_inspect(topology_file: Path) -> None:
    typer.echo(_load_topology(topology_file).model_dump_json())


@topology_app.command("verify")
def topology_verify(topology_file: Path) -> None:
    topology = _load_topology(topology_file)
    if not TopologyCompiler().verify(topology):
        raise typer.Exit(code=1)
    typer.echo('{"status":"valid"}')


@telemetry_app.command("export")
def telemetry_export(run_id: str, store: str = "sqlite:///tal.db") -> None:
    state = _sqlite(store).load_state(run_id)
    typer.echo(canonical_json(state.model_calls).decode("utf-8"))


@telemetry_app.command("summary")
def telemetry_summary(run_id: str, store: str = "sqlite:///tal.db") -> None:
    """Export deterministic aggregate usage and TAL-context share."""
    state = _sqlite(store).load_state(run_id)
    records = state.model_calls
    usage = aggregate_usage(records)
    typer.echo(
        json.dumps(
            {
                "model_calls": len(records),
                "usage": {kind.value: amount for kind, amount in usage.items()},
                "mean_tal_context_share": (
                    sum(tal_context_share(record) for record in records) / len(records)
                    if records
                    else 0.0
                ),
            },
            sort_keys=True,
        )
    )


@run_app.command("step")
def run_step(run_id: str, proposal_file: Path, store: str = "sqlite:///tal.db") -> None:
    """Execute one step using one fixture proposal JSON object."""
    proposal: Proposal = cast(
        Proposal, TypeAdapter(Proposal).validate_json(proposal_file.read_text(encoding="utf-8"))
    )
    runtime = TALRuntime(_sqlite(store), FakeToolRegistry())
    result = runtime.apply_proposal(run_id, proposal)
    typer.echo(result.model_dump_json())


@fixture_app.command("run")
def fixture_run(
    name: str = typer.Argument("valid_approval_package"),
    offline: bool = typer.Option(True, "--offline"),
) -> None:
    if not offline:
        raise typer.BadParameter("TAL v0.3 fixtures are offline only")
    if name == "all":
        typer.echo(
            json.dumps(
                [run_scenario(scenario).model_dump(mode="json") for scenario in SCENARIO_NAMES]
            )
        )
        return
    if name not in SCENARIO_NAMES:
        raise typer.BadParameter("fixture is not registered")
    typer.echo(run_scenario(name).model_dump_json())
