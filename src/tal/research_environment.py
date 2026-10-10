"""Benchmark-neutral, versioned environments for TAL research episodes.

The production implementations may be backed by a digest-pinned container.  The
protocol deliberately remains small so every environment transition can be recorded
in TAL's controlled trace and reproduced with a deterministic fixture.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import Field, JsonValue

from .core import Identifier, StrictModel, sha256
from .research import BenchmarkId, BenchmarkScore, ResearchTaskManifest, ToolDefinition


class BenchmarkObservation(StrictModel):
    environment_id: Identifier
    state_version: int = Field(ge=0)
    text: str = Field(default="", max_length=100_000)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    terminal: bool = False
    success: bool | None = None


class BenchmarkToolCall(StrictModel):
    call_id: Identifier
    tool_id: Identifier
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    expected_state_version: int = Field(ge=0)


class BenchmarkToolResult(StrictModel):
    call_id: Identifier
    tool_id: Identifier
    observation: BenchmarkObservation
    output: dict[str, JsonValue] = Field(default_factory=dict)
    duplicate: bool = False
    external_effect: bool = False


class BenchmarkEnvironment(Protocol):
    @property
    def benchmark_id(self) -> BenchmarkId: ...

    environment_id: str

    async def reset(self, manifest: ResearchTaskManifest) -> BenchmarkObservation: ...

    def tool_definitions(self) -> tuple[ToolDefinition, ...]: ...

    async def execute_tool(self, call: BenchmarkToolCall) -> BenchmarkToolResult: ...

    async def score(self, final_answer: str) -> BenchmarkScore: ...

    async def close(self) -> None: ...


class EnvironmentProtocolError(RuntimeError):
    pass


class JsonLineContainerEnvironment:
    """Adapter for one JSONL benchmark worker process or container command.

    A command is intentionally injected by the caller.  The frozen experiment stores
    the image digest; this class avoids accepting mutable image tags itself.
    """

    def __init__(
        self,
        *,
        benchmark_id: BenchmarkId,
        environment_id: Identifier,
        command: tuple[str, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> None:
        if not command:
            raise ValueError("container environment command is required")
        self.benchmark_id = benchmark_id
        self.environment_id = environment_id
        self._command = command
        self._tools = tools
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return self._tools

    async def reset(self, manifest: ResearchTaskManifest) -> BenchmarkObservation:
        payload = await self._request({"op": "reset", "manifest": manifest.model_dump(mode="json")})
        return BenchmarkObservation.model_validate(payload)

    async def execute_tool(self, call: BenchmarkToolCall) -> BenchmarkToolResult:
        payload = await self._request({"op": "tool", "call": call.model_dump(mode="json")})
        return BenchmarkToolResult.model_validate(payload)

    async def score(self, final_answer: str) -> BenchmarkScore:
        payload = await self._request({"op": "score", "final_answer": final_answer})
        return BenchmarkScore.model_validate(payload)

    async def close(self) -> None:
        if self._process is None:
            return
        try:
            await self._request({"op": "close"})
        except (EnvironmentProtocolError, ProcessLookupError):
            pass
        process = self._process
        self._process = None
        if process.returncode is None:
            process.terminate()
            await process.wait()

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self._process is None:
                self._process = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            process = self._process
            if process.stdin is None or process.stdout is None:
                raise EnvironmentProtocolError("environment process has no JSONL streams")
            process.stdin.write(json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n")
            await process.stdin.drain()
            line = await process.stdout.readline()
            if not line:
                stderr = b""
                if process.stderr is not None:
                    stderr = await process.stderr.read()
                raise EnvironmentProtocolError(
                    "environment process ended without a response: "
                    + stderr.decode("utf-8", "replace")
                )
            decoded: object = json.loads(line.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise EnvironmentProtocolError("environment response must be a JSON object")
            if "error" in decoded:
                raise EnvironmentProtocolError(str(decoded["error"]))
            return decoded


ToolHandler = Callable[
    [dict[str, JsonValue]], Awaitable[tuple[str, dict[str, JsonValue], bool | None]]
]
ScoreHandler = Callable[[str], Awaitable[BenchmarkScore]]


@dataclass
class ScriptedBenchmarkEnvironment:
    """Deterministic environment used by offline parity and smoke tests."""

    benchmark_id: BenchmarkId
    environment_id: str
    tools: tuple[ToolDefinition, ...]
    initial_text: str = ""
    score_handler: ScoreHandler | None = None
    handlers: Mapping[str, ToolHandler] = field(default_factory=dict)
    _state_version: int = 0
    _terminal: bool = False
    _seen_calls: dict[str, BenchmarkToolResult] = field(default_factory=dict)

    async def reset(self, manifest: ResearchTaskManifest) -> BenchmarkObservation:
        self._state_version = 0
        self._terminal = False
        self._seen_calls.clear()
        return BenchmarkObservation(
            environment_id=self.environment_id,
            state_version=0,
            text=self.initial_text or manifest.goal,
            payload={"task_id": manifest.task_id},
        )

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return self.tools

    async def execute_tool(self, call: BenchmarkToolCall) -> BenchmarkToolResult:
        if call.call_id in self._seen_calls:
            return self._seen_calls[call.call_id].model_copy(update={"duplicate": True})
        if call.expected_state_version != self._state_version:
            raise EnvironmentProtocolError("stale benchmark environment state version")
        if call.tool_id not in {item.tool_id for item in self.tools}:
            raise EnvironmentProtocolError("benchmark tool is not registered")
        handler = self.handlers.get(call.tool_id)
        if handler is None:
            text = "ok"
            payload: dict[str, JsonValue] = {}
            terminal: bool | None = None
        else:
            text, payload, terminal = await handler(call.arguments)
        self._state_version += 1
        if terminal is not None:
            self._terminal = terminal
        result = BenchmarkToolResult(
            call_id=call.call_id,
            tool_id=call.tool_id,
            observation=BenchmarkObservation(
                environment_id=self.environment_id,
                state_version=self._state_version,
                text=text,
                payload=payload,
                terminal=self._terminal,
                success=True if self._terminal else None,
            ),
            output=payload,
        )
        self._seen_calls[call.call_id] = result
        return result

    async def score(self, final_answer: str) -> BenchmarkScore:
        if self.score_handler is not None:
            return await self.score_handler(final_answer)
        return BenchmarkScore(
            success=bool(final_answer.strip()),
            native_score_millionths=1_000_000 if final_answer.strip() else 0,
            scorer_id="scripted-benchmark-scorer",
            scorer_hash=sha256({"environment": self.environment_id}),
        )

    async def close(self) -> None:
        return None
