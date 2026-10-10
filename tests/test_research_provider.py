from __future__ import annotations

import pytest

from tal.research_provider import (
    MeteredProviderClient,
    ProviderBudgetExceeded,
    ProviderBudgetLedger,
    ProviderGateway,
    ProviderMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
    TokenPricing,
    google_generate_content_transport,
    openai_responses_transport,
)


def response(_: dict[str, object]) -> ProviderResponse:
    return ProviderResponse(
        text="answer",
        resolved_model="fixture-001",
        usage=ProviderUsage(input_tokens=1_000, cached_input_tokens=200, output_tokens=100),
        raw_response={"id": "fixture"},
    )


def small_response(_: dict[str, object]) -> ProviderResponse:
    return ProviderResponse(
        text="answer",
        resolved_model="fixture",
        usage=ProviderUsage(input_tokens=10, output_tokens=5),
        raw_response={"id": "fixture"},
    )


def test_metered_provider_receipt_reconciles_cost_and_hard_budget() -> None:
    client = MeteredProviderClient(
        provider="fixture",
        requested_model="fixture",
        pricing=TokenPricing(
            input_microcents_per_million=1_000_000,
            cached_input_microcents_per_million=500_000,
            output_microcents_per_million=2_000_000,
        ),
        budget=ProviderBudgetLedger(maximum_microcents=1_100),
        transport=response,
    )
    text, receipt = client.call({"input": "question"})
    assert text == "answer"
    assert receipt.total_cost_microcents == 1_100
    with pytest.raises(ProviderBudgetExceeded):
        client.call({"input": "second question"})


def test_openai_and_google_transports_normalize_provider_usage() -> None:
    openai = openai_responses_transport(
        "test-key",
        sender=lambda *_: {
            "id": "response-id",
            "model": "gpt-fixture-2026-01-01",
            "output_text": "answer",
            "usage": {"input_tokens": 10, "output_tokens": 3},
        },
    )
    assert openai({"model": "requested"}).usage.output_tokens == 3
    google = google_generate_content_transport(
        "test-key",
        "gemini-fixture",
        sender=lambda *_: {
            "responseId": "response-id",
            "candidates": [{"content": {"parts": [{"text": "answer"}]}}],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 2},
        },
    )
    assert google({"contents": []}).text == "answer"


def test_gateway_reserves_worst_case_before_call_and_records_segment_usage() -> None:
    client = MeteredProviderClient(
        provider="openai",
        requested_model="fixture",
        pricing=TokenPricing(
            input_microcents_per_million=1_000_000,
            output_microcents_per_million=1_000_000,
        ),
        budget=ProviderBudgetLedger(maximum_microcents=2_000),
        transport=small_response,
    )
    result = ProviderGateway(client, client.pricing).complete(
        ProviderRequest(
            model="fixture",
            messages=(ProviderMessage(role="user", content="one two"),),
            max_output_tokens=10,
            context_capacity=20,
            input_segments={"tal_policy": "one two", "task": "three"},
        )
    )
    assert result.receipt.reservation_microcents == 30
    assert result.receipt.input_segments == {"tal_policy": 2, "task": 1}
    assert client.budget.spent_microcents == result.receipt.total_cost_microcents
