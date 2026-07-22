"""Stable exception hierarchy for the public MKB Python SDK."""


class MKBError(Exception):
    """Base class for supported SDK errors."""


class NotFoundError(MKBError):
    """A requested resource does not exist."""


class ConflictError(MKBError):
    """An operation conflicts with current persisted state."""


class ValidationError(MKBError):
    """Caller input does not satisfy the public contract."""


class BackendUnavailableError(MKBError):
    """A configured persistence or execution backend is unavailable."""


class ProviderError(MKBError):
    """An external model or processing provider failed."""


class PipelineExecutionError(MKBError):
    """A pipeline could not complete successfully."""

    def __init__(self, message: str, *, run=None, step_run=None):
        super().__init__(message)
        self.run = run
        self.step_run = step_run
