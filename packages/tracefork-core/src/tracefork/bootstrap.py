"""Process bootstrap for `tracefork record` subprocess execution.

Target scripts run by `tracefork record python script.py` import their
instrumentation from here::

    from tracefork.bootstrap import tools

    @tools.tool()
    async def my_tool(...): ...

This is an explicit, documented service locator for subprocess and CLI
replay usage — the SDK API itself stays dependency-injected (no hidden
global registries in the core engine).
"""

from typing import Any

from tracefork.adapters import ToolBox
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime

_registry: BoundaryRegistry | None = None
_runtime: BoundaryRuntime | None = None
_tools: ToolBox | None = None


def registry() -> BoundaryRegistry:
    """The process-wide boundary registry."""
    global _registry
    if _registry is None:
        _registry = BoundaryRegistry()
    return _registry


def runtime() -> BoundaryRuntime:
    """The process-wide boundary runtime."""
    global _runtime
    if _runtime is None:
        _runtime = BoundaryRuntime(registry=registry())
    return _runtime


def toolbox() -> ToolBox:
    """The process-wide ToolBox, created on first use."""
    global _tools
    if _tools is None:
        _tools = ToolBox(runtime())
    return _tools


class _LazyToolBox:
    """Forwards attribute access to the process-wide :class:`ToolBox`."""

    def __getattr__(self, name: str) -> Any:
        return getattr(toolbox(), name)


tools = _LazyToolBox()
