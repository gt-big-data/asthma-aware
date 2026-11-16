"""Complete-manifest publication and raw/processed forecast catalog access."""
import json

from forecast_job.storage import Conflict
from forecast_pipeline.contracts import (CatalogError, ProductUnavailable, check_current,
    digest, encode, object_name, timestamp, validate_manifest, validate_ref, MAX_MANIFEST_BYTES)


def read_json(obj):
    if len(obj.data) > MAX_MANIFEST_BYTES:
        raise CatalogError("Manifest exceeds size limit")
    try:
        return json.loads(obj.data)
    except (ValueError, UnicodeError):
        raise CatalogError("Manifest is not valid JSON") from None


def read_checked(store, ref):
    obj = store.read(ref["name"], ref["generation"])
    if len(obj.data) != ref["size_bytes"] or digest(obj.data) != ref["sha256"]:
        raise CatalogError("Artifact integrity verification failed")
    return obj


def reference(name, obj, content_type="application/json"):
    return {"name": name, "generation": obj.generation, "sha256": digest(obj.data),
            "size_bytes": len(obj.data), "content_type": content_type}


def create_immutable(store, name, data, content_type="application/json"):
    try:
        # Existing MemoryStore is intentionally usable as a test double.
        if hasattr(store, "path") or hasattr(store, "bucket"):
            return store.write(name, data, expected=0, content_type=content_type)
        return store.write(name, data, expected=0)
    except Conflict:
        existing = store.read(name)
        if existing.data != data:
            raise CatalogError("Immutable artifact identity was reused with different bytes") from None
        return existing


def publish_manifest(store, manifest):
    """Promote only after all referenced immutable files have been verified.

    This accepts ML-produced files, not a model callback. No inference is run.
    Races use generation CAS; an older run can never regress the current pointer.
    """
    product = manifest.get("product")
    validate_manifest(manifest, product)
    for ref in manifest["files"]:
        read_checked(store, ref)
    name = f"{product}/{manifest['run_id']}/{manifest['artifact_version']}/manifest.json"
    obj = create_immutable(store, name, encode(manifest))
    pointer_ref = reference(name, obj)
    pointer_name = f"{product}/current.json"
    for _ in range(3):
        try:
            old_obj = store.read(pointer_name)
            old_pointer = read_json(old_obj)
            old_manifest = validate_manifest(read_json(read_checked(store, old_pointer)), product)
            old_time, new_time = timestamp(old_manifest["initialized_at"]), timestamp(manifest["initialized_at"])
            if old_time > new_time:
                return False
            if old_time == new_time:
                if old_pointer == pointer_ref:
                    return False
                raise CatalogError("A different snapshot is already selected for this NASA run")
            expected = old_obj.generation
        except KeyError:
            expected = 0
        try:
            store.write(pointer_name, encode(pointer_ref), expected=expected)
            return True
        except Conflict:
            continue
    raise Conflict("Current product changed concurrently; retry the complete publication")


class ForecastCatalog:
    def __init__(self, store):
        self.store = store

    def metadata(self, product="processed", now=None):
        if product not in ("raw", "processed"):
            raise CatalogError("Unknown product")
        try:
            pointer = read_json(self.store.read(f"{product}/current.json"))
            validate_ref(pointer, f"{product}/")
            manifest = validate_manifest(read_json(read_checked(self.store, pointer)), product)
        except KeyError:
            raise ProductUnavailable("No complete product has been published") from None
        return check_current(manifest, now)

    def availability(self, now=None):
        result = {}
        for product in ("raw", "processed"):
            try:
                metadata = self.metadata(product, now)
                result[product] = {"status": "available", "run_id": metadata["run_id"],
                    "valid_from": metadata["valid_from"], "valid_until": metadata["valid_until"]}
            except ProductUnavailable:
                result[product] = {"status": "pending" if product == "processed" else "unavailable"}
        result["ml_status"] = "published" if result["processed"]["status"] == "available" else "pending"
        return result

    def delivery(self, name, now=None):
        object_name(name)
        manifest = self.metadata("processed", now)
        selected = next((ref for ref in manifest["files"] if ref["name"] == name), None)
        if selected is None:
            raise ProductUnavailable("Requested object is not in the current processed product")
        return read_checked(self.store, selected).data, selected["content_type"]
