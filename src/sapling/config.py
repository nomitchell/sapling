from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_catalog import DEFAULT_MODEL, preset

load_dotenv()


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    provider: Literal["openai"] = "openai"
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=120)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = "low"
    cadence: float = Field(default=0.5, ge=0, le=1)
    budget_total: float = Field(default=10, ge=0, le=1000000)
    permission_mode: Literal["ask", "balanced", "yolo"] = "balanced"
    execution_backend: Literal["docker", "process"] = os.environ.get("SAPLING_EXECUTION_BACKEND", "docker")
    max_concurrent_holons: int = Field(default=4, ge=1, le=32)
    max_depth: int = Field(default=5, ge=1, le=20)
    experiment_timeout: int = Field(default=300, ge=1, le=86400)
    max_output_tokens: int = Field(default=4096, ge=256, le=64000)
    max_turn_cost_usd: float = Field(default=1, gt=0, le=1000)
    input_cost_per_million: float = Field(default=0.20, ge=0, le=10000)
    cached_input_cost_per_million: float | None = Field(default=0.02, ge=0, le=10000)
    output_cost_per_million: float = Field(default=1.25, ge=0, le=10000)


    @model_validator(mode="after")
    def supported_reasoning(self):
        selected = preset(self.model)
        if selected and self.reasoning_effort not in selected["reasoning_efforts"]:
            raise ValueError(f"{self.model} supports reasoning levels: {', '.join(selected['reasoning_efforts'])}")
        return self


DATA_DIR = Path(os.environ.get("SAPLING_DATA_DIR", ".sapling")).resolve()
DATABASE_URL = os.environ.get(
    "SAPLING_DATABASE_URL", "postgresql+psycopg://sapling:sapling@127.0.0.1:54329/sapling"
)
