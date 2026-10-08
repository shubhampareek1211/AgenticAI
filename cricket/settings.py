"""Validated harness configuration; database setup remains an explicit operation."""

import os
from dataclasses import dataclass

from cricket.db import ConfigurationError


@dataclass(frozen=True)
class HarnessSettings:
    model: str = "vertex_ai/gemini-3.5-flash-lite"
    vertex_location: str = "global"
    max_tool_rounds: int = 5
    context_token_budget: int = 12000
    model_timeout_seconds: int = 60

    def __post_init__(self):
        if not self.model or not self.vertex_location:
            raise ConfigurationError("Set GEMINI_MODEL and VERTEX_LOCATION to nonempty values.")
        if not 1 <= self.max_tool_rounds <= 5:
            raise ConfigurationError("MAX_TOOL_ROUNDS must be between 1 and 5.")
        if self.context_token_budget < 256:
            raise ConfigurationError("CONTEXT_TOKEN_BUDGET must be at least 256.")
        if self.model_timeout_seconds < 1:
            raise ConfigurationError("MODEL_TIMEOUT_SECONDS must be positive.")

    @classmethod
    def from_env(cls):
        """Read documented environment variables without loading a .env file."""
        try:
            return cls(
                model=os.environ.get("GEMINI_MODEL", cls.model),
                vertex_location=os.environ.get("VERTEX_LOCATION", cls.vertex_location),
                max_tool_rounds=int(os.environ.get("MAX_TOOL_ROUNDS", "5")),
                context_token_budget=int(os.environ.get("CONTEXT_TOKEN_BUDGET", "12000")),
                model_timeout_seconds=int(os.environ.get("MODEL_TIMEOUT_SECONDS", "60")),
            )
        except ValueError as exc:
            if isinstance(exc, ConfigurationError):
                raise
            raise ConfigurationError("Harness limits and timeouts must be integers.") from None
