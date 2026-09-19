from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_catalog import DEFAULT_MODEL, DEFAULT_MODELS, MODELS, preset

load_dotenv()


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    provider: Literal["openai", "baseten"] = "openai"
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=120)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = "low"
    cadence: float = Field(default=0.45, ge=0, le=1)
    budget_total: float = Field(default=10, ge=0, le=1000000)
    permission_mode: Literal["ask", "balanced", "yolo"] = "balanced"
    execution_backend: Literal["docker", "process"] = os.environ.get("SAPLING_EXECUTION_BACKEND", "docker")
    max_concurrent_holons: int = Field(default=4, ge=1, le=32)
    max_depth: int | None = Field(default=None, ge=1, le=20)
    experiment_timeout: int = Field(default=300, ge=1, le=86400)
    # Research coordinators return both high-reasoning deliberation and a
    # complete structured decision. Eight thousand tokens is routinely too
    # small for that combination on reasoning models.
    max_output_tokens: int = Field(default=32768, ge=256, le=64000)
    max_turn_cost_usd: float = Field(default=1, gt=0, le=1000)
    input_cost_per_million: float = Field(default=0.20, ge=0, le=10000)
    cached_input_cost_per_million: float | None = Field(default=0.02, ge=0, le=10000)
    output_cost_per_million: float = Field(default=1.25, ge=0, le=10000)
    @model_validator(mode="before")
    @classmethod
    def provider_defaults(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        provider = values.get("provider", "openai")
        values.setdefault("model", DEFAULT_MODELS.get(provider, DEFAULT_MODEL))
        selected = preset(values["model"], provider)
        if selected:
            for key in ("input_cost_per_million", "cached_input_cost_per_million", "output_cost_per_million"):
                values.setdefault(key, selected[key])
            values.setdefault("reasoning_effort", selected["default_reasoning_effort"])
        else:
            # A custom identifier must not silently inherit another model's price.
            values.setdefault("input_cost_per_million", 0)
            values.setdefault("output_cost_per_million", 0)
            values.setdefault("cached_input_cost_per_million", None)
            if provider == "baseten":
                values.setdefault("reasoning_effort", None)
        return values

    @model_validator(mode="after")
    def supported_reasoning(self):
        selected = preset(self.model, self.provider)
        if not selected and any(m["id"] == self.model for m in MODELS):
            raise ValueError(f"{self.model} is not a {self.provider} model preset")
        if selected and self.reasoning_effort not in selected["reasoning_efforts"]:
            raise ValueError(f"{self.model} supports reasoning levels: {', '.join(selected['reasoning_efforts'])}")
        if not selected and self.provider == "baseten" and self.reasoning_effort is not None:
            raise ValueError("Custom Baseten models require provider-default reasoning until capabilities are reviewed")
        return self


DATA_DIR = Path(os.environ.get("SAPLING_DATA_DIR", ".sapling")).resolve()
DATABASE_URL = os.environ.get(
    "SAPLING_DATABASE_URL", "postgresql+psycopg://sapling:sapling@127.0.0.1:54329/sapling"
)
