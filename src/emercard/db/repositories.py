"""Shared repository exceptions for safe service-layer mapping."""


class RepositoryError(Exception):
    """Base class for persistence failures that services may map safely."""


class RepositoryConflictError(RepositoryError):
    """A unique persistence constraint rejected an operation."""


class InvalidIdentifierError(RepositoryError):
    """A caller supplied an invalid MongoDB identifier."""
