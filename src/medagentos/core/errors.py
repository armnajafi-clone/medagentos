"""Error taxonomy.

ERROR_HANDLING.md defines three categories and requires every error to carry an
error code, a message and a trace id. That contract is expressed here once, so
the API layer can translate any error into a response without knowing what went
wrong.
"""

from __future__ import annotations

from typing import Any


class MedAgentError(Exception):
    """Base class for every error the runtime raises deliberately.

    Anything that escapes as a bare ``Exception`` is a bug, not a category.
    """

    category: str = "system"
    code: str = "internal_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        trace_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.trace_id = trace_id
        self.details = details or {}

    def with_trace(self, trace_id: str) -> MedAgentError:
        """Attach the run id this error belongs to, if it was not known at raise time."""
        self.trace_id = trace_id
        return self

    def to_dict(self) -> dict[str, Any]:
        """The wire shape required by ERROR_HANDLING.md."""
        return {
            "error": {
                "category": self.category,
                "code": self.code,
                "message": self.message,
                "trace_id": self.trace_id,
                "details": self.details,
            }
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# -- API errors: the caller sent something invalid -------------------------


class ApiError(MedAgentError):
    category = "api"
    code = "bad_request"
    http_status = 400


class ValidationError(ApiError):
    code = "validation_failed"
    http_status = 422


class NotFoundError(ApiError):
    code = "not_found"
    http_status = 404


class ConflictError(ApiError):
    code = "conflict"
    http_status = 409


# -- Workflow errors: execution failed -------------------------------------


class WorkflowError(MedAgentError):
    category = "workflow"
    code = "workflow_failed"
    http_status = 500


class WorkflowNotFoundError(WorkflowError):
    code = "workflow_not_found"
    http_status = 404


class StepError(WorkflowError):
    """A single step failed. Carries the step name so the trace stays readable."""

    code = "step_failed"

    def __init__(self, step: str, message: str, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.step = step
        self.details.setdefault("step", step)


class CapabilityNotFoundError(WorkflowError):
    code = "capability_not_found"
    http_status = 404


class ToolContractError(WorkflowError):
    """A tool was called with inputs its contract rejects, or returned the wrong shape."""

    code = "tool_contract_violation"
    http_status = 422


class ApprovalRequired(WorkflowError):
    """Not a failure: the run reached a human review node and paused.

    Raised as a control-flow signal so a step can stop a run from deep inside
    plugin code without every intermediate frame having to know about pausing.
    """

    code = "approval_required"
    http_status = 202

    def __init__(self, step: str, message: str = "human approval required", **kwargs: Any):
        super().__init__(message, **kwargs)
        self.step = step
        self.details.setdefault("step", step)


# -- System errors: infrastructure failed ----------------------------------


class SystemError_(MedAgentError):
    """Database, storage or external system failure. Named with a trailing
    underscore to avoid shadowing the builtin ``SystemError``."""

    category = "system"
    code = "system_error"
    http_status = 500


class StorageError(SystemError_):
    code = "storage_error"


class RepositoryError(SystemError_):
    code = "repository_error"
