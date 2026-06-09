"""Application-specific exceptions."""


class PotcastError(Exception):
    """Base class for expected Potcast errors."""


class ConfigError(PotcastError):
    """Raised when configuration is missing, malformed, or invalid."""
