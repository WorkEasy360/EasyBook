"""Registry of narrow, READ-ONLY tools (phase sections 19-22).

There is no write-capable tool and no SQL/query tool — `register()` refuses
a tool not flagged read_only, and ai/tests/test_tools_registry.py asserts the
registered names contain nothing that posts, pays, reconciles, files, sends
or deletes, and nothing that accepts SQL.
"""

from ai.tools.base import Tool

_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if not tool.read_only:
        raise ValueError(f"Phase 10 AI tools must be read-only: {tool.name}")
    if not tool.required_permissions:
        raise ValueError(f"AI tool {tool.name} must declare the permissions it requires.")
    if tool.name in _REGISTRY:
        raise ValueError(f"Duplicate AI tool: {tool.name}")
    _REGISTRY[tool.name] = tool
    return tool


def _load():
    # Import-time registration; imported lazily to keep ai.tools importable
    # without pulling every domain module at Django app-loading time.
    from ai.tools import documents, financial, records  # noqa: F401


def get_tool(name: str) -> Tool | None:
    _load()
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    _load()
    return sorted(_REGISTRY.values(), key=lambda tool: tool.name)
