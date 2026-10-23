"""The Brain: Claude with every registered agent tool attached.

Uses the Anthropic SDK tool runner, so the loop (call -> run tools -> feed back)
is handled by the SDK. Tools flagged `writes=True` are refused unless the
caller passes allow_writes=True (i.e. a human clicked "approve" in the UI).
"""
from __future__ import annotations

import json
from typing import Any, Callable

import anthropic
from anthropic import beta_tool

from jarvis.core import audit, privacy
from jarvis.core.config import settings
from jarvis.core.registry import ToolSpec, all_tools, load_builtin_agents

SYSTEM = """You are JARVIS, the operations co-pilot for a staff member at a retail FX/CFD brokerage.
You help with: market context, execution/bot failures, client retention, deal pipeline and
trader/IB sourcing, project tasks, inbox triage and the user's personal trading plans.

Rules:
- Use tools for any number you state; never invent figures. If a tool errors, say so.
- Lead with the answer, then the 3-5 actions that matter most, each with an owner where known.
- You never place trades, move money or send emails. You draft; a human approves.
- Personal trading: respect the risk plan output exactly; flag that personal-account-dealing
  rules may apply. Client data is confidential - do not echo more personal data than needed."""


def _wrap(spec: ToolSpec, allow_writes: bool) -> Any:
    def call(**kwargs: Any) -> str:
        if spec.writes and not allow_writes:
            return json.dumps({"error": "write action requires human approval in the UI"})
        try:
            result = spec.fn(**kwargs)
            audit.log("jarvis", "tool_call", {"tool": spec.name, "args": kwargs})
            return json.dumps(privacy.for_claude(result), default=str)[:60000]  # client PII masked
        except Exception as e:  # surface to the model instead of crashing the loop
            audit.log("jarvis", "tool_error", {"tool": spec.name, "args": kwargs, "error": str(e)})
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    # Give the wrapper the real function's signature so the SDK builds the schema from it.
    call.__signature__ = __import__("inspect").signature(spec.fn)  # type: ignore[attr-defined]
    call.__annotations__ = {k: v for k, v in getattr(spec.fn, "__annotations__", {}).items() if k != "return"}
    call.__doc__ = spec.description
    return beta_tool(call, name=spec.name, description=spec.description.split("\n\n")[0])


class Brain:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        load_builtin_agents()
        self.client = client or anthropic.Anthropic()

    def ask(self, messages: list[dict[str, Any]], user: str = "user", allow_writes: bool = False,
            on_step: Callable[[str], None] | None = None, system: str | None = None) -> str:
        audit.log(user, "ask", {"prompt": messages[-1]["content"] if messages else ""})
        tools = [_wrap(t, allow_writes) for t in all_tools()]
        try:
            runner = self.client.beta.messages.tool_runner(
                model=settings.model,
                max_tokens=16000,
                system=system or SYSTEM,
                tools=tools,
                messages=messages,
                max_iterations=settings.max_tool_turns,
                output_config={"effort": settings.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            final = None
            for msg in runner:
                final = msg
                if on_step:
                    for b in msg.content:
                        if b.type == "tool_use":
                            on_step(b.name)
        except anthropic.AuthenticationError:
            return "Claude API key missing or invalid - set ANTHROPIC_API_KEY."
        except anthropic.RateLimitError:
            return "Rate limited by the Claude API - try again in a minute."
        except anthropic.APIConnectionError:
            return "Cannot reach the Claude API (network)."
        except anthropic.APIStatusError as e:
            return f"Claude API error {e.status_code}: {e.message}"

        if final is None:
            return "No response."
        if final.stop_reason == "refusal":
            return "The request was declined by the model's safety system. Rephrase or narrow it."
        text = "\n".join(b.text for b in final.content if b.type == "text").strip()
        if final.stop_reason == "max_tokens":
            text += "\n\n_(answer truncated)_"
        audit.log("jarvis", "answer", {"text": text[:4000]})
        return text or "(no text answer)"
