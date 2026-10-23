"""All ORM models.

Importing this package registers every table on ``Base.metadata``, which
is what Alembic autogenerate and ``create_all`` rely on. If you add a new
model module, import it here or Alembic will not see it.
"""

from db.base import Base
from db.models.ingestion import (
    ISSUE_KINDS,
    RUN_STATUSES,
    IngestionIssue,
    IngestionRun,
    RasterFile,
)
from db.models.modeling import (
    SCALER_METHODS,
    Forecast,
    ModelRun,
    ScalerParams,
)
from db.models.observations import (
    GridObservation,
    Station,
    StationObservation,
)
from db.models.reference import (
    VARIABLE_KINDS,
    DataSource,
    GridCell,
    Region,
    Variable,
)
from db.models.socioeconomic import SocioeconomicRecord, Zcta

__all__ = [
    "Base",
    "DataSource",
    "Forecast",
    "GridCell",
    "GridObservation",
    "IngestionIssue",
    "IngestionRun",
    "ISSUE_KINDS",
    "ModelRun",
    "RasterFile",
    "Region",
    "RUN_STATUSES",
    "ScalerParams",
    "SCALER_METHODS",
    "SocioeconomicRecord",
    "Station",
    "StationObservation",
    "Variable",
    "VARIABLE_KINDS",
    "Zcta",
]
