class AppError(Exception):
    """Base class for expected, user-facing errors (mapped to HTTP 400)."""


class NoImageryError(AppError):
    """No cloud-free Sentinel-2 scenes were found for the requested AOI/date range."""


class AoiTooLargeError(AppError):
    """The AOI exceeds the configured max area for a synchronous request."""


class EarthEngineUnavailableError(AppError):
    """Earth Engine credentials are missing or invalid (mapped to HTTP 503)."""
