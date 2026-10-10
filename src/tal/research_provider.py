"""Provider-neutral request accounting with hard microcent budget admission."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

from pydantic import Field

from .core import Identifier, StrictModel, sha256
from .research import ProviderCallReceipt


class ProviderUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class ProviderResponse(StrictModel):
    text: str
    resolved_model: str = Field(min_length=1, max_length=256)
    usage: ProviderUsage
    provider_request_id: str | None = Field(default=None, max_length=512)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class TokenPricing(StrictModel):
    """Integer microcents per million tokens, frozen into the experiment artifact."""

    input_microcents_per_million: int = Field(ge=0)
    cached_input_microcents_per_million: int = Field(default=0, ge=0)
    output_microcents_per_million: int = Field(ge=0)
    reasoning_microcents_per_million: int = Field(default=0, ge=0)


class ProviderBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderBudgetReservation:
    amount_microcents: int


@dataclass
class ProviderBudgetLedger:
    maximum_microcents: int
    spent_microcents: int = 0
    reserved_microcents: int = 0

    def reserve(self, amount: int) -> ProviderBudgetReservation:
        if amount < 0:
            raise ValueError("provider cost must be non-negative")
        if self.spent_microcents + self.reserved_microcents + amount > self.maximum_microcents:
            raise ProviderBudgetExceeded("provider budget would be exceeded")
        self.reserved_microcents += amount
        return ProviderBudgetReservation(amount)

    def commit(self, reservation: ProviderBudgetReservation, actual_cost_microcents: int) -> None:
        if actual_cost_microcents < 0 or actual_cost_microcents > reservation.amount_microcents:
            raise ValueError("provider cost exceeds its pre-call reservation")
        self.reserved_microcents -= reservation.amount_microcents
        self.spent_microcents += actual_cost_microcents

    def release(self, reservation: ProviderBudgetReservation) -> None:
        self.reserved_microcents -= reservation.amount_microcents


ProviderTransport = Callable[[dict[str, Any]], ProviderResponse]
HttpJsonPost = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


class MeteredProviderClient:
    """Wrap an injected provider transport and emit reconcilable receipt records."""

    def __init__(
        self,
        *,
        provider: str,
        requested_model: str,
        pricing: TokenPricing,
        budget: ProviderBudgetLedger,
        transport: ProviderTransport,
    ) -> None:
        self.provider = provider
        self.requested_model = requested_model
        self.pricing = pricing
        self.budget = budget
        self.transport = transport
        self._call_number = 0

    def call(
        self,
        request: dict[str, Any],
        *,
        maximum_cost_microcents: int | None = None,
        latency_milliseconds: int | None = None,
    ) -> tuple[str, ProviderCallReceipt]:
        available = (
            self.budget.maximum_microcents
            - self.budget.spent_microcents
            - self.budget.reserved_microcents
        )
        if maximum_cost_microcents is None and available <= 0:
            raise ProviderBudgetExceeded("provider budget would be exceeded")
        reservation = self.budget.reserve(
            maximum_cost_microcents if maximum_cost_microcents is not None else available
        )
        started = time.perf_counter()
        try:
            response = self.transport(request)
        except BaseException:
            self.budget.release(reservation)
            raise
        usage = response.usage
        input_cost = _cost(
            usage.input_tokens - usage.cached_input_tokens,
            self.pricing.input_microcents_per_million,
        )
        cached_cost = _cost(
            usage.cached_input_tokens, self.pricing.cached_input_microcents_per_million
        )
        output_cost = _cost(usage.output_tokens, self.pricing.output_microcents_per_million)
        reasoning_cost = _cost(
            usage.reasoning_tokens, self.pricing.reasoning_microcents_per_million
        )
        total_cost = input_cost + cached_cost + output_cost + reasoning_cost
        try:
            self.budget.commit(reservation, total_cost)
        except ValueError as error:
            self.budget.release(reservation)
            raise ProviderBudgetExceeded(
                "provider usage exceeded its pre-call reservation"
            ) from error
        self._call_number += 1
        receipt = ProviderCallReceipt(
            call_id=f"provider-call:{self._call_number:08d}",
            provider=self.provider,
            requested_model=self.requested_model,
            resolved_model=response.resolved_model,
            provider_request_id=response.provider_request_id,
            request_hash=sha256(request),
            response_hash=sha256(response.raw_response or {"text": response.text}),
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            latency_milliseconds=(
                latency_milliseconds
                if latency_milliseconds is not None
                else int((time.perf_counter() - started) * 1_000)
            ),
            input_cost_microcents=input_cost + cached_cost,
            output_cost_microcents=output_cost,
            reasoning_cost_microcents=reasoning_cost,
            total_cost_microcents=total_cost,
            reservation_microcents=reservation.amount_microcents,
        )
        return response.text, receipt


class ProviderMessage(StrictModel):
    role: str = Field(pattern=r"^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=2_000_000)


class ProviderRequest(StrictModel):
    model: str = Field(min_length=1, max_length=256)
    messages: tuple[ProviderMessage, ...] = Field(min_length=1)
    max_output_tokens: int = Field(ge=1, le=131_072)
    temperature_milli: int = Field(default=0, ge=0, le=2_000)
    context_capacity: int = Field(ge=1, le=2_000_000)
    input_segments: dict[str, str] = Field(default_factory=dict)


class ProviderResult(StrictModel):
    text: str
    receipt: ProviderCallReceipt


class ProviderGateway:
    """One measurable provider boundary shared by TAL and CONTROL engines."""

    def __init__(self, client: MeteredProviderClient, pricing: TokenPricing) -> None:
        self.client = client
        self.pricing = pricing

    def complete(self, request: ProviderRequest) -> ProviderResult:
        payload = _provider_payload(self.client.provider, request)
        maximum = _maximum_call_cost(request, self.pricing)
        text, receipt = self.client.call(payload, maximum_cost_microcents=maximum)
        segment_counts = {
            name: _estimate_tokens(value) for name, value in request.input_segments.items()
        }
        return ProviderResult(
            text=text, receipt=receipt.model_copy(update={"input_segments": segment_counts})
        )


def _cost(tokens: int, microcents_per_million: int) -> int:
    """Round up to avoid silently exceeding a hard budget through fractional tokens."""

    return (tokens * microcents_per_million + 999_999) // 1_000_000


def dollars_to_microcents(dollars: int) -> int:
    """Convert whole dollars without floating-point arithmetic."""

    if dollars < 0:
        raise ValueError("dollar amount may not be negative")
    return dollars * 100_000_000


def _maximum_call_cost(request: ProviderRequest, pricing: TokenPricing) -> int:
    input_cost = _cost(request.context_capacity, pricing.input_microcents_per_million)
    output_cost = _cost(request.max_output_tokens, pricing.output_microcents_per_million)
    reasoning_cost = _cost(request.max_output_tokens, pricing.reasoning_microcents_per_million)
    return input_cost + output_cost + reasoning_cost


def _estimate_tokens(value: str) -> int:
    return len(value.split())


def _provider_payload(provider: str, request: ProviderRequest) -> dict[str, Any]:
    if provider == "openai":
        return {
            "model": request.model,
            "input": [item.model_dump(mode="json") for item in request.messages],
            "max_output_tokens": request.max_output_tokens,
            "temperature_milli": request.temperature_milli,
        }
    if provider == "google":
        system = [item.content for item in request.messages if item.role == "system"]
        contents = [
            {
                "role": "model" if item.role == "assistant" else "user",
                "parts": [{"text": item.content}],
            }
            for item in request.messages
            if item.role != "system"
        ]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature_milli": request.temperature_milli,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        return payload
    raise ValueError(f"unsupported provider {provider}")


def receipt_artifact_id(receipt: ProviderCallReceipt) -> Identifier:
    return "receipt:" + receipt.call_id.removeprefix("provider-call:")


def openai_responses_transport(
    api_key: str,
    *,
    sender: HttpJsonPost | None = None,
) -> ProviderTransport:
    """Create an OpenAI Responses transport without retaining or serializing the key."""

    if not api_key:
        raise ValueError("OpenAI API key is required")
    post = sender or _post_json

    def transport(request: dict[str, Any]) -> ProviderResponse:
        payload_request = dict(request)
        if "temperature_milli" in payload_request:
            payload_request["temperature"] = int(payload_request.pop("temperature_milli")) / 1_000
        payload = post(
            "https://api.openai.com/v1/responses",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload_request,
        )
        usage = payload.get("usage", {})
        output_text = payload.get("output_text") or _openai_output_text(payload)
        return ProviderResponse(
            text=str(output_text),
            resolved_model=str(payload.get("model") or payload_request.get("model")),
            usage=ProviderUsage(
                input_tokens=int(usage.get("input_tokens", 0)),
                cached_input_tokens=int(
                    usage.get("input_tokens_details", {}).get("cached_tokens", 0)
                ),
                output_tokens=int(usage.get("output_tokens", 0)),
                reasoning_tokens=int(
                    usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
                ),
            ),
            provider_request_id=str(payload["id"]) if payload.get("id") is not None else None,
            raw_response=payload,
        )

    return transport


def google_generate_content_transport(
    api_key: str,
    model: str,
    *,
    sender: HttpJsonPost | None = None,
) -> ProviderTransport:
    """Create a Gemini generateContent transport for an explicitly frozen model id."""

    if not api_key or not model:
        raise ValueError("Google API key and model are required")
    post = sender or _post_json

    def transport(request: dict[str, Any]) -> ProviderResponse:
        payload_request = dict(request)
        generation_config = dict(payload_request.get("generationConfig", {}))
        if "temperature_milli" in generation_config:
            generation_config["temperature"] = (
                int(generation_config.pop("temperature_milli")) / 1_000
            )
            payload_request["generationConfig"] = generation_config
        payload = post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            {"Content-Type": "application/json"},
            payload_request,
        )
        usage = payload.get("usageMetadata", {})
        candidates = payload.get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(str(part.get("text", "")) for part in parts)
        return ProviderResponse(
            text=text,
            resolved_model=model,
            usage=ProviderUsage(
                input_tokens=int(usage.get("promptTokenCount", 0)),
                cached_input_tokens=int(usage.get("cachedContentTokenCount", 0)),
                output_tokens=int(usage.get("candidatesTokenCount", 0)),
                reasoning_tokens=int(usage.get("thoughtsTokenCount", 0)),
            ),
            provider_request_id=payload.get("responseId"),
            raw_response=payload,
        )

    return transport


def _openai_output_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(str(content.get("text", "")))
    return "".join(chunks)


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=120) as response:  # noqa: S310 - caller fixes trusted provider URL
        parsed: object = json.loads(response.read().decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("provider response must be a JSON object")
    return parsed
