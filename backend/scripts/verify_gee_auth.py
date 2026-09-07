"""Standalone check that Earth Engine credentials in backend/.env work.

Run from the backend/ directory:
    python scripts/verify_gee_auth.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app import config  # noqa: E402
from app.services.gee_auth import initialize_earth_engine  # noqa: E402


def main() -> int:
    using_service_account = bool(
        config.GEE_SERVICE_ACCOUNT_EMAIL and config.GEE_SERVICE_ACCOUNT_KEY
    )
    print(f"Project id:   {config.GEE_PROJECT_ID or '(missing)'}")
    print(f"Auth mode:    {'service account' if using_service_account else 'user login'}")
    if using_service_account:
        print(f"Account:      {config.GEE_SERVICE_ACCOUNT_EMAIL}")
        print(f"Key file:     {config.GEE_SERVICE_ACCOUNT_KEY}")
        if not Path(config.GEE_SERVICE_ACCOUNT_KEY).exists():
            print(f"\nFAILED: key file not found at {config.GEE_SERVICE_ACCOUNT_KEY}")
            return 1

    try:
        initialize_earth_engine()
    except Exception as exc:
        print(f"\nFAILED to initialize Earth Engine:\n  {exc}")
        if not using_service_account:
            print("\nIf you have not logged in yet, run:  earthengine authenticate")
        return 1

    print("\nInitialized. Running a live query against the Sentinel-2 archive...")
    try:
        count = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate("2024-01-01", "2024-01-31")
            .filterBounds(ee.Geometry.Point([77.4126, 23.2599]))  # Bhopal
            .size()
            .getInfo()
        )
    except Exception as exc:
        print(f"\nFAILED to query Earth Engine:\n  {exc}")
        print("\nMost common cause: the Cloud project is not registered for Earth Engine.")
        print("Register it at https://code.earthengine.google.com/register - see backend/SETUP.md.")
        return 1

    print(f"SUCCESS: found {count} Sentinel-2 scenes over Bhopal in January 2024.")
    print("Earth Engine is correctly configured.")
    print("\nNext: python scripts/run_bhopal.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
