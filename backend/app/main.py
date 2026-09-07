import logging
from contextlib import asynccontextmanager

import ee
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.models.schemas import HealthResponse
from app.routers.change_detection import router as change_detection_router
from app.services.gee_auth import initialize_earth_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Credential problems are logged rather than raised so the server still
    # boots and /health can report exactly what is wrong. Failing to start
    # would leave no way to diagnose the setup.
    try:
        initialize_earth_engine()
        logger.info("Earth Engine initialized.")
    except Exception as exc:
        logger.error("Earth Engine initialization failed: %s", exc)
        logger.error("See backend/SETUP.md and run: python scripts/verify_gee_auth.py")
    yield


app = FastAPI(
    title="Urban Change Detection API",
    description="AMCBI-based built-up change detection over Copernicus Sentinel-2 imagery.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(change_detection_router)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Confirm the API is up and that Earth Engine credentials actually work."""
    try:
        value = ee.Number(1).add(1).getInfo()
        return HealthResponse(status="ok", earth_engine="connected", detail={"test_computation": value})
    except Exception as exc:
        return HealthResponse(status="degraded", earth_engine="unavailable", detail=str(exc))
