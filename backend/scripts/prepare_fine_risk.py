"""Prepare/cache fine-grid historical inputs and fit a fixed model-matched reference."""
import argparse
import hashlib
from pathlib import Path

from app.services.fine_risk import write_surface, fit_fine_reference
from app.services.risk_artifacts import reference_id


def prepare_layers(source, model_dir, sources=None, cache=Path("data/risk/layers"), *, analysis_lead_hours=None):
    from scripts.downscale_forecast import write_layers, selected_sources, checkpoint_path
    names = selected_sources(model_dir, sources)
    identities = [reference_id(source), *[reference_id(checkpoint_path(model_dir, name)) for name in names], str(analysis_lead_hours)]
    key = hashlib.sha256("\n".join(identities).encode()).hexdigest()
    output = cache / f"{key}.nc"
    if not output.exists():
        write_layers(source, model_dir, output, sources=names, analysis_lead_hours=analysis_lead_hours)
    return output


def prepare(source, model_dir, cache=Path("data/risk/surface"), *, sources=None, analysis_lead_hours=None):
    """Immutable cache keys include raw data and checkpoint content, not filenames."""
    from scripts.downscale_forecast import surface_sources, checkpoint_path
    names = surface_sources(model_dir, sources)
    if "o3" not in names:
        raise ValueError("Fine risk requires the O3 checkpoint")
    identity = [reference_id(source), *[reference_id(checkpoint_path(model_dir, name)) for name in names], str(analysis_lead_hours)]
    key = hashlib.sha256("\n".join(identity).encode()).hexdigest()
    output = cache / f"surface-v1-{key}.nc"
    if output.exists():
        return output
    cache.mkdir(parents=True, exist_ok=True)
    layers = prepare_layers(source, model_dir, names, cache=cache / "layers", analysis_lead_hours=analysis_lead_hours)
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
    parser.add_argument("--models", nargs="+")
    parser.add_argument("--analysis-lead-hours", type=float, help="Explicit experimental lead assumption; ML validation required")
    args = parser.parse_args()
    paths = [prepare(path, args.model_dir, sources=args.models, analysis_lead_hours=args.analysis_lead_hours) for path in args.input]
    print(fit_fine_reference(paths, args.reference_output, provisional=args.provisional))
