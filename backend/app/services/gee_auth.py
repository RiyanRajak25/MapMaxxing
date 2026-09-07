from pathlib import Path

import ee

from app import config
from app.exceptions import EarthEngineUnavailableError

_initialized = False


def initialize_earth_engine() -> None:
    """Initialize the Earth Engine client.

    Two credential modes are supported, in priority order:

    1. Service account - set GEE_SERVICE_ACCOUNT_EMAIL and GEE_SERVICE_ACCOUNT_KEY.
       Use this for deployment, where no human can complete a browser flow.
    2. User OAuth - run `earthengine authenticate` once; the refresh token is
       cached under ~/.config/earthengine/. This is the fast path for local work.

    Earth Engine has no API-key authentication. A Google Cloud `AIza...` key
    (Maps, Places, etc.) will not work here regardless of its permissions.

    Idempotent - safe to call on every app startup or script run.
    """
    global _initialized
    if _initialized:
        return

    if not config.GEE_PROJECT_ID:
        raise RuntimeError(
            "GEE_PROJECT_ID is not set in backend/.env. Earth Engine requires a registered "
            "Google Cloud project - see backend/SETUP.md."
        )

    if config.GEE_SERVICE_ACCOUNT_EMAIL and config.GEE_SERVICE_ACCOUNT_KEY:
        if not Path(config.GEE_SERVICE_ACCOUNT_KEY).exists():
            raise RuntimeError(
                f"Service account key not found at {config.GEE_SERVICE_ACCOUNT_KEY}. "
                "Fix the path in backend/.env, or clear the service account settings to fall "
                "back to `earthengine authenticate`."
            )
        credentials = ee.ServiceAccountCredentials(
            config.GEE_SERVICE_ACCOUNT_EMAIL, config.GEE_SERVICE_ACCOUNT_KEY
        )
        ee.Initialize(credentials, project=config.GEE_PROJECT_ID)
    else:
        # Picks up cached user credentials from `earthengine authenticate`.
        ee.Initialize(project=config.GEE_PROJECT_ID)

    _initialized = True


def require_earth_engine() -> None:
    """Guard for request handlers.

    Startup deliberately tolerates bad credentials so the server still boots and
    /health can explain the problem, which means request handlers have to check
    for themselves rather than assume initialization succeeded.
    """
    if _initialized:
        return
    try:
        initialize_earth_engine()
    except Exception as exc:
        raise EarthEngineUnavailableError(
            f"Earth Engine is not configured on the server ({exc}). "
            "Run 'earthengine authenticate' and set GEE_PROJECT_ID in backend/.env - "
            "see backend/SETUP.md - then confirm with 'python scripts/verify_gee_auth.py'."
        ) from exc
