"""Expected failures raised by the internal diagnostic engine."""


class DiagnosticEngineError(RuntimeError):
    """Base class for failures callers may handle without exposing a traceback."""


class DiagnosticNotFoundError(LookupError, DiagnosticEngineError):
    """The requested diagnostic session, task, response, or result does not exist."""


class InvalidTransitionError(DiagnosticEngineError):
    """A state transition is not valid from the persisted state."""


class DiagnosticIdempotencyConflictError(DiagnosticEngineError):
    """An idempotency key was reused with a different operation payload."""


class DiagnosticConcurrencyError(DiagnosticEngineError):
    """Another local writer prevented the operation from acquiring SQLite safely."""


class CandidateUnavailableError(DiagnosticEngineError):
    """No compatible candidate can be materialised for the requested task."""


class EvaluationConflictError(DiagnosticEngineError):
    """An attempt or append-only correction conflicts with persisted history."""


class InvalidSubmissionContractError(DiagnosticEngineError):
    """A response does not match the versioned contract of its presented task."""
