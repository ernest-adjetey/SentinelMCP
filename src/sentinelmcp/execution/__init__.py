"""Transport-neutral execution models, bindings, and adapters."""

from sentinelmcp.execution.bindings import (
    BindingNotFoundError,
    InMemoryRuntimeBindingRegistry,
    RuntimeBindingResolver,
    RuntimeToolBinding,
)
from sentinelmcp.execution.http import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    HTTPToolAdapter,
    HTTPToolTarget,
)
from sentinelmcp.execution.models import (
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSuccess,
    InvalidExecutionRequestError,
    RequestId,
)
from sentinelmcp.execution.protocol import ToolAdapter
from sentinelmcp.execution.synthetic import SyntheticHandler, SyntheticToolAdapter

__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "AdapterFailure",
    "AdapterInvocation",
    "AdapterResult",
    "AdapterSuccess",
    "BindingNotFoundError",
    "ExecutionContext",
    "ExecutionError",
    "ExecutionErrorCode",
    "ExecutionFailure",
    "ExecutionId",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionSuccess",
    "HTTPToolAdapter",
    "HTTPToolTarget",
    "InMemoryRuntimeBindingRegistry",
    "InvalidExecutionRequestError",
    "RequestId",
    "RuntimeBindingResolver",
    "RuntimeToolBinding",
    "SyntheticHandler",
    "SyntheticToolAdapter",
    "ToolAdapter",
]
