"""
Generic agent runner wrapping google-adk Runner + InMemorySessionService.

Extracted from extraction.py to be reused by review, projection,
and feedback agents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Result of an agent run."""
    final_text: str = ""
    events_collected: list = field(default_factory=list)
    success: bool = True
    error: str | None = None


class AgentRunner:
    """Reusable wrapper around google-adk Runner + InMemorySessionService."""

    def __init__(self, agent: Agent, app_name: str):
        self.agent = agent
        self.app_name = app_name
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
    ) -> RunResult:
        """Send a message and collect all events. Returns RunResult."""
        initial_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=message)],
        )

        result = RunResult()

        try:
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=initial_message,
            ):
                if progress_callback and event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.function_call:
                            name = part.function_call.name or "tool"
                            progress_callback(
                                {
                                    "tool": name,
                                    "label": name,
                                    "message": f"Agent called {name}",
                                    "stage": "tool_call",
                                }
                            )
                        elif part.text:
                            text = part.text.strip()
                            if text:
                                progress_callback(
                                    {
                                        "label": "agent",
                                        "message": text[:240],
                                        "stage": "agent_text",
                                    }
                                )
                if verbose and event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            logger.info("Agent: %s", part.text[:200])
                        if part.function_call:
                            logger.info(
                                "Tool call: %s(%s)",
                                part.function_call.name,
                                list(part.function_call.args.keys()) if part.function_call.args else [],
                            )
                result.events_collected.append(event)
                if event.is_final_response() and event.content and event.content.parts:
                    result.final_text = "\n".join(
                        p.text for p in event.content.parts if p.text
                    )
        except Exception as exc:
            logger.error("Agent run failed: %s", exc)
            result.success = False
            result.error = str(exc)

        return result
