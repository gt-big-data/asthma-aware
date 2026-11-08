# AsthmaAware ML

A configurable PyTorch U-Net for downscaling experiments. A spatial U-Net encodes
either interpolated forecasts or fine-grid geography. A separate coarse forecast
CNN and optional time features join it at the bottleneck. Independent output
branches predict a residual correction per target.

This is an untrained architecture with independent-source data preparation,
geographic regridding, masked losses, and a minimal training integration. Real
training remains gated by source-specific data readiness; see below.

## Setup and checks

From `ml-work`, with Python 3.10 or newer:

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```

Only PyTorch is required at runtime. Tests use Python's standard `unittest`.

## Inspect TIFF data

Install the optional raster dependency from `ml-work`:

```bash
python3 -m pip install -e '.[data]'
python3 asthma_aware/data_testing.py
```

The script discovers `.tif` and `.tiff` files directly in `ml-work`, regardless of
the current working directory. It prints spatial and full array shapes, raster
profiles, geographic metadata, dataset tags, and per-band metadata without reading
pixel arrays. You can also pass specific TIFF paths as command-line arguments.

## Independent models for each data source

`ResidualUNet` remains available as the shared multitarget architecture. For now,
use `build_source_models` to create a separate U-Net per observation/label source.
Every model has its own backbone and output heads; no weights are shared. Each
source can use its own dates, batch size, grid, forecast predictors, normalization,
training/validation split, optimizer, and checkpoint.

The source names and target groups are configurable. For the files currently in
this folder, one possible grouping is NO2 satellite labels, O3 satellite labels,
and station weather labels:

```python
import torch
from asthma_aware import SourceModelConfig, build_source_models


def configure_source_models(no2_forecast_channels, o3_forecast_channels,
                            weather_forecast_channels):
    configs = {
        "no2_satellite": SourceModelConfig(
            forecast_channels=no2_forecast_channels,
            target_names=("no2_column",),
        ),
        "o3_satellite": SourceModelConfig(
            forecast_channels=o3_forecast_channels,
            target_names=("o3_column",),
        ),
        "station_weather": SourceModelConfig(
            forecast_channels=weather_forecast_channels,
            target_names=("temperature_c", "dewpoint_c"),
        ),
    }
    models = build_source_models(configs)
    optimizers = {
        source: torch.optim.Adam(model.parameters(), lr=1e-3)
        for source, model in models.items()
    }
    return configs, models, optimizers
```

Call only the model for the source batch you have:

```python
prediction = models["no2_satellite"](no2_forecast, baseline=no2_baseline)
```

O3 and weather data are not needed for this call or for an NO2 optimizer step.
Separate loaders can iterate over each source's own dates. Reusing one config
for several source names still creates independent models. You can also split
weather into separate temperature and dewpoint models by creating two configs.

`geography_channels=0` is the source-config default. All architecture options from
`ResidualUNet` remain configurable, including `time_channels`. Each source may
use all relevant forecast inputs; its output count comes from its own
`target_names`. The station file currently contains temperature and dewpoint
labels; dewpoint is not a humidity label. Derive and validate a humidity target
before configuring a humidity output. Satellite column definitions and matching
GEOS-CF baselines also need to be finalized before training.

Save and load each model independently, including its constructor settings:

```python
from dataclasses import asdict

source = "no2_satellite"
torch.save({
    "source": source,
    "model_config": asdict(configs[source]),
    "model_state_dict": models[source].state_dict(),
    "optimizer_state_dict": optimizers[source].state_dict(),
}, f"{source}.pt")

saved = torch.load(f"{source}.pt", map_location="cpu", weights_only=True)
restored = SourceModelConfig(**saved["model_config"]).build_model()
restored.load_state_dict(saved["model_state_dict"])
```

There is no cross-source date join. **Within each source**, forecasts and labels
must still agree on valid times, averaging periods, spatial alignment, units, and
physical quantities. Label sources supervise the predictions; their later
observations are not forecast inputs. This factory does not load the TIFF or
station files, infer dates, or train models. Training still needs a source-specific
observation operator (satellite footprints versus station sampling) and masked
loss. Record units, grid definitions, training-only scalers, and evaluation splits
alongside each checkpoint once that pipeline is implemented.

## Usage

All data channel counts are constructor arguments. Target count comes from
`target_names`; output height and width come from `baseline` at each forward
pass. Coarse input dimensions may differ from the output dimensions. The same
model accepts different rectangular or odd spatial sizes without rebuilding it.

### Without geography

Set `geography_channels=0` and omit `geography`. The model interpolates **all
forecast input channels** to the baseline grid and feeds them into
`spatial_encoder` (previously named `geography_encoder`). This encoder builds the
U-Net's multiscale features and skip connections. Hidden encoder/decoder widths
remain positive; no zero-channel convolution or placeholder tensor is needed.

```python
import torch
from asthma_aware import ResidualUNet


def make_prediction(
    forecast,            # [batch, forecast_channels, coarse_height, coarse_width]
    baseline,            # [batch, number_of_targets, output_height, output_width]
    target_names,        # Ordered to match baseline and label channels.
    time_features=None,  # Optional [batch, time_channels].
):
    model = ResidualUNet(
        forecast_channels=forecast.shape[1],
        geography_channels=0,
        target_names=target_names,
        encoder_channels=(32, 64, 128, 256),  # Configurable feature widths/depth.
        forecast_encoder_channels=(32, 64),
        head_channels=32,
        time_channels=0 if time_features is None else time_features.shape[1],
    ).to(device=forecast.device, dtype=forecast.dtype)

    model.eval()
    with torch.no_grad():
        prediction = model(forecast, baseline=baseline, time_features=time_features)
    return model, prediction
```

The helper illustrates tensor-derived dimensions; create the model once outside
your batch/training loop. For the proposed five-output experiment, target names
could be `("no2", "pm25", "o3", "temperature", "humidity")`. These names are only
channel identifiers: resolve each quantity, units, vertical extent, and averaging
period before preparing labels. A single name builds a single-target model.

The forecast-only architecture is:

```text
Forecast --> interpolate to baseline grid --> spatial encoder -- skips --+
    |                                              |                   |
    +--> coarse forecast CNN --> resize --------> fusion --> decoder <--+
                                                   ^           |
                                             time features   target heads
                                                               |
Baseline ----------------------------------------------------> (+) --> output
```

Interpolation supplies the requested grid size. Useful fine-scale detail still
depends on training observations and informative predictors.

### With geography

The original geography mode remains available. Set `geography_channels` to your
geographic channel count and call
`model(forecast, geography, baseline, time_features)`. The spatial encoder then
uses geography instead of interpolated forecast fields. Geography must match the
baseline grid. Passing geography to a forecast-only model, or omitting it from a
geography-enabled model, raises an error. Encoder weights have the appropriate
input channel count for the chosen mode; choose the mode when constructing the
model. The encoder's state-dict keys now use `spatial_encoder` in both modes.

Set `time_channels=0` and omit `time_features` when time conditioning is disabled.
Otherwise supply numeric time features, such as scaled forecast lead time and
sine/cosine encodings of valid hour and season. Their encoding is external to
the model. Each example represents one forecast valid time.

## Tensor and geographic contracts

| Input | Shape | Meaning |
| --- | --- | --- |
| `forecast` | `[B, Cf, Hc, Wc]` | Coarse pollutants and forecast meteorology |
| `geography` | `[B, Cg, H, W]` or `None` | Geographic predictors when `Cg > 0` |
| `baseline` | `[B, T, H, W]` | Required aligned target baseline; defines output size |
| `time_features` | `[B, Ct]` | Optional lead-time/calendar features |
| Return value | `[B, T, H, W]` | Baseline plus target-specific corrections |

- No region, forecast length, grid resolution, or data channel count is fixed.
  A 1 km grid is specified by the upstream grid definition, not the network.
- Forecast, baseline, and any geography must cover the **same geographic extent
  and axis orientation**. Explicitly reproject/regrid them upstream. Internal bilinear
  resizing aligns feature-array sizes and assumes this geographic alignment;
  equal array sizes alone do not establish it. Crop/regrid a larger forecast
  domain to the matching extent before calling this model.
- Supply `baseline` already aligned to the fine output grid. Its target order,
  quantity, units, and numerical scaling must match the labels. For example, a
  surface concentration cannot serve as a tropospheric column baseline.
- Normalize inputs/targets using training data only. When predicting in scaled
  target space, transform the baseline into that same space before adding the
  learned correction, then invert scaling after inference.
- Inputs must be finite floating-point tensors with the same batch size, device,
  and dtype. Handle missing inputs with an upstream mask/imputation strategy.
  The model validates tensor shapes/dtypes, but does not scan for NaNs.
- With `D = len(encoder_channels)`, both fine-grid dimensions must be at least
  `2 ** (D - 1)`. They need not be divisible by this number. The coarse grid can
  be as small as one cell. Encoder widths are at least two channels so GroupNorm
  also works with a batch of one at a one-cell bottleneck.
- Each output is unconstrained. Choose target-specific bounds/transforms once
  physical definitions are settled; Celsius temperature, for example, may be
  negative. Coarse-cell means are not forcibly preserved.

Final head convolutions are zero-initialized by default, making the initial
prediction equal to the supplied baseline. This delays gradients into the shared
backbone until the heads start learning. Set `zero_init_residual=False` for
ordinary random initialization instead.

The synthetic tests check both modes, shapes, initialization, gradient flow
through input branches and every head, configurable depth, and invalid-input handling.
They do not measure forecast skill or establish independent 1 km information.

PyTorch references: [bilinear interpolation](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.interpolate.html)
and [GroupNorm](https://docs.pytorch.org/docs/stable/generated/torch.nn.GroupNorm.html).

## Independent datasets and loaders

The data pipeline uses four independent sources: `no2` (tropospheric column),
`o3` (total column), `temperature`, and `dewpoint`. Each has its own dates,
loader, model, normalization, optimizer, and checkpoint. Dataset code is in
`asthma_aware/data_pipeline.py`; the preparation command is `prepare_data.py`.
Install dependencies with `python3 -m pip install -e '.[data]'`.

From `ml-work`:

```bash
python3 -m asthma_aware.prepare_data --config data_config.json --output prepared
# Once sources are ready, also compute training-only normalization:
python3 -m asthma_aware.prepare_data --config data_config.json --output prepared --fit-normalization
```

Paths in the configuration resolve relative to the configuration file. The
command produces `prepared.json`, `readiness.json`, and separate source catalog
and sample-index JSON files. It does not train. Blocked sources retain temporal
candidate indexes for inspection, but constructing their training loaders raises
an error. Preparation scans all usable candidate inputs and labels; failed samples
are listed with their exclusion reason. Fix missing files/metadata and rerun
preparation after every input change. File size/mtime fingerprints detect stale
inputs; they are provenance checks, not cryptographic content hashes.

### Current files and blockers

The inspected GEOS NetCDF contains 1,441 **analysis hourly means stamped at :30**,
with an 18 x 17 latitude/longitude grid, covering the first five days of each
month from October 2025 to September 2026. It is not an operational forecast
archive. Analysis-model performance must not be reported as forecast skill.

- NO2 and station labels cover 2023 and currently have no GEOS date overlap.
- O3 has 55 provisional noon matches with the existing GEOS file. Its label units are confirmed as mol/m².
  The original-observation mask and GEOS total-column baseline are still required.
- Both satellite rasters are filled. Finite filled values are not evidence of
  original measurements. Supply a separate 0/1 raster, with matching grid and
  band order, where 1 means an original acceptable observation. Filled labels
  without that mask cannot enter training. Use masks derived from the original
  retrieval/export, never an all-ones mask created from filled values.
- `totalcol_o3` is a configurable placeholder for a future total-column variable,
  not a variable claimed to exist in the current NetCDF. `o3` surface mixing
  ratio is allowed as a predictor, never as a total-column baseline.

### Matching and quantities

Satellite bands use their `YYYYMMDD` or `YYYY-MM-DD` descriptions. Each date
matches exactly 12:30 UTC, representing the **12:00–13:00 UTC** interval. This
is an explicitly assumed interval, not the satellite acquisition hour. Missing
dates are skipped; one satellite image is never replicated over 24 hours.

An optional `acquisition_sidecar` is a JSON list of
`{"file": "labels.tif", "band": 1, "acquisition_time": "2023-01-01T18:23:00Z"}`.
File paths resolve relative to that sidecar. The catalog/index preserves known
acquisition times, but supplying this file does NOT change noon matching. Actual
acquisition-time matching needs a later explicit policy implementation.

Station supervision preserves the Parquet validity masks and round-hour times.
The two GEOS records at target hour minus/plus 30 minutes must both exist;
their fields are averaged before deriving the baseline. This approximates a
round-hour input from hourly means and never bridges missing days. The labels
remain point observations, not hourly averages. Station masks should already
reflect the agreed +/-15-minute observation tolerance and source-specific QC.

NO2 satellite umol/m2 is converted to mol/m2. GEOS NO2 in 1e15 molecules/cm2
uses `1e19 / 6.02214076e23`. Supported column units are listed in `COLUMN_UNITS`;
unknown units fail rather than being guessed. Temperature baselines convert K
to Celsius. Dew point uses the Magnus liquid-water formula:
`e = q*p/(0.622 + 0.378*q)` (Pa), `l = ln(e/610.94)`,
`Td = 243.04*l/(17.625-l)` (Celsius). This is dew point, not frost point.
Non-finite or physically invalid inputs and supersaturation beyond a 0.5 C
tolerance fail preflight. These empirical constants approximate saturation
vapor pressure; very cold conditions need a dedicated assessment. Model-layer
weather and station measurement heights also differ.

### Grids and tensors

Default output: 420 x 408 cells, EPSG:4326, transform
`[0.01, 0, -85.60, 0, -0.01, 34.98]`. This is a **0.01-degree** grid, not
independent 1 km satellite detail. Each source may override `grid` and
`coarse_shape`. GEOS fields are bilinearly regridded onto a coextensive coarse
grid before feature processing; baselines are bilinearly mapped to the fine grid.
Coverage is checked against native cell footprints; boundary values use the
raster resampler's edge behavior rather than extrapolating beyond coverage.
Satellite labels use nearest-cell transfer, with original-observation masks
applied first so filled pixels cannot contaminate interpolation. Station
coordinates are transformed from WGS84 into the output grid and sampled using
the containing cell. Outside stations are masked, with safe placeholder indices.

Per-source `predictors` is an ordered list of NetCDF variables; defaults are the
relevant surface pollutant plus temperature, specific humidity, surface pressure,
and PBL height (meteorology only for weather sources). NetCDF must have regular,
monotonic 1D `lat`/`lon`, a unique increasing center-of-hour `time`, and analysis
provenance. Multiple files may be configured, but overlapping timestamps fail.
Sidecar metadata is cross-checked when present. Inputs retain their native units
until training-only normalization; target baselines use target units.

| Field | Dataset item | Batched |
| --- | --- | --- |
| forecast | `[Cf,Hc,Wc]` | `[B,Cf,Hc,Wc]` |
| baseline | `[1,H,W]` | `[B,1,H,W]` |
| time_features | `[4]` | `[B,4]` |
| satellite targets/valid | `[1,H,W]` | `[B,1,H,W]` |
| station targets/valid | `[N,1]` | `[B,N,1]` |
| station rows/cols | `[N]` | `[B,N]` |

Time features are sine/cosine of UTC hour and annual phase. No initialization or
lead time is invented for analyses. Each weather dataset has a fixed sorted
station list. Invalid labels use finite zero placeholders and false masks.

### Loading and training integration

```python
import json
from pathlib import Path
from asthma_aware.data_pipeline import make_loader
from asthma_aware.train_model import (
    initialize_training, train_one_epoch, save_source_checkpoint,
)

root = Path('prepared')
bundle = json.loads((root / 'prepared.json').read_text())
loaders = {}
for source, report in bundle['reports'].items():
    if report['ready']:
        normalization = json.loads((root / f'{source}_normalization.json').read_text())
        loaders[source] = make_loader(bundle, source, 'train', normalization,
                                      batch_size=1, num_workers=0, seed=42)

# Do this only when ready sources exist and you intend to start training.
if loaders:
    models, optimizers, configs = initialize_training(loaders)
    for source, loader in loaders.items():
        metrics = train_one_epoch(models[source], loader, optimizers[source])
        save_source_checkpoint(root / f'{source}.pt', source, models[source],
                               optimizers[source], configs[source], loader, epoch=1)
```

Use `if __name__ == '__main__':` around script entrypoints when enabling worker
processes. Readers open lazily per worker with a four-handle limit and discard
handles when pickled. Call `loader.dataset.close()` after use. Validation/test
loaders take the SAME training normalization and never shuffle.

Split dates are chronological within each source: floor(70%) training, through
floor(85%) validation, remaining test. All hours of each date stay together;
small datasets may have empty splits, which raise clear loader errors. Scalers
are fitted only on valid training targets and training input grids, and include
a source/index signature. Constant channels use standard deviation 1. Baselines
and labels share the target scaling. A target without training observations
cannot produce a scaler or a ready loader.

The loss averages squared error over valid pixels or valid station samples.
Missing-only batches return `None` and skip the optimizer step. Separate models
avoid raster/station count competition. `mean_batch_loss` is a training diagnostic,
not a dataset-wide physical-unit evaluation metric. Evaluate inverse-scaled MAE/
RMSE separately. Current splits hold out time, not station locations; they do not
establish accuracy at unseen stations or independent fine-resolution detail.

Tests cover temporal matching/gaps, unit and baseline validation, masks, separate
source readiness, geographic mapping, training-only statistics, missing losses,
batching, and a synthetic forward/backward epoch for all four independent models.
