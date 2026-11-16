"""Validate/publish ML team output files. Does not load or execute a model."""
from copy import deepcopy
from pathlib import Path

from forecast_pipeline.catalog import create_immutable, publish_manifest, read_checked, read_json, reference
from forecast_pipeline.contracts import CatalogError, MAX_OBJECT_BYTES, digest, validate_manifest, validate_ref


def publish_ml_outputs(manifest, files_root, raw_store, processed_store, delivery_store):
    """Stage supplied output, then copy/reverify into the non-sensitive CDN store.

    Input file records use relative_path in addition to the normal checksum,
    MIME, name and size fields. Generations are rebuilt independently in each
    bucket. No pointer changes until every delivered object has verified bytes.
    """
    root = Path(files_root).resolve(strict=True)
    if not root.is_dir():
        raise CatalogError("ML output root must be a directory")
    candidate = deepcopy(manifest)
    declarations = candidate.get("files")
    if not isinstance(declarations, list) or not declarations:
        raise CatalogError("Actual ML output files are required")
    source_files = []
    for item in declarations:
        if not isinstance(item, dict) or set(item) - {"name", "generation", "sha256", "size_bytes", "content_type", "relative_path"}:
            raise CatalogError("Unexpected ML file declaration fields")
        relative = item.get("relative_path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise CatalogError("ML file path must be relative to the supplied root")
        try:
            path = (root / relative).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError):
            raise CatalogError("ML file escapes the supplied output root or is absent") from None
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_OBJECT_BYTES:
            raise CatalogError("ML file size is invalid")
        source_files.append(path)
        item.pop("relative_path")
        item["generation"] = 1
    validate_manifest(candidate, "processed")
    raw_ref = candidate["input_raw_manifest"]
    validate_ref(raw_ref, "raw/")
    raw_manifest = validate_manifest(read_json(read_checked(raw_store, raw_ref)), "raw")
    for source_ref in raw_manifest["files"]:
        read_checked(raw_store, source_ref)
    for key in ("run_id", "initialized_at"):
        if raw_manifest[key] != candidate[key]:
            raise CatalogError("ML output is not linked to the declared NASA source run")
    from forecast_pipeline.contracts import timestamp
    if timestamp(candidate["valid_from"]) < timestamp(raw_manifest["valid_from"]) or timestamp(candidate["valid_until"]) > timestamp(raw_manifest["valid_until"]):
        raise CatalogError("ML output cannot invent forecast coverage beyond raw inputs")
    raw_bounds, output_bounds = raw_manifest["bounds"], candidate["bounds"]
    if not (raw_bounds["min_lat"] <= output_bounds["min_lat"] <= output_bounds["max_lat"] <= raw_bounds["max_lat"]
            and raw_bounds["min_lon"] <= output_bounds["min_lon"] <= output_bounds["max_lon"] <= raw_bounds["max_lon"]):
        raise CatalogError("ML output geographic coverage exceeds supplied raw bounds")
    # Verify all local files before any upload, then read each once per stage.
    for path, declaration in zip(source_files, declarations):
        data = path.read_bytes()
        if len(data) != declaration["size_bytes"] or digest(data) != declaration["sha256"]:
            raise CatalogError("Supplied ML file checksum/size mismatch")
    staged = deepcopy(candidate)
    staged["files"] = []
    for path, declaration in zip(source_files, declarations):
        data = path.read_bytes()
        # Recheck against a file change between preflight and upload.
        if len(data) != declaration["size_bytes"] or digest(data) != declaration["sha256"]:
            raise CatalogError("ML output changed during publication")
        obj = create_immutable(processed_store, declaration["name"], data, declaration["content_type"])
        staged["files"].append(reference(declaration["name"], obj, declaration["content_type"]))
    # Staging has no public pointer. The complete manifest is immutable provenance.
    from forecast_pipeline.contracts import encode
    name = f"processed/{staged['run_id']}/{staged['artifact_version']}/manifest.json"
    create_immutable(processed_store, name, encode(staged))
    delivered = deepcopy(candidate)
    delivered["files"] = []
    for ref in staged["files"]:
        source = read_checked(processed_store, ref)
        obj = create_immutable(delivery_store, ref["name"], source.data, ref["content_type"])
        delivered["files"].append(reference(ref["name"], obj, ref["content_type"]))
    return {"promoted": publish_manifest(delivery_store, delivered), "manifest": delivered}
