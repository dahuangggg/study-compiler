class LearningWikiError(Exception):
    """Base error for expected user-facing failures."""


class ValidationError(LearningWikiError):
    """Raised when external input violates a public contract."""


class StateError(LearningWikiError):
    """Raised when vault state prevents an operation."""
