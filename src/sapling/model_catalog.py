"""Reviewed model presets; prices remain visible and editable by the user.

Prices are standard USD per million text tokens, verified 2026-09-19 against
the linked official model pages. Account availability is checked separately.
"""
DEFAULT_MODEL = "gpt-5.4-nano"
MODELS = [
    {"id": "gpt-5.4-nano", "label": "GPT-5.4 nano · low cost", "reasoning_efforts": ["none", "low", "medium", "high", "xhigh"], "input_cost_per_million": 0.20, "cached_input_cost_per_million": 0.02, "output_cost_per_million": 1.25},
    {"id": "gpt-5.4-mini", "label": "GPT-5.4 mini", "reasoning_efforts": ["none", "low", "medium", "high", "xhigh"], "input_cost_per_million": 0.75, "cached_input_cost_per_million": 0.075, "output_cost_per_million": 4.50},
    {"id": "gpt-5-mini", "label": "GPT-5 mini", "reasoning_efforts": ["minimal", "low", "medium", "high"], "input_cost_per_million": 0.25, "cached_input_cost_per_million": 0.025, "output_cost_per_million": 2.00},
]
for entry in MODELS:
    entry.update(price_verified_at="2026-09-19", source_url=f"https://developers.openai.com/api/docs/models/{entry['id']}")


def preset(model):
    return next((m for m in MODELS if model == m["id"] or model.startswith(m["id"] + "-20")), None)


def merge_settings(current, changes):
    if "model" in changes and changes["model"] != current.get("model"):
        selected = preset(changes["model"])
        rates = {
            key: selected[key] if selected else None if key == "cached_input_cost_per_million" else 0
            for key in ("input_cost_per_million", "cached_input_cost_per_million", "output_cost_per_million")
        }
        current = current | rates
    return current | changes
