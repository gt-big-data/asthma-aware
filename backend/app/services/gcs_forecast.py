"""Cloud Storage publication and reads for dashboard forecasts."""
import logging
import json
from collections import OrderedDict
from threading import Lock
from functools import lru_cache
from pathlib import Path
from tempfile import gettempdir
from uuid import UUID, uuid4

from google.api_core.exceptions import GoogleAPIError, PreconditionFailed
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage
from pydantic import AwareDatetime, BaseModel

from app.services.geos_dashboard import ForecastUnavailable, PublishedForecast

CURRENT = "published/current.json"
logger = logging.getLogger(__name__)


class Publication(BaseModel):
    publication_id: UUID
    issued_at: AwareDatetime
    has_downscaled_layers: bool = False
    has_risk: bool = False

    @property
    def artifact(self) -> str:
        return f"published/{self.publication_id}/forecast.json"


@lru_cache(maxsize=1)
def get_bucket(name: str) -> storage.Bucket:
    return storage.Client().bucket(name)


@lru_cache(maxsize=4)
def read_artifact(bucket_name: str, publication_id: UUID) -> PublishedForecast:
    blob = get_bucket(bucket_name).blob(f"published/{publication_id}/forecast.json")
    run = PublishedForecast.model_validate_json(blob.download_as_bytes(timeout=10))
    if run.risk_artifact is not None:
        run = run.model_copy(update={"risk_artifact": f"published/{publication_id}/risk.npz"})
    return run


def load_gcs_run(bucket_name: str) -> PublishedForecast:
    try:
        blob = get_bucket(bucket_name).blob(CURRENT)
        pointer = Publication.model_validate_json(blob.download_as_bytes(timeout=5))
        run = read_artifact(bucket_name, pointer.publication_id)
        if run.issued_at != pointer.issued_at:
            raise ValueError("Forecast issue time differs from the current pointer")
        return run
    except (GoogleAPIError, GoogleAuthError, ValueError) as error:
        logger.warning("Could not read the GCS dashboard forecast: %s", type(error).__name__)
        raise ForecastUnavailable() from error


def publish_gcs(
    bucket_name: str, source: Path, run: PublishedForecast, *, layers: Path | None = None, risk: Path | None = None, history: Path | None = None
) -> Publication:
    bucket = get_bucket(bucket_name)
    pointer = Publication(
        publication_id=uuid4(), issued_at=run.issued_at, has_downscaled_layers=layers is not None, has_risk=risk is not None,
    )
    prefix = f"raw/geos-cf/{pointer.publication_id}"
    bucket.blob(f"{prefix}/forecast.nc").upload_from_filename(
        source, content_type="application/x-netcdf", if_generation_match=0, timeout=60,
    )
    bucket.blob(pointer.artifact).upload_from_string(
        run.model_dump_json(), content_type="application/json", if_generation_match=0, timeout=60,
    )
    if layers is not None:
        bucket.blob(f"published/{pointer.publication_id}/downscaled.nc").upload_from_filename(
            layers, content_type="application/x-netcdf", if_generation_match=0, timeout=60,
        )

    if risk is not None:
        bucket.blob(f"published/{pointer.publication_id}/risk.npz").upload_from_filename(
            risk, content_type="application/octet-stream", if_generation_match=0, timeout=60,
        )

    if history is not None:
        bucket.blob(f"history/{pointer.publication_id}/risk.npz").upload_from_filename(
            history, content_type="application/octet-stream", if_generation_match=0, timeout=60,
        )

    for _ in range(5):
        try:
            current = bucket.get_blob(CURRENT, timeout=10)
            generation = 0
            if current is not None:
                generation = int(current.generation)
                selected = Publication.model_validate_json(
                    current.download_as_bytes(if_generation_match=generation, timeout=10)
                )
                if selected.issued_at > pointer.issued_at:
                    logger.info("Kept newer GCS forecast issued at %s", selected.issued_at)
                    return selected
            bucket.blob(CURRENT).upload_from_string(
                pointer.model_dump_json(),
                content_type="application/json",
                if_generation_match=generation,
                timeout=10,
            )
            return pointer
        except PreconditionFailed:
            # Another publisher won the race. Compare issue times again.
            continue
    raise RuntimeError("The GCS forecast pointer changed repeatedly during publication")


def cached_risk_file(bucket_name: str, key: str) -> Path:
    parts = key.split("/")
    if len(parts) != 3 or parts[0] not in ("published", "history") or parts[2] != "risk.npz":
        raise ForecastUnavailable("Invalid risk object key")
    publication_id = UUID(parts[1])
    directory = Path(gettempdir()) / "asthmaaware-risk" / bucket_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{parts[0]}-{publication_id}.npz"
    if path.exists():
        remember_risk_file(path)
        return path
    pending = directory / f".{uuid4()}.tmp"
    try:
        get_bucket(bucket_name).blob(key).download_to_filename(pending, timeout=120)
        from app.services.risk_artifacts import load_artifact

        load_artifact(pending)
        pending.replace(path)
        remember_risk_file(path)
        return path
    except (GoogleAPIError, GoogleAuthError, ValueError, OSError) as error:
        raise ForecastUnavailable("Risk artifact is unavailable") from error
    finally:
        pending.unlink(missing_ok=True)


_risk_files = OrderedDict()
_risk_file_lock = Lock()


def remember_risk_file(path):
    with _risk_file_lock:
        _risk_files[path] = None
        _risk_files.move_to_end(path)
        while len(_risk_files) > 4:
            old, _ = _risk_files.popitem(last=False)
            try:
                old.unlink(missing_ok=True)
            except PermissionError:
                logger.info("Retained risk file currently read by another request")


cached_risk_file.cache_clear = _risk_files.clear


def sync_history(bucket_name: str, pipeline=None) -> Path:
    directory = Path(gettempdir()) / "asthmaaware-risk-history" / bucket_name
    if pipeline:
        import hashlib
        directory /= hashlib.sha256(pipeline.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        blobs = [blob for blob in get_bucket(bucket_name).list_blobs(prefix="history/") if blob.name.endswith("/risk.npz")]
        if pipeline:
            blobs.sort(key=lambda blob: blob.time_created, reverse=True)
        retained = set()
        for blob in blobs:
            source = cached_risk_file(bucket_name, blob.name)
            if pipeline:
                import numpy as np
                with np.load(source, allow_pickle=False) as saved:
                    if json.loads(str(saved["metadata"]))["preprocessing"] != pipeline:
                        continue
            target = directory / source.name
            if not target.exists():
                # Hard links are atomic and avoid copying the artifact for each request.
                try:
                    target.hardlink_to(source)
                except FileExistsError:
                    pass
            retained.add(target)
            if pipeline and len(retained) == 2:
                break
        if pipeline:
            for old in directory.glob("*.npz"):
                if old not in retained:
                    old.unlink(missing_ok=True)
    except (GoogleAPIError, GoogleAuthError, ValueError, OSError) as error:
        raise ForecastUnavailable("Historical exposure archive is unavailable") from error
    return directory
