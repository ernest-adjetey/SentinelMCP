"""Runnable in-memory Phase 3 demo gateway."""

from sentinelmcp.adapters.rest.app import create_execution_app
from sentinelmcp.application import create_demo_runtime

runtime = create_demo_runtime()
app = create_execution_app(runtime.executor)

__all__ = ["app", "runtime"]
