"""Prepare/cache fine-grid historical inputs and fit a fixed model-matched reference."""
import argparse
import hashlib
from pathlib import Path

from app.services.fine_risk import write_surface, fit_fine_reference
from app.services.risk_artifacts import NO2_MODES, reference_id


def prepare_layers(source, model_dir, sources=None, cache=Path("data/risk/layers")):
    from scripts.downscale_forecast import write_layers, selected_sources
    names = selected_sources(model_dir, sources)
    identities = [reference_id(source), *[reference_id(model_dir / f"{name}_best.pt") for name in names]]
    key = hashlib.sha256("\n".join(identities).encode()).hexdigest()
    output = cache / f"{key}.nc"
    if not output.exists():
        write_layers(source, model_dir, output, sources=names)
    return output


def surface_models(no2_mode):
    """Checkpoints whose layers the fine surface inputs use."""
    return ["o3", "no2"] if no2_mode == "column-scaled" else ["o3"]


def prepare(source, model_dir, cache=Path("data/risk/surface"), *, no2_mode="native"):
    """Immutable cache keys include raw data and checkpoint content, not filenames."""
    models = surface_models(no2_mode)
    key = "-".join([reference_id(source), *(reference_id(model_dir / f"{name}_best.pt") for name in models)])
    version = "surface-v1" if no2_mode == "native" else "surface-no2col-v1"
    output = cache / f"{version}-{key}.nc"
    if output.exists():
        return output
    cache.mkdir(parents=True, exist_ok=True)
    layers = prepare_layers(source, model_dir, models, cache=cache / "layers")
    write_surface(source, layers, output, no2_mode=no2_mode)
    return output


if __name__ == "__main__":
    import torch
    torch.set_num_threads(2)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reference-output", type=Path, required=True)
    parser.add_argument("--provisional", action="store_true")
    parser.add_argument("--no2-surface", choices=NO2_MODES, default="native",
                        help="column-scaled needs no2_best.pt and inputs with tropcol_no2")
    args = parser.parse_args()
    paths = [prepare(path, args.model_dir, no2_mode=args.no2_surface) for path in args.input]
    print(fit_fine_reference(paths, args.reference_output, provisional=args.provisional))
