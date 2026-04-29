"""Shared utilities for agent modules."""

from __future__ import annotations

import asyncio
import functools
import os
import threading
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


_loop_lock = threading.Lock()
_loop_ready = threading.Event()
_loop: asyncio.AbstractEventLoop | None = None


def _loop_thread_main() -> None:
    """Run a dedicated event loop forever for sync->async bridging."""
    global _loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _loop = loop
    _loop_ready.set()
    loop.run_forever()


def _get_or_start_loop() -> asyncio.AbstractEventLoop:
    """Return the shared background loop, starting it once on first use."""
    global _loop
    if _loop and _loop.is_running():
        return _loop

    with _loop_lock:
        if _loop and _loop.is_running():
            return _loop
        _loop_ready.clear()
        t = threading.Thread(target=_loop_thread_main, name="mkb-agent-loop", daemon=True)
        t.start()

    _loop_ready.wait(timeout=5)
    if not _loop or not _loop.is_running():
        raise RuntimeError("Failed to initialize shared async loop for agent execution")
    return _loop


def run_async_sync(coro):
    """Execute a coroutine from sync code on a stable, shared event loop."""
    loop = _get_or_start_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def sync_agent_run(async_fn):
    """Decorator that wraps an async agent function with init_db + asyncio.run."""
    @functools.wraps(async_fn)
    def wrapper(*args, **kwargs):
        init_db()
        return run_async_sync(async_fn(*args, **kwargs))
    return wrapper


@dataclass
class SpaceConfig:
    """Lightweight carrier for Space attributes needed by the projection agent."""
    domain: str
    system_prompt: str
    extraction_schema: dict
    field_descriptions: dict
