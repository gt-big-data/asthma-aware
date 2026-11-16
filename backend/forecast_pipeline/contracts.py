"""Storage and ML handoff contracts. No model implementation or predictions."""
from datetime import datetime, timezone
import hashlib
import json
import re

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_OBJECT_BYTES = 64 * 1024 * 1024
RAW_SCHEMA = "nasa-geos-cf-raw-v1"
PROCESSED_SCHEMA = "asthma-ml-output-v1"
CONTENT_TYPES = {"application/json", "application/x-netcdf", "image/png", "image/webp", "application/vnd.mapbox-vector-tile"}


class CatalogError(ValueError):
    """A contract, object integrity or publication invariant failed."""


class ProductUnavailable(LookupError):
    """No current product exists or its validity interval does not cover now."""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise CatalogError("UTC timestamp required") from None


def object_name(value):
    if not isinstance(value, str) or len(value) > 512 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", value):
        raise CatalogError("Invalid object name")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise CatalogError("Object paths must be normalized")
    return value


def validate_ref(ref, prefix):
    if not isinstance(ref, dict) or set(ref) != {"name", "generation", "sha256", "size_bytes", "content_type"}:
        raise CatalogError("Invalid object reference fields")
    name = object_name(ref["name"])
    if not name.startswith(prefix) or type(ref["generation"]) is not int or ref["generation"] < 1:
        raise CatalogError("Object generation/prefix mismatch")
    if not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]) or type(ref["size_bytes"]) is not int or not 0 < ref["size_bytes"] <= MAX_OBJECT_BYTES:
        raise CatalogError("Object checksum/size is invalid")
    if ref["content_type"] not in CONTENT_TYPES:
        raise CatalogError("Unsupported forecast content type")
    return ref


def validate_manifest(manifest, product):
    try:
        if product not in ("raw", "processed"):
            raise CatalogError("Unknown product")
        schema = RAW_SCHEMA if product == "raw" else PROCESSED_SCHEMA
        base_fields = {"schema_version", "product", "source", "run_id", "artifact_version", "initialized_at", "valid_from", "valid_until", "bounds", "variables", "files"}
        allowed = base_fields | ({"ml_status", "time_convention", "data_sha256"} if product == "raw" else {"audience", "contains_user_data", "model", "input_raw_manifest"})
        if set(manifest) - allowed or not base_fields <= set(manifest):
            raise CatalogError("Unexpected or missing product manifest fields")
        if manifest["schema_version"] != schema or manifest["product"] != product:
            raise CatalogError("Unsupported product/schema")
        if manifest["source"] != "NASA GEOS-CF v2":
            raise CatalogError("Unexpected forecast source")
        if not re.fullmatch(r"\d{8}_09z", manifest["run_id"]):
            raise CatalogError("Invalid NASA run identity")
        initialized = timestamp(manifest["initialized_at"])
        if initialized.strftime("%Y%m%d_%Hz") != manifest["run_id"] or initialized.minute or initialized.second or initialized.microsecond:
            raise CatalogError("Run identity does not match initialization")
        start, end = timestamp(manifest["valid_from"]), timestamp(manifest["valid_until"])
        if not initialized <= start < end or (end - initialized).total_seconds() > 120 * 3600:
            raise CatalogError("Invalid forecast validity interval")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", manifest["artifact_version"]):
            raise CatalogError("Invalid artifact version")
        bounds = manifest["bounds"]
        if set(bounds) != {"min_lat", "max_lat", "min_lon", "max_lon"}:
            raise CatalogError("Invalid forecast bounds")
        if not (-90 <= bounds["min_lat"] <= bounds["max_lat"] <= 90 and -180 <= bounds["min_lon"] <= bounds["max_lon"] <= 180):
            raise CatalogError("Invalid geographic extent")
        if not isinstance(manifest["variables"], dict) or not manifest["variables"] or any(not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k) or not isinstance(v, str) or not 1 <= len(v) <= 128 for k, v in manifest["variables"].items()):
            raise CatalogError("Variable units must be explicit")
        prefix = f"{product}/{manifest['run_id']}/{manifest['artifact_version']}/"
        files = manifest["files"]
        if not isinstance(files, list) or not 1 <= len(files) <= 10000:
            raise CatalogError("Invalid file count")
        for ref in files:
            validate_ref(ref, prefix)
        if len({ref["name"] for ref in files}) != len(files):
            raise CatalogError("Duplicate file reference")
        if product == "processed":
            if manifest["audience"] != "environmental-public" or manifest.get("contains_user_data") is not False:
                raise CatalogError("Only public environmental ML artifacts may be delivered")
            model = manifest["model"]
            if set(model) != {"name", "version", "input_contract"} or any(not isinstance(v, str) or not v.strip() for v in model.values()):
                raise CatalogError("ML output requires actual model provenance")
            if model["input_contract"] != RAW_SCHEMA:
                raise CatalogError("ML input contract is incompatible")
            validate_ref(manifest["input_raw_manifest"], "raw/")
        if len(encode(manifest)) > MAX_MANIFEST_BYTES:
            raise CatalogError("Manifest exceeds size limit")
    except (KeyError, TypeError, OverflowError, ValueError) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError("Invalid forecast manifest") from exc
    return manifest


def check_current(manifest, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise CatalogError("Current time must be timezone-aware")
    if not timestamp(manifest["valid_from"]) <= now < timestamp(manifest["valid_until"]):
        raise ProductUnavailable("Product does not cover the current time")
    return manifest
