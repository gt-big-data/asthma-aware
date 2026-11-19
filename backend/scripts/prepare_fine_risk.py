"""Prepare/cache fine-grid historical inputs and fit a fixed model-matched reference."""
import argparse
import hashlib
from pathlib import Path

from app.services.fine_risk import write_surface, fit_fine_reference
from app.services.risk_artifacts import reference_id


def prepare_layers(source, model_dir, sources=None, cache=Path("data/risk/layers")):
    from scripts.downscale_forecast import write_layers, selected_sources
    names = selected_sources(model_dir, sources)
    identities = [reference_id(source), *[reference_id(model_dir / f"{name}_best.pt") for name in names]]
    key = hashlib.sha256("\n".join(identities).encode()).hexdigest()
    output = cache / f"{key}.nc"
    if not output.exists():
        write_layers(source, model_dir, output, sources=names)
    return output


def prepare(source, model_dir, cache=Path("data/risk/surface")):
    """Immutable cache keys include raw data and checkpoint content, not filenames."""
    key = reference_id(source) + "-" + reference_id(model_dir / "o3_best.pt")
    output = cache / f"surface-v1-{key}.nc"
    if output.exists():
        return output
    cache.mkdir(parents=True, exist_ok=True)
    layers = prepare_layers(source, model_dir, ["o3"], cache=cache / "layers")
    write_surface(source, layers, output)
    return output


if __name__ == "__main__":
    import torch
    torch.set_num_threads(2)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reference-output", type=Path, required=True)
    parser.add_argument("--provisional", action="store_true")
    args = parser.parse_args()
    paths = [prepare(path, args.model_dir) for path in args.input]
    print(fit_fine_reference(paths, args.reference_output, provisional=args.provisional))
