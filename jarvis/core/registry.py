"""Agent registry: the plug-in spine of JARVIS.

Every capability (market regime, retention scan, pipeline analysis...) is an
Agent subclass exposing one or more *tools*. A tool is a plain Python function
with type hints and a docstring; the Brain hands these to Claude, the UI calls
them directly, and the scheduler runs them on a timer. Adding a new capability
= drop a new file in jarvis/agents/ and decorate the class with @register.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

_REGISTRY: dict[str, "Agent"] = {}


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str
    writes: bool = False  # True = side effects; Brain requires human approval


class Agent:
    """Base class. Subclasses set `name`/`description` and mark methods with @tool."""

    name: str = "agent"
    description: str = ""

    def __init__(self) -> None:
        self.tools: list[ToolSpec] = [
            ToolSpec(
                name=f"{self.name}__{attr}",
                fn=getattr(self, attr),
                description=inspect.getdoc(getattr(self, attr)) or attr,
                writes=getattr(getattr(self, attr), "_jarvis_writes", False),
            )
            for attr in dir(type(self))
            if getattr(getattr(type(self), attr, None), "_jarvis_tool", False)
        ]

    def health(self) -> dict[str, Any]:
        """Override to report data-source connectivity for the status board."""
        return {"ok": True}


def tool(writes: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark an Agent method as a tool. `writes=True` gates it behind approval."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._jarvis_tool = True  # type: ignore[attr-defined]
        fn._jarvis_writes = writes  # type: ignore[attr-defined]
        return fn

    return deco


def register(cls: type[Agent]) -> type[Agent]:
    inst = cls()
    _REGISTRY[inst.name] = inst
    return cls


def agents() -> dict[str, Agent]:
    return dict(_REGISTRY)


def all_tools() -> list[ToolSpec]:
    return [t for a in _REGISTRY.values() for t in a.tools]


def load_builtin_agents() -> dict[str, Agent]:
    """Import every module in jarvis.agents so their @register decorators run."""
    import importlib
    import pkgutil

    import jarvis.agents as pkg

    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{mod.name}")
    return agents()
