"""Data ingestion pipelines.

Each subpackage owns one upstream source and follows the same shape:

    <source>/client.py      talks to the upstream API, and nothing else
    <source>/collector.py   orchestrates dates/regions, writes files and DB

Collectors are run as modules from the backend/ directory::

    python -m ingestion.satellite.collector --date 2026-09-10
    python -m ingestion.socioeconomic.collector

Shared helpers live at this level (see grid_resample.py). See
db/README.md for how to add a new pipeline.
"""
