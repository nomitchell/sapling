"""Reviewed model presets; prices remain visible and editable by the user.

Prices are standard USD per million text tokens, verified 2026-09-19 against
the linked official model pages. Account availability is checked separately.
"""

DEFAULT_MODEL = "gpt-5.4-nano"
DEFAULT_MODELS = {"openai": DEFAULT_MODEL, "baseten": "openai/gpt-oss-120b"}
MODEL_BASE_URLS = {"openai": "https://api.openai.com/v1", "baseten": "https://inference.baseten.co/v1"}
MODELS = [
    {
        "id": "gpt-6-astra",
        "label": "GPT-6 Astra",
        "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"],
        "input_cost_per_million": 10.0,
        "cached_input_cost_per_million": 1.0,
        "output_cost_per_million": 50.0,
    },
    {
        "id": "gpt-5.6-sol",
        "label": "GPT-5.6 Sol",
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "input_cost_per_million": 4.0,
        "cached_input_cost_per_million": 0.4,
        "output_cost_per_million": 20.0,
    },
    {
        "id": "gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "input_cost_per_million": 2.0,
        "cached_input_cost_per_million": 0.2,
        "output_cost_per_million": 12.0,
    },
    {
        "id": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "input_cost_per_million": 0.2,
        "cached_input_cost_per_million": 0.02,
        "output_cost_per_million": 1.2,
    },
    {
        "id": "gpt-5.4-nano",
        "label": "GPT-5.4 nano · low cost",
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh"],
        "input_cost_per_million": 0.20,
        "cached_input_cost_per_million": 0.02,
        "output_cost_per_million": 1.25,
    },
    {
        "id": "gpt-5.4-mini",
        "label": "GPT-5.4 mini",
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh"],
        "input_cost_per_million": 0.75,
        "cached_input_cost_per_million": 0.075,
        "output_cost_per_million": 4.50,
    },
    {
        "id": "gpt-5-mini",
        "label": "GPT-5 mini",
        "reasoning_efforts": ["minimal", "low", "medium", "high"],
        "input_cost_per_million": 0.25,
        "cached_input_cost_per_million": 0.025,
        "output_cost_per_million": 2.00,
    },
]
for entry in MODELS:
    entry.update(
        provider="openai",
        key=f"openai:{entry['id']}",
        reasoning_control="effort",
        default_reasoning_effort="low",
        price_verified_at="2026-09-19",
        source_url=f"https://developers.openai.com/api/docs/models/{entry['id']}",
    )


BASETEN_REASONING_SOURCE = "https://docs.baseten.co/inference/model-apis/reasoning"
BASETEN_PRICE_SOURCE = "https://www.baseten.co/products/model-apis/"
for model_id, label, rates, efforts, control in [
    ("openai/gpt-oss-120b", "GPT OSS 120B", (0.10, None, 0.50),
     ["none", "minimal", "low", "medium", "high", "xhigh", "max"], "effort"),
    ("zai-org/GLM-5.3-Flash", "GLM 5.3 Flash", (0.15, 0.03, 0.50),
     ["low", "high", "max"], "effort"),
    ("deepseek-ai/DeepSeek-V4.1-Flash", "DeepSeek V4.1 Flash", (0.30, 0.03, 1.20),
     ["none", "low", "high", "max"], "effort"),
    ("zai-org/GLM-5.3", "GLM 5.3", (1.40, 0.14, 4.40),
     ["low", "high", "max"], "effort"),
    ("moonshotai/Kimi-K2.7-Code", "Kimi K2.7 Code", (0.95, 0.16, 4.00),
     ["none", "high"], "toggle"),
    ("moonshotai/Kimi-K3", "Kimi K3", (3.00, 0.30, 15.00),
     ["none", "low", "high", "max"], "effort"),
]:
    MODELS.append({
        "provider": "baseten", "id": model_id, "key": f"baseten:{model_id}", "label": label,
        "input_cost_per_million": rates[0], "cached_input_cost_per_million": rates[1],
        "output_cost_per_million": rates[2], "reasoning_efforts": efforts,
        "reasoning_control": control, "default_reasoning_effort": "high" if control == "toggle" else "low",
        "reasoning_note": "Thinking remains enabled at every level." if "GLM-5.3" in model_id else None,
        "price_verified_at": "2026-09-19", "source_url": BASETEN_PRICE_SOURCE,
        "capability_source_url": BASETEN_REASONING_SOURCE,
    })


def preset(model, provider="openai"):
    if not isinstance(model, str):
        return None
    return next((m for m in MODELS if m["provider"] == provider and (
        model == m["id"] or provider == "openai" and model.startswith(m["id"] + "-20")
    )), None)


def merge_settings(current, changes):
    changes = dict(changes)
    provider = changes.get("provider", current.get("provider", "openai"))
    provider_changed = provider != current.get("provider", "openai")
    if provider_changed and "model" not in changes and provider in DEFAULT_MODELS:
        changes["model"] = DEFAULT_MODELS[provider]
    if provider_changed or "model" in changes and changes["model"] != current.get("model"):
        selected = preset(changes.get("model", current["model"]), provider)
        rates = {
            key: selected[key] if selected else None if key == "cached_input_cost_per_million" else 0
            for key in ("input_cost_per_million", "cached_input_cost_per_million", "output_cost_per_million")
        }
        current = current | rates
        if "reasoning_effort" not in changes:
            effort = current.get("reasoning_effort")
            if not selected or effort not in selected["reasoning_efforts"]:
                changes["reasoning_effort"] = selected["default_reasoning_effort"] if selected else None
    return current | changes


async def account_catalog(vault):
    """Availability is provider-specific; listing a preset is not a live turn check."""
    import asyncio
    from openai import AsyncOpenAI

    async def available_for(provider):
        key = vault.get(provider)
        if not key:
            return provider, None, None
        try:
            async with AsyncOpenAI(api_key=key, base_url=MODEL_BASE_URLS[provider], timeout=15, max_retries=0) as client:
                result = await client.models.list()
            return provider, {model.id for model in result.data}, None
        except Exception:
            return provider, None, f"Could not refresh {provider.title()} model availability. Presets remain available."

    results = await asyncio.gather(*(available_for(provider) for provider in MODEL_BASE_URLS))
    available = {provider: models for provider, models, _ in results}
    errors = [error for _, _, error in results if error]
    return {
        "models": [model | {"available": model["id"] in available[model["provider"]]
                           if available[model["provider"]] is not None else None} for model in MODELS],
        "default_model": DEFAULT_MODEL, "default_provider": "openai", "default_models": DEFAULT_MODELS,
        "error": " ".join(errors) or None,
    }
