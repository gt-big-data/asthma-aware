"""Lookups and upserts for the reference tables.

Collectors need to turn a slug ("atlanta", "no2", "modis-mod13q1") into a
row id constantly. These helpers do that, and cache the grid-cell lookup
because resolving 5,376 (row, col) pairs one query at a time would
dominate the runtime of every ingest.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import DataSource, GridCell, Region, Variable


class UnknownReference(LookupError):
    """A slug that is not in the database.

    Almost always means the seed script has not been run, so say so.
    """

    def __init__(self, kind: str, slug: str) -> None:
        super().__init__(
            f"No {kind} with slug {slug!r}. "
            f"Run `python scripts/seed_reference_data.py` to populate "
            f"regions, variables and data sources."
        )
        self.kind = kind
        self.slug = slug


def get_region(session: Session, slug: str) -> Region:
    region = session.scalar(select(Region).where(Region.slug == slug))
    if region is None:
        raise UnknownReference("region", slug)
    return region


def find_region(session: Session, slug: str) -> Optional[Region]:
    return session.scalar(select(Region).where(Region.slug == slug))


def get_variable(session: Session, slug: str) -> Variable:
    variable = session.scalar(select(Variable).where(Variable.slug == slug))
    if variable is None:
        raise UnknownReference("variable", slug)
    return variable


def get_variables(session: Session, slugs: Sequence[str]) -> List[Variable]:
    """Resolve several variables, preserving the order given.

    Order matters: it is the channel order of the model input tensor.
    """
    rows = session.scalars(select(Variable).where(Variable.slug.in_(list(slugs)))).all()
    by_slug = {v.slug: v for v in rows}
    missing = [s for s in slugs if s not in by_slug]
    if missing:
        raise UnknownReference("variable", ", ".join(missing))
    return [by_slug[s] for s in slugs]


def get_data_source(session: Session, slug: str) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.slug == slug))
    if source is None:
        raise UnknownReference("data source", slug)
    return source


def upsert_data_source(session: Session, slug: str, **fields) -> DataSource:
    """Get-or-create a data source.

    Lets a new collector register its own source on first run rather than
    requiring a schema change or a seed-script edit.
    """
    source = session.scalar(select(DataSource).where(DataSource.slug == slug))
    if source is None:
        source = DataSource(slug=slug, name=fields.pop("name", slug), **fields)
        session.add(source)
        session.flush()
    else:
        for key, value in fields.items():
            if value is not None:
                setattr(source, key, value)
    return source


def upsert_variable(session: Session, slug: str, **fields) -> Variable:
    variable = session.scalar(select(Variable).where(Variable.slug == slug))
    if variable is None:
        variable = Variable(
            slug=slug,
            display_name=fields.pop("display_name", slug),
            kind=fields.pop("kind", "derived"),
            **fields,
        )
        session.add(variable)
        session.flush()
    else:
        for key, value in fields.items():
            if value is not None:
                setattr(variable, key, value)
    return variable


# ---------------------------------------------------------------------------
# Grid cell lookup
# ---------------------------------------------------------------------------

# Keyed by region id. Grid cells are immutable once seeded -- changing the
# grid means retraining the model -- so caching for the life of the
# process is safe.
_CELL_ID_CACHE: Dict[int, Dict[Tuple[int, int], int]] = {}


def get_cell_id_map(session: Session, region: Region) -> Dict[Tuple[int, int], int]:
    """Map ``(row, col) -> grid_cells.id`` for a region.

    Cached per process. Call ``clear_cell_cache()`` in tests that recreate
    the grid.
    """
    cached = _CELL_ID_CACHE.get(region.id)
    if cached is not None:
        return cached

    rows = session.execute(
        select(GridCell.row, GridCell.col, GridCell.id).where(
            GridCell.region_id == region.id
        )
    ).all()
    if not rows:
        raise UnknownReference("grid cells for region", region.slug)

    mapping = {(r, c): cell_id for r, c, cell_id in rows}
    expected = region.grid_rows * region.grid_cols
    if len(mapping) != expected:
        raise RuntimeError(
            f"Region {region.slug!r} declares a {region.grid_rows}x{region.grid_cols} "
            f"grid ({expected} cells) but {len(mapping)} grid_cells rows exist. "
            f"Re-run scripts/seed_reference_data.py."
        )

    _CELL_ID_CACHE[region.id] = mapping
    return mapping


def get_ordered_cell_ids(session: Session, region: Region) -> List[int]:
    """Cell ids in row-major order, i.e. ``arr.ravel()`` order.

    This is the ordering that lets a flat list of values be reshaped
    straight into ``(rows, cols)`` without a per-element lookup.
    """
    mapping = get_cell_id_map(session, region)
    return [
        mapping[(r, c)]
        for r in range(region.grid_rows)
        for c in range(region.grid_cols)
    ]


def clear_cell_cache(region_id: Optional[int] = None) -> None:
    if region_id is None:
        _CELL_ID_CACHE.clear()
    else:
        _CELL_ID_CACHE.pop(region_id, None)
