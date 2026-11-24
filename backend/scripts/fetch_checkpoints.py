"""Fetch versioned GCS checkpoints and verify the repository's pinned identities."""
import json
import os
from pathlib import Path
from uuid import uuid4

from app.services.risk_artifacts import reference_id


def download(bucket, prefix, directory, sources=None):
    manifest = Path(os.getenv("MODEL_MANIFEST_PATH", str(Path(__file__).resolve().parents[2] / "models/checkpoints/manifest.json")))
    entries = json.loads(manifest.read_text(encoding="utf-8"))["checkpoints"]
    wanted = set(sources) if sources else None
    selected = [entry for entry in entries if wanted is None or entry.get("source", entry["file"].removesuffix("_best.pt")) in wanted]
    if not prefix.strip("/") or ".." in prefix.split("/") or (wanted and len(selected) != len(wanted)):
        raise ValueError("Invalid checkpoint prefix or selection")
    directory.mkdir(parents=True, exist_ok=True)
    for entry in selected:
        name = entry["file"]
        if Path(name).name != name or not name.endswith(("_best.pt", "_best_surface.pt")):
            raise ValueError("Invalid checkpoint manifest filename")
        output = directory / name
        def valid(path):
            return path.exists() and path.stat().st_size == entry["bytes"] and reference_id(path) == entry["sha256"]
        if valid(output):
            continue
        pending = directory / f".{name}.{uuid4()}.tmp"
        try:
            bucket.blob(f"{prefix.strip('/')}/{name}").download_to_filename(pending, timeout=120)
            if not valid(pending):
                raise ValueError(f"Checkpoint hash/size mismatch: {name}")
            pending.replace(output)
        finally:
            pending.unlink(missing_ok=True)
