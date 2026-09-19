import asyncio
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from sapling.config import ProjectSettings
from sapling.credentials import CredentialVault
from sapling.integrations.model import BasetenModelRuntime, ModelResponseError, OpenAIModelRuntime, model_runtime
from sapling.model_catalog import DEFAULT_MODELS, MODEL_BASE_URLS, account_catalog, merge_settings, preset


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    arguments: dict


WIRE_DECISION = {
    "summary": "Read the relevant source",
    "arguments": {"entries": [
        {"key": "query", "value": "distribution coverage"},
        {"key": "options", "value": {"entries": [{"key": "limit", "value": 3}]}},
    ]},
}


def completion(content=None, **changes):
    result = {
        "id": "chatcmpl-tested", "object": "chat.completion", "created": 1,
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": content if content is not None else json.dumps(WIRE_DECISION),
            "reasoning_content": "Provider-internal reasoning must not become a decision or transcript.",
        }}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
                  "prompt_tokens_details": {"cached_tokens": 800},
                  "completion_tokens_details": {"reasoning_tokens": 150}},
    }
    return result | changes


def adapter(handler, *, model="openai/gpt-oss-120b", effort="low"):
    client = openai.AsyncOpenAI(
        api_key="test-baseten-credential", base_url=MODEL_BASE_URLS["baseten"], max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return BasetenModelRuntime("test-baseten-credential", model, effort, 2, 8, 0.5, client=client)


@pytest.mark.asyncio
async def test_baseten_real_sdk_request_and_decision_contract():
    async def handler(request):
        assert str(request.url) == "https://inference.baseten.co/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-baseten-credential"
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "low"
        assert body["max_tokens"] == 900 and body["stream"] is False
        assert "store" not in body and "reasoning" not in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][2] == {"role": "user", "content": "Compare these hypotheses."}
        assert body["messages"][-1]["role"] == "user"  # Continuation after assistant activity.
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] and schema["schema"]["additionalProperties"] is False
        assert schema["schema"]["properties"]["arguments"] == {"$ref": "#/$defs/SaplingJsonObject"}
        return httpx.Response(200, json=completion())

    model = adapter(handler)
    try:
        result = await model.turn({"conversation": [
            {"role": "user", "text": "Compare these hypotheses."},
            {"role": "assistant", "text": "Reading the relevant paper."},
        ], "source_catalog": [{"url": "https://example.org/paper"}]}, Decision, "Research instructions", 900)
        assert result.decision.arguments == {"query": "distribution coverage", "options": {"limit": 3}}
        assert result.usage == {"input_tokens": 1000, "output_tokens": 200, "cached_input_tokens": 800}
        assert result.cost_usd == pytest.approx((200 * 2 + 800 * 0.5 + 200 * 8) / 1_000_000)
        assert "internal reasoning" not in result.decision.model_dump_json()
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("effort,enabled", [("none", False), ("high", True)])
async def test_toggle_models_only_send_documented_thinking_control(effort, enabled):
    async def handler(request):
        body = json.loads(request.content)
        assert body["chat_template_args"] == {"enable_thinking": enabled}
        assert "reasoning_effort" not in body
        return httpx.Response(200, json=completion())

    model = adapter(handler, model="moonshotai/Kimi-K2.7-Code", effort=effort)
    try:
        await model.turn({}, Decision, "Research")
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["invalid_json", "invalid_map", "invalid_decision", "length", "refusal", "tool_call"])
async def test_paid_invalid_responses_keep_usage_and_execute_no_decision(problem):
    response = completion()
    message = response["choices"][0]["message"]
    if problem == "invalid_json":
        message["content"] = "not JSON"
    elif problem == "invalid_map":
        message["content"] = json.dumps({"summary": "x", "arguments": {"entries": [{"key": "missing-value"}]}})
    elif problem == "invalid_decision":
        message["content"] = json.dumps({"summary": "x", "arguments": {"entries": []}, "unapproved": True})
    elif problem == "length":
        response["choices"][0]["finish_reason"] = "length"
    elif problem == "refusal":
        message["refusal"] = "refused"
    else:
        message["tool_calls"] = [{"id": "unexpected", "type": "function", "function": {"name": "run", "arguments": "{}"}}]

    model = adapter(lambda _: httpx.Response(200, json=response))
    try:
        with pytest.raises(ModelResponseError) as caught:
            await model.turn({}, Decision, "Research", turn_id="paid-invalid")
        assert caught.value.cost_usd > 0
        assert caught.value.usage["output_tokens"] == 200
        assert caught.value.response_id == "chatcmpl-tested"
        assert not model._active
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,zero_cost", [(400, True), (401, True), (402, True), (429, True), (500, False), (529, False)])
async def test_request_rejection_versus_ambiguous_provider_failure(status, zero_cost):
    model = adapter(lambda _: httpx.Response(status, json={"error": {"message": "Request failed"}}))
    try:
        with pytest.raises(openai.APIStatusError) as caught:
            await model.turn({}, Decision, "Research")
        assert (getattr(caught.value, "cost_usd", None) == 0) is zero_cost
        assert not model._active
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {"prompt_tokens": -1, "completion_tokens": 4}, {"prompt_tokens": 1}])
async def test_missing_or_invalid_usage_never_becomes_a_free_turn(usage):
    model = adapter(lambda _: httpx.Response(200, json=completion(usage=usage)))
    try:
        with pytest.raises(ModelResponseError) as caught:
            await model.turn({}, Decision, "Research")
        assert not hasattr(caught.value, "cost_usd")
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("named", [False, True])
async def test_cancellation_and_close_interrupt_pending_http_and_preserve_unknown_cost(named):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    model = adapter(handler)
    turn = asyncio.create_task(model.turn({}, Decision, "Research", turn_id="active" if named else None))
    await asyncio.wait_for(started.wait(), 2)
    if named:
        assert await model.cancel("active")
        assert not await model.cancel("missing")
    await model.close()
    with pytest.raises(asyncio.CancelledError):
        await turn
    assert cancelled.is_set() and not model._active


def test_provider_switch_updates_model_prices_and_supported_controls():
    current = ProjectSettings().model_dump()
    switched = ProjectSettings.model_validate(merge_settings(current, {"provider": "baseten"}))
    assert switched.model == DEFAULT_MODELS["baseten"]
    assert switched.input_cost_per_million == 0.10
    assert switched.output_cost_per_million == 0.50
    assert switched.cached_input_cost_per_million is None
    kimi = ProjectSettings.model_validate(merge_settings(switched.model_dump(), {"model": "moonshotai/Kimi-K2.7-Code"}))
    assert kimi.reasoning_effort == "high"
    assert kimi.input_cost_per_million == 0.95
    assert preset(kimi.model) is None
    assert preset(kimi.model, "baseten")["key"] == "baseten:moonshotai/Kimi-K2.7-Code"
    back = ProjectSettings.model_validate(merge_settings(kimi.model_dump(), {"provider": "openai"}))
    assert back.model == DEFAULT_MODELS["openai"]
    assert isinstance(model_runtime("openai", None, "model"), OpenAIModelRuntime)
    assert isinstance(model_runtime("baseten", None, "model"), BasetenModelRuntime)
    with pytest.raises(ValueError, match="Unsupported model provider"):
        model_runtime("unrecognized", None, "model")


@pytest.mark.parametrize("settings", [
    {"provider": "baseten", "model": "gpt-5.4-nano"},
    {"provider": "openai", "model": "moonshotai/Kimi-K3"},
    {"provider": "baseten", "model": "moonshotai/Kimi-K2.7-Code", "reasoning_effort": "medium"},
    {"provider": "baseten", "model": "custom-slug", "reasoning_effort": "high"},
])
def test_settings_reject_provider_mismatch_or_unverified_reasoning(settings):
    with pytest.raises(ValidationError):
        ProjectSettings(**settings)


def test_automatic_depth_and_explicit_limits_are_distinct():
    assert ProjectSettings().max_depth is None
    assert ProjectSettings(max_depth=3).max_depth == 3
    assert ProjectSettings(provider="baseten").input_cost_per_million == 0.10
    with pytest.raises(ValidationError):
        ProjectSettings(max_depth=0)


@pytest.mark.asyncio
async def test_custom_provider_requires_explicit_prices_but_omits_unknown_reasoning_controls():
    settings = ProjectSettings(provider="baseten", model="custom/model")
    assert settings.reasoning_effort is None
    assert settings.input_cost_per_million == settings.output_cost_per_million == 0
    async def handler(request):
        body = json.loads(request.content)
        assert "reasoning_effort" not in body and "chat_template_args" not in body
        return httpx.Response(200, json=completion())
    model = adapter(handler, model=settings.model, effort=settings.reasoning_effort)
    model.input_rate = model.output_rate = 0
    with pytest.raises(ValueError, match="Configure model input and output prices"):
        await model.turn({}, Decision, "Research")
    model.input_rate, model.output_rate = 1, 2
    try:
        result = await model.turn({}, Decision, "Research")
        assert result.cost_usd > 0
    finally:
        await model.close()


def test_baseten_credential_uses_existing_vault_without_exposing_values(monkeypatch):
    secrets = {}
    monkeypatch.setattr("keyring.set_password", lambda service, provider, key: secrets.update({(service, provider): key}))
    monkeypatch.setattr("keyring.get_password", lambda service, provider: secrets.get((service, provider)))
    monkeypatch.setattr("keyring.delete_password", lambda service, provider: secrets.pop((service, provider)))
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    vault = CredentialVault()
    vault.set("baseten", "test-credential")
    assert vault.get("baseten") == "test-credential"
    vault.delete("baseten")
    assert vault.get("baseten") is None


@pytest.mark.asyncio
async def test_account_catalog_keeps_provider_availability_independent(monkeypatch):
    class Client:
        def __init__(self, *, api_key, base_url, **kwargs):
            self.base_url = base_url
            self.models = self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def list(self):
            models = ["openai/gpt-oss-120b"] if "baseten" in self.base_url else ["gpt-5.4-nano"]
            return SimpleNamespace(data=[SimpleNamespace(id=model) for model in models])

    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    catalog = await account_catalog(SimpleNamespace(get=lambda provider: "credential-only-used-by-client"))
    available = {model["key"] for model in catalog["models"] if model["available"]}
    assert available == {"openai:gpt-5.4-nano", "baseten:openai/gpt-oss-120b"}
    assert "credential-only-used-by-client" not in json.dumps(catalog)
    assert len({model["key"] for model in catalog["models"]}) == len(catalog["models"])
