"""
Generic agent runner wrapping google-adk Runner + InMemorySessionService.

Extracted from extraction.py to be reused by review, projection,
and feedback agents.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from google.adk.agents import Agent
from google.adk.runners import RunConfig, Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


def _is_retryable_provider_error(error: str) -> bool:
    """Heuristic for transient provider-side errors worth retrying."""
    e = error.lower()
    retry_markers = [
        "input length",
        "badrequesterror",
        "ratelimiterror",
        "rate limit",
        "too many requests",
        "quota",
        "token-limit",
        "temporar",
        "timeout",
        "429",
        "jsondecodeerror",
        "expecting ',' delimiter",
        "expecting property name enclosed in double quotes",
        "unterminated string",
        "extra data",
        "tool call arguments",
    ]
    return any(marker in e for marker in retry_markers)


@dataclass
class RunResult:
    """Result of an agent run."""
    final_text: str = ""
    events_collected: list = field(default_factory=list)
    success: bool = True
    error: str | None = None


class AgentRunner:
    """Reusable wrapper around google-adk Runner + InMemorySessionService."""

    def __init__(self, agent: Agent, app_name: str, max_llm_calls: int = 40):
        self.agent = agent
        self.app_name = app_name
        self.max_llm_calls = max_llm_calls
        self.session_service = InMemorySessionService()
        self.runner = Runner(
            agent=agent,
            app_name=app_name,
            session_service=self.session_service,
        )

    async def create_session(self, session_id: str, user_id: str = "mkb_system"):
        """Create a new session."""
        return await self.session_service.create_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )

    async def run(
        self,
        session_id: str,
        message: str,
        user_id: str = "mkb_system",
        verbose: bool = False,
        progress_callback=None,
        max_retries: int | None = None,
        retry_delay: float = 5.0,
    ) -> RunResult:
        """Send a message and collect all events. Returns RunResult.

        Retries up to *max_retries* times on transient provider errors such as
        the Qwen/OpenAI 400 "Range of input length" error which sometimes fires
        spuriously even when the request is within limits.
        """
        if max_retries is None:
            from mkb.runtime_settings import get_setting

            max_retries = max(1, int(get_setting("agent_retry_count")))

        for attempt in range(1, max_retries + 1):
            result = await self._run_once(
                session_id=session_id,
                message=message,
                user_id=user_id,
                verbose=verbose,
                progress_callback=progress_callback,
            )
            if result.success:
                return result
            # Retry on transient/misclassified provider errors.
            error_str = result.error or ""
            if _is_retryable_provider_error(error_str):
                if attempt < max_retries:
                    if progress_callback:
                        progress_callback(
                            {
                                "label": "retry",
                                "message": (
                                    f"Transient model error on attempt {attempt}/{max_retries}: "
                                    f"{error_str[:180]}. Retrying in {retry_delay:.1f}s."
                                ),
                                "stage": "retry",
                            }
                        )
                    logger.warning(
                        "Transient provider error on attempt %d/%d: %s — retrying in %.1fs",
                        attempt, max_retries, error_str[:200], retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
            # Non-retryable error or retries exhausted
            return result
        # Should not be reached, but return last result just in case
        return result  # type: ignore[return-value]

    async def _run_once(
        self,
        session_id: str,
        message: str,
        user_id: str = "mkb_system",
        verbose: bool = False,
        progress_callback=None,
    ) -> RunResult:
        initial_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=message)],
        )

        result = RunResult()
        text_parts_seen: list[str] = []

        try:
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=initial_message,
                run_config=RunConfig(max_llm_calls=getattr(self, "max_llm_calls", 40)),
            ):
                if progress_callback and event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.function_call:
                            name = part.function_call.name or "tool"
                            args = dict(part.function_call.args or {})
                            progress_callback(
                                {
                                    "tool": name,
                                    "label": name,
                                    "message": f"Agent called {name}",
                                    "stage": "tool_call",
                                    "payload": args,
                                }
                            )
                        elif getattr(part, "function_response", None):
                            resp = part.function_response
                            response_name = getattr(resp, "name", None) or "tool"
                            payload = getattr(resp, "response", None)
                            if isinstance(payload, dict):
                                if payload.get("error"):
                                    message = f"{response_name} error: {str(payload['error'])[:220]}"
                                else:
                                    message = f"{response_name} returned data"
                            else:
                                preview = str(payload)[:220] if payload is not None else "no data"
                                message = f"{response_name} result: {preview}"
                            progress_callback(
                                {
                                    "tool": response_name,
                                    "label": response_name,
                                    "message": message,
                                    "stage": "tool_result",
                                    "payload": (
                                        payload
                                        if isinstance(payload, (dict, list, str, int, float, bool)) or payload is None
                                        else str(payload)
                                    ),
                                }
                            )
                        elif part.text:
                            text = part.text.strip()
                            if text:
                                text_parts_seen.append(text)
                                progress_callback(
                                    {
                                        "label": "agent",
                                        "message": text[:1200],
                                        "stage": "agent_text",
                                    }
                                )
                if verbose or logger.isEnabledFor(logging.DEBUG):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                logger.debug("Agent text: %s", part.text[:500])
                            if part.function_call:
                                logger.debug(
                                    "Tool call: %s(%s)",
                                    part.function_call.name,
                                    list(part.function_call.args.keys()) if part.function_call.args else [],
                                )
                            if getattr(part, "function_response", None):
                                resp = part.function_response
                                logger.debug(
                                    "Tool result: %s -> %s",
                                    getattr(resp, "name", "?"),
                                    str(getattr(resp, "response", ""))[:500],
                                )
                result.events_collected.append(event)
                if event.is_final_response() and event.content and event.content.parts:
                    result.final_text = "\n".join(
                        p.text for p in event.content.parts if p.text
                    )

            # Some providers stream text but do not consistently flag a final
            # response event. Fall back to the latest streamed text so the UI
            # can still display an assistant reply.
            if not result.final_text and text_parts_seen:
                result.final_text = text_parts_seen[-1]
        except Exception as exc:
            logger.error("Agent run failed: %s", exc)
            result.success = False
            result.error = f"{type(exc).__name__}: {exc}"

        return result
