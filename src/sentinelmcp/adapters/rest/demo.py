"""Runnable in-memory Phase 3 demo gateway."""

from sentinelmcp.adapters.rest.app import create_execution_app
from sentinelmcp.application import create_demo_runtime
from sentinelmcp.policy import DenyAllAuthenticator

runtime = create_demo_runtime()
app = create_execution_app(runtime.executor, DenyAllAuthenticator())

__all__ = ["app", "runtime"]
