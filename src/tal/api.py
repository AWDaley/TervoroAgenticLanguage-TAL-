"""Optional thin FastAPI adapter over compiler and validation services."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .adapters import FakeToolRegistry
from .compiler import compile_manifest, render_tal_english
from .coordination import ArchitectureId, CompiledExecutionTopology, TopologyCompiler
from .core import StrictModel
from .events import Event, RuntimeState
from .invoice import InvoiceFormSubmission, TALManifest
from .profiles import compile_profile, load_agent_scaling_reference
from .runtime import TALRuntime
from .storage import InMemoryEventStore
from .telemetry import ModelCallRecord
from .validation import ValidationPipeline


class ValidationResponse(StrictModel):
    status: str = "valid"


class RenderResponse(StrictModel):
    tal_english: str


def create_app() -> FastAPI:
    app = FastAPI(title="Tervoro Agentic Language", version="0.3.0")
    runtime = TALRuntime(InMemoryEventStore(), FakeToolRegistry())

    @app.get("/", response_class=HTMLResponse)
    def local_form() -> str:
        return """<!doctype html><title>TAL invoice form</title>
<h1>Tervoro Agentic Language</h1>
<p>Submit a closed invoice form as JSON to preview canonical TAL.</p>
<textarea id=payload rows=20 cols=100 placeholder='InvoiceFormSubmission JSON'></textarea>
<button onclick='preview()'>Preview</button><pre id=out></pre>
<script>
async function preview() {
  let r = await fetch('/v1/manifests:compile', {
    method: 'POST', headers: {'content-type':'application/json'}, body: payload.value
  });
  out.textContent = await r.text();
}
</script>"""

    @app.get("/v1/domains")
    def domains() -> list[str]:
        return ["tal.business.invoice_resolution-0.3.0"]

    @app.get("/v1/topologies")
    def topologies() -> dict[str, object]:
        bundle = load_agent_scaling_reference()
        return {
            "profile_set": bundle.profile_set_id,
            "source_ref": bundle.source_ref,
            "source_revision": bundle.source_revision,
            "architectures": [item.architecture.value for item in bundle.profiles],
        }

    @app.get("/v1/topologies/{architecture}")
    def compiled_topology(architecture: ArchitectureId) -> CompiledExecutionTopology:
        return compile_profile(architecture)

    @app.post("/v1/topologies:verify")
    def verify_topology(topology: CompiledExecutionTopology) -> ValidationResponse:
        if not TopologyCompiler().verify(topology):
            raise HTTPException(status_code=422, detail="compiled topology hash is invalid")
        return ValidationResponse()

    @app.post("/v1/manifests:compile")
    def compile_endpoint(form: InvoiceFormSubmission) -> TALManifest:
        manifest = compile_manifest(form)
        return manifest

    @app.post("/v1/manifests:validate")
    def validate_endpoint(manifest: TALManifest) -> ValidationResponse:
        rejection = ValidationPipeline().validate(manifest)
        if rejection is not None:
            raise HTTPException(status_code=422, detail=rejection.model_dump(mode="json"))
        return ValidationResponse()

    @app.post("/v1/manifests:render")
    def render_endpoint(form: InvoiceFormSubmission) -> RenderResponse:
        return RenderResponse(tal_english=render_tal_english(compile_manifest(form)))

    @app.post("/v1/runs")
    def start_run(manifest: TALManifest) -> RuntimeState:
        result = runtime.start(manifest)
        if not isinstance(result, RuntimeState):
            raise HTTPException(status_code=422, detail=result.model_dump(mode="json"))
        return result

    @app.get("/v1/runs/{run_id}")
    def inspect_run(run_id: str) -> RuntimeState:
        try:
            return runtime.store.load_state(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown run") from error

    @app.get("/v1/runs/{run_id}/events")
    def run_events(run_id: str) -> tuple[Event, ...]:
        try:
            return runtime.store.load_events(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown run") from error

    @app.get("/v1/runs/{run_id}/telemetry")
    def run_telemetry(run_id: str) -> tuple[ModelCallRecord, ...]:
        try:
            return runtime.store.load_state(run_id).model_calls
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown run") from error

    return app
