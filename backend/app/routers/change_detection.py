import ee
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import gettempdir
from threading import Lock
from uuid import uuid4
from zipfile import ZipFile
from io import BytesIO
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.exceptions import AppError, EarthEngineUnavailableError
from app.models.schemas import ChangeDetectionRequest, ChangeDetectionResponse
from app.services.change_detection import run_change_detection

router = APIRouter(prefix="/api", tags=["change-detection"])

EARTH_ENGINE_DOWNLOAD_HOST = "earthengine.googleapis.com"
EXPORT_DIR = Path(gettempdir()) / "urban-change-exports"
EXPORT_DIR.mkdir(exist_ok=True)
EXPORT_JOBS: dict[str, dict[str, str]] = {}
EXPORT_LOCK = Lock()
EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=2)
LAYER_DOWNLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=5)
LAYER_DOWNLOAD_TIMEOUT_SECONDS = 120


class ShapefileExportRequest(BaseModel):
    export_urls: dict[str, str]


# Defined with `def` rather than `async def` so FastAPI runs this blocking
# Earth Engine work in a threadpool instead of stalling the event loop.
@router.post("/change-detection", response_model=ChangeDetectionResponse)
def change_detection(request: ChangeDetectionRequest) -> ChangeDetectionResponse:
    try:
        result = run_change_detection(
            aoi_geojson=request.aoi.model_dump(),
            year1=request.year1,
            year2=request.year2,
            cloud_threshold=request.cloud_threshold,
            include_wetland=request.include_wetland,
        )
    except EarthEngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ee.EEException as exc:
        raise HTTPException(
            status_code=502, detail=f"Earth Engine request failed: {exc}"
        ) from exc

    return ChangeDetectionResponse(**result)


def _download_layer(layer_name: str, export_url: str) -> tuple[str, bytes]:
    parsed_url = urlparse(export_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != EARTH_ENGINE_DOWNLOAD_HOST:
        raise ValueError(f"Invalid Earth Engine export URL for {layer_name}")
    request = Request(export_url, headers={"User-Agent": "urban-change-detection/1.0"})
    with urlopen(request, timeout=LAYER_DOWNLOAD_TIMEOUT_SECONDS) as download:
        return layer_name, download.read()


def _fetch_export(job_id: str, export_urls: dict[str, str]) -> None:
    file_path = EXPORT_DIR / f"{job_id}.zip"
    try:
        with EXPORT_LOCK:
            EXPORT_JOBS[job_id] = {"status": "preparing", "detail": "Downloading vector layers"}
        downloads = {
            future: layer_name
            for layer_name, export_url in export_urls.items()
            for future in [LAYER_DOWNLOAD_EXECUTOR.submit(_download_layer, layer_name, export_url)]
        }
        layer_archives = {}
        for future in as_completed(downloads):
            layer_name, archive = future.result()
            layer_archives[layer_name] = archive
            with EXPORT_LOCK:
                EXPORT_JOBS[job_id] = {
                    "status": "preparing",
                    "detail": f"Downloaded {len(layer_archives)} of {len(export_urls)} layers",
                }

        with ZipFile(file_path, "w") as output_zip:
            for layer_name, archive in layer_archives.items():
                layer_zip = BytesIO(archive)
                with ZipFile(layer_zip) as source_zip:
                    for member in source_zip.infolist():
                        if member.is_dir():
                            continue
                        suffix = Path(member.filename).suffix.lower()
                        if suffix in {".shp", ".shx", ".dbf", ".prj", ".cpg"}:
                            output_zip.writestr(f"{layer_name}{suffix}", source_zip.read(member))
    except Exception as exc:
        with EXPORT_LOCK:
            EXPORT_JOBS[job_id] = {"status": "error", "detail": f"Earth Engine export failed: {exc}"}
        return

    with EXPORT_LOCK:
        EXPORT_JOBS[job_id] = {"status": "ready", "filename": file_path.name}


@router.post("/export-shapefile")
def start_shapefile_export(request: ShapefileExportRequest) -> dict[str, str]:
    """Start downloading an Earth Engine ZIP without blocking the browser."""
    job_id = uuid4().hex
    with EXPORT_LOCK:
        EXPORT_JOBS[job_id] = {"status": "preparing"}
    EXPORT_EXECUTOR.submit(_fetch_export, job_id, request.export_urls)
    return {"job_id": job_id, "status": "preparing"}


@router.get("/export-shapefile/{job_id}")
def shapefile_export_status(job_id: str) -> dict[str, str]:
    with EXPORT_LOCK:
        job = EXPORT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job["status"] == "ready":
        return {"status": "ready", "download_url": f"/api/export-shapefile/{job_id}/download"}
    return job


@router.get("/export-shapefile/{job_id}/download")
def download_shapefile_export(job_id: str) -> FileResponse:
    with EXPORT_LOCK:
        job = EXPORT_JOBS.get(job_id)
    if job is None or job["status"] != "ready":
        raise HTTPException(status_code=404, detail="Export is not ready")
    return FileResponse(
        EXPORT_DIR / job["filename"],
        media_type="application/zip",
        filename="urban_change_layers.zip",
    )
