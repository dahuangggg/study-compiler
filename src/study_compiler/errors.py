class StudyCompilerError(Exception):
    """Base error for expected user-facing failures."""


class ValidationError(StudyCompilerError):
    """Raised when external input violates a public contract."""


class StateError(StudyCompilerError):
    """Raised when workspace state prevents an operation."""
