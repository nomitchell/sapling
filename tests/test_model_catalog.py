from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from sapling.config import ProjectSettings
from sapling.integrations.model import OpenAIModelRuntime
from sapling.model_catalog import DEFAULT_MODEL, merge_settings, preset


def test_default_model_and_reasoning_match_reviewed_preset():
    settings = ProjectSettings()
    selected = preset(settings.model)
    assert settings.model == DEFAULT_MODEL and selected is not None
    assert settings.reasoning_effort in selected["reasoning_efforts"]
    for key in ("input_cost_per_million", "cached_input_cost_per_million", "output_cost_per_million"):
        assert getattr(settings, key) == selected[key]


@pytest.mark.parametrize(
    ("model", "effort"),
    [("gpt-5-mini", "xhigh"), ("gpt-5-mini-2025-08-07", "none"), ("gpt-5.4-nano", None)],
)
def test_known_models_reject_unsupported_or_omitted_reasoning(model, effort):
    with pytest.raises(ValidationError, match="supports reasoning levels"):
        ProjectSettings(model=model, reasoning_effort=effort)


def test_custom_model_accepts_null_reasoning_and_requires_explicit_normal_prices():
    merged = merge_settings(ProjectSettings().model_dump(), {"model": "custom-nonreasoning", "reasoning_effort": None})
    settings = ProjectSettings.model_validate(merged)
    assert settings.reasoning_effort is None
    assert settings.cached_input_cost_per_million is None
    assert settings.input_cost_per_million == settings.output_cost_per_million == 0
    assert "null" in str(ProjectSettings.model_json_schema()["properties"]["reasoning_effort"])


def test_known_model_switch_loads_reviewed_prices_and_explicit_overrides_win():
    current = ProjectSettings().model_dump()
    settings = ProjectSettings.model_validate(merge_settings(current, {"model": "gpt-5.4-mini", "output_cost_per_million": 5}))
    selected = preset("gpt-5.4-mini")
    assert settings.input_cost_per_million == selected["input_cost_per_million"]
    assert settings.cached_input_cost_per_million == selected["cached_input_cost_per_million"]
    assert settings.output_cost_per_million == 5
    custom = merge_settings(current, {"model": "custom", "cached_input_cost_per_million": 0.5})
    assert custom["cached_input_cost_per_million"] == 0.5


@pytest.mark.asyncio
async def test_unknown_cache_price_uses_normal_rate_and_null_omits_reasoning():
    class Decision(BaseModel):
        summary: str

    settings = ProjectSettings.model_validate(
        merge_settings(
            ProjectSettings().model_dump(),
            {"model": "custom-nonreasoning", "reasoning_effort": None, "input_cost_per_million": 2, "output_cost_per_million": 8},
        )
    )
    async def parse(**kwargs):
        assert "reasoning" not in kwargs
        return SimpleNamespace(
            output_parsed=Decision(summary="done"), id="mock-response", status="completed",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, input_tokens_details=SimpleNamespace(cached_tokens=800)),
        )
    model = OpenAIModelRuntime(
        "sk-test-real-shaped", settings.model, settings.reasoning_effort,
        settings.input_cost_per_million, settings.output_cost_per_million, settings.cached_input_cost_per_million,
        client=SimpleNamespace(responses=SimpleNamespace(parse=parse)),
    )
    result = await model.turn({}, Decision, "Research")
    assert result.cached_input_tokens == 800
    # Unknown cached pricing is conservatively charged at the configured input
    # price; it cannot silently make 800 input tokens free.
    assert result.cost_usd == pytest.approx((1000 * 2 + 200 * 8) / 1_000_000)
