"""The data-access layer.

Import these rather than writing queries inline -- it keeps the SQL in
one place and means a schema change touches one module, not every
collector.

    from db.repositories import forecasts, grid, ingestion, reference
    from db.repositories import scalers, socioeconomic, stations
"""

from db.repositories import (  # noqa: F401
    forecasts,
    grid,
    ingestion,
    reference,
    scalers,
    socioeconomic,
    stations,
)

__all__ = [
    "forecasts",
    "grid",
    "ingestion",
    "reference",
    "scalers",
    "socioeconomic",
    "stations",
]
