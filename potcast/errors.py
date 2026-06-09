"""Application-specific exceptions."""


class PotcastError(Exception):
    """Base class for expected Potcast errors."""


class ConfigError(PotcastError):
    """Raised when configuration is missing, malformed, or invalid."""


class StorageError(PotcastError):
    """Raised when runtime metadata cannot be read or written."""


class DownloadError(PotcastError):
    """Raised when episode media cannot be downloaded or safely replaced."""


class OutputBackendError(PotcastError):
    """Raised when an output backend cannot start, control, or stop playback."""
