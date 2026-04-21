"""Shared utilities for agent modules."""

from __future__ import annotations

import asyncio
import functools
import os
from dataclasses import dataclass

from google.adk.models.lite_llm import LiteLlm

from mkb.config import settings
from mkb.db.engine import init_db


def ensure_llm_env() -> None:
    """Set OpenAI environment variables from settings if configured."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)


def create_llm(model: str | None = None) -> LiteLlm:
    """Create a LiteLlm instance with env setup."""
    ensure_llm_env()
    return LiteLlm(model=model or settings.extraction_model)


def sync_agent_run(async_fn):
    """Decorator that wraps an async agent function with init_db + asyncio.run."""
    @functools.wraps(async_fn)
    def wrapper(*args, **kwargs):
        init_db()
        return asyncio.run(async_fn(*args, **kwargs))
    return wrapper


@dataclass
class SpaceConfig:
    """Lightweight carrier for Space attributes needed by the projection agent."""
    domain: str
    system_prompt: str
    extraction_schema: dict
    field_descriptions: dict
