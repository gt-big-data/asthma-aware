# Retired prototype

`map_prototype/` preserves the obsolete map API, ConvLSTM model, sample arrays,
and related services. `raw_rasters/` retains the historical input rasters.
`prototype_README.md` is historical documentation, not current setup guidance.
`run_model.py` is an unfinished experiment with unreachable code and is not a job.

Nothing here is imported by the active API or collected by the normal test suite.
Do not deploy this API. Preserving the artifacts avoids losing earlier research;
it does not establish their accuracy or compatibility with the new architecture.

The scripts `build_latest_sequence.py`, `build_latest_sequence_from_db.py`,
`fit_scalers.py`, and `backfill_raw_rasters.py` in `../scripts/` are historical
utilities. They are not the GEOS-CF-to-GCS pipeline. Their paths now target this
archive, and their old scaling/grid/model assumptions still require review.
